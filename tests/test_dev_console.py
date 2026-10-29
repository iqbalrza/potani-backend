"""Konsol test web dan endpoint /dev (hanya mode dev)."""
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import auth
from tests.test_api import setup_pot


def test_console_served_in_dev_mode(client):
    r = client.get("/console")
    assert r.status_code == 200 and "Konsol Test Potani" in r.text
    assert client.get("/", follow_redirects=False).headers["location"] == "/console"


def test_dev_routes_absent_in_firebase_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_mode", "firebase")
    from app.main import create_app

    c = TestClient(create_app())
    assert c.get("/console").status_code == 404
    assert c.post("/dev/jobs/run_verify_queue").status_code in (401, 404)
    assert "/dev/simulate" not in c.get("/openapi.json").json()["paths"]


def test_full_loop_from_console_endpoints(client):
    pot_id = setup_pot(client)
    s = client.post("/sessions", json={"device_id": "dev-1", "pot_id": pot_id, "stage": "pre_plant"}, headers=auth()).json()
    m = client.post("/dev/simulate", json={"device_id": "dev-1", "ph": 4.8, "moisture": 50, "temperature": 25},
                    headers=auth()).json()
    assert m["pot_id"] == pot_id and m["is_demo"] is True
    assert client.get(f"/sessions/{s['session_id']}", headers=auth()).json()["status"] == "completed"

    plan = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "alpha", "source": "user_choice"},
                       headers=auth()).json()["plan"]
    client.post(f"/plans/{plan['plan_id']}/done", headers=auth())
    cp = plan["checkpoints"][0]["checkpoint_id"]
    assert client.post(f"/dev/checkpoints/{cp}/make-due", headers=auth("bob")).status_code == 404
    client.post(f"/dev/checkpoints/{cp}/make-due", headers=auth())
    assert client.post("/dev/jobs/scan_due_checkpoints", headers=auth()).json()["processed"] == 1

    client.post("/sessions", json={"device_id": "dev-1", "pot_id": pot_id, "stage": "post_remediation"}, headers=auth())
    client.post("/dev/simulate", json={"device_id": "dev-1", "ph": 6.2, "moisture": 50, "temperature": 25}, headers=auth())
    assert client.post("/dev/jobs/run_verify_queue", headers=auth()).json()["processed"] == 1
    assert client.get(f"/plans/{plan['plan_id']}", headers=auth()).json()["state"] == "ACHIEVED"

    titles = [n["title"] for n in client.get("/dev/notifications", headers=auth()).json()["notifications"]]
    assert titles[:2] == ["Target tercapai", "Waktunya ukur ulang"]
    logs = client.get("/dev/agent-logs?feature=F6", headers=auth()).json()["logs"]
    assert any(log["tool"] == "verify_target" for log in logs)
    assert client.get("/dev/agent-logs", headers=auth("bob")).json()["logs"] == []


def test_simulate_requires_owned_device(client):
    setup_pot(client)
    r = client.post("/dev/simulate", json={"device_id": "dev-1", "ph": 6, "moisture": 50, "temperature": 25},
                    headers=auth("bob"))
    assert r.status_code == 404
    assert client.post("/dev/jobs/hapus_semua", headers=auth()).status_code == 422
