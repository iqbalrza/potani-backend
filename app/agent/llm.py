"""Klien Groq dengan timeout dan retry (PRD 7.1). Gagal → None; pemanggil wajib punya fallback."""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class LLMResponse(dict):
    """{"content": str | None, "tool_calls": [{"id", "name", "arguments"}], "model": str}"""


class LLMClient(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str | None = None, json_mode: bool = False) -> LLMResponse | None: ...


class GroqClient:
    def __init__(self) -> None:
        s = get_settings()
        self.model = s.llm_model
        self._client = None
        self.last_ok: bool | None = None
        self.last_error: str | None = None
        if s.groq_api_key:
            from groq import Groq

            self._client = Groq(api_key=s.groq_api_key, timeout=s.llm_timeout_s, max_retries=0)

    @property
    def configured(self) -> bool:
        return self._client is not None

    def chat(self, messages, tools=None, tool_choice=None, json_mode=False):
        if self._client is None:
            return None
        s = get_settings()
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": s.llm_temperature}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if s.llm_reasoning_effort and self.model.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = s.llm_reasoning_effort  # token penalaran ikut dihitung kuota TPM

        from groq import APIConnectionError, APIStatusError, APITimeoutError

        waited = 0.0
        for attempt in range(s.llm_max_retries + 1):
            delay = min(4.0, 0.5 * 2**attempt) + random.random() * 0.2
            try:
                resp = self._client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                self.last_ok = True
                return LLMResponse(
                    content=msg.content,
                    tool_calls=[{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                                for tc in (msg.tool_calls or [])],
                    model=self.model,
                )
            except APIStatusError as e:
                code = ((e.body or {}).get("error") or {}).get("code") if isinstance(e.body, dict) else None
                self.last_error = f"{e.status_code}:{code or 'error'}"
                # tool_use_failed: argumen tool hasil model tidak lolos validasi skema Groq; generasi ulang bisa lolos.
                retryable = e.status_code == 429 or e.status_code >= 500 or code == "tool_use_failed"
                log.warning("llm_error", extra={"status": e.status_code, "code": code, "attempt": attempt})
                if not retryable or attempt == s.llm_max_retries:
                    break
                if e.status_code == 429:
                    # Kuota TPM pulih per menit: ikuti retry-after bila masih dalam batas tunggu, selain itu menyerah.
                    try:
                        delay = float(e.response.headers.get("retry-after", delay))
                    except (TypeError, ValueError):
                        pass
                    if waited + delay > s.llm_rate_limit_wait_s:
                        break
            except (APIConnectionError, APITimeoutError):
                self.last_error = "connection"
                log.warning("llm_connection_error", extra={"attempt": attempt})
                if attempt == s.llm_max_retries:
                    break
            except Exception:  # noqa: BLE001 - LLM tidak boleh menjatuhkan endpoint
                self.last_error = "unexpected"
                log.exception("llm_unexpected_error")
                break
            time.sleep(delay)
            waited += delay
        self.last_ok = False
        return None


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = GroqClient()
    return _client


def set_llm(client: LLMClient | None) -> None:
    """Ganti klien LLM (dipakai test)."""
    global _client
    _client = client


def compact_json(obj: Any) -> str:
    """Serialisasi hasil tool untuk LLM tanpa nilai null dan spasi (hemat token)."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if v is not None}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x

    return json.dumps(strip(obj), ensure_ascii=False, separators=(",", ":"), default=str)


def error_tag(llm: Any) -> str:
    """Kode error untuk agent_logs, mis. 'llm_unavailable:429:rate_limit_exceeded'."""
    detail = getattr(llm, "last_error", None)
    return f"llm_unavailable:{detail}" if detail else "llm_unavailable"


def assistant_message(resp: LLMResponse) -> dict:
    """Ubah respons menjadi pesan assistant format OpenAI/Groq untuk riwayat percakapan."""
    msg: dict[str, Any] = {"role": "assistant", "content": resp.get("content") or ""}
    if resp.get("tool_calls"):
        msg["tool_calls"] = [
            {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in resp["tool_calls"]
        ]
    return msg
