import time
import uuid

from sqlalchemy import select

from app.agent import llm as llm_module
from app.db.models import AgentLog
from app.db.session import SessionLocal
from app.ingest import process_data_message
from tests.conftest import FakeLLM, auth, data_payload


def setup_pot(client, uid="alice", light="full", container="pot_sedang", device="dev-1"):
    assert client.post("/devices/pair", json={"device_id": device}, headers=auth(uid)).status_code == 200
    body = {"name": "Pot Cabai"}
    if light:
        body["light_level"] = light
    if container:
        body["container_type"] = container
    r = client.post("/pots", json=body, headers=auth(uid))
    assert r.status_code == 201
    return r.json()["pot_id"]


def ingest_via_session(client, pot_id, stage="pre_plant", uid="alice", device="dev-1", **reading):
    r = client.post("/sessions", json={"device_id": device, "pot_id": pot_id, "stage": stage}, headers=auth(uid))
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    with SessionLocal() as db:
        process_data_message(db, data_payload(device_id=device, client_measurement_id=str(uuid.uuid4()), **reading),
                             topic_device_id=device)
    r = client.get(f"/sessions/{sid}", headers=auth(uid))
    assert r.json()["status"] == "completed"
    return r.json()["measurement"]


# ---------------------------------------------------------------- dasar

def test_health_without_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "ok" and body["api"] == "ok" and "llm" in body


def test_auth_required_with_error_format(client):
    r = client.get("/pots")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert client.get("/pots", headers={"Authorization": "Bearer palsu"}).status_code == 401


def test_validation_error_format(client):
    r = client.post("/pots", json={"name": "", "light_level": "gelap"}, headers=auth())
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and "light_level" in err["fields"]


def test_chat_message_max_500(client):
    r = client.post("/chat", json={"message": "a" * 501}, headers=auth())
    assert r.status_code == 422


def test_profile_update(client):
    r = client.patch("/me", json={"materials_owned": ["abu kayu", " "], "light_default": "partial"}, headers=auth())
    assert r.status_code == 200 and r.json()["materials_owned"] == ["abu kayu"]


# ---------------------------------------------------------------- kepemilikan

def test_users_only_access_their_own_pots_and_devices(client):
    pot_id = setup_pot(client, "alice")
    assert client.get(f"/pots/{pot_id}", headers=auth("bob")).status_code == 404
    assert client.patch(f"/pots/{pot_id}", json={"name": "x"}, headers=auth("bob")).status_code == 404
    r = client.post("/devices/pair", json={"device_id": "dev-1"}, headers=auth("bob"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "device_taken"
    r = client.post("/sessions", json={"device_id": "dev-1", "pot_id": pot_id, "stage": "pre_plant"},
                    headers=auth("bob"))
    assert r.status_code == 404
    assert client.post("/evaluate", json={"pot_id": pot_id, "crop_id": "alpha"}, headers=auth("bob")).status_code == 404


def test_pot_crud(client):
    pot_id = setup_pot(client)
    r = client.patch(f"/pots/{pot_id}", json={"light_level": "shade", "name": "Pot Teras"}, headers=auth())
    assert r.json()["light_level"] == "shade" and r.json()["name"] == "Pot Teras"
    assert len(client.get("/pots", headers=auth()).json()["pots"]) == 1


# ---------------------------------------------------------------- sesi & bacaan

def test_session_polling_and_measurement_history(client):
    pot_id = setup_pot(client)
    m = ingest_via_session(client, pot_id, ph=6.2)
    assert m["pot_id"] == pot_id and m["stage"] == "pre_plant"
    hist = client.get(f"/measurements?pot_id={pot_id}", headers=auth()).json()["measurements"]
    assert len(hist) == 1


def test_orphan_measurement_can_be_linked(client):
    pot_id = setup_pot(client)
    with SessionLocal() as db:
        process_data_message(db, data_payload(), topic_device_id="dev-1")
    orphans = client.get("/measurements?unassigned=true", headers=auth()).json()["measurements"]
    assert len(orphans) == 1
    mid = orphans[0]["measurement_id"]
    assert client.get("/measurements?unassigned=true", headers=auth("bob")).json()["measurements"] == []
    r = client.patch(f"/measurements/{mid}", json={"pot_id": pot_id, "stage": "control"}, headers=auth())
    assert r.status_code == 200 and r.json()["pot_id"] == pot_id
    r = client.patch(f"/measurements/{mid}", json={"pot_id": pot_id, "stage": "control"}, headers=auth())
    assert r.status_code == 409


# ---------------------------------------------------------------- F2 / F3 tanpa LLM

def test_recommend_requires_pre_plant_reading(client):
    pot_id = setup_pot(client)
    r = client.post("/recommend", json={"pot_id": pot_id}, headers=auth())
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_pre_plant_reading"


def test_recommend_without_llm_returns_null_narrative(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=6.2, moisture=50, temperature=25)
    start = time.perf_counter()
    r = client.post("/recommend", json={"pot_id": pot_id}, headers=auth())
    assert time.perf_counter() - start < 0.3
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["narrative"] is None
    assert [c["crop_id"] for c in body["candidates"]] == ["alpha", "beta"]
    with SessionLocal() as db:
        logs = db.scalars(select(AgentLog).where(AgentLog.feature == "F2")).all()
    assert {"run_suitability", "get_weather", "llm.narrative"} <= {log.tool for log in logs}


def test_recommend_with_llm_narrative_grounded(client):
    llm_module.set_llm(FakeLLM([{"content": "Alpha dan Beta sama-sama cocok dengan skor 1.0."}]))
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=6.2, moisture=50, temperature=25)
    assert client.post("/recommend", json={"pot_id": pot_id}, headers=auth()).json()["narrative"].startswith("Alpha")


def test_recommend_drops_narrative_with_invented_numbers(client):
    llm_module.set_llm(FakeLLM([{"content": "Alpha butuh pH 6.8 dan pupuk 15 gram."}]))
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=6.2, moisture=50, temperature=25)
    assert client.post("/recommend", json={"pot_id": pot_id}, headers=auth()).json()["narrative"] is None


def test_recommend_asks_user_for_missing_context_then_resumes(client):
    pot_id = setup_pot(client, light=None, container=None)
    ingest_via_session(client, pot_id, ph=5.8, moisture=60, temperature=25)
    r = client.post("/recommend", json={"pot_id": pot_id}, headers=auth()).json()
    assert r["status"] == "need_input"
    q = r["question"]
    r = client.post("/agent/answers", json={"question_id": q["question_id"], "answer": "jawaban asal"}, headers=auth())
    assert r.status_code == 422
    r = client.post("/agent/answers", json={"question_id": q["question_id"], "answer": q["options"][2]},
                    headers=auth()).json()
    assert r["result"]["status"] == "need_input"  # lanjut tanya wadah
    q2 = r["result"]["question"]
    r = client.post("/agent/answers", json={"question_id": q2["question_id"], "answer": q2["options"][0]},
                    headers=auth()).json()
    assert r["result"]["status"] == "ok"
    assert [c["crop_id"] for c in r["result"]["candidates"]] == ["gamma"]  # teduh
    pot = client.get(f"/pots/{pot_id}", headers=auth()).json()
    assert pot["light_level"] == "shade" and pot["container_type"] == "pot_kecil"


def test_recommend_needs_remeasure(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=5.6, moisture=50, temperature=25)
    body = client.post("/recommend", json={"pot_id": pot_id}, headers=auth()).json()
    assert body["status"] == "needs_remeasure" and body["needs_remeasure"] is True
    # Kandidat dari hasil yang meminta ukur ulang tidak bisa dipilih sebagai rekomendasi.
    r = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "alpha", "source": "recommender"},
                    headers=auth())
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_a_candidate"


def test_evaluate_plant_first(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=4.8, moisture=50, temperature=25)
    r = client.post("/evaluate", json={"pot_id": pot_id, "crop_id": "alpha"}, headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "perlu_perbaikan" and body["narrative"] is None
    assert body["remediation_hint"][0]["options"][0]["material"] == "abu kayu"
    assert client.post("/evaluate", json={"pot_id": pot_id, "crop_id": "kaktus"}, headers=auth()).status_code == 404


def test_crops_lists_sources(client):
    body = client.get("/crops", headers=auth()).json()
    delta = next(c for c in body["crops"] if c["crop_id"] == "delta")
    assert delta["params"]["ph"]["unsourced"] is True and delta["params"]["ph"]["source"] is None
    assert body["is_demo"] is True


# ---------------------------------------------------------------- planting → rencana → checkpoint

def test_planting_creates_plan_and_done_flow(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=4.8, moisture=50, temperature=25)
    client.post("/recommend", json={"pot_id": pot_id}, headers=auth())
    r = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "alpha", "source": "recommender"},
                    headers=auth())
    assert r.status_code == 201, r.text
    body = r.json()
    plan = body["plan"]
    assert body["planting"]["status"] == "planned"
    assert plan["state"] == "PLANNED" and plan["kind"] == "soil_first"
    assert plan["steps"][0]["source"] == "TEST"
    assert plan["checkpoints"][0]["status"] == "scheduled"

    r = client.post(f"/plans/{plan['plan_id']}/done", headers=auth())
    assert r.json()["state"] == "AWAITING_CHECKPOINT"
    assert client.post(f"/plans/{plan['plan_id']}/done", headers=auth()).status_code == 409
    assert len(client.get("/checkpoints?status=scheduled", headers=auth()).json()["checkpoints"]) == 1
    assert client.get(f"/plans/{plan['plan_id']}", headers=auth("bob")).status_code == 404

    r = client.patch(f"/plantings/{body['planting']['planting_id']}", json={"status": "growing"}, headers=auth())
    assert r.json()["status"] == "growing" and r.json()["planted_at"]


def test_planting_recommender_must_be_candidate(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=6.2, moisture=50, temperature=25)
    client.post("/recommend", json={"pot_id": pot_id}, headers=auth())
    r = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "gamma", "source": "recommender"},
                    headers=auth())
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_a_candidate"
    r = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "gamma", "source": "user_choice"},
                    headers=auth())
    assert r.status_code == 201


def test_planting_without_problems_has_no_plan(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=6.2, moisture=50, temperature=25)
    r = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "alpha", "source": "user_choice"},
                    headers=auth())
    assert r.json()["plan"] is None and r.json()["evaluation"]["verdict"] == "cocok"


# ---------------------------------------------------------------- tanpa LLM: semua endpoint inti tetap 200

def test_core_endpoints_work_when_llm_is_down(client):
    pot_id = setup_pot(client)
    ingest_via_session(client, pot_id, ph=4.8, moisture=50, temperature=25)
    for path, body in (("/recommend", {"pot_id": pot_id}), ("/evaluate", {"pot_id": pot_id, "crop_id": "alpha"})):
        r = client.post(path, json=body, headers=auth())
        assert r.status_code == 200 and r.json()["narrative"] is None
    r = client.post("/plantings", json={"pot_id": pot_id, "crop_id": "alpha", "source": "user_choice"},
                    headers=auth())
    assert r.status_code == 201 and r.json()["plan"]["generated_by"] == "template"
    r = client.post("/chat", json={"message": "Bagaimana kondisi pot saya?", "pot_id": pot_id}, headers=auth())
    assert r.status_code == 200
    assert r.json()["narrative"] is None and "4.8" in r.json()["reply"]


def test_internal_errors_are_not_leaked(client, monkeypatch):
    from app.agent.workflows import soil_first

    def boom(*a, **kw):
        raise RuntimeError("password=rahasia db://internal")

    monkeypatch.setattr("app.api.routes.recommend.recommend", boom)
    pot_id = setup_pot(client)
    c = client.__class__(client.app, raise_server_exceptions=False)
    r = c.post("/recommend", json={"pot_id": pot_id}, headers=auth())
    assert r.status_code == 500
    assert "rahasia" not in r.text and r.json()["error"]["code"] == "internal_error"
    assert soil_first  # modul tetap utuh
