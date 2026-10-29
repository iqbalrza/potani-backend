"""F6: closed-loop verification sebagai state machine (PRD 7.6).

State disimpan di `plans.state` karena satu putaran bisa berminggu-minggu. Keputusan "target tercapai"
selalu deterministik; LLM hanya dipakai untuk menyusun revisi rencana pada langkah REPLANNING.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import assistant_message, compact_json, error_tag, get_llm
from app.agent.planning import template_plan
from app.agent.tools import F6_TOOLS, ToolContext, schemas
from app.config import get_settings
from app.db.models import AgentQuestion, Checkpoint, Measurement, Plan, Planting, Pot, User
from app.db.types import utcnow
from app.domain.catalog import PARAM_LABELS, get_crop
from app.domain.remediation import get_remediation
from app.repo import latest_measurement, plan_dict, reading_of

log = logging.getLogger(__name__)

TRANSITIONS: dict[tuple[str, str], str] = {
    ("PLANNED", "mark_done"): "AWAITING_CHECKPOINT",
    ("AWAITING_CHECKPOINT", "due"): "CHECKPOINT_DUE",
    ("AWAITING_CHECKPOINT", "measured"): "VERIFYING",
    ("CHECKPOINT_DUE", "measured"): "VERIFYING",
    ("CHECKPOINT_DUE", "missed"): "MISSED",
    ("MISSED", "measured"): "VERIFYING",
    ("VERIFYING", "achieved"): "ACHIEVED",
    ("VERIFYING", "partial"): "AWAITING_CHECKPOINT",  # masih ada checkpoint lain pada versi ini
    ("VERIFYING", "not_achieved"): "REPLANNING",
    ("REPLANNING", "replanned"): "PLANNED",
    ("REPLANNING", "escalate"): "ESCALATED",
    ("REPLANNING", "ask_user"): "WAITING_USER",
    ("REPLANNING", "closed"): "CLOSED",
    ("WAITING_USER", "answered"): "REPLANNING",
}
TERMINAL = {"ACHIEVED", "ESCALATED", "CLOSED"}
WAITING_FOR_MEASUREMENT = ("AWAITING_CHECKPOINT", "CHECKPOINT_DUE", "MISSED")

F6_SYSTEM_PROMPT = """Kamu adalah agent perawatan tanah Potani yang merevisi rencana yang belum berhasil.
Aturan wajib:
1. Keputusan target tercapai atau tidak sudah dihitung kode. Kamu tidak menentukan rentang, angka, atau dosis.
2. Hanya pakai data dari hasil tool dan konteks yang diberikan. Dosis hanya boleh dari get_remediation dan
   setiap langkah berbahan wajib punya remediation_ref.
3. Akhiri dengan SALAH SATU: (a) update_plan lalu schedule_checkpoint untuk setiap target,
   (b) log_outcome bila pengguna menyatakan tanaman gagal/berhenti, atau (c) escalate bila tidak ada opsi aman.
4. Bila data kurang, pakai ask_user.
5. Gunakan bahasa Indonesia sehari-hari."""


class InvalidTransition(Exception):
    pass


def transition(plan: Plan, event: str) -> str:
    new = TRANSITIONS.get((plan.state, event))
    if new is None:
        raise InvalidTransition(f"{plan.state} --{event}--> ?")
    if event == "replanned":
        plan.replan_count += 1
    plan.state = new
    return new


def _owner(db: Session, plan: Plan) -> User:
    pot = db.get(Pot, plan.pot_id)
    return db.get(User, pot.owner_id)


def _ctx(db: Session, plan: Plan, step: str, run_id=None) -> ToolContext:
    ctx = ToolContext(db=db, user=_owner(db, plan), feature="F6", step=step, pot_id=plan.pot_id,
                      plan_id=plan.id, planting_id=plan.planting_id, plan_kind=plan.kind)
    if run_id is not None:
        ctx.run_id = run_id
    return ctx


# ---------------------------------------------------------------- aksi pengguna

def mark_done(db: Session, plan: Plan) -> Plan:
    """PLANNED → AWAITING_CHECKPOINT; jadwal checkpoint dihitung ulang mulai sekarang."""
    transition(plan, "mark_done")
    s = get_settings()
    now = utcnow()
    for cp in db.scalars(select(Checkpoint).where(Checkpoint.plan_id == plan.id,
                                                  Checkpoint.plan_version == plan.version,
                                                  Checkpoint.status == "scheduled")):
        cp.due_at = now + s.sim_days(cp.due_in_days)
    db.flush()
    return plan


# ---------------------------------------------------------------- dipanggil dari ingest (P2)

def attach_measurement_to_checkpoints(db: Session, m: Measurement) -> list[Checkpoint]:
    """Tutup checkpoint jatuh tempo pada pot ini dengan pengukuran baru (status → measured)."""
    if m.pot_id is None or m.invalid:
        return []
    q = (select(Checkpoint).join(Plan, Plan.id == Checkpoint.plan_id)
         .where(Checkpoint.pot_id == m.pot_id,
                Checkpoint.status.in_(("scheduled", "reminded", "missed")),
                Checkpoint.due_at <= utcnow(),
                Checkpoint.plan_version == Plan.version,
                Plan.state.in_(WAITING_FOR_MEASUREMENT)))
    closed = []
    for cp in db.scalars(q):
        if getattr(m, cp.param) is None:
            continue
        cp.status = "measured"
        cp.measurement_id = m.id
        closed.append(cp)
    db.flush()
    return closed


# ---------------------------------------------------------------- scheduler (P3)

def scan_due_checkpoints(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    q = (select(Checkpoint, Plan).join(Plan, Plan.id == Checkpoint.plan_id)
         .where(Checkpoint.status == "scheduled", Checkpoint.due_at <= now,
                Checkpoint.plan_version == Plan.version,
                Plan.state.in_(("AWAITING_CHECKPOINT", "CHECKPOINT_DUE"))))
    count = 0
    for cp, plan in db.execute(q).all():
        if plan.state == "AWAITING_CHECKPOINT":
            transition(plan, "due")
        cp.status = "reminded"
        cp.reminder_count = 1
        cp.last_reminded_at = now
        ctx = _ctx(db, plan, "scan_due_checkpoints")
        ctx.call("send_reminder", {"title": "Waktunya ukur ulang",
                                   "body": f"Ukur {PARAM_LABELS[cp.param].lower()} pot dengan Potani untuk "
                                           "memeriksa hasil rencana perawatan.",
                                   "deep_link": f"potani://plans/{plan.id}"})
        db.commit()
        count += 1
    return count


def scan_missed(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    s = get_settings()
    cutoff = now - s.sim_days(s.missed_after_days)
    q = (select(Checkpoint, Plan).join(Plan, Plan.id == Checkpoint.plan_id)
         .where(Checkpoint.status == "reminded", Checkpoint.last_reminded_at <= cutoff,
                Checkpoint.plan_version == Plan.version,
                Plan.state.in_(("CHECKPOINT_DUE", "MISSED"))))
    count = 0
    for cp, plan in db.execute(q).all():
        if plan.state == "CHECKPOINT_DUE":
            transition(plan, "missed")
        rereminders_sent = cp.reminder_count - 1
        ctx = _ctx(db, plan, "scan_missed")
        if rereminders_sent < s.max_rereminders:
            cp.reminder_count += 1
            cp.last_reminded_at = now
            ctx.call("send_reminder", {"title": "Pengingat: ukur ulang tanah",
                                       "body": "Pengukuran ulang belum masuk. Ukur dengan Potani agar rencana "
                                               "bisa diperiksa.",
                                       "deep_link": f"potani://plans/{plan.id}"})
        else:
            cp.status = "missed"
            ctx.log("mark_missed", {"checkpoint_id": str(cp.id)}, {"status": "missed"}, 0)
        db.commit()
        count += 1
    return count


def run_verify_queue(db: Session) -> int:
    q = (select(Checkpoint).join(Plan, Plan.id == Checkpoint.plan_id)
         .where(Checkpoint.status == "measured",
                Plan.state.in_((*WAITING_FOR_MEASUREMENT, "VERIFYING"))))
    count = 0
    for cp in db.scalars(q).all():
        try:
            verify_checkpoint(db, cp)
            db.commit()
            count += 1
        except Exception:  # noqa: BLE001 - satu item gagal tidak menghentikan antrean
            db.rollback()
            log.exception("verify_failed", extra={"checkpoint_id": str(cp.id)})
    return count


# ---------------------------------------------------------------- VERIFYING

def check_target(plan: Plan, cp: Checkpoint, m: Measurement) -> dict:
    value = getattr(m, cp.param)
    target = next((t for t in plan.targets or [] if t["param"] == cp.param), None)
    achieved = target is not None and value is not None and target["min"] <= value <= target["max"]
    condition = None
    if target is not None and value is not None and not achieved:
        condition = "low" if value < target["min"] else "high"
    return {"param": cp.param, "value": value, "target": target, "achieved": achieved, "condition": condition,
            "measurement_id": str(m.id)}


def verify_checkpoint(db: Session, cp: Checkpoint) -> str:
    plan = db.get(Plan, cp.plan_id)
    if plan.state in WAITING_FOR_MEASUREMENT:
        transition(plan, "measured")
    ctx = _ctx(db, plan, "verify")
    m = db.get(Measurement, cp.measurement_id)
    start = time.perf_counter()
    result = check_target(plan, cp, m)
    ctx.log("verify_target", {"checkpoint_id": str(cp.id)}, result, int((time.perf_counter() - start) * 1000))
    cp.verify_result = result
    cp.status = "verified"

    if not result["achieved"]:
        transition(plan, "not_achieved")
        db.flush()
        return replan(db, plan, result)

    remaining = db.scalars(select(Checkpoint).where(
        Checkpoint.plan_id == plan.id, Checkpoint.plan_version == plan.version, Checkpoint.id != cp.id,
        Checkpoint.status.in_(("scheduled", "reminded", "measured", "missed")))).first()
    if remaining is not None:
        transition(plan, "partial")
        db.flush()
        return plan.state

    transition(plan, "achieved")
    planting = db.get(Planting, plan.planting_id) if plan.planting_id else None
    crop = get_crop(plan.crop_id) if plan.crop_id else None
    name = crop.get("display_name", plan.crop_id) if crop else "tanamanmu"
    body = (f"Tanah sudah sesuai target. Silakan mulai menanam {name}."
            if planting is not None and planting.status == "planned"
            else "Target perawatan tercapai. Lanjutkan perawatan dan ukur kontrol secara berkala.")
    ctx.call("send_reminder", {"title": "Target tercapai", "body": body, "deep_link": f"potani://plans/{plan.id}"})
    db.flush()
    return plan.state


# ---------------------------------------------------------------- REPLANNING

def replan(db: Session, plan: Plan, verify_result: dict, answer: dict | None = None, run_id=None) -> str:
    s = get_settings()
    ctx = _ctx(db, plan, "replan", run_id)
    if plan.replan_count >= s.max_replans:
        ctx.call("escalate", {"pot_id": str(plan.pot_id),
                              "reason": "Target belum tercapai setelah beberapa kali revisi rencana."})
        transition(plan, "escalate")
        db.flush()
        return plan.state

    outcome = _agent_replan(ctx, plan, verify_result, answer)
    if outcome is None:
        outcome = _template_replan(ctx, plan, verify_result)
    if outcome == "replanned":
        transition(plan, "replanned")
        ctx.call("send_reminder", {"title": "Rencana diperbarui",
                                   "body": "Target belum tercapai, jadi rencana perawatan sudah direvisi. "
                                           "Cek langkah barunya di aplikasi.",
                                   "deep_link": f"potani://plans/{plan.id}"})
    elif outcome == "escalate":
        transition(plan, "escalate")
    elif outcome == "ask_user":
        transition(plan, "ask_user")
        if ctx.pending_question is not None:
            ctx.pending_question.context = {"verify_result": verify_result}
    elif outcome == "closed":
        transition(plan, "closed")
    db.flush()
    return plan.state


def _agent_replan(ctx: ToolContext, plan: Plan, verify_result: dict, answer: dict | None) -> str | None:
    """Loop agent (maks F6_MAX_ITERATIONS tool). None → pakai fallback deterministik."""
    s = get_settings()
    llm = get_llm()
    db = ctx.db
    ctx.generated_by = "llm"
    m = latest_measurement(db, plan.pot_id)
    context = {
        "plan": plan_dict(db, plan),
        "verify_result": verify_result,
        "latest_reading": reading_of(m) if m else None,
        "verify_after_days": {p: s.verify_delay_days(p) for p in PARAM_LABELS},
        "replan_count": plan.replan_count,
        "max_replans": s.max_replans,
    }
    if answer:
        context["user_answer"] = answer
    messages = [{"role": "system", "content": F6_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)}]
    tools = schemas(F6_TOOLS)
    updated = scheduled = False
    validation_failures = 0
    for _ in range(s.f6_max_iterations):
        start = time.perf_counter()
        resp = llm.chat(messages, tools=tools)
        latency = int((time.perf_counter() - start) * 1000)
        ctx.log("llm", {"messages": len(messages)}, resp and {"content": resp.get("content"),
                                                              "tool_calls": resp.get("tool_calls")},
                latency, None if resp else error_tag(llm), getattr(llm, "model", None))
        if resp is None:
            return None
        messages.append(assistant_message(resp))
        if not resp.get("tool_calls"):
            messages.append({"role": "user", "content": "Akhiri dengan update_plan + schedule_checkpoint, "
                                                        "log_outcome, atau escalate."})
            continue
        for tc in resp["tool_calls"]:
            try:
                args = json.loads(tc["arguments"] or "{}")
            except ValueError:
                args = tc["arguments"]
            if isinstance(args, dict) and tc["name"] in ("update_plan", "schedule_checkpoint"):
                args["plan_id"] = str(plan.id)  # agent hanya boleh mengubah rencana ini
            result = ctx.call(tc["name"], args, allowed=F6_TOOLS)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": compact_json(result)})
            ok = "error" not in result
            if tc["name"] == "update_plan":
                if ok:
                    updated, scheduled = True, False
                else:
                    validation_failures += 1
            elif tc["name"] == "schedule_checkpoint" and ok and updated:
                scheduled = True
            elif tc["name"] == "escalate" and ok:
                return "escalate"
            elif tc["name"] == "log_outcome" and ok:
                return "closed"
            elif tc["name"] == "ask_user" and ok:
                return "ask_user"
        if validation_failures > s.plan_max_regenerations:
            return None
        if updated and scheduled:
            _ensure_checkpoints(ctx, plan)
            return "replanned"
    if updated:
        _ensure_checkpoints(ctx, plan)
        return "replanned"
    return None


def _ensure_checkpoints(ctx: ToolContext, plan: Plan) -> None:
    """Setiap target versi baru wajib punya checkpoint; lengkapi secara deterministik bila agent lupa."""
    s = get_settings()
    have = {c.param for c in ctx.db.scalars(select(Checkpoint).where(
        Checkpoint.plan_id == plan.id, Checkpoint.plan_version == plan.version,
        Checkpoint.status == "scheduled"))}
    for t in plan.targets or []:
        if t["param"] not in have:
            ctx.call("schedule_checkpoint", {"plan_id": str(plan.id), "param": t["param"],
                                             "due_in_days": s.verify_delay_days(t["param"])})


def _template_replan(ctx: ToolContext, plan: Plan, verify_result: dict) -> str:
    """Fallback deterministik: pilih opsi perbaikan berikutnya (berputar sesuai replan_count)."""
    param, condition = verify_result["param"], verify_result.get("condition")
    rem = get_remediation(param, condition, ctx.user.materials_owned) if condition else None
    if not rem or not rem["options"] or not plan.crop_id:
        ctx.call("escalate", {"pot_id": str(plan.pot_id),
                              "reason": f"Belum ada opsi perbaikan bersumber untuk {PARAM_LABELS[param].lower()}."})
        return "escalate"
    option = rem["options"][(plan.replan_count + 1) % len(rem["options"])]
    rem = {**rem, "options": [option]}
    evaluation = {"per_param": {param: {"status": condition, "value": verify_result["value"]}}}
    new_plan = template_plan(plan.crop_id, evaluation, {param: rem}, growing=plan.kind == "remediation")
    ctx.generated_by = "template"
    res = ctx.call("update_plan", {"plan_id": str(plan.id), "changes": new_plan,
                                   "reason": "Target belum tercapai; rencana disusun ulang dari rules."})
    if "error" in res:
        ctx.call("escalate", {"pot_id": str(plan.pot_id), "reason": "Rencana perbaikan tidak bisa disusun ulang."})
        return "escalate"
    _ensure_checkpoints(ctx, plan)
    return "replanned"


def answer_question(db: Session, q: AgentQuestion, answer: str) -> str:
    """Jawaban `ask_user` melanjutkan run F6 yang sama."""
    plan = db.get(Plan, q.plan_id)
    transition(plan, "answered")
    db.flush()
    verify_result = (q.context or {}).get("verify_result") or {}
    return replan(db, plan, verify_result, {"question": q.question, "answer": answer}, run_id=q.run_id)

