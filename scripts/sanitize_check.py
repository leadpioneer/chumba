#!/usr/bin/env python3
"""Проверка принудительной маскировки: маркер из task не должен уйти в облако.

Кладёт в задачу имя и приватный маркер, вызывает delegate_to_worker и проверяет
по outbound-логу: исходник сохранён локально, а в payload к воркеру маркера нет.
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env, mcp_call  # noqa: E402


def main():
    cfg = load_env()
    marker = "LEAK_MARKER_" + uuid.uuid4().hex[:8]
    task = (
        f"Меня зовут Иван Петров, мой приватный токен {marker}. "
        "Найди и кратко изложи, что такое llama.cpp."
    )
    print("MARKER=" + marker)

    text = mcp_call(
        "http://127.0.0.1:8086/mcp",
        cfg.get("DELEGATE_MCP_TOKEN", ""),
        "delegate_to_worker",
        {"task": task},
    )
    parsed = json.loads(text)
    print("delegate_status:", "error" if "error" in parsed else "ok")

    files = sorted(Path("logs/outbound").glob("worker-*.jsonl"), key=lambda p: p.stat().st_mtime)
    rec = json.loads(files[-1].read_text(encoding="utf-8").splitlines()[-1])

    original = rec.get("task_original") or ""
    sanitized = rec.get("task_sanitized") or ""
    outbound = rec["outbound_messages"][1]["content"] if rec.get("outbound_messages") else ""

    print("task_original:  ", original[:160])
    print("task_sanitized:", sanitized[:160])
    print("sanitize info:  ", json.dumps(rec.get("sanitize"), ensure_ascii=False))
    print("marker_in_task_original:   ", marker in original)
    print("marker_in_task_sanitized:  ", marker in sanitized)
    print("marker_in_outbound_payload:", marker in outbound)

    ok = (marker in original) and (marker not in sanitized) and (marker not in outbound)
    print("SANITIZE_OK=" + str(ok))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
