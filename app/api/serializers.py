"""Bentuk respons JSON untuk entitas utama."""
from __future__ import annotations

from app.db.models import Device, Planting, Pot, Session, User
from app.db.types import utcnow


def user_out(u: User) -> dict:
    return {"user_id": str(u.id), "name": u.name, "location_lat": u.location_lat, "location_lon": u.location_lon,
            "light_default": u.light_default, "container_default": u.container_default,
            "materials_owned": u.materials_owned or []}


def device_out(d: Device) -> dict:
    return {"device_id": d.device_id, "fw_version": d.fw_version, "battery": d.battery, "online": d.online,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}


def pot_out(p: Pot) -> dict:
    return {"pot_id": str(p.id), "name": p.name, "container_type": p.container_type,
            "light_level": p.light_level, "active": p.active, "needs_help": p.needs_help,
            "help_reason": p.help_reason, "is_demo": p.is_demo}


def planting_out(p: Planting) -> dict:
    return {"planting_id": str(p.id), "pot_id": str(p.pot_id), "crop_id": p.crop_id, "source": p.source,
            "status": p.status, "planted_at": p.planted_at.isoformat() if p.planted_at else None,
            "outcome_note": p.outcome_note}


def session_status(s: Session) -> str:
    if s.measurement_id:
        return "completed"
    if s.closed_at is not None or s.expires_at <= utcnow():
        return "expired"
    return "open"


def session_out(s: Session) -> dict:
    return {"session_id": str(s.id), "device_id": s.device_id, "pot_id": str(s.pot_id), "stage": s.stage,
            "opened_at": s.opened_at.isoformat(), "expires_at": s.expires_at.isoformat(),
            "status": session_status(s), "measurement_id": str(s.measurement_id) if s.measurement_id else None}
