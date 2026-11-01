#!/usr/bin/env python3
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "context-memory"))
import fixtures
from run_short_stage import Api, ensure_auth, import_chat, send_turn, setup_mode

BASE = "http://127.0.0.1:3001"


def run_case(label, question, max_tokens=64):
    api = Api(BASE, "http://127.0.0.1:8091")
    ensure_auth(api)
    setup_mode(api, "B0", "http://chumba-exp-trace:8080/v1", 12000)
    messages = fixtures.chain(fixtures.build_history("short", 4000), "guard-" + uuid.uuid4().hex[:8])
    chat_id = import_chat(api, messages, f"guard {label}")
    started = time.perf_counter()
    status, response, _ = send_turn(
        api, chat_id, messages[-1]["id"], question, max_tokens=max_tokens, stream=True
    )
    elapsed = time.perf_counter() - started
    text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
    return {
        "case": label,
        "status": status,
        "wall_s": round(elapsed, 2),
        "response": text[:2000],
        "budget_error": "CONTEXT_BUDGET" in text or "budget" in text.lower(),
    }


def main():
    results = []
    results.append(run_case("normal", "Ответь одним словом: OK", max_tokens=64))
    results.append(run_case("oversized", " ".join(__import__("uuid").uuid4().hex for _ in range(7000)), max_tokens=64))
    output = ROOT / "benchmark-results" / "context-memory" / "guard-runtime.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
