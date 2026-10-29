"""Simulator probe ESP32 untuk demo/test manual. Payload selalu diberi is_demo=true.

Kirim lewat broker MQTT:
    python scripts/simulate_probe.py --device potani-demo-01 --ph 5.2 --moisture 45 --temperature 27 --host localhost --port 1883 --no-tls
Tanpa broker (langsung ke logika ingest P2, memakai DATABASE_URL):
    python scripts/simulate_probe.py --device potani-demo-01 --ph 5.2 --direct
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build_payload(args) -> dict:
    return {
        "device_id": args.device, "soil_adc": 2000, "moisture": args.moisture, "status": "ok",
        "ph_adc": 1800, "ph_voltage": 1.5, "ph": args.ph, "temperature": args.temperature,
        "soil_score": 0, "measured_at": int(time.time()), "client_measurement_id": str(uuid.uuid4()),
        "fw_version": "sim-0.1", "stable": True, "is_demo": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="potani-demo-01")
    ap.add_argument("--ph", type=float, required=True)
    ap.add_argument("--moisture", type=float, default=50.0)
    ap.add_argument("--temperature", type=float, default=27.0)
    ap.add_argument("--direct", action="store_true", help="tulis langsung lewat logika ingest, tanpa broker")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--username")
    ap.add_argument("--password")
    args = ap.parse_args()
    payload = build_payload(args)

    if args.direct:
        from app.db.session import session_scope
        from app.ingest import process_data_message

        with session_scope() as db:
            m = process_data_message(db, payload, topic_device_id=args.device)
            print(json.dumps({"measurement_id": str(m.id) if m else None,
                              "pot_id": str(m.pot_id) if m and m.pot_id else None}))
        return

    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion

    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=f"sim-{uuid.uuid4().hex[:6]}")
    if args.username:
        client.username_pw_set(args.username, args.password)
    if not args.no_tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.connect(args.host, args.port)
    client.loop_start()
    client.publish(f"potani/{args.device}/status", json.dumps({"status": "online", "battery": 90}), qos=1,
                   retain=True).wait_for_publish()
    client.publish(f"potani/{args.device}/data", json.dumps(payload), qos=1).wait_for_publish()
    client.loop_stop()
    client.disconnect()
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
