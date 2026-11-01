#!/usr/bin/env python3
"""Проверка MCP-сервиса study: запись попытки -> прогресс -> повторение.

Вызывает инструменты study по Streamable HTTP (как в e2e_loop.py) и проверяет
round-trip: карточка сохраняется, интервал повторения считается, статистика растёт.

    python scripts\\study_check.py
"""

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env, mcp_call  # noqa: E402

STUDY_MCP_URL = os.environ.get("STUDY_MCP_URL", "http://127.0.0.1:8088/mcp")
SELFTEST_TOPIC = "__selftest__"


def call(tool, args):
    return mcp_call(STUDY_MCP_URL, "", tool, args)


def main():
    load_env()  # проверяем, что .env читается (сервису study секреты не нужны)

    print("study_record ...")
    question = "selftest-" + uuid.uuid4().hex[:8]
    rec = json.loads(
        call(
            "study_record",
            {
                "topic": SELFTEST_TOPIC,
                "question": question,
                "user_answer": "ok",
                "grade": "correct",
                "expected": "ok",
                "source": "self-check",
            },
        )
    )
    rec_ok = rec.get("ok") is True and rec.get("grade") == "correct" and rec.get("interval_days") == 7
    print("  ->", json.dumps(rec, ensure_ascii=False)[:240])

    prog = json.loads(call("study_progress", {"topic": SELFTEST_TOPIC}))
    prog_ok = prog.get("total_attempts", 0) >= 1 and prog.get("total_cards", 0) >= 1
    print(" progress:", json.dumps(prog, ensure_ascii=False)[:240])

    topics = json.loads(call("study_topics", {}))
    sections = topics.get("sections") or []
    topics_ok = len(sections) >= 8
    print(f" topics: sections={len(sections)}")

    plan = json.loads(call("study_plan", {"limit": 3}))
    plan_ok = isinstance(plan.get("plan"), list) and len(plan["plan"]) > 0
    print(f" plan: {len(plan.get('plan') or [])}")

    due = json.loads(call("study_due", {"limit": 5}))
    due_ok = isinstance(due.get("due"), list)
    print(f" due: count={due.get('count')}")

    ok = rec_ok and prog_ok and topics_ok and plan_ok and due_ok
    print("STUDY_OK=" + str(ok))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
