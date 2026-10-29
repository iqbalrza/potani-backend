"""Rencana, checkpoint, dan jawaban ask_user (F6)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent import loop_f6
from app.agent.workflows.soil_first import recommend
from app.api.deps import DB, CurrentUser, require_pot
from app.config import get_settings
from app.db.models import AgentQuestion, Checkpoint, Plan, Pot
from app.errors import ApiError
from app.repo import checkpoint_dict, plan_dict

router = APIRouter(tags=["plans"])


class AnswerIn(BaseModel):
    question_id: uuid.UUID
    answer: str = Field(min_length=1, max_length=200)


def _plan(db, user, plan_id) -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise ApiError(404, "plan_not_found", "Rencana tidak ditemukan.")
    require_pot(db, user, plan.pot_id)
    return plan


def _plan_out(db, plan: Plan) -> dict:
    out = plan_dict(db, plan)
    q = db.scalars(select(AgentQuestion).where(AgentQuestion.plan_id == plan.id, AgentQuestion.status == "pending")
                   .order_by(AgentQuestion.created_at.desc())).first()
    out["pending_question"] = ({"question_id": str(q.id), "question": q.question, "options": q.options}
                               if q else None)
    out["demo_mode"] = get_settings().demo_mode
    return out


@router.get("/plans")
def list_plans(user: CurrentUser, db: DB, pot_id: uuid.UUID | None = None):
    q = select(Plan).where(Plan.pot_id.in_(select(Pot.id).where(Pot.owner_id == user.id)))
    if pot_id:
        q = q.where(Plan.pot_id == require_pot(db, user, pot_id).id)
    return {"plans": [plan_dict(db, p) for p in db.scalars(q.order_by(Plan.created_at.desc()).limit(50))]}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: uuid.UUID, user: CurrentUser, db: DB):
    return _plan_out(db, _plan(db, user, plan_id))


@router.post("/plans/{plan_id}/done")
def plan_done(plan_id: uuid.UUID, user: CurrentUser, db: DB):
    plan = _plan(db, user, plan_id)
    try:
        loop_f6.mark_done(db, plan)
    except loop_f6.InvalidTransition:
        raise ApiError(409, "invalid_state", "Rencana ini tidak sedang menunggu dikerjakan.")
    db.commit()
    return _plan_out(db, plan)


@router.get("/checkpoints")
def list_checkpoints(user: CurrentUser, db: DB,
                     status: Literal["scheduled", "reminded", "measured", "verified", "missed", "cancelled"] | None = None,
                     pot_id: uuid.UUID | None = None):
    q = select(Checkpoint).where(Checkpoint.pot_id.in_(select(Pot.id).where(Pot.owner_id == user.id)))
    if status:
        q = q.where(Checkpoint.status == status)
    if pot_id:
        q = q.where(Checkpoint.pot_id == pot_id)
    return {"checkpoints": [checkpoint_dict(c) for c in db.scalars(q.order_by(Checkpoint.due_at).limit(200))]}


@router.post("/agent/answers")
def post_answer(body: AnswerIn, user: CurrentUser, db: DB):
    q = db.get(AgentQuestion, body.question_id)
    if q is None or q.user_id != user.id:
        raise ApiError(404, "question_not_found", "Pertanyaan tidak ditemukan.")
    if q.status != "pending":
        raise ApiError(409, "already_answered", "Pertanyaan ini sudah dijawab.")
    if q.options and body.answer not in q.options:
        raise ApiError(422, "invalid_answer", "Pilih salah satu jawaban yang tersedia.")
    q.answer, q.status = body.answer, "answered"

    if q.feature == "F2" and q.field and q.pot_id:
        pot = require_pot(db, user, q.pot_id)
        value = (q.context or {}).get("value_map", {}).get(body.answer, body.answer)
        setattr(pot, q.field, value)
        db.flush()
        result = recommend(db, user, pot)
        db.commit()
        return {"status": "answered", "feature": "F2", "result": result}

    if q.feature == "F6" and q.plan_id:
        plan = _plan(db, user, q.plan_id)
        if plan.state != "WAITING_USER":
            db.commit()
            return {"status": "answered", "feature": "F6", "plan": _plan_out(db, plan)}
        loop_f6.answer_question(db, q, body.answer)
        db.commit()
        return {"status": "answered", "feature": "F6", "plan": _plan_out(db, plan)}

    db.commit()
    return {"status": "answered", "feature": q.feature}
