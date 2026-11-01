#!/usr/bin/env python3
"""Reproduce a real Open WebUI tool loop through the streaming UI path."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402
from run_short_stage import (  # noqa: E402
    Api,
    RESULTS,
    chat_messages,
    ensure_auth,
    extract_answer,
    get_chat,
    import_chat,
    send_turn,
    setup_mode,
    trace_offset,
    trace_since,
)

PROBE_LOG_CONTAINER = "/app/backend/data/exp_request_probe.jsonl"
PROBE_FILE = Path(__file__).resolve().parent / "exp_request_probe_filter.py"
TOOL_FILE = Path(__file__).resolve().parent / "exp_probe_tool.py"

PROMPTS = {
    "basic": (
        "Вызови инструмент probe ровно один раз с marker='A'. "
        "После результата ответь только словом DONE."
    ),
    "multi": (
        "Вызови инструмент probe дважды: один раз с marker='A', второй раз с marker='B'. "
        "Если можно, сделай два вызова в одном ответе. После результатов ответь DONE."
    ),
    "error": (
        "Сначала вызови probe_fail с marker='X'. Если инструмент вернул ошибку, "
        "вызови probe с marker='FIXED' и затем ответь DONE."
    ),
    "overflow": (
        "Вызови инструмент huge_probe ровно один раз с marker='huge'. "
        "После результата ответь DONE."
    ),
}


def upsert_function(api: Api, function_id: str, name: str, content: str) -> None:
    form = {
        "id": function_id,
        "name": name,
        "type": "filter",
        "content": content,
        "meta": {"description": "isolated experiment probe", "manifest": {"title": name, "version": "0.1.0"}},
    }
    status, _ = api.get(f"/api/v1/functions/id/{function_id}")
    if status == 200:
        status, response = api.post(f"/api/v1/functions/id/{function_id}/update", form)
    else:
        status, response = api.post("/api/v1/functions/create", form)
    if status != 200:
        raise RuntimeError(f"function install failed: {status} {response}")
    function = api.get(f"/api/v1/functions/id/{function_id}")[1] or {}
    if not function.get("is_active"):
        status, response = api.post(f"/api/v1/functions/id/{function_id}/toggle", {})
        if status != 200:
            raise RuntimeError(f"function activate failed: {status} {response}")
    function = api.get(f"/api/v1/functions/id/{function_id}")[1] or {}
    if not function.get("is_global"):
        status, response = api.post(f"/api/v1/functions/id/{function_id}/toggle/global", {})
        if status != 200:
            raise RuntimeError(f"function global failed: {status} {response}")


def upsert_tool(api: Api, tool_id: str, name: str, content: str) -> None:
    form = {"id": tool_id, "name": name, "content": content, "meta": {"description": "isolated experiment tool"}}
    status, _ = api.get(f"/api/v1/tools/id/{tool_id}")
    if status == 200:
        status, response = api.post(f"/api/v1/tools/id/{tool_id}/update", form)
    else:
        status, response = api.post("/api/v1/tools/create", form)
    if status != 200:
        raise RuntimeError(f"tool install failed: {status} {response}")


def docker_exec(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "chumba-exp-owui", *command], capture_output=True, text=True, timeout=60)


def output_items(message_map: dict) -> list[dict]:
    items = []
    for message in message_map.values():
        if isinstance(message.get("output"), list):
            items.extend(message["output"])
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(PROMPTS), default="basic")
    parser.add_argument("--history-chars", type=int, default=30000)
    args = parser.parse_args()

    api = Api("http://127.0.0.1:3001", "http://127.0.0.1:8091")
    ensure_auth(api)
    setup_mode(api, "B", "http://chumba-exp-trace:8080/v1", 21504)
    upsert_function(api, "exp_request_probe", "Experiment Request Probe", PROBE_FILE.read_text(encoding="utf-8"))
    upsert_tool(api, "exp_probe_tool", "Experiment Probe Tool", TOOL_FILE.read_text(encoding="utf-8"))
    docker_exec(["sh", "-c", f": > {PROBE_LOG_CONTAINER}"])

    prefix = f"tool-{args.scenario}-" + uuid.uuid4().hex[:8]
    messages = fixtures.chain(fixtures.build_history("long", args.history_chars), prefix)
    chat_id = import_chat(api, messages, f"tool loop {args.scenario}")
    last_id = messages[-1]["id"]
    question = PROMPTS[args.scenario]
    offset = trace_offset()
    started = time.perf_counter()
    status, response, _ = send_turn(
        api,
        chat_id,
        last_id,
        question,
        max_tokens=128,
        tool_ids=["exp_probe_tool"],
        stream=True,
    )
    wall = time.perf_counter() - started
    records = trace_since(offset)
    chat = get_chat(api, chat_id)
    message_map = chat_messages(chat)
    items = output_items(message_map)
    calls = [item for item in items if item.get("type") == "function_call"]
    results = [item for item in items if item.get("type") == "function_call_output"]
    final_answer = ""
    for message in message_map.values():
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            if message.get("content").strip():
                final_answer = message["content"].strip()

    probe_local = RESULTS / "probe" / f"{prefix}.jsonl"
    probe_local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["docker", "cp", f"chumba-exp-owui:{PROBE_LOG_CONTAINER}", str(probe_local)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    probe_records = []
    if probe_local.exists():
        for line in probe_local.read_text(encoding="utf-8").splitlines():
            try:
                probe_records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    chat_records = [record for record in records if record.get("kind") == "chat"]
    result = {
        "time": datetime.now(timezone.utc).isoformat(),
        "scenario": args.scenario,
        "chat_id": chat_id,
        "status": status,
        "wall_s": round(wall, 2),
        "answer": final_answer or extract_answer(response),
        "function_calls": [
            {
                "name": item.get("name"),
                "arguments": item.get("arguments"),
                "status": item.get("status"),
                "call_id": item.get("call_id") or item.get("id"),
            }
            for item in calls
        ],
        "function_results": [
            {
                "name": item.get("name"),
                "call_id": item.get("call_id"),
                "status": item.get("status"),
                "content_chars": len(str(item.get("output") or item.get("content") or "")),
                "content_head": str(item.get("output") or item.get("content") or "")[:160],
            }
            for item in results
        ],
        "probe_records": probe_records,
        "trace_chat_requests": [
            {
                "prompt_tokens": (((record.get("response") or {}).get("usage") or {}).get("prompt_tokens")),
                "message_count": (record.get("message_meta") or {}).get("message_count"),
                "roles": (record.get("message_meta") or {}).get("roles"),
                "content_chars": (record.get("message_meta") or {}).get("content_chars_total"),
                "tool_schema_count": record.get("tool_schema_count"),
                "tool_call_ids": (record.get("message_meta") or {}).get("tool_call_ids"),
                "tool_result_ids": (record.get("message_meta") or {}).get("tool_result_ids"),
                "orphan_tool_results": (record.get("message_meta") or {}).get("orphan_tool_results"),
                "status": (record.get("response") or {}).get("status"),
                "error": (record.get("response") or {}).get("error"),
            }
            for record in chat_records
        ],
        "checks": {
            "backend_requests": len(chat_records),
            "executed_calls": len(calls),
            "tool_results": len(results),
            "request_hook_calls": len(probe_records),
            "request_hook_saw_tools": any(record.get("tool_schema_count") for record in probe_records),
            "request_hook_saw_tool_results": any(record.get("tool_result_ids") for record in probe_records),
            "second_request_after_result": len(chat_records) >= 2,
            "answer_nonempty": bool((final_answer or extract_answer(response)).strip()),
        },
    }
    output = RESULTS / ("tool-loop-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "tool-loop.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("results:", output / "tool-loop.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
