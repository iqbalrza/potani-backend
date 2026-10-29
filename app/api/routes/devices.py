"""Pairing perangkat."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.api.serializers import device_out
from app.db.models import Device
from app.errors import ApiError

router = APIRouter(prefix="/devices", tags=["devices"])


class PairIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:]+$")


@router.post("/pair")
def pair(body: PairIn, user: CurrentUser, db: DB):
    dev = db.scalars(select(Device).where(Device.device_id == body.device_id)).first()
    if dev is None:
        dev = Device(device_id=body.device_id, owner_id=user.id, online=False)
        db.add(dev)
    elif dev.owner_id is None:
        dev.owner_id = user.id
    elif dev.owner_id != user.id:
        raise ApiError(409, "device_taken", "Perangkat ini sudah dipasangkan dengan akun lain.")
    db.commit()
    return device_out(dev)


@router.get("")
def list_devices(user: CurrentUser, db: DB):
    devs = db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.created_at)).all()
    return {"devices": [device_out(d) for d in devs]}
