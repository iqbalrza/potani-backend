"""Pemeriksa aturan 2: setiap angka di teks LLM harus berasal dari output tool (atau pesan pengguna)."""
from __future__ import annotations

import json
import re

_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", "."))
    except ValueError:
        return None


def collect_numbers(obj) -> set[float]:
    """Kumpulkan semua angka dari struktur JSON (nilai numerik dan angka di dalam string)."""
    found: set[float] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            found.add(float(x))
        elif isinstance(x, str):
            for tok in _NUM.findall(x):
                v = _to_float(tok)
                if v is not None:
                    found.add(v)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            pass
    walk(obj)
    return found


def _matches(v: float, allowed: set[float]) -> bool:
    for a in allowed:
        for cand in (a, a * 100):  # skor 0.82 boleh ditulis 82%
            if abs(v - cand) < 1e-9:
                return True
            for nd in (0, 1, 2):
                if abs(v - round(cand, nd)) < 1e-9:
                    return True
    return False


def ungrounded_numbers(text: str, sources: list) -> list[str]:
    """Angka di `text` yang tidak ditemukan di `sources`. Penanda daftar (1. 2.) diabaikan."""
    if not text:
        return []
    allowed: set[float] = set()
    for src in sources:
        allowed |= collect_numbers(src)
    cleaned = _LIST_MARKER.sub(" ", text)
    bad = []
    for tok in _NUM.findall(cleaned):
        v = _to_float(tok)
        if v is not None and not _matches(v, allowed):
            bad.append(tok)
    return bad
