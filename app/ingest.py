"""Logika ingest data probe (PRD 5.3). Dipakai mqtt_worker (P2) dan endpoint penautan manual."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.loop_f6 import attach_measurement_to_checkpoints
from app.db.models import Device, Measurement, Pot, Session as LinkSession
from app.db.types import utcnow
from app.repo import active_planting

log = logging.getLogger(__name__)

PHYSICAL_RANGES = {"ph": (0.0, 14.0), "moisture": (0.0, 100.0), "temperature": (-10.0, 60.0)}
SESSION_SLACK = timedelta(seconds=10)


class DataPayload(BaseModel):
    # Field wajib dari proposal
    device_id: str
    soil_adc: float
    moisture: float
    status: str
    ph_adc: float
    ph_voltage: float
    ph: float
    temperature: float
    soil_score: float
    # Field opsional (PRD firmware)
    measured_at: float | None = None
    client_measurement_id: str | None = None
    fw_version: str | None = None
    stable: bool | None = None
    is_demo: bool = False


def _decode(payload: bytes | str | dict) -> dict | None:
    if isinstance(payload, dict):
        return payload
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _measured_at(epoch: float | None, now: datetime) -> datetime:
    if epoch is None:
        return now
    try:
        ts = datetime.fromtimestamp(float(epoch), timezone.utc)
    except (ValueError, OverflowError, OSError):
        return now
    if ts > now + timedelta(minutes=5) or ts.year < 2024:
        return now  # jam perangkat belum sinkron
    return ts


def _device(db: Session, device_id: str) -> Device:
    dev = db.scalars(select(Device).where(Device.device_id == device_id)).first()
    if dev is None:
        dev = Device(device_id=device_id, online=True)
        db.add(dev)
        db.flush()
    return dev


def link_measurement(db: Session, m: Measurement, pot: Pot, stage: str, session: LinkSession | None = None) -> None:
    """Tautkan bacaan ke pot, lalu tutup checkpoint jatuh tempo (memicu Verify F6 lewat antrean)."""
    planting = active_planting(db, pot.id)
    m.pot_id = pot.id
    m.stage = stage
    m.planting_id = planting.id if planting else None
    if session is not None:
        m.session_id = session.id
        session.closed_at = utcnow()
        session.measurement_id = m.id
    db.flush()
    attach_measurement_to_checkpoints(db, m)


def process_data_message(db: Session, payload: bytes | str | dict, topic_device_id: str | None = None) -> Measurement | None:
    """Validasi → dedupe → simpan → tautkan ke sesi terbuka. Return None bila diabaikan."""
    now = utcnow()
    data = _decode(payload)
    device_id = (data or {}).get("device_id") or topic_device_id
    if data is None or not isinstance(device_id, str) or not device_id:
        log.warning("mqtt_payload_dropped", extra={"topic_device_id": topic_device_id})
        return None
    device_id = device_id[:64]

    invalid_reason = None
    parsed: DataPayload | None = None
    try:
        parsed = DataPayload.model_validate({**data, "device_id": device_id})
    except ValidationError as e:
        invalid_reason = "skema tidak valid: " + ", ".join(str(err["loc"][0]) for err in e.errors() if err["loc"])
    if topic_device_id and device_id != topic_device_id:
        invalid_reason = "device_id payload tidak sama dengan topik"

    cmid = parsed.client_measurement_id if parsed else data.get("client_measurement_id")
    cmid = str(cmid)[:64] if cmid else None
    if cmid and db.scalars(select(Measurement.id).where(Measurement.client_measurement_id == cmid)).first():
        log.info("mqtt_duplicate", extra={"client_measurement_id": cmid})
        return None

    if parsed and invalid_reason is None:
        for field, (lo, hi) in PHYSICAL_RANGES.items():
            v = getattr(parsed, field)
            if not lo <= v <= hi:
                invalid_reason = f"{field} di luar rentang fisik"
                break

    device = _device(db, device_id)
    device.last_seen_at = now
    device.online = True
    if parsed and parsed.fw_version:
        device.fw_version = parsed.fw_version[:32]

    valid = invalid_reason is None and parsed is not None
    m = Measurement(
        device_id=device_id,
        ph=parsed.ph if valid else None,
        moisture=parsed.moisture if valid else None,
        temperature=parsed.temperature if valid else None,
        soil_score=parsed.soil_score if valid else None,
        raw=data,
        measured_at=_measured_at(parsed.measured_at if parsed else None, now),
        client_measurement_id=cmid,
        invalid=not valid,
        invalid_reason=invalid_reason,
        is_demo=bool(parsed.is_demo) if parsed else False,
    )
    db.add(m)
    try:
        db.flush()
    except IntegrityError:  # dedupe balapan antar-pesan
        db.rollback()
        return None

    if valid:
        session = db.scalars(
            select(LinkSession).where(LinkSession.device_id == device_id, LinkSession.closed_at.is_(None),
                                      LinkSession.expires_at > now,
                                      LinkSession.opened_at <= m.measured_at + SESSION_SLACK)
            .order_by(LinkSession.opened_at.desc()).limit(1)).first()
        if session is not None:
            pot = db.get(Pot, session.pot_id)
            link_measurement(db, m, pot, session.stage, session)
    db.commit()
    log.info("measurement_saved", extra={"measurement_id": str(m.id), "device_id": device_id,
                                         "linked": m.pot_id is not None, "invalid": m.invalid})
    return m


def process_status_message(db: Session, device_id: str, payload: bytes | str | dict) -> Device | None:
    data = _decode(payload)
    if data is None:
        # LWT sering berupa string polos "offline"
        text = payload.decode() if isinstance(payload, bytes) else str(payload)
        data = {"status": text.strip()}
    device = _device(db, device_id[:64])
    status = str(data.get("status", "")).lower()
    device.online = status == "online"
    if status == "online":
        device.last_seen_at = utcnow()
    if isinstance(data.get("battery"), (int, float)):
        device.battery = float(data["battery"])
    if data.get("fw_version"):
        device.fw_version = str(data["fw_version"])[:32]
    db.commit()
    return device
