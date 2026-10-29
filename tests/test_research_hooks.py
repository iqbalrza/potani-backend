"""Hook evaluasi riset (PRD 11): format skenario RQ3, skrip RQ1, laporan RQ5."""
import json
from pathlib import Path

from app.agent.tools import TOOLS
from app.ml.noise_stability import stability

SCENARIOS = Path(__file__).parent / "agent_eval" / "scenarios.jsonl"


def test_rq3_has_at_least_50_wellformed_scenarios():
    rows = [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 50
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["input"] and isinstance(r["expected_args"], dict)
        assert all(t in TOOLS for t in r["expected_tools"])


def test_rq1_noise_stability_runs():
    out = stability([({"ph": 6.2, "moisture": 50, "temperature": 25}, "full"),
                     ({"ph": 5.6, "moisture": 50, "temperature": 25}, "full")], trials=50, seed=1)
    assert out["n_readings"] == 2 and 0 <= out["top_change_rate_mean"] <= 1


def test_rq5_report_runs(db, owner):
    import importlib.util

    spec = importlib.util.spec_from_file_location("rq5", Path(__file__).parents[1] / "scripts" / "rq5_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.report(include_demo=True)
    assert out["plans"] == 0 and out["achieved_ratio"] is None
