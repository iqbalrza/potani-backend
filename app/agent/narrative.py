"""Narasi LLM opsional di atas hasil deterministik. Gagal atau angka tak bersumber → None (aturan 2 dan 4)."""
from __future__ import annotations

import json
import time

from app.agent.grounding import ungrounded_numbers
from app.agent.llm import error_tag, get_llm
from app.agent.tools import ToolContext


def narrate(ctx: ToolContext, system_prompt: str, payload: dict, step: str) -> str | None:
    llm = get_llm()
    start = time.perf_counter()
    resp = llm.chat([{"role": "system", "content": system_prompt},
                     {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}])
    latency = int((time.perf_counter() - start) * 1000)
    text = (resp.get("content") or "").strip() if resp else None
    bad = ungrounded_numbers(text or "", [payload])
    error = error_tag(llm) if resp is None else ("ungrounded_numbers" if bad else None)
    if resp is not None and not text:
        error = "empty_reply"
    ctx.step = step
    ctx.log(f"llm.{step}", {"keys": sorted(payload)}, {"text": text, "ungrounded": bad}, latency, error,
            getattr(llm, "model", None))
    return None if error else text
