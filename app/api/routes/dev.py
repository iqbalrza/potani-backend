"""Endpoint khusus pengembangan + konsol test web. Hanya dipasang bila AUTH_MODE=dev."""
from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require_pot
from app.db.models import AgentLog, Checkpoint, Device
from app.db.types import utcnow
from app.errors import ApiError
from app.ingest import process_data_message
from app.notify import fcm
from app.repo import checkpoint_dict, measurement_dict
from app.workers.scheduler_worker import JOBS

router = APIRouter(tags=["dev"])
CONSOLE = Path(__file__).resolve().parents[2] / "static" / "console.html"


class SimulateIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    ph: float = Field(ge=-5, le=20)
    moisture: float = Field(ge=-50, le=200)
    temperature: float = Field(ge=-40, le=100)


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/console")


@router.get("/console", include_in_schema=False)
def console():
    return FileResponse(CONSOLE, media_type="text/html")


@router.post("/dev/simulate")
def simulate(body: SimulateIn, user: CurrentUser, db: DB):
    """Meniru pesan `potani/{device_id}/data` dari ESP32 (is_demo=true), lewat logika ingest P2 yang sama."""
    dev = db.scalars(select(Device).where(Device.device_id == body.device_id)).first()
    if dev is None or dev.owner_id != user.id:
        raise ApiError(404, "device_not_found", "Perangkat tidak ditemukan. Pasangkan perangkat terlebih dahulu.")
    payload = {"device_id": body.device_id, "soil_adc": 2000, "moisture": body.moisture, "status": "ok",
               "ph_adc": 1800, "ph_voltage": 1.5, "ph": body.ph, "temperature": body.temperature, "soil_score": 0,
               "measured_at": utcnow().timestamp(), "client_measurement_id": str(uuid.uuid4()),
               "fw_version": "console-sim", "is_demo": True}
    m = process_data_message(db, payload, topic_device_id=body.device_id)
    return measurement_dict(m) if m else {"ignored": True}


@router.post("/dev/jobs/{name}")
def run_job(name: Literal["scan_due_checkpoints", "scan_missed", "run_verify_queue", "expire_sessions",
                          "device_offline"], user: CurrentUser, db: DB):
    """Jalankan satu job scheduler (P3) sekarang juga."""
    processed = JOBS[name][0](db)
    db.commit()
    return {"job": name, "processed": processed}


@router.post("/dev/checkpoints/{checkpoint_id}/make-due")
def make_due(checkpoint_id: uuid.UUID, user: CurrentUser, db: DB, days_ago: float = Query(default=0, ge=0, le=30)):
    """Majukan waktu: checkpoint dianggap jatuh tempo (atau sudah lewat `days_ago` hari)."""
    cp = db.get(Checkpoint, checkpoint_id)
    if cp is None:
        raise ApiError(404, "checkpoint_not_found", "Checkpoint tidak ditemukan.")
    require_pot(db, user, cp.pot_id)
    shift = timedelta(days=days_ago, seconds=1)
    cp.due_at = utcnow() - shift
    if cp.last_reminded_at is not None:
        cp.last_reminded_at = utcnow() - shift
    db.commit()
    return checkpoint_dict(cp)


@router.get("/dev/agent-logs")
def agent_logs(user: CurrentUser, db: DB, limit: int = Query(default=60, ge=1, le=300),
               feature: str | None = None):
    q = select(AgentLog).where(AgentLog.user_id == user.id)
    if feature:
        q = q.where(AgentLog.feature == feature)
    rows = db.scalars(q.order_by(AgentLog.created_at.desc()).limit(limit)).all()
    return {"logs": [{"at": r.created_at.isoformat(), "run_id": str(r.run_id), "feature": r.feature, "step": r.step,
                      "tool": r.tool, "args": r.args, "result": r.result, "latency_ms": r.latency_ms,
                      "llm_model": r.llm_model, "error": r.error} for r in rows]}


@router.get("/dev/notifications")
def notifications(user: CurrentUser):
    """Push yang 'terkirim' saat FCM nonaktif (hanya dari proses API ini)."""
    return {"notifications": [n for n in reversed(fcm.OUTBOX) if n["user_id"] == str(user.id)][:30]}
