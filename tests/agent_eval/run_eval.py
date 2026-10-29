"""RQ3: akurasi pemilihan tool dan argumen TaniBot per model Groq.

Butuh GROQ_API_KEY. Tool tidak dieksekusi; yang dinilai hanya pemanggilan tool pertama dari LLM.

    python tests/agent_eval/run_eval.py --models openai/gpt-oss-20b openai/gpt-oss-120b --delay 1.5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.tanibot import _EXPLICIT_WRITE, build_system_prompt  # noqa: E402
from app.agent.tools import F7_TOOLS, F7_WRITE_TOOLS, schemas  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Pot  # noqa: E402

SCENARIOS = Path(__file__).with_name("scenarios.jsonl")
DUMMY_POT = "00000000-0000-4000-8000-000000000001"
DUMMY_POT_ROW = Pot(id=uuid.UUID(DUMMY_POT), name="Pot Uji", light_level="full", container_type="pot_sedang")


def load() -> list[dict]:
    return [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]


def args_match(expected: dict, actual: dict) -> bool:
    for k, v in expected.items():
        v = DUMMY_POT if v == "{pot_id}" else v
        if actual.get(k) != v:
            return False
    return True


def evaluate_model(model: str, scenarios: list[dict], delay: float = 0.0) -> dict:
    from app.agent.llm import GroqClient

    get_settings().llm_model = model
    client = GroqClient()
    if not client.configured:
        raise SystemExit("GROQ_API_KEY belum diisi.")
    tool_ok = args_ok = failures = 0
    rows = []
    for sc in scenarios:
        allowed = set(F7_TOOLS) | (F7_WRITE_TOOLS if _EXPLICIT_WRITE.search(sc["input"]) else set())
        messages = [{"role": "system", "content": build_system_prompt(DUMMY_POT_ROW)},
                    {"role": "user", "content": sc["input"]}]
        start = time.perf_counter()
        resp = client.chat(messages, tools=schemas(allowed))
        latency = int((time.perf_counter() - start) * 1000)
        if delay:
            time.sleep(delay)  # jaga di bawah batas rate limit akun
        if resp is None:
            failures += 1
            rows.append({"id": sc["id"], "error": client.last_error or "llm_failed"})
            continue
        first = resp["tool_calls"][0] if resp["tool_calls"] else None
        name = first["name"] if first else None
        try:
            actual = json.loads(first["arguments"]) if first else {}
        except ValueError:
            actual = {}
        t_ok = name in sc["expected_tools"]
        a_ok = t_ok and args_match(sc["expected_args"], actual)
        tool_ok += t_ok
        args_ok += a_ok
        rows.append({"id": sc["id"], "tool": name, "tool_ok": t_ok, "args_ok": a_ok, "latency_ms": latency})
    n = len(scenarios)
    answered = n - failures
    return {"model": model, "n": n, "tool_accuracy": round(tool_ok / n, 4), "args_accuracy": round(args_ok / n, 4),
            "tool_accuracy_answered": round(tool_ok / answered, 4) if answered else None,
            "llm_failures": failures, "errors": dict(Counter(r["error"] for r in rows if "error" in r)), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=[get_settings().llm_model])
    ap.add_argument("--out", help="simpan hasil lengkap ke file JSON")
    ap.add_argument("--delay", type=float, default=0.0, help="jeda antar-panggilan (detik)")
    args = ap.parse_args()
    scenarios = load()
    results = [evaluate_model(m, scenarios, args.delay) for m in args.models]
    for r in results:
        print(f"{r['model']}: tool={r['tool_accuracy']:.2%} args={r['args_accuracy']:.2%} "
              f"gagal={r['llm_failures']} {r['errors'] or ''}")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
