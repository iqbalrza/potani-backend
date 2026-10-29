"""Tipe kolom portabel: JSONB/ARRAY di PostgreSQL, JSON di SQLite (untuk test)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeDecorator

JSONType = JSON().with_variant(JSONB(), "postgresql")
StrArray = JSON().with_variant(ARRAY(Text()), "postgresql")


class UTCDateTime(TypeDecorator):
    """Datetime yang selalu timezone-aware (UTC), juga di SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
