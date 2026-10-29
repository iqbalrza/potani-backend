"""RQ5: proporsi checkpoint yang mencapai target dan jumlah revisi rencana per kasus.

    python scripts/rq5_report.py [--include-demo]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import AgentLog, Checkpoint, Plan, Pot  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def report(include_demo: bool) -> dict:
    with SessionLocal() as db:
        q = select(Plan).join(Pot, Pot.id == Plan.pot_id)
        if not include_demo:
            q = q.where(Pot.is_demo.is_(False))
        plans = db.scalars(q).all()
        plan_ids = [p.id for p in plans]
        cps = db.scalars(select(Checkpoint).where(Checkpoint.plan_id.in_(plan_ids))).all() if plan_ids else []
        logs = db.scalars(select(AgentLog).where(AgentLog.plan_id.in_(plan_ids))).all() if plan_ids else []
    verified = [c for c in cps if c.status == "verified" and c.verify_result]
    achieved = [c for c in verified if c.verify_result.get("achieved")]
    return {
        "plans": len(plans),
        "plan_states": dict(Counter(p.state for p in plans)),
        "checkpoints": dict(Counter(c.status for c in cps)),
        "verified_checkpoints": len(verified),
        "achieved_ratio": round(len(achieved) / len(verified), 4) if verified else None,
        "replans_per_plan": dict(Counter(p.replan_count for p in plans)),
        "plans_generated_by": dict(Counter(p.generated_by for p in plans)),
        "f6_tool_calls": dict(Counter(log.tool for log in logs if log.feature == "F6")),
        "include_demo": include_demo,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-demo", action="store_true")
    print(json.dumps(report(ap.parse_args().include_demo), indent=2))
