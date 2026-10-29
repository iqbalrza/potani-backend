"""Query bersama yang dipakai API, agent, dan worker."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentLog, Checkpoint, Measurement, Plan, Planting, Pot, User
from app.db.types import utcnow


def as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def owned_pot(db: Session, user: User, pot_id: Any) -> Pot | None:
    pid = as_uuid(pot_id)
    if pid is None:
        return None
    pot = db.get(Pot, pid)
    return pot if pot is not None and pot.owner_id == user.id else None


def latest_measurement(db: Session, pot_id: uuid.UUID, stage: str | None = None) -> Measurement | None:
    q = select(Measurement).where(Measurement.pot_id == pot_id, Measurement.invalid.is_(False))
    if stage:
        q = q.where(Measurement.stage == stage)
    return db.scalars(q.order_by(Measurement.measured_at.desc()).limit(1)).first()


def reading_of(m: Measurement) -> dict:
    return {"ph": m.ph, "moisture": m.moisture, "temperature": m.temperature}


def measurement_dict(m: Measurement) -> dict:
    return {
        "measurement_id": str(m.id),
        "pot_id": str(m.pot_id) if m.pot_id else None,
        "device_id": m.device_id,
        "stage": m.stage,
        "ph": m.ph,
        "moisture": m.moisture,
        "temperature": m.temperature,
        "soil_score": m.soil_score,
        "measured_at": m.measured_at.isoformat(),
        "invalid": m.invalid,
        "is_demo": m.is_demo,
    }


def age_hours(m: Measurement) -> float:
    return round((utcnow() - m.measured_at).total_seconds() / 3600, 1)


def active_planting(db: Session, pot_id: uuid.UUID) -> Planting | None:
    q = (select(Planting).where(Planting.pot_id == pot_id, Planting.status.in_(("planned", "growing")))
         .order_by(Planting.created_at.desc()).limit(1))
    return db.scalars(q).first()


def last_candidates(db: Session, pot_id: uuid.UUID) -> set[str]:
    """Himpunan kandidat dari run_suitability terakhir untuk pot (dicatat di agent_logs)."""
    q = (select(AgentLog).where(AgentLog.pot_id == pot_id, AgentLog.tool == "run_suitability",
                                AgentLog.error.is_(None))
         .order_by(AgentLog.created_at.desc()).limit(1))
    log = db.scalars(q).first()
    # Hasil yang meminta ukur ulang tidak boleh dipakai untuk memilih tanaman (PRD 7.5 langkah 3).
    if not log or not isinstance(log.result, dict) or log.result.get("needs_remeasure"):
        return set()
    return {c["crop_id"] for c in log.result.get("candidates", [])}


def plan_dict(db: Session, plan: Plan) -> dict:
    cps = db.scalars(select(Checkpoint).where(Checkpoint.plan_id == plan.id)
                     .order_by(Checkpoint.due_at)).all()
    return {
        "plan_id": str(plan.id),
        "pot_id": str(plan.pot_id),
        "planting_id": str(plan.planting_id) if plan.planting_id else None,
        "kind": plan.kind,
        "crop_id": plan.crop_id,
        "goal": plan.goal,
        "steps": plan.steps,
        "targets": plan.targets,
        "state": plan.state,
        "replan_count": plan.replan_count,
        "version": plan.version,
        "generated_by": plan.generated_by,
        "checkpoints": [checkpoint_dict(c) for c in cps],
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }


def checkpoint_dict(c: Checkpoint) -> dict:
    return {
        "checkpoint_id": str(c.id),
        "plan_id": str(c.plan_id),
        "pot_id": str(c.pot_id),
        "param": c.param,
        "due_in_days": c.due_in_days,
        "due_at": c.due_at.isoformat(),
        "status": c.status,
        "plan_version": c.plan_version,
        "measurement_id": str(c.measurement_id) if c.measurement_id else None,
        "verify_result": c.verify_result,
    }
