"""F3 Plant-first: evaluasi kecocokan tanaman pilihan + saran perbaikan bersumber."""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent.narrative import narrate
from app.agent.tools import ToolContext
from app.api.deps import DB, CurrentUser, require_pot
from app.domain import evaluator
from app.domain.catalog import data_is_demo, get_crop
from app.errors import ApiError
from app.repo import latest_measurement, measurement_dict, reading_of

router = APIRouter(tags=["evaluate"])

EVAL_PROMPT = """Kamu adalah asisten tanam Potani. Jelaskan hasil evaluasi di JSON dalam maksimal 2 paragraf
bahasa Indonesia sehari-hari: apa artinya untuk pengguna dan langkah perbaikan yang tersedia beserta sumbernya.
Aturan wajib: verdict sudah diputuskan kode dan tidak boleh kamu ubah; jangan menambah angka, rentang, atau dosis
yang tidak ada di JSON. Untuk menjelaskan kondisi tiap parameter, ikuti isi field `message`-nya; jangan menyimpulkan
sendiri apakah nilai di atas atau di bawah rentang.
Gaya: teks biasa tanpa markdown, tabel, atau daftar bernomor; maksimal 2 paragraf pendek (total sekitar 80 kata);
jangan menyebut nama field teknis seperti verify_after_days atau limiting_param."""


class EvaluateIn(BaseModel):
    pot_id: uuid.UUID
    crop_id: str = Field(min_length=1, max_length=64)


@router.post("/evaluate")
def post_evaluate(body: EvaluateIn, user: CurrentUser, db: DB):
    pot = require_pot(db, user, body.pot_id)
    if get_crop(body.crop_id) is None:
        raise ApiError(404, "crop_not_found", "Tanaman tidak dikenal.")
    m = latest_measurement(db, pot.id)
    if m is None:
        raise ApiError(409, "no_reading", "Belum ada bacaan untuk pot ini. Ukur dulu dengan Potani.")
    result = evaluator.evaluate(body.crop_id, reading_of(m), pot.light_level or user.light_default,
                                user.materials_owned)
    ctx = ToolContext(db=db, user=user, feature="F3", pot_id=pot.id, step="evaluate")
    ctx.log("run_evaluator", {"crop_id": body.crop_id, "pot_id": str(pot.id)}, result, 0)
    narrative = narrate(ctx, EVAL_PROMPT, {
        "crop": result["display_name"], "verdict": result["verdict"], "verdict_text": result["verdict_text"],
        "per_param": result["per_param"], "light": result["light"], "remediation_hint": result["remediation_hint"],
    }, "narrative")
    db.commit()
    return {**result, "measurement": measurement_dict(m), "narrative": narrative, "is_demo": data_is_demo()}
