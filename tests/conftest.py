"""Fixture bersama. Test berjalan di SQLite in-memory dengan auth mode dev dan LLM palsu."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
os.environ.update({
    "APP_ENV": "test",
    "AUTH_MODE": "dev",
    "DATABASE_URL": "sqlite://",
    "CROPS_FILE": str(FIXTURES / "crops.test.json"),
    "REMEDIATION_FILE": str(FIXTURES / "remediation_rules.test.json"),
    "GROQ_API_KEY": "",
    "FCM_ENABLED": "false",
    "TIME_ACCELERATION": "1",
    "LOG_LEVEL": "WARNING",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agent import llm as llm_module  # noqa: E402
from app.db.models import Base, Device, Pot, User  # noqa: E402
from app.db.session import SessionLocal, make_engine, set_engine  # noqa: E402
from app.notify import fcm  # noqa: E402


class FakeLLM:
    """LLM palsu: `script` berisi daftar respons (dict) atau callable(messages, tools) → dict/None."""

    model = "fake-llm"
    configured = True

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.last_ok = True

    def chat(self, messages, tools=None, tool_choice=None, json_mode=False):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "json_mode": json_mode})
        if not self.script:
            return None
        item = self.script.pop(0)
        if callable(item):
            item = item(messages, tools)
        if item is None:
            return None
        return llm_module.LLMResponse(content=item.get("content"), tool_calls=item.get("tool_calls", []),
                                      model=self.model)


def tool_call(name: str, args: dict, call_id: str | None = None) -> dict:
    import json

    return {"id": call_id or f"call_{uuid.uuid4().hex[:8]}", "name": name, "arguments": json.dumps(args)}


class DownLLM(FakeLLM):
    """Mensimulasikan LLM gagal (mis. LLM_MODEL salah): selalu None."""

    def chat(self, *a, **kw):
        self.last_ok = False
        return None


@pytest.fixture(autouse=True)
def _fresh_db():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    set_engine(engine)
    llm_module.set_llm(DownLLM())
    fcm.OUTBOX.clear()
    yield
    engine.dispose()


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def auth(uid: str = "alice") -> dict:
    return {"Authorization": f"Bearer dev:{uid}"}


@pytest.fixture
def owner(db):
    """Pengguna + perangkat + pot dengan cahaya penuh."""
    user = User(firebase_uid="alice", name="Alice", materials_owned=[])
    db.add(user)
    db.flush()
    dev = Device(device_id="dev-1", owner_id=user.id)
    pot = Pot(owner_id=user.id, name="Pot A", light_level="full", container_type="pot_sedang")
    db.add_all([dev, pot])
    db.commit()
    return user, dev, pot


def data_payload(**overrides) -> dict:
    p = {"device_id": "dev-1", "soil_adc": 2100, "moisture": 50.0, "status": "ok", "ph_adc": 1800,
         "ph_voltage": 1.45, "ph": 6.0, "temperature": 25.0, "soil_score": 80}
    p.update(overrides)
    return p
