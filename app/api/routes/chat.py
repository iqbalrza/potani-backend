"""F7 TaniBot."""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent import tanibot
from app.api.deps import DB, CurrentUser, require_pot

router = APIRouter(tags=["chat"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    pot_id: uuid.UUID | None = None


@router.post("/chat")
def post_chat(body: ChatIn, user: CurrentUser, db: DB):
    pot = require_pot(db, user, body.pot_id) if body.pot_id else None
    result = tanibot.chat(db, user, body.message.strip(), pot)
    db.commit()
    return result
