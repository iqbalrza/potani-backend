"""Pot milik pengguna."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require_pot
from app.api.serializers import planting_out, pot_out
from app.db.models import Pot
from app.repo import active_planting, latest_measurement, measurement_dict

router = APIRouter(prefix="/pots", tags=["pots"])

Light = Literal["full", "partial", "shade"]


class PotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    container_type: str | None = Field(default=None, max_length=64)
    light_level: Light | None = None


class PotUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    container_type: str | None = Field(default=None, max_length=64)
    light_level: Light | None = None
    active: bool | None = None
    needs_help: bool | None = None


@router.get("")
def list_pots(user: CurrentUser, db: DB, include_inactive: bool = False):
    q = select(Pot).where(Pot.owner_id == user.id)
    if not include_inactive:
        q = q.where(Pot.active.is_(True))
    return {"pots": [pot_out(p) for p in db.scalars(q.order_by(Pot.created_at)).all()]}


@router.post("", status_code=201)
def create_pot(body: PotCreate, user: CurrentUser, db: DB):
    pot = Pot(owner_id=user.id, **body.model_dump())
    db.add(pot)
    db.commit()
    return pot_out(pot)


@router.get("/{pot_id}")
def get_pot(pot_id: uuid.UUID, user: CurrentUser, db: DB):
    pot = require_pot(db, user, pot_id)
    m = latest_measurement(db, pot.id)
    planting = active_planting(db, pot.id)
    return {**pot_out(pot), "latest_measurement": measurement_dict(m) if m else None,
            "active_planting": planting_out(planting) if planting else None}


@router.patch("/{pot_id}")
def update_pot(pot_id: uuid.UUID, body: PotUpdate, user: CurrentUser, db: DB):
    pot = require_pot(db, user, pot_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("needs_help") is False:
        pot.help_reason = None
    for k, v in data.items():
        if v is not None or k in ("container_type", "light_level"):
            setattr(pot, k, v)
    db.commit()
    return pot_out(pot)
