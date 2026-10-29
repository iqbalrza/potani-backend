import pytest

from app.config import get_settings
from app.domain.suitability import run_suitability, score_param

SPEC = (4.0, 5.5, 6.5, 8.0)  # abs_min, opt_min, opt_max, abs_max


@pytest.mark.parametrize("x, expected", [
    (3.0, 0.0),    # di luar batas absolut
    (4.0, 0.0),    # tepi absolut
    (4.75, 0.5),   # tengah lereng bawah
    (5.5, 1.0),    # tepi optimal
    (6.0, 1.0),    # di dalam optimal
    (7.25, 0.5),   # tengah lereng atas
    (8.0, 0.0),    # tepi absolut atas
    (8.5, 0.0),    # di luar absolut atas
])
def test_score_param_five_points(x, expected):
    assert score_param(x, *SPEC) == pytest.approx(expected)


def test_unsourced_params_are_skipped_and_reported():
    r = run_suitability({"ph": 6.2, "moisture": 50, "temperature": 24}, "full")
    beta = next(c for c in r["candidates"] if c["crop_id"] == "beta")
    assert "moisture" not in beta["per_param"]
    assert "moisture" in r["skipped_params"] and "temperature" in r["skipped_params"]
    delta = next(e for e in r["excluded"] if e["crop_id"] == "delta")
    assert "belum ada acuan" in delta["reason"]


def test_light_filter_excludes_with_reason():
    r = run_suitability({"ph": 6.0, "moisture": 50, "temperature": 24}, "shade")
    excluded = {e["crop_id"]: e["reason"] for e in r["excluded"]}
    assert excluded["beta"] == "butuh cahaya penuh"
    assert "alpha" in excluded
    assert [c["crop_id"] for c in r["candidates"]] == ["gamma"]


def test_total_is_minimum_and_limiting_param():
    # alpha: pH 5.0 → (5.0-4.0)/1.5 = 0.6667; moisture 50 → 1; suhu 25 → 1
    r = run_suitability({"ph": 5.0, "moisture": 50, "temperature": 25}, "full")
    alpha = next(c for c in r["candidates"] if c["crop_id"] == "alpha")
    assert alpha["score"] == pytest.approx(0.6667, abs=1e-4)
    assert alpha["limiting_param"] == "ph"
    assert alpha["score_product"] == pytest.approx(0.6667, abs=1e-4)
    assert any("pH sedikit di bawah" in reason for reason in alpha["reasons"])


def test_candidates_sorted_and_threshold(monkeypatch):
    r = run_suitability({"ph": 6.2, "moisture": 55, "temperature": 22}, "full")
    scores = [c["score"] for c in r["candidates"]]
    assert scores == sorted(scores, reverse=True)
    monkeypatch.setattr(get_settings(), "suitability_threshold", 1.01)
    r2 = run_suitability({"ph": 6.2, "moisture": 55, "temperature": 22}, "full")
    assert r2["candidates"] == []
    assert all("score" in e for e in r2["excluded"] if e["crop_id"] in ("alpha", "beta"))


def test_near_boundary_and_needs_remeasure():
    # pH 5.6 dekat opt_min alpha (5.5) dan opt_min beta (6.0, jarak 0.4 ≤ MAE 0.5)
    r = run_suitability({"ph": 5.6, "moisture": 50, "temperature": 25}, "full")
    alpha = next(c for c in r["candidates"] if c["crop_id"] == "alpha")
    assert alpha["per_param"]["ph"]["near_boundary"] is True
    assert r["needs_remeasure"] is True


def test_no_remeasure_when_values_far_from_boundaries():
    r = run_suitability({"ph": 6.2, "moisture": 50, "temperature": 25}, "full")
    alpha = next(c for c in r["candidates"] if c["crop_id"] == "alpha")
    assert alpha["per_param"]["ph"]["near_boundary"] is False
    assert r["needs_remeasure"] is False
