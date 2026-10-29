"""Penyusunan rencana: LLM → validator → ulang (maks 2) → template deterministik (PRD 7.4)."""
from __future__ import annotations

import json
import time

from app.agent.llm import error_tag, get_llm
from app.agent.plan_validator import validate_plan
from app.agent.tools import ToolContext
from app.config import get_settings
from app.domain.catalog import PARAM_LABELS, get_crop
from app.domain.remediation import get_remediation

PLAN_SYSTEM_PROMPT = """Kamu adalah perencana perawatan tanah Potani.
Aturan wajib:
1. Kamu TIDAK menentukan cocok/tidaknya tanaman, rentang, angka, atau dosis. Semua itu sudah dihitung kode.
2. Hanya pakai data dari JSON yang diberikan. Jangan menulis angka apa pun di teks 'action' kecuali dosis yang
   persis sama dengan opsi perbaikan yang kamu rujuk lewat 'remediation_ref'.
3. Setiap langkah yang memakai bahan wajib punya 'remediation_ref' dari daftar opsi.
4. 'targets' hanya untuk parameter di remediation_options dan harus berada di dalam rentang optimal tanaman. 'checkpoints.due_in_days' minimal sama dengan
   'verify_after_days' parameternya.
   Setiap target wajib punya checkpoint.
5. Maksimal 6 langkah. Gunakan bahasa Indonesia sehari-hari yang singkat.
Balas HANYA dengan JSON: {"goal": str, "crop_id": str, "steps": [{"order": int, "action": str,
"remediation_ref": str|null}], "targets": [{"param": str, "min": number, "max": number}],
"checkpoints": [{"param": str, "due_in_days": number}]}"""


def problems_of(evaluation: dict) -> list[tuple[str, str]]:
    return [(p, v["status"]) for p, v in evaluation["per_param"].items() if v["status"] in ("low", "high")]


def remediation_options(evaluation: dict, materials_owned: list[str] | None) -> dict[str, dict]:
    out = {}
    for param, status in problems_of(evaluation):
        rem = get_remediation(param, status, materials_owned)
        if rem["options"]:
            out[param] = rem
    return out


def template_plan(crop_id: str, evaluation: dict, remediations: dict[str, dict], growing: bool = False) -> dict | None:
    """Rencana deterministik dari opsi perbaikan bersumber pertama untuk setiap masalah."""
    if not remediations:
        return None
    crop = get_crop(crop_id)
    s = get_settings()
    steps, targets, checkpoints = [], [], []
    for param, rem in remediations.items():
        opt = rem["options"][0]
        if opt.get("dose") is not None:
            action = f"Berikan {opt['material']} sebanyak {opt['dose']} {opt['unit']} per {opt['per']}"
        else:
            action = f"Lakukan: {opt['material']}"
        steps.append({"order": len(steps) + 1, "action": action, "remediation_ref": opt["ref"]})
        spec = crop["params"][param]
        targets.append({"param": param, "min": spec["opt_min"], "max": spec["opt_max"]})
        checkpoints.append({"param": param, "due_in_days": s.verify_delay_days(param)})
    steps.append({"order": len(steps) + 1, "remediation_ref": None,
                  "action": "Tandai rencana sebagai dikerjakan, lalu ukur ulang tanah dengan Potani saat pengingat muncul"})
    labels = " dan ".join(PARAM_LABELS[p].lower() for p in remediations)
    name = crop.get("display_name", crop_id)
    goal = f"Memperbaiki {labels} untuk {name}" if growing else f"Memperbaiki {labels} sebelum menanam {name}"
    return {"goal": goal, "crop_id": crop_id, "steps": steps[:6], "targets": targets, "checkpoints": checkpoints}


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def llm_plan(ctx: ToolContext, crop_id: str, evaluation: dict, remediations: dict[str, dict],
             extra_context: dict | None = None) -> dict | None:
    """Minta LLM menyusun rencana; validasi; minta ulang maks PLAN_MAX_REGENERATIONS kali."""
    llm = get_llm()
    s = get_settings()
    crop = get_crop(crop_id)
    payload = {
        "crop_id": crop_id,
        "crop_optimal": {p: {"opt_min": v["opt_min"], "opt_max": v["opt_max"]}
                         for p, v in crop["params"].items() if not v.get("unsourced")},
        "evaluation": {p: {"value": v.get("value"), "status": v["status"]}
                       for p, v in evaluation["per_param"].items()},
        "remediation_options": remediations,
        **(extra_context or {}),
    }
    messages = [{"role": "system", "content": PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    for attempt in range(1 + s.plan_max_regenerations):
        start = time.perf_counter()
        resp = llm.chat(messages, json_mode=True)
        latency = int((time.perf_counter() - start) * 1000)
        if resp is None:
            ctx.log("llm.plan", {"attempt": attempt}, None, latency, error_tag(llm), getattr(llm, "model", None))
            return None
        plan = _parse_json(resp.get("content"))
        errors = ["Balasan bukan JSON yang valid."] if plan is None else validate_plan(
            plan, candidate_ids=ctx.candidate_ids or set(), user_choice=ctx.user_choice)
        if plan is not None:
            extra = {t.get("param") for t in plan.get("targets") or [] if isinstance(t, dict)} - set(remediations)
            if extra:
                errors.append(f"Target hanya untuk parameter yang perlu diperbaiki ({sorted(remediations)}); "
                              f"hapus target {sorted(extra)}.")
        ctx.log("llm.plan", {"attempt": attempt}, {"plan": plan, "validation_errors": errors}, latency,
                "validation_failed" if errors else None, resp.get("model"))
        if not errors:
            return plan
        messages.append({"role": "assistant", "content": resp.get("content") or ""})
        messages.append({"role": "user", "content": "Rencana ditolak validator:\n- " + "\n- ".join(errors)
                         + "\nPerbaiki dan balas ulang dengan JSON saja."})
    return None


def build_and_save_plan(ctx: ToolContext, crop_id: str, evaluation: dict, growing: bool = False) -> dict | None:
    """Susun rencana (LLM atau template), simpan lewat create_plan, lalu jadwalkan checkpoint."""
    remediations = remediation_options(evaluation, ctx.user.materials_owned)
    if not remediations:
        return None
    plan = llm_plan(ctx, crop_id, evaluation, remediations)
    ctx.generated_by = "llm"
    if plan is None:
        plan = template_plan(crop_id, evaluation, remediations, growing)
        ctx.generated_by = "template"
    ctx.step = "create_plan"
    created = ctx.call("create_plan", {"plan": plan})
    if "plan_id" not in created and ctx.generated_by == "llm":
        plan = template_plan(crop_id, evaluation, remediations, growing)
        ctx.generated_by = "template"
        created = ctx.call("create_plan", {"plan": plan})
    if "plan_id" not in created:
        return None
    ctx.step = "schedule_checkpoint"
    for c in created["plan"]["checkpoints"]:
        ctx.call("schedule_checkpoint", {"plan_id": created["plan_id"], "param": c["param"],
                                         "due_in_days": c["due_in_days"]})
    return created
