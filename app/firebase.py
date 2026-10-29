"""Inisialisasi firebase-admin (auth + FCM) secara malas."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings


@lru_cache
def get_firebase_app():
    import firebase_admin
    from firebase_admin import credentials

    s = get_settings()
    cred = credentials.Certificate(s.firebase_credentials_file) if s.firebase_credentials_file else None
    options = {"projectId": s.firebase_project_id} if s.firebase_project_id else None
    return firebase_admin.initialize_app(cred, options)
