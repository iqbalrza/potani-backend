import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agent import llm as llm_module
from app.agent import loop_f6
from app.agent.workflows.soil_first import choose_crop
from app.db.models import AgentLog, AgentQuestion, Checkpoint, Measurement, Plan, Pot
from app.db.types import utcnow
from app.ingest import process_data_message
from app.notify import fcm
from tests.conftest import FakeLLM, data_payload, tool_call
from tests.test_mqtt_ingest import open_session


def make_plan(db, owner, ph=4.8, moisture=50.0) -> Plan:
    user, _, pot = owner
    db.add(Measurement(device_id="dev-1", pot_id=pot.id, stage="pre_plant", ph=ph, moisture=moisture,
                       temperature=25.0))
    db.flush()
    out = choose_crop(db, user, pot, "alpha", "user_choice")
    db.commit()
    return db.get(Plan, uuid.UUID(out["plan_id"]))


def checkpoints(db, plan, version=None):
    q = select(Checkpoint).where(Checkpoint.plan_id == plan.id)
    if version:
        q = q.where(Checkpoint.plan_version == version)
    return db.scalars(q.order_by(Checkpoint.created_at)).all()


def make_due(db, plan):
    for cp in checkpoints(db, plan, plan.version):
        if cp.status == "scheduled":
            cp.due_at = utcnow() - timedelta(seconds=1)
    db.commit()


def measure(db, pot, ph, moisture=50.0, stage="post_remediation"):
    open_session(db, pot, stage=stage)
    return process_data_message(db, data_payload(ph=ph, moisture=moisture, client_measurement_id=str(uuid.uuid4())),
                                topic_device_id="dev-1")


def cycle(db, owner, plan, ph):
    """Satu putaran: dikerjakan → jatuh tempo → ukur → verifikasi."""
    loop_f6.mark_done(db, plan)
    db.commit()
    make_due(db, plan)
    loop_f6.scan_due_checkpoints(db)
    measure(db, owner[2], ph)
    loop_f6.run_verify_queue(db)
    db.refresh(plan)


# ---------------------------------------------------------------- tabel transisi

@pytest.mark.parametrize("state, event, expected", list(
    (s, e, t) for (s, e), t in loop_f6.TRANSITIONS.items()))
def test_every_transition(state, event, expected):
    plan = Plan(state=state, replan_count=0)
    assert loop_f6.transition(plan, event) == expected
    assert plan.state == expected
    if event == "replanned":
        assert plan.replan_count == 1


@pytest.mark.parametrize("state, event", [("PLANNED", "measured"), ("ACHIEVED", "mark_done"),
                                          ("ESCALATED", "replanned"), ("VERIFYING", "due")])
def test_invalid_transitions_raise(state, event):
    with pytest.raises(loop_f6.InvalidTransition):
        loop_f6.transition(Plan(state=state, replan_count=0), event)


# ---------------------------------------------------------------- alur lengkap

def test_plan_created_with_template_and_checkpoint(db, owner):
    plan = make_plan(db, owner)
    assert plan.state == "PLANNED" and plan.generated_by == "template"
    assert plan.steps[0]["remediation_ref"] == "ph.low.1"  # opsi rumah tangga dulu
    assert plan.targets == [{"param": "ph", "min": 5.5, "max": 7.0}]
    [cp] = checkpoints(db, plan)
    assert cp.param == "ph" and cp.due_in_days == 56 and cp.status == "scheduled"


def test_achieved_flow(db, owner):
    plan = make_plan(db, owner)
    loop_f6.mark_done(db, plan)
    db.commit()
    assert plan.state == "AWAITING_CHECKPOINT"
    [cp] = checkpoints(db, plan)
    assert cp.due_at > utcnow() + timedelta(days=55)

    assert loop_f6.scan_due_checkpoints(db) == 0  # belum jatuh tempo
    make_due(db, plan)
    assert loop_f6.scan_due_checkpoints(db) == 1
    assert loop_f6.scan_due_checkpoints(db) == 0  # idempoten
    db.refresh(plan)
    db.refresh(cp)
    assert plan.state == "CHECKPOINT_DUE" and cp.status == "reminded"
    assert fcm.OUTBOX[-1]["title"] == "Waktunya ukur ulang"

    measure(db, owner[2], ph=6.2)
    db.refresh(cp)
    assert cp.status == "measured"
    assert loop_f6.run_verify_queue(db) == 1
    assert loop_f6.run_verify_queue(db) == 0  # idempoten
    db.refresh(plan)
    db.refresh(cp)
    assert plan.state == "ACHIEVED" and cp.status == "verified" and cp.verify_result["achieved"] is True
    assert "Silakan mulai menanam Alpha" in fcm.OUTBOX[-1]["body"]


def test_not_achieved_replans_with_template_fallback(db, owner):
    plan = make_plan(db, owner)
    cycle(db, owner, plan, ph=5.0)
    assert plan.state == "PLANNED" and plan.replan_count == 1 and plan.version == 2
    assert plan.steps[0]["remediation_ref"] == "ph.low.0"  # opsi berikutnya
    assert len(plan.history) == 1
    new_cps = checkpoints(db, plan, version=2)
    assert len(new_cps) == 1 and new_cps[0].status == "scheduled"
    old = checkpoints(db, plan, version=1)[0]
    assert old.status == "verified" and old.verify_result["condition"] == "low"


def test_escalated_on_third_replan(db, owner):
    plan = make_plan(db, owner)
    for expected in (1, 2, 3):
        cycle(db, owner, plan, ph=5.0)
        assert plan.state == "PLANNED" and plan.replan_count == expected
    cycle(db, owner, plan, ph=5.0)
    assert plan.state == "ESCALATED"
    pot = db.get(Pot, plan.pot_id)
    db.refresh(pot)
    assert pot.needs_help is True and pot.help_reason


def test_partial_verification_waits_for_other_checkpoint(db, owner):
    plan = make_plan(db, owner, ph=4.8, moisture=30.0)  # pH dan kelembapan rendah
    params = {c.param: c for c in checkpoints(db, plan)}
    assert set(params) == {"ph", "moisture"}
    loop_f6.mark_done(db, plan)
    params["moisture"].due_at = utcnow() - timedelta(seconds=1)
    db.commit()
    loop_f6.scan_due_checkpoints(db)
    measure(db, owner[2], ph=4.8, moisture=50.0)
    loop_f6.run_verify_queue(db)
    db.refresh(plan)
    assert plan.state == "AWAITING_CHECKPOINT"
    db.refresh(params["ph"])
    assert params["ph"].status == "scheduled"


def test_missed_flow_with_two_rereminders(db, owner, monkeypatch):
    plan = make_plan(db, owner)
    loop_f6.mark_done(db, plan)
    db.commit()
    make_due(db, plan)
    loop_f6.scan_due_checkpoints(db)
    [cp] = checkpoints(db, plan)
    now = utcnow()
    step = timedelta(days=3, seconds=1)
    assert loop_f6.scan_missed(db, now) == 0  # belum 3 hari
    assert loop_f6.scan_missed(db, now + step) == 1
    db.refresh(plan)
    db.refresh(cp)
    assert plan.state == "MISSED" and cp.reminder_count == 2
    loop_f6.scan_missed(db, now + 2 * step)
    db.refresh(cp)
    assert cp.reminder_count == 3 and cp.status == "reminded"
    loop_f6.scan_missed(db, now + 3 * step)
    db.refresh(cp)
    assert cp.status == "missed" and cp.reminder_count == 3  # maks 2 pengingat ulang

    # Pengukuran terlambat tetap bisa diverifikasi.
    measure(db, owner[2], ph=6.2)
    loop_f6.run_verify_queue(db)
    db.refresh(plan)
    assert plan.state == "ACHIEVED"


def test_agent_replan_uses_llm_tools(db, owner):
    plan = make_plan(db, owner)
    pid = str(plan.id)
    changes = {"goal": "Naikkan pH dengan kapur", "crop_id": "alpha",
               "steps": [{"order": 1, "action": "Taburkan kapur dolomit 10 gram per pot", "remediation_ref": "ph.low.0"}],
               "targets": [{"param": "ph", "min": 5.5, "max": 7.0}], "checkpoints": [{"param": "ph", "due_in_days": 56}]}
    llm_module.set_llm(FakeLLM([
        {"tool_calls": [tool_call("get_remediation", {"param": "ph", "condition": "low"})]},
        {"tool_calls": [tool_call("update_plan", {"plan_id": pid, "changes": changes, "reason": "abu kayu kurang"}),
                        tool_call("schedule_checkpoint", {"plan_id": pid, "param": "ph", "due_in_days": 56})]},
    ]))
    cycle(db, owner, plan, ph=5.0)
    assert plan.state == "PLANNED" and plan.generated_by == "llm" and plan.replan_count == 1
    assert plan.steps[0]["dose"] == 10 and plan.steps[0]["source"] == "TEST"
    tools = [log.tool for log in db.scalars(select(AgentLog).where(AgentLog.feature == "F6"))]
    assert {"get_remediation", "update_plan", "schedule_checkpoint", "verify_target"} <= set(tools)


def test_agent_invalid_plans_fall_back_to_template(db, owner):
    plan = make_plan(db, owner)
    pid = str(plan.id)
    bad = {"goal": "x", "crop_id": "alpha",
           "steps": [{"order": 1, "action": "Taburkan kapur dolomit 99 gram", "remediation_ref": "ph.low.0"}],
           "targets": [{"param": "ph", "min": 5.5, "max": 7.0}], "checkpoints": []}
    llm_module.set_llm(FakeLLM([{"tool_calls": [tool_call("update_plan", {"plan_id": pid, "changes": bad,
                                                                          "reason": "r"})]}] * 3))
    cycle(db, owner, plan, ph=5.0)
    assert plan.state == "PLANNED" and plan.generated_by == "template"
    errors = db.scalars(select(AgentLog).where(AgentLog.tool == "update_plan",
                                               AgentLog.error == "validation_failed")).all()
    assert len(errors) == 3


def test_ask_user_waits_then_answer_resumes_same_run(db, owner):
    plan = make_plan(db, owner)
    pid = str(plan.id)
    llm_module.set_llm(FakeLLM([{"tool_calls": [tool_call("ask_user", {
        "question": "Bahan apa yang kamu punya?", "options": ["abu kayu", "kapur dolomit"]})]}]))
    cycle(db, owner, plan, ph=5.0)
    assert plan.state == "WAITING_USER"
    q = db.scalars(select(AgentQuestion).where(AgentQuestion.plan_id == plan.id)).one()
    assert q.status == "pending" and q.context["verify_result"]["param"] == "ph"

    changes = {"goal": "Pakai kapur", "crop_id": "alpha",
               "steps": [{"order": 1, "action": "Taburkan kapur dolomit", "remediation_ref": "ph.low.0"}],
               "targets": [{"param": "ph", "min": 6.0, "max": 7.0}]}
    llm_module.set_llm(FakeLLM([{"tool_calls": [
        tool_call("update_plan", {"plan_id": pid, "changes": changes, "reason": "pengguna punya kapur"}),
        tool_call("schedule_checkpoint", {"plan_id": pid, "param": "ph", "due_in_days": 56})]}]))
    q.answer, q.status = "kapur dolomit", "answered"
    loop_f6.answer_question(db, q, "kapur dolomit")
    db.commit()
    assert plan.state == "PLANNED" and plan.replan_count == 1
    run_ids = {log.run_id for log in db.scalars(select(AgentLog).where(AgentLog.tool.in_(("ask_user", "update_plan"))))}
    assert run_ids == {q.run_id}


def test_agent_identical_revision_rejected(db, owner):
    plan = make_plan(db, owner)
    pid = str(plan.id)
    same = {"goal": plan.goal, "crop_id": "alpha", "steps": plan.steps, "targets": plan.targets}
    llm_module.set_llm(FakeLLM([{"tool_calls": [tool_call("update_plan", {"plan_id": pid, "changes": same,
                                                                          "reason": "ulang"})]}] * 3))
    cycle(db, owner, plan, ph=5.0)
    rejected = db.scalars(select(AgentLog).where(AgentLog.tool == "update_plan",
                                                 AgentLog.error == "validation_failed")).all()
    assert len(rejected) == 3 and "sama persis" in rejected[0].result["validation_errors"][0]
    assert plan.state == "PLANNED" and plan.generated_by == "template"  # fallback memilih opsi lain
    assert plan.steps[0]["remediation_ref"] == "ph.low.0"


def test_agent_escalate_tool(db, owner):
    plan = make_plan(db, owner)
    llm_module.set_llm(FakeLLM([{"tool_calls": [tool_call("escalate", {"pot_id": str(plan.pot_id),
                                                                       "reason": "Tidak ada opsi aman"})]}]))
    cycle(db, owner, plan, ph=5.0)
    assert plan.state == "ESCALATED"
