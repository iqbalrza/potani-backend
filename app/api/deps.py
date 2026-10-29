"""Dependency bersama untuk route."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db.models import Pot, User
from app.db.session import get_db
from app.errors import ApiError
from app.repo import owned_pot

DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


def require_pot(db: Session, user: User, pot_id: Any) -> Pot:
    """Pengguna hanya bisa mengakses pot miliknya; pot orang lain diperlakukan sebagai tidak ada."""
    pot = owned_pot(db, user, pot_id)
    if pot is None:
        raise ApiError(404, "pot_not_found", "Pot tidak ditemukan.")
    return pot
