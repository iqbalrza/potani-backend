import json
import time
from datetime import timedelta

from sqlalchemy import func, select

from app.db.models import Device, Measurement, Session
from app.db.types import utcnow
from app.ingest import process_data_message, process_status_message
from app.workers.mqtt_worker import handle_message, parse_topic
from tests.conftest import data_payload


def open_session(db, pot, stage="pre_plant", device_id="dev-1", seconds=120):
    now = utcnow()
    s = Session(device_id=device_id, pot_id=pot.id, stage=stage, opened_at=now,
                expires_at=now + timedelta(seconds=seconds))
    db.add(s)
    db.commit()
    return s


def count(db):
    return db.scalar(select(func.count()).select_from(Measurement))


def test_valid_payload_with_session_is_linked(db, owner):
    _, _, pot = owner
    sess = open_session(db, pot)
    start = time.perf_counter()
    m = process_data_message(db, json.dumps(data_payload()).encode(), topic_device_id="dev-1")
    assert time.perf_counter() - start < 2.0
    db.refresh(sess)
    assert m.pot_id == pot.id and m.stage == "pre_plant" and m.session_id == sess.id
    assert sess.closed_at is not None and sess.measurement_id == m.id
    assert m.invalid is False and m.ph == 6.0
    dev = db.scalars(select(Device).where(Device.device_id == "dev-1")).one()
    assert dev.last_seen_at is not None and dev.online is True


def test_valid_payload_without_session_is_unassigned(db, owner):
    m = process_data_message(db, data_payload(), topic_device_id="dev-1")
    assert m.pot_id is None and m.invalid is False


def test_expired_session_is_not_used(db, owner):
    _, _, pot = owner
    open_session(db, pot, seconds=-1)
    m = process_data_message(db, data_payload(), topic_device_id="dev-1")
    assert m.pot_id is None


def test_out_of_range_is_saved_invalid_with_raw(db, owner):
    _, _, pot = owner
    sess = open_session(db, pot)
    m = process_data_message(db, data_payload(ph=15.2), topic_device_id="dev-1")
    assert m.invalid is True and "ph" in m.invalid_reason
    assert m.ph is None and m.raw["ph"] == 15.2
    assert m.pot_id is None  # bacaan invalid tidak dipakai dan tidak menutup sesi
    db.refresh(sess)
    assert sess.closed_at is None


def test_schema_invalid_is_saved_invalid(db, owner):
    payload = data_payload()
    del payload["ph_voltage"]
    m = process_data_message(db, payload, topic_device_id="dev-1")
    assert m.invalid is True and "ph_voltage" in m.invalid_reason


def test_garbage_payload_is_dropped(db, owner):
    assert process_data_message(db, b"not json", topic_device_id="dev-1") is None
    assert count(db) == 0


def test_duplicate_client_measurement_id_ignored(db, owner):
    p = data_payload(client_measurement_id="11111111-2222-3333-4444-555555555555")
    assert process_data_message(db, p, topic_device_id="dev-1") is not None
    assert process_data_message(db, p, topic_device_id="dev-1") is None
    assert count(db) == 1


def test_offline_measured_at_is_respected(db, owner):
    ts = (utcnow() - timedelta(hours=3)).timestamp()
    m = process_data_message(db, data_payload(measured_at=ts, client_measurement_id="abc"), topic_device_id="dev-1")
    assert abs((m.measured_at - utcnow()).total_seconds() + 3 * 3600) < 5


def test_old_offline_data_does_not_link_to_new_session(db, owner):
    _, _, pot = owner
    open_session(db, pot)
    ts = (utcnow() - timedelta(hours=3)).timestamp()
    m = process_data_message(db, data_payload(measured_at=ts, client_measurement_id="old"), topic_device_id="dev-1")
    assert m.pot_id is None


def test_unknown_device_is_registered_unowned(db):
    m = process_data_message(db, data_payload(device_id="new-dev"), topic_device_id="new-dev")
    dev = db.scalars(select(Device).where(Device.device_id == "new-dev")).one()
    assert m is not None and dev.owner_id is None


def test_topic_device_mismatch_marked_invalid(db, owner):
    m = process_data_message(db, data_payload(device_id="other"), topic_device_id="dev-1")
    assert m.invalid is True


def test_status_message_updates_device(db, owner):
    process_status_message(db, "dev-1", json.dumps({"status": "online", "battery": 87, "fw_version": "1.2.0"}))
    dev = db.scalars(select(Device).where(Device.device_id == "dev-1")).one()
    assert dev.online and dev.battery == 87 and dev.fw_version == "1.2.0"
    process_status_message(db, "dev-1", b"offline")
    db.refresh(dev)
    assert dev.online is False


def test_topic_parsing_and_handler(db, owner):
    assert parse_topic("potani/dev-1/data") == ("dev-1", "data")
    assert parse_topic("potani/dev-1/other") is None
    handle_message("potani/dev-1/data", json.dumps(data_payload(client_measurement_id="h1")).encode())
    handle_message("potani/dev-1/data", b"{broken")  # tidak boleh melempar
    assert count(db) == 1
