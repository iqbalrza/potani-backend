"""GroqClient: retry, retry-after pada 429, tool_use_failed, dan kegagalan non-retry (tanpa jaringan)."""
from types import SimpleNamespace

import httpx
import pytest
from groq import BadRequestError, NotFoundError, RateLimitError

from app.agent import llm as llm_module
from app.config import get_settings


def _err(cls, status, code, headers=None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    body = {"error": {"code": code, "message": code}}
    return cls(code, response=httpx.Response(status, headers=headers or {}, request=req, json=body), body=body)


def _ok(content="halo"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def client(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_module.time, "sleep", sleeps.append)
    c = llm_module.GroqClient.__new__(llm_module.GroqClient)
    c.model, c.last_ok, c.last_error = "openai/gpt-oss-20b", None, None
    c.sleeps = sleeps

    def install(*outcomes):
        seq = list(outcomes)
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        c._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return calls

    c.install = install
    return c


def test_429_waits_retry_after_then_succeeds(client):
    client.install(_err(RateLimitError, 429, "rate_limit_exceeded", {"retry-after": "3"}), _ok())
    assert client.chat([{"role": "user", "content": "x"}])["content"] == "halo"
    assert client.sleeps == [3.0]


def test_429_gives_up_when_retry_after_exceeds_budget(client):
    calls = client.install(_err(RateLimitError, 429, "rate_limit_exceeded", {"retry-after": "45"}), _ok())
    assert client.chat([{"role": "user", "content": "x"}]) is None
    assert client.sleeps == [] and len(calls) == 1
    assert client.last_error == "429:rate_limit_exceeded"
    assert llm_module.error_tag(client) == "llm_unavailable:429:rate_limit_exceeded"


def test_tool_use_failed_is_retried(client):
    calls = client.install(_err(BadRequestError, 400, "tool_use_failed"), _ok("oke"))
    assert client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])["content"] == "oke"
    assert len(calls) == 2


def test_model_not_found_is_not_retried(client):
    calls = client.install(_err(NotFoundError, 404, "model_not_found"), _ok())
    assert client.chat([{"role": "user", "content": "x"}]) is None
    assert len(calls) == 1 and client.last_error == "404:model_not_found"


def test_reasoning_effort_only_for_gpt_oss(client):
    calls = client.install(_ok(), _ok())
    client.chat([{"role": "user", "content": "x"}])
    assert calls[0]["reasoning_effort"] == get_settings().llm_reasoning_effort
    client.model = "qwen/qwen3.8-27b"
    client.chat([{"role": "user", "content": "x"}])
    assert "reasoning_effort" not in calls[1]


def test_compact_json_drops_nulls():
    assert llm_module.compact_json({"a": 1, "b": None, "c": [{"d": None, "e": 2}]}) == '{"a":1,"c":[{"e":2}]}'
