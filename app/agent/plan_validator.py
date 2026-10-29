"""Validator rencana deterministik (PRD 7.4)."""
from __future__ import annotations

import re

from app.config import get_settings
from app.domain.catalog import PARAMS, get_crop, get_rule, is_sourced

MAX_STEPS = 6
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[float]:
    return {float(t.replace(",", ".")) for t in _NUM.findall(text or "")}


def validate_plan(plan: dict, *, candidate_ids: set[str] | None = None, user_choice: bool = False) -> list[str]:
    """Kembalikan daftar pesan error; kosong berarti valid."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["Rencana harus berupa objek JSON."]

    crop_id = plan.get("crop_id")
    crop = get_crop(crop_id) if crop_id else None
    # Aturan 1
    if crop is None:
        errors.append(f"crop_id '{crop_id}' tidak dikenal.")
    elif not user_choice and crop_id not in (candidate_ids or set()):
        errors.append(f"crop_id '{crop_id}' tidak ada di himpunan kandidat terakhir.")

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("Rencana wajib punya minimal 1 langkah.")
        steps = []
    # Aturan 5
    if len(steps) > MAX_STEPS:
        errors.append(f"Maksimal {MAX_STEPS} langkah, rencana berisi {len(steps)}.")

    # Aturan 2
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or not step.get("action"):
            errors.append(f"Langkah {i} wajib punya 'action'.")
            continue
        ref = step.get("remediation_ref")
        nums = _numbers(step["action"])
        if ref:
            rule = get_rule(ref)
            if rule is None:
                errors.append(f"Langkah {i}: remediation_ref '{ref}' tidak ada di rules.")
                continue
            if not rule.get("source"):
                errors.append(f"Langkah {i}: remediation_ref '{ref}' tidak punya sumber.")
                continue
            if "dose" in step and step["dose"] is not None and step["dose"] != rule.get("dose"):
                errors.append(f"Langkah {i}: dosis {step['dose']} tidak sama dengan rules ({rule.get('dose')}).")
            if "unit" in step and step["unit"] is not None and step["unit"] != rule.get("unit"):
                errors.append(f"Langkah {i}: satuan '{step['unit']}' tidak sama dengan rules ('{rule.get('unit')}').")
            allowed = _numbers(str(rule.get("dose") or "")) | _numbers(rule.get("per") or "")
            if rule.get("dose") is not None:
                allowed.add(float(rule["dose"]))
            extra = nums - allowed
            if extra:
                errors.append(f"Langkah {i}: angka {sorted(extra)} di teks tidak sesuai dosis rules.")
        elif nums:
            errors.append(f"Langkah {i}: berisi angka tetapi tidak merujuk remediation_ref.")

    # Aturan 3
    targets = plan.get("targets") or []
    if not isinstance(targets, list):
        errors.append("'targets' harus berupa daftar.")
        targets = []
    target_params = set()
    for t in targets:
        param = t.get("param") if isinstance(t, dict) else None
        if param not in PARAMS:
            errors.append(f"Target dengan param '{param}' tidak dikenal.")
            continue
        target_params.add(param)
        spec = crop["params"].get(param) if crop else None
        if not is_sourced(spec):
            errors.append(f"Target {param}: tanaman belum punya rentang bersumber.")
            continue
        lo, hi = t.get("min"), t.get("max")
        if lo is None or hi is None or lo > hi:
            errors.append(f"Target {param}: min/max tidak valid.")
        elif lo < spec["opt_min"] or hi > spec["opt_max"]:
            errors.append(f"Target {param} [{lo}, {hi}] di luar rentang optimal "
                          f"[{spec['opt_min']}, {spec['opt_max']}].")

    # Aturan 4
    s = get_settings()
    for c in plan.get("checkpoints") or []:
        param = c.get("param") if isinstance(c, dict) else None
        if param not in PARAMS:
            errors.append(f"Checkpoint dengan param '{param}' tidak dikenal.")
            continue
        due = c.get("due_in_days")
        min_due = s.verify_delay_days(param)
        if not isinstance(due, (int, float)) or due < min_due:
            errors.append(f"Checkpoint {param}: due_in_days {due} lebih pendek dari jeda minimum {min_due}.")
        if param not in target_params:
            errors.append(f"Checkpoint {param} tidak punya target.")
    checkpoint_params = {c.get("param") for c in plan.get("checkpoints") or [] if isinstance(c, dict)}
    if checkpoint_params:  # update_plan boleh tanpa checkpoint; dijadwalkan terpisah untuk setiap target
        for param in sorted(target_params - checkpoint_params):
            errors.append(f"Target {param} tidak punya checkpoint, jadi tidak akan pernah diverifikasi.")
    return errors


def normalize_plan(plan: dict) -> dict:
    """Lengkapi langkah dengan dosis/sumber dari rules agar aplikasi bisa menampilkan sumber."""
    steps = []
    for i, step in enumerate(plan.get("steps") or [], start=1):
        step = {"order": i, "action": step["action"], "remediation_ref": step.get("remediation_ref")}
        rule = get_rule(step["remediation_ref"]) if step["remediation_ref"] else None
        if rule:
            step.update({k: rule.get(k) for k in ("material", "dose", "unit", "per", "source")})
        steps.append(step)
    return {
        "goal": plan.get("goal") or "",
        "crop_id": plan.get("crop_id"),
        "steps": steps,
        "targets": [{"param": t["param"], "min": t["min"], "max": t["max"]} for t in plan.get("targets") or []],
        "checkpoints": [{"param": c["param"], "due_in_days": c["due_in_days"]}
                        for c in plan.get("checkpoints") or []],
    }
