"""Daftar tanaman + rentang + sumber, dan health check."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agent.llm import get_llm
from app.api.deps import CurrentUser
from app.config import get_settings
from app.db.session import SessionLocal
from app.domain.catalog import data_is_demo, get_crops

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/crops", tags=["crops"])
def list_crops(user: CurrentUser):
    return {"is_demo": data_is_demo(),
            "crops": [{"crop_id": cid, "display_name": c.get("display_name", cid), "params": c["params"],
                       "light": c["light"]} for cid, c in get_crops().items()]}


@router.get("/health", tags=["health"])
def health():
    s = get_settings()
    db_ok = True
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        log.exception("health_db_failed")
        db_ok = False
    llm = get_llm()
    llm_status = "not_configured" if not getattr(llm, "configured", True) else (
        "error" if getattr(llm, "last_ok", None) is False else "ok")
    body = {"status": "ok" if db_ok else "degraded", "api": "ok", "db": "ok" if db_ok else "error",
            "llm": llm_status, "llm_model": getattr(llm, "model", None), "demo_mode": s.demo_mode,
            "time_acceleration": s.time_acceleration, "data_is_demo": data_is_demo()}
    return JSONResponse(body, status_code=200 if db_ok else 503)
