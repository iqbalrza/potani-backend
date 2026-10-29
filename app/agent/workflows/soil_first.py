"""F2 Soil-first workflow (PRD 7.5). Urutan langkah ditentukan kode, bukan LLM."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.narrative import narrate
from app.agent.planning import build_and_save_plan
from app.agent.tools import ToolContext
from app.db.models import Planting, Pot, User
from app.domain import evaluator
from app.domain.catalog import data_is_demo
from app.errors import ApiError
from app.repo import last_candidates, latest_measurement, measurement_dict, reading_of

LIGHT_QUESTION = {
    "question": "Seberapa banyak cahaya matahari yang diterima pot ini?",
    "options": ["Cahaya penuh (kena matahari langsung hampir seharian)", "Cahaya sebagian (setengah hari)",
                "Teduh (jarang kena matahari langsung)"],
    "field": "light_level",
    "value_map": {"Cahaya penuh (kena matahari langsung hampir seharian)": "full",
                  "Cahaya sebagian (setengah hari)": "partial",
                  "Teduh (jarang kena matahari langsung)": "shade"},
}
CONTAINER_QUESTION = {
    "question": "Wadah tanamnya seperti apa?",
    "options": ["Pot kecil", "Pot sedang", "Pot besar", "Polybag", "Tanah langsung / bedengan"],
    "field": "container_type",
    "value_map": {"Pot kecil": "pot_kecil", "Pot sedang": "pot_sedang", "Pot besar": "pot_besar",
                  "Polybag": "polybag", "Tanah langsung / bedengan": "tanah_langsung"},
}

NARRATIVE_PROMPT = """Kamu adalah asisten tanam Potani. Tulis ringkasan singkat (maksimal 2 paragraf,
bahasa Indonesia sehari-hari) yang membandingkan kandidat tanaman di data JSON: kelebihan dan kompromi masing-masing.
Aturan wajib: jangan menentukan cocok/tidak (sudah dihitung kode), jangan menambah rentang, dosis, atau angka
yang tidak ada di JSON, dan jangan mengarang informasi di luar JSON.
Gaya: teks biasa tanpa markdown, tabel, atau daftar bernomor; maksimal 2 paragraf pendek (total sekitar 80 kata);
jangan menyebut nama field teknis seperti verify_after_days atau limiting_param."""


def _ask(ctx: ToolContext, spec: dict) -> dict:
    res = ctx.call("ask_user", {"question": spec["question"], "options": spec["options"], "field": spec["field"]})
    if ctx.pending_question is not None:
        ctx.pending_question.context = {"value_map": spec["value_map"]}
    return res


def narrative_for(ctx: ToolContext, suitability: dict, weather: dict | None) -> str | None:
    top = suitability["candidates"][:3]
    if not top:
        return None
    payload = {
        "candidates": [{"crop": c["display_name"], "score": c["score"], "limiting_param": c["limiting_param"],
                        "reasons": c["reasons"]} for c in top],
        "skipped_params": suitability["skipped_params"],
        "weather": weather if weather and weather.get("available") else None,
    }
    return narrate(ctx, NARRATIVE_PROMPT, payload, "narrative")


def recommend(db: Session, user: User, pot: Pot) -> dict:
    ctx = ToolContext(db=db, user=user, feature="F2", pot_id=pot.id)

    # 1. Bacaan sesi pre_plant
    ctx.step = "read_pre_plant"
    m = latest_measurement(db, pot.id, "pre_plant")
    ctx.log("read_pre_plant", {"pot_id": str(pot.id)}, measurement_dict(m) if m else None, 0)
    if m is None:
        raise ApiError(409, "no_pre_plant_reading",
                       "Belum ada bacaan tanah awal untuk pot ini. Ukur dulu dengan Potani pada tahap sebelum tanam.")

    # 2. Konteks pot
    ctx.step = "context"
    if not (pot.light_level or user.light_default):
        return {"status": "need_input", "question": _ask(ctx, LIGHT_QUESTION), "run_id": str(ctx.run_id)}
    if not (pot.container_type or user.container_default):
        return {"status": "need_input", "question": _ask(ctx, CONTAINER_QUESTION), "run_id": str(ctx.run_id)}

    # 3. Skor kesesuaian
    ctx.step = "suitability"
    result = ctx.call("run_suitability", {"pot_id": str(pot.id)})
    if "error" in result:
        raise ApiError(409, "suitability_failed", result["error"])
    base = {"run_id": str(ctx.run_id), "measurement": measurement_dict(m), "is_demo": data_is_demo()}
    if result["needs_remeasure"]:
        return {"status": "needs_remeasure", **base, **result, "narrative": None,
                "message": "Banyak nilai berada dekat batas rentang. Ukur ulang tanah agar hasilnya lebih pasti."}

    # 4. Cuaca (opsional)
    ctx.step = "weather"
    weather = ctx.call("get_weather", {"lat": user.location_lat, "lon": user.location_lon})

    # 5. Ringkasan LLM (hanya dari output tool)
    narrative = narrative_for(ctx, result, weather)
    return {"status": "ok", **base, **result, "weather": weather, "narrative": narrative}


def choose_crop(db: Session, user: User, pot: Pot, crop_id: str, source: str) -> dict:
    """Langkah 6-7 F2 (dan alur plant-first): buat planting, lalu rencana bila ada parameter di luar optimal."""
    candidates = last_candidates(db, pot.id)
    if source == "recommender" and crop_id not in candidates:
        raise ApiError(422, "not_a_candidate",
                       "Tanaman ini tidak ada di daftar rekomendasi terakhir. Minta rekomendasi ulang atau pilih "
                       "sebagai pilihan sendiri.")
    planting = Planting(pot_id=pot.id, crop_id=crop_id, source=source, status="planned")
    db.add(planting)
    db.flush()

    ctx = ToolContext(db=db, user=user, feature="F2", pot_id=pot.id, planting_id=planting.id,
                      plan_kind="soil_first" if source == "recommender" else "remediation",
                      user_choice=source == "user_choice", candidate_ids=candidates)
    ctx.step = "evaluate"
    m = latest_measurement(db, pot.id, "pre_plant") or latest_measurement(db, pot.id)
    evaluation = None
    plan = None
    if m is not None:
        evaluation = evaluator.evaluate(crop_id, reading_of(m), pot.light_level or user.light_default,
                                        user.materials_owned)
        ctx.log("run_evaluator", {"crop_id": crop_id, "pot_id": str(pot.id)}, evaluation, 0)
        if any(v["status"] in ("low", "high") for v in evaluation["per_param"].values()):
            plan = build_and_save_plan(ctx, crop_id, evaluation)
    return {"planting": planting, "evaluation": evaluation, "plan_id": plan and plan["plan_id"],
            "run_id": str(ctx.run_id)}
