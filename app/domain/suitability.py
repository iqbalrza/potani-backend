"""Lapisan A: skor kesesuaian berbentuk fungsi EcoCrop (PRD 6.2). Sepenuhnya deterministik."""
from __future__ import annotations

import math

from app.config import get_settings
from app.domain.catalog import PARAM_LABELS, PARAMS, RANGE_KEYS, get_crops, is_sourced


def score_param(x: float, abs_min: float, opt_min: float, opt_max: float, abs_max: float) -> float:
    """s(x): 0 di luar batas absolut, linier di lereng, 1 di dalam rentang optimal."""
    if x < abs_min or x > abs_max:
        return 0.0
    if x < opt_min:
        return (x - abs_min) / (opt_min - abs_min) if opt_min > abs_min else 1.0
    if x <= opt_max:
        return 1.0
    return (abs_max - x) / (abs_max - opt_max) if abs_max > opt_max else 1.0


def is_near_boundary(x: float, spec: dict, mae: float) -> bool:
    return any(abs(x - spec[k]) <= mae for k in RANGE_KEYS)


def param_reason(param: str, x: float, spec: dict, near: bool) -> str | None:
    label = PARAM_LABELS[param]
    if x <= spec["abs_min"]:
        reason = f"{label} terlalu rendah, di luar batas yang bisa ditoleransi"
    elif x >= spec["abs_max"]:
        reason = f"{label} terlalu tinggi, di luar batas yang bisa ditoleransi"
    elif x < spec["opt_min"]:
        reason = f"{label} sedikit di bawah rentang optimal"
    elif x > spec["opt_max"]:
        reason = f"{label} sedikit di atas rentang optimal"
    else:
        reason = None
    if near:
        hint = f"{label} dekat batas rentang, hasil bisa berubah bila diukur ulang"
        reason = f"{reason}; {hint.lower()}" if reason else hint
    return reason


def score_crop(crop: dict, reading: dict) -> dict:
    """Nilai satu tanaman terhadap bacaan. Parameter tanpa sumber/bacaan dilewati."""
    s = get_settings()
    per_param: dict[str, dict] = {}
    skipped: list[str] = []
    reasons: list[str] = []
    for param in PARAMS:
        spec = crop["params"].get(param)
        x = reading.get(param)
        if not is_sourced(spec) or x is None:
            skipped.append(param)
            continue
        near = is_near_boundary(x, spec, s.mae(param))
        sc = round(score_param(x, *(spec[k] for k in RANGE_KEYS)), 4)
        per_param[param] = {"value": x, "score": sc, "near_boundary": near, "source": spec["source"]}
        r = param_reason(param, x, spec, near)
        if r:
            reasons.append(r)

    if not per_param:
        return {"crop_id": crop["crop_id"], "score": None, "per_param": {}, "skipped": skipped, "reasons": []}

    # Faktor pembatas: skor terkecil; seri → utamakan yang dekat batas, lalu urutan parameter.
    limiting = min(per_param, key=lambda p: (per_param[p]["score"], not per_param[p]["near_boundary"]))
    total = per_param[limiting]["score"]
    product = round(math.prod(v["score"] for v in per_param.values()), 4)
    return {
        "crop_id": crop["crop_id"],
        "display_name": crop.get("display_name", crop["crop_id"]),
        "score": total,
        "score_product": product,
        "limiting_param": limiting,
        "per_param": per_param,
        "skipped": skipped,
        "reasons": reasons,
    }


def run_suitability(reading: dict, light_level: str | None, container_type: str | None = None,
                    crops: dict | None = None) -> dict:
    s = get_settings()
    crops = crops if crops is not None else get_crops()
    candidates, excluded, scored = [], [], []
    skipped_params: set[str] = set()

    for crop_id, crop in crops.items():
        light = crop.get("light") or {}
        if light_level and light.get("source") and light_level not in light.get("allowed", []):
            excluded.append({"crop_id": crop_id, "reason": _light_reason(light.get("allowed", []))})
            continue
        result = score_crop(crop, reading)
        skipped_params.update(result["skipped"])
        if result["score"] is None:
            excluded.append({"crop_id": crop_id, "reason": "belum ada acuan untuk parameter yang diukur"})
            continue
        scored.append(result)
        if result["score"] >= s.suitability_threshold:
            candidates.append(result)
        else:
            excluded.append({
                "crop_id": crop_id,
                "reason": result["reasons"][0] if result["reasons"] else "skor kesesuaian di bawah ambang",
                "score": result["score"],
                "limiting_param": result["limiting_param"],
            })

    candidates.sort(key=lambda c: (-c["score"], -c["score_product"], c["crop_id"]))
    near_count = sum(1 for r in scored if r["per_param"][r["limiting_param"]]["near_boundary"])
    needs_remeasure = bool(scored) and near_count > len(scored) / 2

    return {
        "candidates": [{k: c[k] for k in ("crop_id", "display_name", "score", "score_product",
                                          "limiting_param", "per_param", "reasons")} for c in candidates],
        "excluded": excluded,
        "skipped_params": sorted(skipped_params, key=PARAMS.index),
        "needs_remeasure": needs_remeasure,
        "threshold": s.suitability_threshold,
        "reading": {p: reading.get(p) for p in PARAMS},
        "light_level": light_level,
        "container_type": container_type,
    }


def _light_reason(allowed: list[str]) -> str:
    if allowed == ["full"]:
        return "butuh cahaya penuh"
    names = {"full": "cahaya penuh", "partial": "cahaya sebagian", "shade": "teduh"}
    return "butuh " + " atau ".join(names.get(a, a) for a in allowed) if allowed else "tidak cocok dengan cahaya pot"
