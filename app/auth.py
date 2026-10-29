"""Auth: verifikasi Firebase ID token. Mode `dev` menerima token "dev:<uid>" (hanya pengembangan/test)."""
from __future__ import annotations

import logging

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.errors import ApiError

log = logging.getLogger(__name__)


def _verify_token(token: str) -> dict:
    s = get_settings()
    if s.auth_mode == "dev":
        if not token.startswith("dev:") or len(token) <= 4:
            raise ApiError(401, "unauthorized", "Token tidak valid.")
        uid = token[4:][:128]
        return {"uid": uid, "name": uid}
    try:
        from firebase_admin import auth as fb_auth

        from app.firebase import get_firebase_app

        return fb_auth.verify_id_token(token, app=get_firebase_app())
    except Exception:  # noqa: BLE001 - jangan bocorkan alasan internal
        log.info("token_rejected")
        raise ApiError(401, "unauthorized", "Sesi login tidak valid atau sudah kedaluwarsa. Silakan masuk lagi.")


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "Silakan masuk terlebih dahulu.")
    claims = _verify_token(authorization[7:].strip())
    uid = claims["uid"]
    user = db.scalars(select(User).where(User.firebase_uid == uid)).first()
    if user is None:
        user = User(firebase_uid=uid, name=claims.get("name"), materials_owned=[])
        db.add(user)
        db.commit()
    return user
