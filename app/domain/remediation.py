"""F4: opsi perbaikan bersumber (PRD 6.4). Deterministik."""
from __future__ import annotations

from app.config import get_settings
from app.domain.catalog import PARAM_LABELS, get_rules

CONDITIONS = ("low", "high")


def get_remediation(param: str, condition: str, materials_owned: list[str] | None = None) -> dict:
    """Opsi rumah tangga lebih dulu, lalu pupuk komersial; hanya opsi yang punya `source`."""
    owned = {m.strip().lower() for m in (materials_owned or [])}
    options = (get_rules().get(param) or {}).get(condition) or []
    sourced = [o for o in options if o.get("source")]
    unsourced = [o["material"] for o in options if not o.get("source")]

    def key(o: dict):
        return (not o.get("household", False), o["material"].lower() not in owned, o["ref"])

    result = []
    for o in sorted(sourced, key=key):
        result.append({**{k: o.get(k) for k in ("ref", "material", "household", "dose", "unit", "per",
                                                 "source", "notes")},
                       "owned": o["material"].lower() in owned})
    return {
        "param": param,
        "condition": condition,
        "label": PARAM_LABELS.get(param, param),
        "options": result,
        "skipped_unsourced": unsourced,
        "verify_after_days": get_settings().verify_delay_days(param) if param in PARAM_LABELS else None,
        "message": None if result else "Belum ada acuan perbaikan yang bersumber untuk kondisi ini.",
    }
