"""Riwayat bacaan dan penautan bacaan yatim."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require_pot
from app.db.models import Device, Measurement, Pot
from app.errors import ApiError
from app.ingest import link_measurement
from app.repo import measurement_dict

router = APIRouter(prefix="/measurements", tags=["measurements"])


class LinkIn(BaseModel):
    pot_id: uuid.UUID
    stage: Literal["pre_plant", "post_remediation", "control", "symptom", "end"]


@router.get("")
def list_measurements(user: CurrentUser, db: DB, pot_id: uuid.UUID | None = None, unassigned: bool = False,
                      limit: int = Query(default=50, ge=1, le=200)):
    q = select(Measurement)
    if unassigned:
        own_devices = select(Device.device_id).where(Device.owner_id == user.id)
        q = q.where(Measurement.pot_id.is_(None), Measurement.device_id.in_(own_devices))
    elif pot_id:
        q = q.where(Measurement.pot_id == require_pot(db, user, pot_id).id)
    else:
        q = q.where(Measurement.pot_id.in_(select(Pot.id).where(Pot.owner_id == user.id)))
    rows = db.scalars(q.order_by(Measurement.measured_at.desc()).limit(limit)).all()
    return {"measurements": [measurement_dict(m) for m in rows]}


@router.patch("/{measurement_id}")
def link(measurement_id: uuid.UUID, body: LinkIn, user: CurrentUser, db: DB):
    m = db.get(Measurement, measurement_id)
    dev = db.scalars(select(Device).where(Device.device_id == m.device_id)).first() if m else None
    if m is None or dev is None or dev.owner_id != user.id:
        raise ApiError(404, "measurement_not_found", "Bacaan tidak ditemukan.")
    if m.pot_id is not None:
        raise ApiError(409, "already_linked", "Bacaan ini sudah tertaut ke pot.")
    if m.invalid:
        raise ApiError(409, "invalid_measurement", "Bacaan ini tidak valid sehingga tidak bisa dipakai.")
    pot = require_pot(db, user, body.pot_id)
    link_measurement(db, m, pot, body.stage)
    db.commit()
    return measurement_dict(m)
