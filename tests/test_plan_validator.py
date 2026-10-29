import copy

from app.agent.plan_validator import normalize_plan, validate_plan

VALID = {
    "goal": "Menaikkan pH sebelum menanam Alpha",
    "crop_id": "alpha",
    "steps": [
        {"order": 1, "action": "Taburkan abu kayu 20 gram per pot lalu aduk", "remediation_ref": "ph.low.1"},
        {"order": 2, "action": "Siram dan tandai rencana dikerjakan", "remediation_ref": None},
    ],
    "targets": [{"param": "ph", "min": 5.5, "max": 6.5}],
    "checkpoints": [{"param": "ph", "due_in_days": 56}],
}


def plan(**changes):
    p = copy.deepcopy(VALID)
    p.update(changes)
    return p


def test_valid_plan_passes():
    assert validate_plan(VALID, candidate_ids={"alpha"}) == []


def test_crop_must_be_candidate_unless_user_choice():
    assert validate_plan(VALID, candidate_ids={"beta"})
    assert validate_plan(VALID, candidate_ids=set(), user_choice=True) == []


def test_rejects_dose_different_from_rules():
    p = plan(steps=[{"order": 1, "action": "Taburkan abu kayu", "remediation_ref": "ph.low.1", "dose": 50}])
    errors = validate_plan(p, candidate_ids={"alpha"})
    assert any("dosis" in e for e in errors)


def test_rejects_number_in_text_not_matching_rule():
    p = plan(steps=[{"order": 1, "action": "Taburkan abu kayu 50 gram", "remediation_ref": "ph.low.1"}])
    assert any("tidak sesuai dosis" in e for e in validate_plan(p, candidate_ids={"alpha"}))


def test_rejects_unknown_or_unsourced_ref():
    p = plan(steps=[{"order": 1, "action": "Pakai bahan", "remediation_ref": "ph.low.9"}])
    assert any("tidak ada" in e for e in validate_plan(p, candidate_ids={"alpha"}))
    p = plan(steps=[{"order": 1, "action": "Pakai cangkang telur", "remediation_ref": "ph.low.2"}])
    assert any("sumber" in e for e in validate_plan(p, candidate_ids={"alpha"}))


def test_rejects_numbers_without_ref():
    p = plan(steps=[{"order": 1, "action": "Siram 3 kali sehari", "remediation_ref": None}])
    assert validate_plan(p, candidate_ids={"alpha"})


def test_rejects_target_outside_optimal():
    p = plan(targets=[{"param": "ph", "min": 5.0, "max": 6.5}])
    assert any("di luar rentang optimal" in e for e in validate_plan(p, candidate_ids={"alpha"}))


def test_rejects_target_without_sourced_range():
    p = plan(crop_id="beta", targets=[{"param": "moisture", "min": 40, "max": 60}],
             checkpoints=[{"param": "moisture", "due_in_days": 1}])
    assert any("bersumber" in e for e in validate_plan(p, candidate_ids={"beta"}))


def test_rejects_too_short_checkpoint_delay():
    p = plan(checkpoints=[{"param": "ph", "due_in_days": 7}])
    assert any("jeda minimum" in e for e in validate_plan(p, candidate_ids={"alpha"}))


def test_rejects_target_without_checkpoint():
    p = plan(targets=[{"param": "ph", "min": 5.5, "max": 6.5}, {"param": "moisture", "min": 40, "max": 60}])
    assert any("tidak punya checkpoint" in e for e in validate_plan(p, candidate_ids={"alpha"}))
    # update_plan tanpa checkpoint tetap boleh (dijadwalkan terpisah)
    assert validate_plan(plan(checkpoints=[]), candidate_ids={"alpha"}) == []


def test_rejects_more_than_six_steps():
    steps = [{"order": i, "action": "Aduk tanah", "remediation_ref": None} for i in range(1, 8)]
    assert any("Maksimal 6" in e for e in validate_plan(plan(steps=steps), candidate_ids={"alpha"}))


def test_normalize_fills_rule_fields():
    norm = normalize_plan(VALID)
    assert norm["steps"][0]["dose"] == 20 and norm["steps"][0]["source"] == "TEST"
    assert norm["steps"][1]["remediation_ref"] is None
