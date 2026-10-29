"""F7 TaniBot: loop ReAct dengan tool calling (PRD 7.7)."""
from __future__ import annotations

import json
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.grounding import ungrounded_numbers
from app.agent.llm import assistant_message, compact_json, error_tag, get_llm
from app.agent.tools import F7_TOOLS, F7_WRITE_TOOLS, ToolContext, schemas
from app.config import get_settings
from app.db.models import ChatMessage, Pot, User
from app.domain.catalog import get_crops
from app.repo import active_planting

HISTORY_LIMIT = 10

SYSTEM_PROMPT = """Kamu adalah TaniBot, pendamping tanam Potani untuk penanam pot dan pekarangan di kota.
Aturan wajib:
1. Keputusan cocok atau tidak hanya dari hasil tool (run_suitability, run_evaluator). Kamu tidak boleh menentukan
   kecocokan, rentang, angka, atau dosis sendiri.
2. Semua angka di jawabanmu harus berasal dari hasil tool pada percakapan ini. Jangan mengarang angka.
3. Bila data kurang, minta pengguna mengukur dengan Potani atau mengirim foto. Jangan menebak diagnosis.
4. Jawab maksimal 3 paragraf pendek dengan bahasa Indonesia sehari-hari.
5. Cantumkan sumber panduan (field `source`) bila ada di hasil tool.
6. Data berlabel DEMO hanyalah contoh pengembangan; sebutkan itu bila kamu memakainya.
7. Jangan pernah menanyakan ID (pot_id, crop_id) ke pengguna. Pakai pot aktif dan daftar tanaman di bawah;
   cocokkan nama tanaman yang disebut pengguna ke crop_id-nya.
8. Tulis teks biasa tanpa markdown, tabel, atau nama field teknis.
Pilihan tool:
- kondisi/bacaan pot sekarang → get_latest_reading
- tanaman apa yang cocok untuk pot → run_suitability
- apakah tanaman X cocok / apa yang perlu diperbaiki untuk X → run_evaluator
- cara menaikkan/menurunkan pH, kelembapan, suhu → get_remediation (param + condition low/high)
- rentang kebutuhan tanaman X → get_crop_requirements
- riwayat, rencana, atau perkembangan pot → get_pot_history
- cuaca → get_weather
- gejala tanaman (daun kuning, layu, bercak) → ask_user untuk detail; jangan menebak diagnosis
- permintaan pengingat → send_reminder"""


def build_system_prompt(pot: Pot | None, crop_id: str | None = None) -> str:
    """System prompt + konteks pot dan katalog tanaman agar model tidak perlu menanyakan ID."""
    lines = [SYSTEM_PROMPT, ""]
    if pot is not None:
        lines.append(f"Pot aktif: pot_id={pot.id}, nama={pot.name}, cahaya={pot.light_level or 'belum diisi'}, "
                     f"wadah={pot.container_type or 'belum diisi'}.")
        lines.append(f"Tanaman aktif di pot ini: {crop_id or 'belum ada'}.")
    else:
        lines.append("Tidak ada pot aktif; bila pertanyaan menyangkut pot, minta pengguna memilih pot di aplikasi.")
    lines.append("Daftar tanaman (crop_id: nama): "
                 + ", ".join(f"{cid}: {c.get('display_name', cid)}" for cid, c in get_crops().items()) + ".")
    return "\n".join(lines)

# Tool write hanya bila pengguna meminta secara eksplisit (deteksi deterministik).
_EXPLICIT_WRITE = re.compile(r"\b(ingatkan|pengingat|reminder|jadwalkan|jadwal ukur|set alarm)\b", re.IGNORECASE)

FALLBACK_REPLY = ("Maaf, TaniBot sedang tidak bisa menyusun jawaban. Kamu tetap bisa melihat bacaan terbaru, "
                  "rekomendasi, dan evaluasi tanaman di aplikasi.")


def _history(db: Session, user: User, pot_id: uuid.UUID | None) -> list[dict]:
    q = select(ChatMessage).where(ChatMessage.user_id == user.id)
    if pot_id:
        q = q.where(ChatMessage.pot_id == pot_id)
    rows = db.scalars(q.order_by(ChatMessage.created_at.desc()).limit(HISTORY_LIMIT)).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def _fallback(ctx: ToolContext, pot: Pot | None, tool_results: list) -> str:
    """Jawaban deterministik ketika LLM gagal: tampilkan data, tanpa penafsiran."""
    if pot is None:
        return FALLBACK_REPLY
    latest = next((r for r in tool_results if isinstance(r, dict) and "reading" in r), None)
    if latest is None:
        latest = ctx.call("get_latest_reading", {"pot_id": str(pot.id)})
    reading = latest.get("reading")
    if not reading:
        return FALLBACK_REPLY + " Belum ada bacaan untuk pot ini, silakan ukur dengan Potani."
    parts = []
    for key, label, unit in (("ph", "pH", ""), ("moisture", "kelembapan", "%"), ("temperature", "suhu", "°C")):
        if reading.get(key) is not None:
            parts.append(f"{label} {reading[key]}{unit}")
    return f"{FALLBACK_REPLY} Bacaan terbaru pot {pot.name}: " + ", ".join(parts) + "."


def chat(db: Session, user: User, message: str, pot: Pot | None = None) -> dict:
    s = get_settings()
    ctx = ToolContext(db=db, user=user, feature="F7", pot_id=pot.id if pot else None, step="chat")
    allowed = set(F7_TOOLS)
    if _EXPLICIT_WRITE.search(message):
        allowed |= F7_WRITE_TOOLS

    planting = active_planting(db, pot.id) if pot is not None else None
    system = build_system_prompt(pot, planting.crop_id if planting else None)
    messages = [{"role": "system", "content": system}, *_history(db, user, ctx.pot_id),
                {"role": "user", "content": message}]
    db.add(ChatMessage(user_id=user.id, pot_id=ctx.pot_id, role="user", content=message))

    llm = get_llm()
    tool_results: list = []
    reply: str | None = None
    narrative_ok = False
    corrected = False
    for i in range(s.f7_max_iterations + 1):
        tools = schemas(allowed) if i < s.f7_max_iterations else None  # iterasi terakhir: wajib menjawab
        start = time.perf_counter()
        resp = llm.chat(messages, tools=tools)
        latency = int((time.perf_counter() - start) * 1000)
        ctx.step = f"iter_{i}"
        ctx.log("llm", {"messages": len(messages)},
                resp and {"content": resp.get("content"), "tool_calls": resp.get("tool_calls")},
                latency, None if resp else error_tag(llm), getattr(llm, "model", None))
        if resp is None:
            break
        if resp.get("tool_calls"):
            messages.append(assistant_message(resp))
            for tc in resp["tool_calls"]:
                result = ctx.call(tc["name"], tc["arguments"], allowed=allowed)
                tool_results.append(result)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": compact_json(result)})
                if tc["name"] == "ask_user" and "question_id" in result:
                    reply = result["question"]
                    if result.get("options"):
                        reply += "\n" + "\n".join(f"- {o}" for o in result["options"])
                    narrative_ok = True
            if narrative_ok:
                break
            continue
        text = (resp.get("content") or "").strip()
        bad = ungrounded_numbers(text, [*tool_results, message, *[m["content"] for m in messages
                                                                   if m["role"] == "user"]])
        if bad and not corrected:
            ctx.log("grounding_check", {"numbers": bad}, {"ok": False}, 0, "ungrounded_numbers")
            corrected = True
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "Jawabanmu memuat angka yang tidak ada di hasil tool: "
                             + ", ".join(bad) + ". Tulis ulang tanpa angka tersebut."})
            continue
        if bad:
            ctx.log("grounding_check", {"numbers": bad}, {"ok": False}, 0, "ungrounded_numbers")
            break
        reply, narrative_ok = text, True
        break

    if not narrative_ok:
        reply = _fallback(ctx, pot, tool_results)
    db.add(ChatMessage(user_id=user.id, pot_id=ctx.pot_id, role="assistant", content=reply,
                       tool_calls=ctx.trace or None))
    db.flush()
    return {"reply": reply, "tool_trace": ctx.trace, "run_id": str(ctx.run_id),
            "narrative": reply if narrative_ok else None,
            "pending_question_id": str(ctx.pending_question.id) if ctx.pending_question else None}
