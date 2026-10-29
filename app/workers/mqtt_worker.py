"""P2: subscriber MQTT. Jalankan: python -m app.workers.mqtt_worker"""
from __future__ import annotations

import logging
import ssl

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from app.config import get_settings
from app.db.session import session_scope
from app.ingest import process_data_message, process_status_message
from app.logging_setup import setup_logging

log = logging.getLogger("potani.mqtt_worker")

TOPICS = [("potani/+/data", 1), ("potani/+/status", 1)]


def parse_topic(topic: str) -> tuple[str, str] | None:
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "potani" or parts[2] not in ("data", "status") or not parts[1]:
        return None
    return parts[1], parts[2]


def handle_message(topic: str, payload: bytes) -> None:
    parsed = parse_topic(topic)
    if parsed is None:
        return
    device_id, kind = parsed
    try:
        with session_scope() as db:
            if kind == "data":
                process_data_message(db, payload, topic_device_id=device_id)
            else:
                process_status_message(db, device_id, payload)
    except Exception:  # noqa: BLE001 - satu pesan rusak tidak boleh mematikan worker
        log.exception("mqtt_message_failed", extra={"topic": topic})


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        log.error("mqtt_connect_failed", extra={"reason": str(reason_code)})
        return
    log.info("mqtt_connected")
    client.subscribe(TOPICS)  # subscribe ulang setiap reconnect


def on_disconnect(client, userdata, flags, reason_code, properties):
    log.warning("mqtt_disconnected", extra={"reason": str(reason_code)})


def on_message(client, userdata, msg):
    handle_message(msg.topic, msg.payload)


def build_client() -> mqtt.Client:
    s = get_settings()
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=s.mqtt_client_id,
                         clean_session=False)
    if s.mqtt_username:
        client.username_pw_set(s.mqtt_username, s.mqtt_password)
    if s.mqtt_tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.reconnect_delay_set(min_delay=1, max_delay=60)  # backoff eksponensial bawaan paho
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level)
    client = build_client()
    log.info("mqtt_worker_starting", extra={"host": s.mqtt_host, "port": s.mqtt_port, "tls": s.mqtt_tls})
    client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=60)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
