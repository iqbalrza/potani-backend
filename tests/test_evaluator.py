from app.domain.evaluator import evaluate
from app.domain.remediation import get_remediation


def test_cocok_when_all_ok():
    r = evaluate("alpha", {"ph": 6.2, "moisture": 50, "temperature": 25}, "full")
    assert r["verdict"] == "cocok"
    assert all(v["status"] == "ok" for v in r["per_param"].values())
    assert r["remediation_hint"] == []


def test_perlu_perbaikan_when_low_inside_absolute():
    r = evaluate("alpha", {"ph": 4.8, "moisture": 50, "temperature": 25}, "full")
    assert r["per_param"]["ph"]["status"] == "low"
    assert r["per_param"]["ph"]["out_of_absolute"] is False
    assert r["verdict"] == "perlu_perbaikan"
    hint = r["remediation_hint"][0]
    assert hint["param"] == "ph" and hint["condition"] == "low"


def test_high_inside_absolute():
    r = evaluate("alpha", {"ph": 6.2, "moisture": 70, "temperature": 25}, "full")
    assert r["per_param"]["moisture"]["status"] == "high"
    assert r["verdict"] == "perlu_perbaikan"


def test_tidak_cocok_when_outside_absolute():
    r = evaluate("alpha", {"ph": 3.5, "moisture": 50, "temperature": 25}, "full")
    assert r["per_param"]["ph"]["out_of_absolute"] is True
    assert r["verdict"] == "tidak_cocok"
    assert r["remediation_hint"], "low/high tetap disertai remediation_hint"


def test_no_reference_param_is_reported_not_judged():
    r = evaluate("beta", {"ph": 6.5, "moisture": 99, "temperature": 20}, "full")
    assert r["per_param"]["moisture"]["status"] == "no_reference"
    assert r["no_reference"] == ["moisture"]
    assert r["verdict"] == "cocok"


def test_near_boundary_suggests_remeasure():
    r = evaluate("alpha", {"ph": 5.3, "moisture": 50, "temperature": 25}, "full")
    assert r["per_param"]["ph"]["status"] == "near_boundary"
    assert r["remeasure_suggested"] is True
    # 5.3 < opt_min 5.5: di luar sisi bawah, masih dalam MAE
    assert r["per_param"]["ph"]["boundary_side"] == "lower"
    assert "sedikit di luar batas bawah" in r["per_param"]["ph"]["message"]


def test_near_boundary_side_inside_upper():
    r = evaluate("alpha", {"ph": 6.8, "moisture": 50, "temperature": 25}, "full")  # opt 5.5–7.0
    assert r["per_param"]["ph"]["boundary_side"] == "upper"
    assert "masih di dalam rentang optimal tetapi dekat batas atas" in r["per_param"]["ph"]["message"]


def test_light_mismatch_is_tidak_cocok():
    r = evaluate("beta", {"ph": 6.5, "moisture": 50, "temperature": 20}, "shade")
    assert r["light"]["status"] == "tidak_sesuai"
    assert r["verdict"] == "tidak_cocok"


def test_remediation_household_first_sourced_only():
    r = get_remediation("ph", "low", [])
    materials = [o["material"] for o in r["options"]]
    assert materials == ["abu kayu", "kapur dolomit"]  # rumah tangga dulu
    assert "cangkang telur" not in materials and r["skipped_unsourced"] == ["cangkang telur"]
    assert all(o["source"] for o in r["options"])
    assert r["verify_after_days"] == 56


def test_remediation_owned_materials_flagged():
    r = get_remediation("ph", "low", ["Kapur Dolomit"])
    assert {o["material"]: o["owned"] for o in r["options"]} == {"abu kayu": False, "kapur dolomit": True}


def test_remediation_without_sources_returns_message():
    r = get_remediation("temperature", "high", [])
    assert r["options"] == [] and r["message"]
