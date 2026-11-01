#!/usr/bin/env python3
"""Branch isolation check for built-in context compaction."""

from __future__ import annotations

import json
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
    append_filler,
    chat_messages,
    collect_summaries,
    current_id,
    ensure_auth,
    get_chat,
    import_chat,
    last_assistant_id,
    setup_mode,
)


def append_marker(api: Api, chat_id: str, parent_id: str, marker: str) -> str:
    chat = get_chat(api, chat_id)
    existing = chat_messages(chat)
    user_id = "um-" + uuid.uuid4().hex[:10]
    assistant_id = "am-" + uuid.uuid4().hex[:10]
    user = fixtures.new_user_message(user_id, parent_id, f"{marker}: синтетическая ветка")
    assistant = {
        "id": assistant_id,
        "parentId": user_id,
        "childrenIds": [],
        "role": "assistant",
        "content": f"Ветка {marker} принята.",
        "model": "orchestrator",
        "done": True,
        "timestamp": int(time.time()),
    }
    if parent_id in existing:
        existing[parent_id]["childrenIds"] = [user_id]
    merged = dict(existing)
    merged[user_id] = user
    merged[assistant_id] = assistant
    status, response = api.post(
        f"/api/v1/chats/{chat_id}",
        {"chat": {"history": {"currentId": assistant_id, "messages": merged}}},
    )
    if status != 200:
        raise RuntimeError(f"append marker failed: {status} {response}")
    return assistant_id


def compact(api: Api, chat_id: str) -> dict:
    status, response = api.post(f"/api/v1/chats/{chat_id}/compact", {})
    if status != 200:
        raise RuntimeError(f"compact failed: {status} {response}")
    return response if isinstance(response, dict) else {"raw": response}


def summaries_from_chat(api: Api, chat_id: str) -> list[str]:
    chat = get_chat(api, chat_id)
    values = []
    for message in chat_messages(chat).values():
        value = message.get("contextSummary")
        if isinstance(value, str) and value.strip():
            values.append(value)
    return values


def main() -> int:
    api = Api("http://127.0.0.1:3001", "http://127.0.0.1:8091")
    ensure_auth(api)
    status, props = api.trace_get("/props?model=orchestrator")
    window = int(props["default_generation_settings"]["n_ctx"])
    setup_mode(api, "B0", "http://chumba-exp-trace:8080/v1", 12000)
    print("window", window, "mode B0")

    prefix = "branch-" + uuid.uuid4().hex[:8]
    messages = fixtures.chain(fixtures.build_history("branch", 60000), prefix)
    chat_id = import_chat(api, messages, "branch check")
    last_id = last_assistant_id(chat_messages(get_chat(api, chat_id)), current_id(get_chat(api, chat_id)))
    # First checkpoint before forking.
    first = compact(api, chat_id)
    base_summaries = summaries_from_chat(api, chat_id)
    print("base compact:", json.dumps(first, ensure_ascii=False)[:500])
    print("base summaries:", len(base_summaries), [len(x) for x in base_summaries])

    chat = get_chat(api, chat_id)
    fork_status, fork = api.post(f"/api/v1/chats/{chat_id}/fork", {"message_id": current_id(chat) or last_id})
    if fork_status != 200 or not isinstance(fork, dict):
        raise RuntimeError(f"fork failed: {fork_status} {fork}")
    fork_id = fork["id"]
    print("fork id:", fork_id)

    original_parent = append_marker(api, chat_id, current_id(get_chat(api, chat_id)) or last_id, "BRANCH_MARKER_ORIGINAL_7f3a")
    fork_parent = append_marker(api, fork_id, current_id(get_chat(api, fork_id)) or fork.get("current_message_id"), "BRANCH_MARKER_FORK_9c2d")
    original_compact = compact(api, chat_id)
    fork_compact = compact(api, fork_id)

    original_summaries = summaries_from_chat(api, chat_id)
    fork_summaries = summaries_from_chat(api, fork_id)
    original_text = "\n".join(original_summaries)
    fork_text = "\n".join(fork_summaries)
    result = {
        "time": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "base_chat_id": chat_id,
        "fork_chat_id": fork_id,
        "base_summary_count": len(base_summaries),
        "original_summary_count": len(original_summaries),
        "fork_summary_count": len(fork_summaries),
        "original_compact": original_compact,
        "fork_compact": fork_compact,
        "original_summary_has_original_marker": "BRANCH_MARKER_ORIGINAL_7f3a" in original_text,
        "original_summary_has_fork_marker": "BRANCH_MARKER_FORK_9c2d" in original_text,
        "fork_summary_has_fork_marker": "BRANCH_MARKER_FORK_9c2d" in fork_text,
        "fork_summary_has_original_marker": "BRANCH_MARKER_ORIGINAL_7f3a" in fork_text,
        "original_summary_len": [len(x) for x in original_summaries],
        "fork_summary_len": [len(x) for x in fork_summaries],
    }
    output = RESULTS / ("branch-check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "branch.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("results:", output / "branch.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
