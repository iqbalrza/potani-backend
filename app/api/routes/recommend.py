"""F2 Soil-first: rekomendasi tanaman + pemilihan tanaman (planting)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent.workflows.soil_first import choose_crop, recommend
from app.api.deps import DB, CurrentUser, require_pot
from app.api.serializers import planting_out
from app.db.models import Plan, Planting
from app.db.types import utcnow
from app.domain.catalog import get_crop
from app.errors import ApiError
from app.repo import plan_dict

router = APIRouter(tags=["recommend"])


class RecommendIn(BaseModel):
    pot_id: uuid.UUID


class PlantingCreate(BaseModel):
    pot_id: uuid.UUID
    crop_id: str = Field(min_length=1, max_length=64)
    source: Literal["recommender", "user_choice"] = "recommender"


class PlantingUpdate(BaseModel):
    status: Literal["growing", "harvested", "failed", "abandoned"]
    outcome_note: str | None = Field(default=None, max_length=1000)


@router.post("/recommend")
def post_recommend(body: RecommendIn, user: CurrentUser, db: DB):
    pot = require_pot(db, user, body.pot_id)
    result = recommend(db, user, pot)
    db.commit()
    return result


@router.post("/plantings", status_code=201)
def create_planting(body: PlantingCreate, user: CurrentUser, db: DB):
    pot = require_pot(db, user, body.pot_id)
    if get_crop(body.crop_id) is None:
        raise ApiError(404, "crop_not_found", "Tanaman tidak dikenal.")
    out = choose_crop(db, user, pot, body.crop_id, body.source)
    db.commit()
    plan = db.get(Plan, uuid.UUID(out["plan_id"])) if out["plan_id"] else None
    return {"planting": planting_out(out["planting"]), "evaluation": out["evaluation"],
            "plan": plan_dict(db, plan) if plan else None, "run_id": out["run_id"]}


@router.patch("/plantings/{planting_id}")
def update_planting(planting_id: uuid.UUID, body: PlantingUpdate, user: CurrentUser, db: DB):
    planting = db.get(Planting, planting_id)
    if planting is None:
        raise ApiError(404, "planting_not_found", "Data tanam tidak ditemukan.")
    require_pot(db, user, planting.pot_id)
    planting.status = body.status
    if body.outcome_note is not None:
        planting.outcome_note = body.outcome_note
    if body.status == "growing" and planting.planted_at is None:
        planting.planted_at = utcnow()
    db.commit()
    return planting_out(planting)
