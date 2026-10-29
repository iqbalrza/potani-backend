"""Profil pengguna (memori jangka panjang)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser
from app.api.serializers import user_out

router = APIRouter(tags=["auth"])


class MeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)
    light_default: Literal["full", "partial", "shade"] | None = None
    container_default: str | None = Field(default=None, max_length=64)
    materials_owned: list[str] | None = Field(default=None, max_length=30)
    fcm_token: str | None = Field(default=None, max_length=4096)


@router.get("/me")
def get_me(user: CurrentUser):
    return user_out(user)


@router.patch("/me")
def update_me(body: MeUpdate, user: CurrentUser, db: DB):
    data = body.model_dump(exclude_unset=True)
    if "materials_owned" in data and data["materials_owned"] is not None:
        data["materials_owned"] = [m.strip()[:64] for m in data["materials_owned"] if m.strip()]
    for k, v in data.items():
        setattr(user, k, v)
    db.commit()
    return user_out(user)
