from sqlalchemy import select

from app.agent import llm as llm_module
from app.agent import tanibot
from app.agent.grounding import ungrounded_numbers
from app.db.models import AgentLog, ChatMessage, Measurement
from app.notify import fcm
from tests.conftest import FakeLLM, tool_call


def add_reading(db, pot, ph=6.2):
    db.add(Measurement(device_id="dev-1", pot_id=pot.id, stage="control", ph=ph, moisture=50.0, temperature=25.0))
    db.commit()


def test_react_loop_calls_tools_and_answers_with_grounded_numbers(db, owner):
    user, _, pot = owner
    add_reading(db, pot)
    fake = FakeLLM([
        {"tool_calls": [tool_call("get_latest_reading", {"pot_id": str(pot.id)})]},
        {"content": "pH pot kamu 6.2 dan kelembapan 50%, masih aman untuk Alpha."},
    ])
    llm_module.set_llm(fake)
    out = tanibot.chat(db, user, "Gimana kondisi pot saya?", pot)
    assert out["narrative"] == out["reply"] and "6.2" in out["reply"]
    assert out["tool_trace"] == [{"tool": "get_latest_reading", "args": {"pot_id": str(pot.id)}, "ok": True}]
    assert fake.calls[0]["tools"] is not None
    assert "send_reminder" not in {t["function"]["name"] for t in fake.calls[0]["tools"]}
    roles = [m.role for m in db.scalars(select(ChatMessage).order_by(ChatMessage.created_at))]
    assert roles == ["user", "assistant"]


def test_invented_numbers_trigger_correction_then_fallback(db, owner):
    user, _, pot = owner
    add_reading(db, pot)
    llm_module.set_llm(FakeLLM([
        {"content": "Tambahkan kapur 37 gram."},
        {"content": "Tambahkan kapur 42 gram."},
    ]))
    out = tanibot.chat(db, user, "Berapa dosis kapur?", pot)
    assert out["narrative"] is None
    assert "37" not in out["reply"] and "42" not in out["reply"]
    errors = db.scalars(select(AgentLog).where(AgentLog.error == "ungrounded_numbers")).all()
    assert len(errors) == 2


def test_correction_succeeds_on_retry(db, owner):
    user, _, pot = owner
    llm_module.set_llm(FakeLLM([{"content": "Pakai 37 gram."}, {"content": "Ukur dulu dengan Potani ya."}]))
    out = tanibot.chat(db, user, "Berapa dosis kapur?", pot)
    assert out["reply"] == "Ukur dulu dengan Potani ya."


def test_write_tools_only_when_explicitly_requested(db, owner):
    user, _, pot = owner
    fake = FakeLLM([
        {"tool_calls": [tool_call("send_reminder", {"title": "Siram", "body": "Siram pot"})]},
        {"content": "Baik."},
    ])
    llm_module.set_llm(fake)
    out = tanibot.chat(db, user, "Halo", pot)
    assert out["tool_trace"][0]["ok"] is False and fcm.OUTBOX == []

    fake = FakeLLM([
        {"tool_calls": [tool_call("send_reminder", {"title": "Siram", "body": "Siram pot"})]},
        {"content": "Pengingat sudah dikirim."},
    ])
    llm_module.set_llm(fake)
    out = tanibot.chat(db, user, "Tolong ingatkan saya menyiram", pot)
    assert out["tool_trace"][0]["ok"] is True and fcm.OUTBOX[-1]["title"] == "Siram"


def test_cannot_read_other_users_pot(db, owner):
    user, _, _ = owner
    from app.db.models import Pot, User

    other = User(firebase_uid="bob", materials_owned=[])
    db.add(other)
    db.flush()
    pot_b = Pot(owner_id=other.id, name="Pot Bob")
    db.add(pot_b)
    db.commit()
    llm_module.set_llm(FakeLLM([
        {"tool_calls": [tool_call("get_latest_reading", {"pot_id": str(pot_b.id)})]},
        {"content": "Data pot tidak ditemukan."},
    ]))
    out = tanibot.chat(db, user, "Lihat pot bob", None)
    assert out["tool_trace"][0]["ok"] is False


def test_max_iterations_forces_answer(db, owner):
    user, _, pot = owner
    loop = [{"tool_calls": [tool_call("get_weather", {})]}] * 5 + [{"content": "Cuaca belum tersedia."}]
    fake = FakeLLM(loop)
    llm_module.set_llm(fake)
    out = tanibot.chat(db, user, "Cuaca?", pot)
    assert len(out["tool_trace"]) == 5
    assert fake.calls[-1]["tools"] is None and out["reply"] == "Cuaca belum tersedia."


def test_ask_user_returns_question(db, owner):
    user, _, pot = owner
    llm_module.set_llm(FakeLLM([{"tool_calls": [tool_call("ask_user", {
        "question": "Daunnya menguning di bagian mana?", "options": ["daun tua", "daun muda"]})]}]))
    out = tanibot.chat(db, user, "Daun cabai saya kuning", pot)
    assert out["pending_question_id"] and "daun tua" in out["reply"]


def test_history_limited_to_last_10_messages(db, owner):
    user, _, pot = owner
    for i in range(14):
        db.add(ChatMessage(user_id=user.id, pot_id=pot.id, role="user", content=f"pesan {i}"))
    db.commit()
    fake = FakeLLM([{"content": "Oke."}])
    llm_module.set_llm(fake)
    tanibot.chat(db, user, "baru", pot)
    sent = fake.calls[0]["messages"]
    assert len(sent) == 1 + 10 + 1  # system + 10 riwayat + pesan baru


def test_tool_schemas_allow_null_for_optional_fields():
    from app.agent.tools import TOOLS

    params = TOOLS["ask_user"][1]["function"]["parameters"]
    assert params["properties"]["question"]["type"] == "string"           # wajib: tetap ketat
    assert params["properties"]["options"]["type"] == ["array", "null"]   # opsional: boleh null
    light = TOOLS["run_suitability"][1]["function"]["parameters"]["properties"]["light_level"]
    assert None in light["enum"]


def test_tools_accept_null_optional_args(db, owner):
    user, _, pot = owner
    add_reading(db, pot)
    llm_module.set_llm(FakeLLM([
        {"tool_calls": [tool_call("get_pot_history", {"pot_id": str(pot.id), "limit": None}),
                        tool_call("get_remediation", {"param": "ph", "condition": "low", "materials_owned": None})]},
        {"content": "Ukur ulang ya."},
    ]))
    out = tanibot.chat(db, user, "Riwayat pot?", pot)
    assert [t["ok"] for t in out["tool_trace"]] == [True, True]


def test_grounding_helper():
    assert ungrounded_numbers("pH 6,2 dan skor 82%", [{"ph": 6.2, "score": 0.82}]) == []
    assert ungrounded_numbers("1. Ukur ulang\n2. Siram", []) == []
    assert ungrounded_numbers("butuh 15 gram", [{"dose": 10}]) == ["15"]
