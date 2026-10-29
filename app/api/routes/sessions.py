"""Sesi tautan: jendela waktu agar pengukuran MQTT berikutnya masuk ke pot yang benar."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.deps import DB, CurrentUser, require_pot
from app.api.serializers import session_out
from app.config import get_settings
from app.db.models import Device, Measurement, Session
from app.db.types import utcnow
from app.errors import ApiError
from app.repo import measurement_dict

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    pot_id: uuid.UUID
    stage: Literal["pre_plant", "post_remediation", "control", "symptom", "end"]


@router.post("", status_code=201)
def open_session(body: SessionCreate, user: CurrentUser, db: DB):
    dev = db.scalars(select(Device).where(Device.device_id == body.device_id)).first()
    if dev is None or dev.owner_id != user.id:
        raise ApiError(404, "device_not_found", "Perangkat tidak ditemukan. Pasangkan perangkat terlebih dahulu.")
    pot = require_pot(db, user, body.pot_id)
    now = utcnow()
    # Satu perangkat hanya punya satu sesi terbuka.
    db.execute(update(Session).where(Session.device_id == dev.device_id, Session.closed_at.is_(None))
               .values(closed_at=now))
    sess = Session(device_id=dev.device_id, pot_id=pot.id, stage=body.stage, opened_at=now,
                   expires_at=now + timedelta(seconds=get_settings().session_window_s))
    db.add(sess)
    db.commit()
    return session_out(sess)


@router.get("/{session_id}")
def get_session(session_id: uuid.UUID, user: CurrentUser, db: DB):
    sess = db.get(Session, session_id)
    if sess is None:
        raise ApiError(404, "session_not_found", "Sesi tidak ditemukan.")
    require_pot(db, user, sess.pot_id)
    out = session_out(sess)
    m = db.get(Measurement, sess.measurement_id) if sess.measurement_id else None
    out["measurement"] = measurement_dict(m) if m else None
    return out
