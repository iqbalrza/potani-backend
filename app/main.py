"""P1: FastAPI app. Jalankan: uvicorn app.main:app"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import errors
from app.api.routes import auth, chat, crops, devices, evaluate, measurements, plans, pots, recommend, sessions
from app.config import get_settings
from app.logging_setup import setup_logging


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level)
    app = FastAPI(title="Potani Backend", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type"])
    errors.install(app)
    for module in (crops, auth, devices, pots, sessions, measurements, recommend, evaluate, plans, chat):
        app.include_router(module.router)
    if s.auth_mode == "dev":
        from app.api.routes import dev  # konsol test + endpoint simulasi; tidak pernah aktif di produksi

        app.include_router(dev.router)
    return app


app = create_app()
