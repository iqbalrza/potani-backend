"""F3: evaluasi kecocokan tanaman pilihan pengguna (PRD 6.3). Deterministik."""
from __future__ import annotations

from app.config import get_settings
from app.domain.catalog import PARAM_LABELS, PARAMS, get_crop, is_sourced
from app.domain.remediation import get_remediation

VERDICT_TEXT = {
    "cocok": "Tanah ini cocok untuk tanaman pilihanmu.",
    "perlu_perbaikan": "Tanaman ini bisa ditanam setelah tanah diperbaiki dulu.",
    "tidak_cocok": "Kondisi tanah saat ini di luar batas yang bisa ditoleransi tanaman ini.",
}


def param_status(x: float, spec: dict, mae: float) -> tuple[str, bool]:
    """Kembalikan (status, out_of_absolute)."""
    if x < spec["abs_min"]:
        return "low", True
    if x > spec["abs_max"]:
        return "high", True
    near_opt = abs(x - spec["opt_min"]) <= mae or abs(x - spec["opt_max"]) <= mae
    if near_opt:
        return "near_boundary", False
    if x < spec["opt_min"]:
        return "low", False
    if x > spec["opt_max"]:
        return "high", False
    return "ok", False


def evaluate(crop_id: str, reading: dict, light_level: str | None = None,
             materials_owned: list[str] | None = None) -> dict:
    crop = get_crop(crop_id)
    if crop is None:
        raise KeyError(crop_id)
    s = get_settings()
    per_param: dict[str, dict] = {}
    hints = []
    out_of_abs = False
    has_fix = False

    for param in PARAMS:
        spec = crop["params"].get(param)
        x = reading.get(param)
        label = PARAM_LABELS[param]
        if not is_sourced(spec):
            per_param[param] = {"value": x, "status": "no_reference", "message": f"{label}: belum ada acuan"}
            continue
        if x is None:
            per_param[param] = {"value": None, "status": "no_reading", "message": f"{label}: belum diukur"}
            continue
        status, outside = param_status(x, spec, s.mae(param))
        entry = {
            "value": x,
            "status": status,
            "out_of_absolute": outside,
            "optimal": {"min": spec["opt_min"], "max": spec["opt_max"]},
            "absolute": {"min": spec["abs_min"], "max": spec["abs_max"]},
            "source": spec["source"],
        }
        if status == "near_boundary":
            # Sisi batas dinyatakan eksplisit agar narasi LLM tidak menebak arah.
            side = "bawah" if abs(x - spec["opt_min"]) <= abs(x - spec["opt_max"]) else "atas"
            inside = spec["opt_min"] <= x <= spec["opt_max"]
            entry["boundary_side"] = "lower" if side == "bawah" else "upper"
            entry["message"] = (f"{label} masih di dalam rentang optimal tetapi dekat batas {side}; "
                                if inside else
                                f"{label} sedikit di luar batas {side} rentang optimal, masih dalam toleransi sensor; ") \
                + "sebaiknya ukur ulang untuk memastikan"
        elif status in ("low", "high"):
            out_of_abs = out_of_abs or outside
            has_fix = True
            arah = "rendah" if status == "low" else "tinggi"
            entry["message"] = (f"{label} terlalu {arah}, di luar batas yang bisa ditoleransi tanaman" if outside
                                else f"{label} terlalu {arah} dibanding rentang optimal")
            hints.append(get_remediation(param, status, materials_owned))
        else:
            entry["message"] = f"{label} sesuai rentang optimal"
        per_param[param] = entry

    light = crop.get("light") or {}
    light_status = "no_reference"
    if light.get("source") and light_level:
        light_status = "ok" if light_level in light.get("allowed", []) else "tidak_sesuai"

    if out_of_abs or light_status == "tidak_sesuai":
        verdict = "tidak_cocok"
    elif has_fix:
        verdict = "perlu_perbaikan"
    else:
        verdict = "cocok"

    return {
        "crop_id": crop_id,
        "display_name": crop.get("display_name", crop_id),
        "verdict": verdict,
        "verdict_text": VERDICT_TEXT[verdict],
        "per_param": per_param,
        "light": {"status": light_status, "pot_light": light_level, "allowed": light.get("allowed", []),
                  "source": light.get("source")},
        "no_reference": [p for p, v in per_param.items() if v["status"] == "no_reference"],
        "remeasure_suggested": any(v["status"] == "near_boundary" for v in per_param.values()),
        "remediation_hint": hints,
    }
