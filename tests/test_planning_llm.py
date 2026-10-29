"""F2 langkah 7: rencana LLM → validator → minta ulang (maks 2) → template."""
import json
import uuid

from sqlalchemy import select

from app.agent import llm as llm_module
from app.agent.workflows.soil_first import choose_crop
from app.db.models import AgentLog, Checkpoint, Measurement, Plan
from tests.conftest import FakeLLM

GOOD = {"goal": "Naikkan pH", "crop_id": "alpha",
        "steps": [{"order": 1, "action": "Taburkan abu kayu 20 gram per pot", "remediation_ref": "ph.low.1"}],
        "targets": [{"param": "ph", "min": 6.0, "max": 7.0}], "checkpoints": [{"param": "ph", "due_in_days": 60}]}
BAD = {**GOOD, "steps": [{"order": 1, "action": "Taburkan abu kayu 50 gram", "remediation_ref": "ph.low.1"}]}


def run(db, owner):
    user, _, pot = owner
    db.add(Measurement(device_id="dev-1", pot_id=pot.id, stage="pre_plant", ph=4.8, moisture=50.0, temperature=25.0))
    db.flush()
    out = choose_crop(db, user, pot, "alpha", "user_choice")
    db.commit()
    return db.get(Plan, uuid.UUID(out["plan_id"]))


def test_llm_plan_retried_after_validation_error(db, owner):
    fake = FakeLLM([{"content": json.dumps(BAD)}, {"content": json.dumps(GOOD)}])
    llm_module.set_llm(fake)
    plan = run(db, owner)
    assert plan.generated_by == "llm" and plan.targets[0]["min"] == 6.0
    assert "ditolak validator" in fake.calls[1]["messages"][-1]["content"]
    cp = db.scalars(select(Checkpoint).where(Checkpoint.plan_id == plan.id)).one()
    assert cp.due_in_days == 60


def test_llm_plan_falls_back_to_template_after_two_retries(db, owner):
    fake = FakeLLM([{"content": json.dumps(BAD)}] * 3)
    llm_module.set_llm(fake)
    plan = run(db, owner)
    assert len(fake.calls) == 3 and plan.generated_by == "template"
    logs = db.scalars(select(AgentLog).where(AgentLog.tool == "llm.plan")).all()
    assert [log.error for log in logs] == ["validation_failed"] * 3


def test_llm_plan_rejects_targets_for_params_without_problem(db, owner):
    extra = {**GOOD, "targets": [*GOOD["targets"], {"param": "temperature", "min": 20, "max": 30}],
             "checkpoints": [*GOOD["checkpoints"], {"param": "temperature", "due_in_days": 1}]}
    fake = FakeLLM([{"content": json.dumps(extra)}, {"content": json.dumps(GOOD)}])
    llm_module.set_llm(fake)
    plan = run(db, owner)
    assert [t["param"] for t in plan.targets] == ["ph"]
    assert "hapus target ['temperature']" in fake.calls[1]["messages"][-1]["content"]


def test_non_json_reply_handled(db, owner):
    llm_module.set_llm(FakeLLM([{"content": "maaf"}, {"content": "```json\n" + json.dumps(GOOD) + "\n```"}]))
    assert run(db, owner).generated_by == "llm"
