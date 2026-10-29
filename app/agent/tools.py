"""Definisi + implementasi tool agent (PRD 7.2). Setiap pemanggilan dicatat ke agent_logs."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.plan_validator import normalize_plan, validate_plan
from app.config import get_settings
from app.db.models import (AgentLog, AgentQuestion, Checkpoint, Measurement, Plan, Planting, Pot,
                           User)
from app.db.types import utcnow
from app.domain import evaluator, suitability
from app.domain.catalog import PARAMS, get_crop
from app.domain.remediation import get_remediation as domain_get_remediation
from app.notify import fcm
from app.repo import (age_hours, checkpoint_dict, last_candidates, latest_measurement,
                      measurement_dict, owned_pot, plan_dict, reading_of)

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Error yang pesannya aman ditampilkan (bahasa Indonesia)."""


@dataclass
class ToolContext:
    db: Session
    user: User
    feature: str
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    step: str = ""
    pot_id: uuid.UUID | None = None
    plan_id: uuid.UUID | None = None
    planting_id: uuid.UUID | None = None
    plan_kind: str = "soil_first"
    user_choice: bool = False
    candidate_ids: set[str] | None = None
    generated_by: str = "template"
    trace: list[dict] = field(default_factory=list)
    pending_question: AgentQuestion | None = None

    def log(self, tool: str, args: dict, result: Any, latency_ms: int, error: str | None = None,
            llm_model: str | None = None, pot_id: Any = None) -> None:
        pid = pot_id or args.get("pot_id") if isinstance(args, dict) else pot_id
        try:
            pid = uuid.UUID(str(pid)) if pid else self.pot_id
        except ValueError:
            pid = self.pot_id
        self.db.add(AgentLog(
            run_id=self.run_id, feature=self.feature, step=self.step or tool, tool=tool,
            args=jsonable_encoder(args), result=jsonable_encoder(result), latency_ms=latency_ms,
            llm_model=llm_model, error=error, user_id=self.user.id, pot_id=pid, plan_id=self.plan_id,
        ))
        self.db.flush()

    def call(self, name: str, args: dict | str | None, allowed: set[str] | None = None) -> dict:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = None
        if not isinstance(args, dict):
            result, error = {"error": "Argumen tool harus berupa objek JSON."}, "bad_arguments"
            self.log(name, {"raw": str(args)}, result, 0, error)
            self.trace.append({"tool": name, "args": {}, "ok": False})
            return result
        start = time.perf_counter()
        error = None
        if name not in TOOLS or (allowed is not None and name not in allowed):
            result, error = {"error": f"Tool '{name}' tidak tersedia."}, "tool_not_allowed"
        else:
            try:
                result = TOOLS[name][0](self, **args)
            except ToolError as e:
                result, error = {"error": str(e)}, str(e)
            except TypeError:
                result, error = {"error": "Argumen tool tidak sesuai."}, "bad_arguments"
            except Exception:  # noqa: BLE001
                log.exception("tool_failed", extra={"tool": name})
                result, error = {"error": "Terjadi kesalahan saat menjalankan tool."}, "internal_error"
        if error is None and isinstance(result, dict) and result.get("validation_errors"):
            error = "validation_failed"
        result = jsonable_encoder(result)
        self.log(name, args, result, int((time.perf_counter() - start) * 1000), error)
        self.trace.append({"tool": name, "args": jsonable_encoder(args), "ok": error is None})
        return result


# ---------------------------------------------------------------- implementasi

def _pot(ctx: ToolContext, pot_id: Any) -> Pot:
    pot = owned_pot(ctx.db, ctx.user, pot_id or ctx.pot_id)
    if pot is None:
        raise ToolError("Pot tidak ditemukan.")
    return pot


def _plan(ctx: ToolContext, plan_id: Any) -> Plan:
    try:
        plan = ctx.db.get(Plan, uuid.UUID(str(plan_id or ctx.plan_id)))
    except ValueError:
        plan = None
    if plan is None or owned_pot(ctx.db, ctx.user, plan.pot_id) is None:
        raise ToolError("Rencana tidak ditemukan.")
    return plan


def get_latest_reading(ctx: ToolContext, pot_id: str | None = None) -> dict:
    pot = _pot(ctx, pot_id)
    m = latest_measurement(ctx.db, pot.id)
    if m is None:
        return {"pot_id": str(pot.id), "reading": None,
                "message": "Belum ada bacaan untuk pot ini. Silakan ukur dengan Potani."}
    return {"pot_id": str(pot.id), "reading": measurement_dict(m), "age_hours": age_hours(m)}


def get_pot_history(ctx: ToolContext, pot_id: str | None = None, limit: int = 10) -> dict:
    pot = _pot(ctx, pot_id)
    limit = max(1, min(int(limit or 10), 50))
    ms = ctx.db.scalars(select(Measurement).where(Measurement.pot_id == pot.id, Measurement.invalid.is_(False))
                        .order_by(Measurement.measured_at.desc()).limit(limit)).all()
    plans = ctx.db.scalars(select(Plan).where(Plan.pot_id == pot.id).order_by(Plan.created_at.desc())
                           .limit(5)).all()
    plantings = ctx.db.scalars(select(Planting).where(Planting.pot_id == pot.id)
                               .order_by(Planting.created_at.desc()).limit(5)).all()
    return {
        "pot": {"pot_id": str(pot.id), "name": pot.name, "light_level": pot.light_level,
                "container_type": pot.container_type},
        "measurements": [measurement_dict(m) for m in ms],
        "plans": [plan_dict(ctx.db, p) for p in plans],
        "plantings": [{"planting_id": str(p.id), "crop_id": p.crop_id, "status": p.status,
                       "source": p.source, "planted_at": p.planted_at} for p in plantings],
    }


def get_crop_requirements(ctx: ToolContext, crop_id: str) -> dict:
    crop = get_crop(crop_id)
    if crop is None:
        raise ToolError("Tanaman tidak dikenal.")
    return {"crop_id": crop_id, "display_name": crop.get("display_name"), "params": crop["params"],
            "light": crop["light"]}


def run_suitability(ctx: ToolContext, pot_id: str | None = None, reading: dict | None = None,
                    light_level: str | None = None, container_type: str | None = None) -> dict:
    measurement_id = None
    if pot_id or (reading is None and ctx.pot_id):
        pot = _pot(ctx, pot_id)
        m = latest_measurement(ctx.db, pot.id, "pre_plant") or latest_measurement(ctx.db, pot.id)
        if m is None:
            raise ToolError("Belum ada bacaan untuk pot ini. Silakan ukur dengan Potani.")
        reading = reading_of(m)
        measurement_id = str(m.id)
        light_level = light_level or pot.light_level or ctx.user.light_default
        container_type = container_type or pot.container_type or ctx.user.container_default
    if not isinstance(reading, dict):
        raise ToolError("Butuh pot_id atau reading.")
    reading = {p: reading.get(p) for p in PARAMS}
    result = suitability.run_suitability(reading, light_level, container_type)
    result["measurement_id"] = measurement_id
    return result


def run_evaluator(ctx: ToolContext, crop_id: str, pot_id: str | None = None) -> dict:
    pot = _pot(ctx, pot_id)
    if get_crop(crop_id) is None:
        raise ToolError("Tanaman tidak dikenal.")
    m = latest_measurement(ctx.db, pot.id)
    if m is None:
        raise ToolError("Belum ada bacaan untuk pot ini. Silakan ukur dengan Potani.")
    result = evaluator.evaluate(crop_id, reading_of(m), pot.light_level or ctx.user.light_default,
                                ctx.user.materials_owned)
    result["measurement_id"] = str(m.id)
    return result


def get_remediation(ctx: ToolContext, param: str, condition: str,
                    materials_owned: list[str] | None = None) -> dict:
    if param not in PARAMS or condition not in ("low", "high"):
        raise ToolError("param atau condition tidak dikenal.")
    owned = materials_owned if materials_owned is not None else (ctx.user.materials_owned or [])
    return domain_get_remediation(param, condition, owned)


def get_weather(ctx: ToolContext, lat: float | None = None, lon: float | None = None) -> dict:
    # P2: integrasi prakiraan cuaca asli. Sementara stub agar alur tetap jalan.
    return {"available": False, "message": "Prakiraan cuaca belum tersedia."}


def ask_user(ctx: ToolContext, question: str, options: list[str] | None = None, field: str | None = None) -> dict:
    q = AgentQuestion(run_id=ctx.run_id, feature=ctx.feature, user_id=ctx.user.id, pot_id=ctx.pot_id,
                      plan_id=ctx.plan_id, question=question[:500], options=list(options or [])[:6],
                      field=field)
    ctx.db.add(q)
    ctx.db.flush()
    ctx.pending_question = q
    return {"question_id": str(q.id), "status": "pending", "question": q.question, "options": q.options}


def create_plan(ctx: ToolContext, plan: dict) -> dict:
    if ctx.pot_id is None:
        raise ToolError("Konteks pot tidak ada.")
    candidates = ctx.candidate_ids if ctx.candidate_ids is not None else last_candidates(ctx.db, ctx.pot_id)
    errors = validate_plan(plan, candidate_ids=candidates, user_choice=ctx.user_choice)
    if errors:
        return {"error": "Rencana tidak valid.", "validation_errors": errors}
    norm = normalize_plan(plan)
    row = Plan(pot_id=ctx.pot_id, planting_id=ctx.planting_id, kind=ctx.plan_kind, crop_id=norm["crop_id"],
               goal=norm["goal"], steps=norm["steps"], targets=norm["targets"], state="PLANNED",
               generated_by=ctx.generated_by, history=[])
    ctx.db.add(row)
    ctx.db.flush()
    ctx.plan_id = row.id
    return {"plan_id": str(row.id), "plan": norm}


def update_plan(ctx: ToolContext, plan_id: str, changes: dict, reason: str = "") -> dict:
    plan = _plan(ctx, plan_id)
    current = {"goal": plan.goal, "crop_id": plan.crop_id, "steps": plan.steps, "targets": plan.targets,
               "checkpoints": []}
    merged = {**current, **{k: v for k, v in (changes or {}).items() if k in current}}
    planting = ctx.db.get(Planting, plan.planting_id) if plan.planting_id else None
    user_choice = planting is None or planting.source == "user_choice"
    candidates = last_candidates(ctx.db, plan.pot_id)
    if plan.crop_id:
        candidates.add(plan.crop_id)  # tanaman rencana yang sedang berjalan selalu sah
    errors = validate_plan(merged, candidate_ids=candidates, user_choice=user_choice)
    if errors:
        return {"error": "Rencana tidak valid.", "validation_errors": errors}
    norm = normalize_plan(merged)
    same = ([(s["action"], s["remediation_ref"]) for s in norm["steps"]]
            == [(s.get("action"), s.get("remediation_ref")) for s in plan.steps or []] and norm["targets"] == plan.targets)
    if same and ctx.generated_by == "llm":  # template deterministik boleh mengulang bila opsi bersumber hanya satu
        return {"error": "Rencana tidak valid.", "validation_errors": [
            "Revisi sama persis dengan rencana yang belum berhasil. Pilih opsi perbaikan lain dari get_remediation, "
            "atau escalate bila tidak ada opsi lain."]}
    plan.history = [*(plan.history or []), {
        "version": plan.version, "goal": plan.goal, "steps": plan.steps, "targets": plan.targets,
        "reason": reason, "replaced_at": utcnow().isoformat(),
    }]
    plan.goal, plan.steps, plan.targets = norm["goal"], norm["steps"], norm["targets"]
    plan.version += 1
    plan.generated_by = ctx.generated_by
    for c in ctx.db.scalars(select(Checkpoint).where(Checkpoint.plan_id == plan.id,
                                                     Checkpoint.status.in_(("scheduled", "reminded")))):
        c.status = "cancelled"
    ctx.db.flush()
    return {"plan_id": str(plan.id), "version": plan.version, "plan": norm}


def schedule_checkpoint(ctx: ToolContext, plan_id: str, param: str, due_in_days: float) -> dict:
    plan = _plan(ctx, plan_id)
    s = get_settings()
    if param not in PARAMS:
        raise ToolError("Parameter tidak dikenal.")
    min_due = s.verify_delay_days(param)
    if not isinstance(due_in_days, (int, float)) or due_in_days < min_due:
        return {"error": "Jeda checkpoint terlalu pendek.",
                "validation_errors": [f"due_in_days minimal {min_due} untuk {param}."]}
    if not any(t.get("param") == param for t in plan.targets or []):
        raise ToolError("Rencana tidak punya target untuk parameter ini.")
    cp = Checkpoint(plan_id=plan.id, pot_id=plan.pot_id, param=param, due_in_days=float(due_in_days),
                    due_at=utcnow() + s.sim_days(due_in_days), status="scheduled", plan_version=plan.version)
    ctx.db.add(cp)
    ctx.db.flush()
    return {"checkpoint_id": str(cp.id), **checkpoint_dict(cp)}


def send_reminder(ctx: ToolContext, title: str, body: str, user_id: str | None = None,
                  deep_link: str | None = None) -> dict:
    if user_id and str(user_id) != str(ctx.user.id):
        raise ToolError("Pengingat hanya bisa dikirim ke akunmu sendiri.")
    return fcm.send_push(ctx.user, title[:100], body[:300], deep_link)


def log_outcome(ctx: ToolContext, planting_id: str, status: str, note: str = "") -> dict:
    if status not in ("growing", "harvested", "failed", "abandoned"):
        raise ToolError("Status tanam tidak dikenal.")
    try:
        planting = ctx.db.get(Planting, uuid.UUID(str(planting_id)))
    except ValueError:
        planting = None
    if planting is None or owned_pot(ctx.db, ctx.user, planting.pot_id) is None:
        raise ToolError("Data tanam tidak ditemukan.")
    planting.status = status
    planting.outcome_note = note[:1000] if note else planting.outcome_note
    if status == "growing" and planting.planted_at is None:
        planting.planted_at = utcnow()
    ctx.db.flush()
    return {"ok": True, "planting_id": str(planting.id), "status": status}


def escalate(ctx: ToolContext, pot_id: str | None = None, reason: str = "") -> dict:
    pot = _pot(ctx, pot_id)
    pot.needs_help = True
    pot.help_reason = (reason or "")[:500] or "Rencana perbaikan belum berhasil setelah beberapa kali dicoba."
    fcm.send_push(ctx.user, "Pot butuh bantuan", f"{pot.name}: {pot.help_reason}", f"potani://pots/{pot.id}")
    ctx.db.flush()
    return {"ok": True, "pot_id": str(pot.id)}


# ---------------------------------------------------------------- skema function calling (Groq)

def _nullable(schema: dict) -> dict:
    """Field opsional boleh null: Groq menolak tool call (400 tool_use_failed) bila model mengisi null."""
    schema = dict(schema)
    if isinstance(schema.get("type"), str):
        schema["type"] = [schema["type"], "null"]
    if "enum" in schema:
        schema["enum"] = [*schema["enum"], None]
    return schema


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    properties = {k: v if k in required else _nullable(v) for k, v in properties.items()}
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_S = {"type": "string"}
_PARAM = {"type": "string", "enum": list(PARAMS)}
_READING = {"type": "object", "properties": {"ph": {"type": "number"}, "moisture": {"type": "number"},
                                              "temperature": {"type": "number"}}}
_PLAN = {"type": "object", "properties": {
    "goal": _S, "crop_id": _S,
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "order": {"type": "integer"}, "action": _S, "remediation_ref": _S}, "required": ["action"]}},
    "targets": {"type": "array", "items": {"type": "object", "properties": {
        "param": _PARAM, "min": {"type": "number"}, "max": {"type": "number"}}}},
    "checkpoints": {"type": "array", "items": {"type": "object", "properties": {
        "param": _PARAM, "due_in_days": {"type": "number"}}}},
}, "required": ["goal", "crop_id", "steps", "targets", "checkpoints"]}

TOOLS: dict[str, tuple[Callable[..., dict], dict]] = {
    "get_latest_reading": (get_latest_reading, _fn(
        "get_latest_reading", "Ambil bacaan sensor terbaru sebuah pot beserta umur datanya (jam).",
        {"pot_id": _S}, ["pot_id"])),
    "get_pot_history": (get_pot_history, _fn(
        "get_pot_history", "Riwayat pot: bacaan berlabel tahap, rencana, checkpoint, dan data tanam.",
        {"pot_id": _S, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["pot_id"])),
    "get_crop_requirements": (get_crop_requirements, _fn(
        "get_crop_requirements", "Rentang pH, kelembapan, suhu, dan cahaya sebuah tanaman beserta sumbernya.",
        {"crop_id": _S}, ["crop_id"])),
    "run_suitability": (run_suitability, _fn(
        "run_suitability", "Hitung tanaman yang cocok untuk tanah pot (skor kesesuaian deterministik).",
        {"pot_id": _S, "reading": _READING, "light_level": {"type": "string", "enum": ["full", "partial", "shade"]},
         "container_type": _S}, [])),
    "run_evaluator": (run_evaluator, _fn(
        "run_evaluator", "Evaluasi apakah tanaman pilihan cocok dengan tanah pot dan apa yang perlu diperbaiki.",
        {"crop_id": _S, "pot_id": _S}, ["crop_id", "pot_id"])),
    "get_remediation": (get_remediation, _fn(
        "get_remediation", "Opsi perbaikan bersumber untuk parameter yang terlalu rendah/tinggi.",
        {"param": _PARAM, "condition": {"type": "string", "enum": ["low", "high"]},
         "materials_owned": {"type": "array", "items": _S}}, ["param", "condition"])),
    "get_weather": (get_weather, _fn(
        "get_weather", "Ringkasan prakiraan cuaca di lokasi pengguna.",
        {"lat": {"type": "number"}, "lon": {"type": "number"}}, [])),
    "ask_user": (ask_user, _fn(
        "ask_user", "Ajukan pertanyaan ke pengguna bila data kurang. Jawaban datang belakangan.",
        {"question": _S, "options": {"type": "array", "items": _S}}, ["question"])),
    "create_plan": (create_plan, _fn(
        "create_plan", "Simpan rencana perbaikan baru (akan divalidasi).", {"plan": _PLAN}, ["plan"])),
    "update_plan": (update_plan, _fn(
        "update_plan", "Revisi rencana yang ada (akan divalidasi). changes berisi goal/steps/targets.",
        {"plan_id": _S, "changes": _PLAN, "reason": _S}, ["plan_id", "changes", "reason"])),
    "schedule_checkpoint": (schedule_checkpoint, _fn(
        "schedule_checkpoint", "Jadwalkan pengukuran ulang untuk memverifikasi rencana.",
        {"plan_id": _S, "param": _PARAM, "due_in_days": {"type": "number"}}, ["plan_id", "param", "due_in_days"])),
    "send_reminder": (send_reminder, _fn(
        "send_reminder", "Kirim notifikasi pengingat ke pengguna.",
        {"user_id": _S, "title": _S, "body": _S, "deep_link": _S}, ["title", "body"])),
    "log_outcome": (log_outcome, _fn(
        "log_outcome", "Catat hasil tanam (growing/harvested/failed/abandoned).",
        {"planting_id": _S, "status": {"type": "string", "enum": ["growing", "harvested", "failed", "abandoned"]},
         "note": _S}, ["planting_id", "status"])),
    "escalate": (escalate, _fn(
        "escalate", "Tandai pot butuh bantuan manusia (penyuluh/komunitas).",
        {"pot_id": _S, "reason": _S}, ["pot_id", "reason"])),
}

READ_TOOLS = {"get_latest_reading", "get_pot_history", "get_crop_requirements", "run_suitability",
              "run_evaluator", "get_remediation", "get_weather"}
F6_TOOLS = {"get_latest_reading", "get_pot_history", "get_remediation", "ask_user", "run_suitability",
            "update_plan", "schedule_checkpoint", "log_outcome", "escalate"}
F7_TOOLS = READ_TOOLS | {"ask_user"}
F7_WRITE_TOOLS = {"send_reminder", "schedule_checkpoint"}


def schemas(names: set[str]) -> list[dict]:
    return [TOOLS[n][1] for n in TOOLS if n in names]

