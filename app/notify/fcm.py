"""Interface push notification (Firebase Cloud Messaging). Gagal kirim tidak boleh menjatuhkan alur."""
from __future__ import annotations

import logging

from app.config import get_settings
from app.firebase import get_firebase_app

log = logging.getLogger(__name__)

# Dicatat untuk test/demo: daftar pesan yang "terkirim" ketika FCM nonaktif.
OUTBOX: list[dict] = []


def send_push(user, title: str, body: str, deep_link: str | None = None) -> dict:
    payload = {"user_id": str(user.id), "title": title, "body": body, "deep_link": deep_link}
    if not get_settings().fcm_enabled:
        OUTBOX.append(payload)
        del OUTBOX[:-200]
        log.info("push_skipped_fcm_disabled", extra={"user_id": str(user.id), "title": title})
        return {"sent": False, "reason": "fcm_disabled"}
    if not user.fcm_token:
        return {"sent": False, "reason": "no_device_token"}
    try:
        from firebase_admin import messaging

        msg = messaging.Message(
            token=user.fcm_token,
            notification=messaging.Notification(title=title, body=body),
            data={"deep_link": deep_link or ""},
        )
        message_id = messaging.send(msg, app=get_firebase_app())
        return {"sent": True, "message_id": message_id}
    except Exception:  # noqa: BLE001
        log.exception("push_failed", extra={"user_id": str(user.id)})
        return {"sent": False, "reason": "send_failed"}
