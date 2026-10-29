"""Pemuat data tanaman (crops.json) dan aturan perbaikan (remediation_rules.json)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

PARAMS = ("ph", "moisture", "temperature")
PARAM_LABELS = {"ph": "pH", "moisture": "Kelembapan tanah", "temperature": "Suhu"}
RANGE_KEYS = ("abs_min", "opt_min", "opt_max", "abs_max")


def is_sourced(spec: dict | None) -> bool:
    """Rentang hanya dipakai bila punya sumber dan keempat batasnya terisi (aturan 3)."""
    if not spec or not spec.get("source"):
        return False
    return all(spec.get(k) is not None for k in RANGE_KEYS)


def _normalize_crops(data: dict) -> dict:
    crops = {}
    for crop_id, crop in data.items():
        if crop_id.startswith("_"):
            continue
        params = {}
        for param, spec in (crop.get("params") or {}).items():
            spec = dict(spec)
            spec["unsourced"] = not is_sourced(spec)
            if not spec.get("source"):
                spec["source"] = None
            params[param] = spec
        light = dict(crop.get("light") or {"allowed": [], "source": None})
        light["unsourced"] = not light.get("source")
        crops[crop_id] = {**crop, "crop_id": crop_id, "params": params, "light": light}
    return crops


@lru_cache(maxsize=8)
def _load_crops(path: str) -> tuple[dict, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _normalize_crops(data), data.get("_meta", {})


@lru_cache(maxsize=8)
def _load_rules(path: str) -> tuple[dict, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = {k: v for k, v in data.items() if not k.startswith("_")}
    for param, conds in rules.items():
        for cond, options in conds.items():
            for i, opt in enumerate(options):
                opt["ref"] = f"{param}.{cond}.{i}"
                opt["unsourced"] = not opt.get("source")
    return rules, data.get("_meta", {})


def get_crops() -> dict:
    return _load_crops(str(get_settings().crops_path()))[0]


def get_rules() -> dict:
    return _load_rules(str(get_settings().remediation_path()))[0]


def data_is_demo() -> bool:
    s = get_settings()
    return bool(_load_crops(str(s.crops_path()))[1].get("is_demo")) or bool(
        _load_rules(str(s.remediation_path()))[1].get("is_demo")
    )


def get_crop(crop_id: str) -> dict | None:
    return get_crops().get(crop_id)


def get_rule(ref: str) -> dict | None:
    try:
        param, cond, idx = ref.split(".")
        return get_rules()[param][cond][int(idx)]
    except (ValueError, KeyError, IndexError):
        return None


def clear_cache() -> None:
    _load_crops.cache_clear()
    _load_rules.cache_clear()
