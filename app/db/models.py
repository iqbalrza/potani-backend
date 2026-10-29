"""Model SQLAlchemy (PRD bagian 4)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import JSONType, StrArray, UTCDateTime, utcnow

STAGES = ("pre_plant", "post_remediation", "control", "symptom", "end")
LIGHT_LEVELS = ("full", "partial", "shade")
PLANTING_STATUSES = ("planned", "growing", "harvested", "failed", "abandoned")
CHECKPOINT_STATUSES = ("scheduled", "reminded", "measured", "verified", "missed")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    location_lat: Mapped[float | None] = mapped_column(Float)
    location_lon: Mapped[float | None] = mapped_column(Float)
    light_default: Mapped[str | None] = mapped_column(String(16))
    container_default: Mapped[str | None] = mapped_column(String(64))
    materials_owned: Mapped[list[str]] = mapped_column(StrArray, default=list)
    fcm_token: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(TimestampMixin, Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    fw_version: Mapped[str | None] = mapped_column(String(32))
    battery: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    online: Mapped[bool] = mapped_column(Boolean, default=False)


class Pot(TimestampMixin, Base):
    __tablename__ = "pots"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    container_type: Mapped[str | None] = mapped_column(String(64))
    light_level: Mapped[str | None] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_help: Mapped[bool] = mapped_column(Boolean, default=False)
    help_reason: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Planting(TimestampMixin, Base):
    __tablename__ = "plantings"

    pot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pots.id"), index=True)
    crop_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))  # recommender / user_choice
    planted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="planned")
    outcome_note: Mapped[str | None] = mapped_column(Text)


class Session(TimestampMixin, Base):
    __tablename__ = "sessions"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"), index=True)
    pot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pots.id"))
    stage: Mapped[str] = mapped_column(String(24))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Tanpa FK untuk menghindari referensi melingkar dengan measurements.session_id
    measurement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Measurement(TimestampMixin, Base):
    __tablename__ = "measurements"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"), index=True)
    pot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pots.id"), index=True)
    planting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plantings.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"))
    stage: Mapped[str | None] = mapped_column(String(24))
    ph: Mapped[float | None] = mapped_column(Float)
    moisture: Mapped[float | None] = mapped_column(Float)
    temperature: Mapped[float | None] = mapped_column(Float)
    soil_score: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict)
    measured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    client_measurement_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    invalid: Mapped[bool] = mapped_column(Boolean, default=False)
    invalid_reason: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"

    pot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pots.id"), index=True)
    planting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plantings.id"))
    kind: Mapped[str] = mapped_column(String(16))  # soil_first / remediation
    crop_id: Mapped[str | None] = mapped_column(String(64))
    goal: Mapped[str] = mapped_column(Text)
    steps: Mapped[list] = mapped_column(JSONType, default=list)
    targets: Mapped[list] = mapped_column(JSONType, default=list)
    state: Mapped[str] = mapped_column(String(24), default="PLANNED", index=True)
    replan_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list] = mapped_column(JSONType, default=list)
    generated_by: Mapped[str] = mapped_column(String(16), default="template")  # llm / template


class Checkpoint(TimestampMixin, Base):
    __tablename__ = "checkpoints"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), index=True)
    pot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pots.id"), index=True)
    param: Mapped[str] = mapped_column(String(16))
    due_in_days: Mapped[float] = mapped_column(Float)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    measurement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("measurements.id"))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    verify_result: Mapped[dict | None] = mapped_column(JSONType)


class AgentLog(TimestampMixin, Base):
    __tablename__ = "agent_logs"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    feature: Mapped[str] = mapped_column(String(4))  # F2 / F3 / F6 / F7
    step: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | list | None] = mapped_column(JSONType)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_model: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    pot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)


class ChatMessage(TimestampMixin, Base):
    __tablename__ = "chat_messages"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    pot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pots.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSONType)


class AgentQuestion(TimestampMixin, Base):
    """Pertanyaan `ask_user` yang menunggu jawaban (asinkron, PRD 7.6)."""

    __tablename__ = "agent_questions"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    feature: Mapped[str] = mapped_column(String(4))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    pot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pots.id"))
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSONType, default=list)
    field: Mapped[str | None] = mapped_column(String(32))  # mis. "light_level" untuk F2
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
