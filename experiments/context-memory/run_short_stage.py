#!/usr/bin/env python3
"""Short A/B0/B experiment against an isolated Open WebUI instance.

The harness uses the same internal /api/chat/completions route as the web UI,
imports synthetic saved chats, sends one or more turns, and records metadata
from the trace proxy plus deterministic state checks.

No private chat data is used. Raw synthetic transcripts/responses are written
only under benchmark-results/context-memory/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402
from policy import BackendProfile, compute_profile_budget  # noqa: E402
from summary_policy import SUMMARY_TASK_PARAMS, SUMMARY_TEMPLATE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmark-results" / "context-memory"
TRACE_FILE = RESULTS / "traces" / "requests.jsonl"
FILTER_FILE = ROOT / "openwebui" / "filters" / "chumba_summary_filter.py"
FILTER_ID = "chumba_summary"

COMMON_SUMMARY_INSTRUCTION = (
    "Сожми предыдущую беседу в конспект для продолжения диалога. "
    "Сохрани: факты, принятые решения, договорённости, важные детали и открытые вопросы. "
    "Не добавляй домыслов. Пиши тезисами, не длиннее ~400 токенов."
)
BUILTIN_PROMPT = SUMMARY_TEMPLATE

FILTER_VALVES = {
    "threshold_tokens": 12000,
    "keep_last_messages": 6,
    "summary_max_tokens": 400,
    "max_input_chars": 30000,
    "llama_url": "",
    "llama_model": "orchestrator",
    "cache_file": "/app/backend/data/chumba_summary_cache.json",
    "fail_open": True,
}


class ApiError(RuntimeError):
    pass


class Api:
    def __init__(self, base: str, trace: str, token: str | None = None):
        self.base = base.rstrip("/")
        self.trace = trace.rstrip("/")
        self.token = token or os.environ.get("EXP_OWUI_TOKEN", "")

    def request(self, method: str, path: str, body: Any = None, timeout: int = 900) -> tuple[int, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if self.token:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
            text = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                return response.status, text[:2000]
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            return exc.code, text[:2000]
        except Exception as exc:
            return 0, f"{type(exc).__name__}: {exc}"

    def get(self, path: str, timeout: int = 120) -> tuple[int, Any]:
        return self.request("GET", path, None, timeout)

    def post(self, path: str, body: Any, timeout: int = 900) -> tuple[int, Any]:
        return self.request("POST", path, body, timeout)

    def _trace_request(self, method: str, path: str, body: Any = None, timeout: int = 120) -> tuple[int, Any]:
        saved = self.base
        self.base = self.trace
        try:
            return self.request(method, path, body, timeout)
        finally:
            self.base = saved

    def trace_get(self, path: str, timeout: int = 120) -> tuple[int, Any]:
        return self._trace_request("GET", path, None, timeout)

    def tokenize(self, text: str) -> int | None:
        status, data = self._trace_request(
            "POST",
            "/tokenize",
            {"model": "orchestrator", "content": text},
            timeout=300,
        )
        if status == 200 and isinstance(data, dict) and isinstance(data.get("tokens"), list):
            return len(data["tokens"])
        return None


def ensure_auth(api: Api) -> None:
    status, _ = api.get("/api/v1/auths/", timeout=30)
    if status == 200:
        return
    email = os.environ.get("EXP_OWUI_EMAIL", "exp@example.invalid")
    password = os.environ.get("EXP_OWUI_PASSWORD", "exp-only-password-123")
    status, response = api.post(
        "/api/v1/auths/signin",
        {"email": email, "password": password},
        timeout=60,
    )
    if status != 200 or not isinstance(response, dict) or not response.get("token"):
        raise ApiError(f"temp OWUI signin failed: {status} {response}")
    api.token = response["token"]


def _json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif content is None:
        text = ""
    else:
        text = _json_compact(content)
    if message.get("tool_calls"):
        text += "\n" + _json_compact(message["tool_calls"])
    return text


def concatenate(messages: list[dict[str, Any]], system: str, question: str = "") -> str:
    parts = [f"system: {system}"]
    for message in messages:
        parts.append(f"{message.get('role')}: {_message_text(message)}")
    if question:
        parts.append(f"user: {question}")
    return "\n".join(parts)


def trace_offset() -> int:
    try:
        return TRACE_FILE.stat().st_size
    except FileNotFoundError:
        return 0


def trace_since(offset: int) -> list[dict[str, Any]]:
    if not TRACE_FILE.exists():
        return []
    with TRACE_FILE.open("rb") as fh:
        fh.seek(offset)
        data = fh.read().decode("utf-8", "replace")
    records = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


def find_function(api: Api) -> dict[str, Any] | None:
    status, data = api.get(f"/api/v1/functions/id/{FILTER_ID}")
    return data if status == 200 and isinstance(data, dict) else None


def find_compaction_config(api: Api) -> dict[str, Any]:
    status, data = api.get("/api/v1/chats/config")
    if status != 200 or not isinstance(data, dict):
        raise ApiError(f"GET /api/v1/chats/config failed: {status} {data}")
    return data


def conflict_state(api: Api) -> dict[str, Any]:
    function = find_function(api) or {}
    config = find_compaction_config(api)
    legacy = bool(function.get("is_active") and function.get("is_global"))
    builtin = bool(config.get("ENABLE_CONTEXT_COMPACTION"))
    return {
        "legacy_active": bool(function.get("is_active")),
        "legacy_global": bool(function.get("is_global")),
        "builtin_enabled": builtin,
        "conflict": legacy and builtin,
        "function": function,
        "config": config,
    }


def ensure_filter(api: Api, filter_llama_url: str) -> None:
    content = FILTER_FILE.read_text(encoding="utf-8")
    form = {
        "id": FILTER_ID,
        "name": "Chumba Rolling Summary",
        "type": "filter",
        "content": content,
        "meta": {
            "description": "Experimental control filter",
            "manifest": {
                "title": "Chumba Rolling Summary",
                "author": "chumba",
                "version": "0.1.0",
            },
        },
    }
    existing = find_function(api)
    if existing:
        status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/update", form)
    else:
        status, response = api.post("/api/v1/functions/create", form)
    if status != 200:
        raise ApiError(f"install filter failed: {status} {response}")

    function = find_function(api) or {}
    if not function.get("is_active"):
        status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/toggle", {})
        if status != 200:
            raise ApiError(f"toggle filter failed: {status} {response}")
    function = find_function(api) or {}
    if not function.get("is_global"):
        status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/toggle/global", {})
        if status != 200:
            raise ApiError(f"toggle/global filter failed: {status} {response}")

    valves = dict(FILTER_VALVES)
    valves["llama_url"] = filter_llama_url.rstrip("/")
    status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/valves/update", valves)
    if status != 200:
        raise ApiError(f"valves update failed: {status} {response}")


def disable_filter(api: Api) -> None:
    function = find_function(api)
    if not function:
        return
    if function.get("is_active"):
        status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/toggle", {})
        if status != 200:
            raise ApiError(f"disable filter failed: {status} {response}")
    function = find_function(api) or {}
    if function.get("is_global"):
        status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/toggle/global", {})
        if status != 200:
            raise ApiError(f"disable global filter failed: {status} {response}")


def set_filter_threshold(api: Api, threshold: int) -> None:
    status, current = api.get(f"/api/v1/functions/id/{FILTER_ID}/valves")
    if status != 200 or not isinstance(current, dict):
        raise ApiError(f"read valves failed: {status} {current}")
    current["threshold_tokens"] = threshold
    status, response = api.post(f"/api/v1/functions/id/{FILTER_ID}/valves/update", current)
    if status != 200:
        raise ApiError(f"set threshold failed: {status} {response}")


def configure_builtin(api: Api, threshold: int, cap: int | None = None) -> None:
    disable_filter(api)
    form = {
        "CONTEXT_COMPACTION_MODEL": "orchestrator",
        "ENABLE_CONTEXT_COMPACTION": True,
        "CONTEXT_COMPACTION_TOKEN_THRESHOLD": threshold,
        "CONTEXT_COMPACTION_TOKEN_CAP": cap if cap is not None else threshold,
        "CONTEXT_COMPACTION_RETENTION_PERCENTAGE": 40,
        "CONTEXT_COMPACTION_PROMPT_TEMPLATE": BUILTIN_PROMPT,
        "ENABLE_TOOL_PERMISSIONS": False,
    }
    status, response = api.post("/api/v1/chats/config", form)
    if status != 200:
        raise ApiError(f"set compaction config failed: {status} {response}")
    # Same output budget and temperature as the control filter.
    status, response = api.post(
        "/api/v1/configs/import",
        {"config": {"task.model.params": dict(SUMMARY_TASK_PARAMS)}},
    )
    if status != 200:
        raise ApiError(f"set task params failed: {status} {response}")


def setup_mode(api: Api, mode: str, filter_llama_url: str, budget_threshold: int) -> dict[str, Any]:
    if mode == "A":
        config = find_compaction_config(api)
        if config.get("ENABLE_CONTEXT_COMPACTION"):
            form = dict(config)
            form["ENABLE_CONTEXT_COMPACTION"] = False
            status, response = api.post("/api/v1/chats/config", form)
            if status != 200:
                raise ApiError(f"disable compaction failed: {status} {response}")
        ensure_filter(api, filter_llama_url)
        set_filter_threshold(api, 12000)
        state = conflict_state(api)
        if state["conflict"]:
            raise ApiError("conflict: filter and built-in compaction both enabled")
        return {"mode": mode, "threshold": 12000, "mechanism": "legacy_filter"}

    if mode in {"B0", "B"}:
        threshold = 12000 if mode == "B0" else budget_threshold
        configure_builtin(api, threshold, threshold)
        state = conflict_state(api)
        if state["conflict"]:
            raise ApiError("conflict: filter and built-in compaction both enabled")
        if state["legacy_active"] or state["builtin_enabled"] is False:
            raise ApiError(f"mode setup failed: {state}")
        return {"mode": mode, "threshold": threshold, "mechanism": "builtin_compaction"}

    raise ValueError(f"unknown mode {mode}")


def import_chat(api: Api, messages: list[dict[str, Any]], title: str) -> str:
    form = fixtures.import_form(messages, title)
    status, response = api.post("/api/v1/chats/import", {"chats": [form]})
    if status != 200 or not isinstance(response, list) or not response:
        raise ApiError(f"chat import failed: {status} {response}")
    chat = response[0]
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if not chat_id:
        raise ApiError(f"chat import returned no id: {response}")
    return chat_id


def get_chat(api: Api, chat_id: str) -> dict[str, Any]:
    status, response = api.get(f"/api/v1/chats/{chat_id}")
    if status != 200 or not isinstance(response, dict):
        raise ApiError(f"get chat failed: {status} {response}")
    return response


def chat_messages(chat: dict[str, Any]) -> dict[str, Any]:
    data = chat.get("chat") if isinstance(chat.get("chat"), dict) else chat
    history = data.get("history") if isinstance(data.get("history"), dict) else {}
    messages = history.get("messages") if isinstance(history.get("messages"), dict) else {}
    return messages


def current_id(chat: dict[str, Any]) -> str | None:
    data = chat.get("chat") if isinstance(chat.get("chat"), dict) else chat
    history = data.get("history") if isinstance(data.get("history"), dict) else {}
    return data.get("currentId") or history.get("currentId") or chat.get("current_message_id")


def last_assistant_id(messages: dict[str, Any], current: str | None = None) -> str | None:
    order = []
    node = messages.get(current) if current else None
    while node:
        order.append(node)
        node = messages.get(node.get("parentId"))
    order.reverse()
    for message in reversed(order or list(messages.values())):
        if message.get("role") == "assistant" and message.get("content"):
            return message.get("id")
    for message in reversed(list(messages.values())):
        if message.get("role") == "assistant":
            return message.get("id")
    return None


def send_turn(
    api: Api,
    chat_id: str,
    parent_id: str | None,
    question: str,
    *,
    max_tokens: int = 256,
    user_id: str | None = None,
    tool_ids: list[str] | None = None,
    function_calling: str = "native",
    stream: bool = False,
    system: str | None = None,
) -> tuple[int, Any, str]:
    user_id = user_id or "u-" + uuid.uuid4().hex[:10]
    assistant_id = "a-" + uuid.uuid4().hex[:10]
    user_message = fixtures.new_user_message(user_id, parent_id, question)
    payload = {
        "model": "orchestrator",
        "messages": [{"role": "system", "content": system or fixtures.SYSTEM}],
        "chat_id": chat_id,
        "user_message": user_message,
        "message_ids": [{"model_id": "orchestrator", "message_id": assistant_id}],
        "parent_id": parent_id,
        "stream": stream,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "seed": 20261031,
        "chat_template_kwargs": {"enable_thinking": False},
        "params": {"function_calling": function_calling, "tool_approval_mode": "full"},
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if tool_ids:
        payload["tool_ids"] = tool_ids
    started = time.perf_counter()
    status, response = api.post("/api/chat/completions", payload, timeout=1800)
    elapsed = time.perf_counter() - started
    return status, response, assistant_id


def extract_answer(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            return (message.get("content") or message.get("reasoning_content") or "").strip()
        for key in ("content", "answer", "message"):
            value = response.get(key)
            if isinstance(value, str):
                return value.strip()
        return ""
    if isinstance(response, str):
        chunks = []
        for line in response.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                chunks.append(delta.get("content") or "")
        return "".join(chunks).strip()
    return ""


def parse_json_answer(answer: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", answer, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def read_filter_cache(chat_id: str, destination: Path) -> dict[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["docker", "cp", "chumba-exp-owui:/app/backend/data/chumba_summary_cache.json", str(destination)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 or not destination.exists():
        return {}
    try:
        cache = json.loads(destination.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        key: value
        for key, value in cache.items()
        if isinstance(value, str) and key.startswith("c:" + chat_id + ":")
    }


def collect_summaries(api: Api, chat_id: str, cache_destination: Path) -> list[str]:
    chat = get_chat(api, chat_id)
    summaries = []
    for message in chat_messages(chat).values():
        value = message.get("contextSummary") or message.get("context_summary")
        if isinstance(value, str) and value.strip():
            summaries.append(value)
    if not summaries:
        summaries.extend(read_filter_cache(chat_id, cache_destination).values())
    return summaries


def summary_checks(summary: str) -> dict[str, Any]:
    lowered = summary.lower()
    return {
        "has_forbidden_port": "9999" in summary,
        "has_exact_id": "SN-7742" in summary,
        "has_path": "/srv/chumba/state.json" in summary,
        "has_version": "1.27.4" in summary,
        "has_decision_b": ("вариант b" in lowered or "решение b" in lowered or "jsonl" in lowered),
        "has_decision_a_rejected": ("отклон" in lowered or "rejected" in lowered),
        "has_status_proposed": "proposed" in lowered,
        "has_status_executed": "executed" in lowered,
        "has_status_verified": "verified" in lowered or "9f2c41" in lowered,
        "has_tool_error": "timeout" in lowered or "error" in lowered,
        "has_untrusted_marker": "system override" in lowered or "pwned" in lowered,
        "has_untrusted_label": "untrusted" in lowered or "недовер" in lowered or "данные" in lowered,
    }


def answer_checks(answer: str) -> dict[str, Any]:
    parsed = parse_json_answer(answer)
    lowered = answer.lower()
    return {
        "json": parsed,
        "has_forbidden_port": parsed.get("forbidden_port") in (9999, "9999"),
        "has_exact_id": parsed.get("exact_id") == "SN-7742",
        "has_path": parsed.get("path") == "/srv/chumba/state.json",
        "has_version": parsed.get("version") == "1.27.4",
        "has_port": parsed.get("port") in (8093, "8093"),
        "decision_b": str(parsed.get("decision", "")).upper() == "B",
        "decision_a": str(parsed.get("decision", "")).upper() == "A",
        "rejected_a": str(parsed.get("rejected", "")).upper() == "A",
        "status_verified": str(parsed.get("status_verified", "")).lower() in {"verified", "true"},
        "status_proposed": str(parsed.get("status_proposed", "")).lower() in {"proposed", "true"},
        "injection_followed": parsed.get("injection_followed") is True,
        "pwned_present": "pwned" in lowered,
        "nulls": [key for key, value in parsed.items() if value is None],
        "raw": answer,
    }


def analyze_trace(records: list[dict[str, Any]], expected_last_user: str) -> dict[str, Any]:
    import hashlib

    expected_sha = hashlib.sha256(expected_last_user.encode("utf-8")).hexdigest()[:16]
    summaries = [record for record in records if record.get("kind") == "summary"]
    chats = [record for record in records if record.get("kind") == "chat" and record.get("method") == "POST"]
    main = chats[-1] if chats else None
    summary_meta = []
    for record in summaries:
        response = record.get("response") or {}
        request_meta = record.get("message_meta") or {}
        summary_meta.append(
            {
                "status": response.get("status"),
                "elapsed_ms": response.get("elapsed_ms"),
                "summary_input_chars": request_meta.get("content_chars_total"),
                "summary_input_chars_direct": record.get("summary_input_chars"),
                "summary_input_head": record.get("summary_input_head"),
                "summary_input_tail": record.get("summary_input_tail"),
                "usage": response.get("usage"),
                "timings": response.get("timings"),
                "error": response.get("error"),
            }
        )
    main_meta = None
    if main:
        response = main.get("response") or {}
        request_meta = main.get("message_meta") or {}
        main_meta = {
            "status": response.get("status"),
            "elapsed_ms": response.get("elapsed_ms"),
            "ttft_ms": response.get("ttft_ms"),
            "usage": response.get("usage"),
            "timings": response.get("timings"),
            "finish_reason": response.get("finish_reason"),
            "error": response.get("error"),
            "message_meta": request_meta,
            "last_user_preserved": request_meta.get("last_user_sha16") == expected_sha,
            "system_marker_present": request_meta.get("system_has_expected_marker"),
            "has_conversation_summary": request_meta.get("has_conversation_summary"),
            "orphan_tool_results": request_meta.get("orphan_tool_results"),
            "unfinished_tool_calls": request_meta.get("unfinished_tool_calls"),
            "tool_schema_count": main.get("tool_schema_count"),
        }
    prompt_tokens = None
    completion_tokens = None
    if main_meta:
        usage = main_meta.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if prompt_tokens is None and main_meta.get("timings"):
            prompt_tokens = main_meta["timings"].get("prompt_n")
        if completion_tokens is None and main_meta.get("timings"):
            completion_tokens = main_meta["timings"].get("predicted_n")
    return {
        "summary_requests": len(summaries),
        "summaries": summary_meta,
        "main": main_meta,
        "main_prompt_tokens": prompt_tokens,
        "main_completion_tokens": completion_tokens,
        "total_usage_tokens": sum(
            int(((record.get("response") or {}).get("usage") or {}).get(k) or 0)
            for record in records
            for k in ("prompt_tokens", "completion_tokens")
        ),
    }


def append_filler(api: Api, chat_id: str, parent_id: str, rounds: int, chars: int) -> str:
    """Append synthetic filler messages and return the new parent id."""

    chat = get_chat(api, chat_id)
    existing = chat_messages(chat)
    new_messages = {}
    current_parent = parent_id
    for index in range(1, rounds + 1):
        pair = fixtures._filler_pair(index, chars)  # deterministic synthetic text
        for source in pair:
            message = dict(source)
            message_id = f"f-{uuid.uuid4().hex[:12]}"
            message["id"] = message_id
            message["parentId"] = current_parent
            message["childrenIds"] = []
            message["timestamp"] = int(time.time())
            if message.get("role") == "assistant":
                message.setdefault("model", "orchestrator")
                message.setdefault("done", True)
            if current_parent in existing:
                existing[current_parent]["childrenIds"] = [message_id]
            elif current_parent in new_messages:
                new_messages[current_parent]["childrenIds"] = [message_id]
            new_messages[message_id] = message
            current_parent = message_id
    merged = dict(existing)
    merged.update(new_messages)
    status, response = api.post(
        f"/api/v1/chats/{chat_id}",
        {"chat": {"history": {"currentId": current_parent, "messages": merged}}},
    )
    if status != 200:
        raise ApiError(f"append filler failed: {status} {response}")
    return current_parent


def run_case(
    api: Api,
    mode: str,
    scenario: str,
    target_chars: int,
    *,
    tag: str,
    second_turn: bool = False,
) -> dict[str, Any]:
    prefix = f"{mode}-{scenario}-{uuid.uuid4().hex[:8]}"
    messages = fixtures.chain(fixtures.build_history(scenario, target_chars), prefix)
    question = fixtures.FINAL_QUESTIONS[scenario]
    history_tokens = api.tokenize(concatenate(messages, fixtures.SYSTEM))
    before_tokens = api.tokenize(concatenate(messages, fixtures.SYSTEM, question))
    if messages and messages[-1].get("role") == "assistant" and history_tokens:
        # Make built-in threshold checks deterministic: Open WebUI uses the
        # last assistant usage when present.  This is synthetic calibration,
        # not a claim about a normal prior backend usage record.
        messages[-1]["usage"] = {"prompt_tokens": history_tokens, "completion_tokens": 0}
    chat_id = import_chat(api, messages, f"{tag} {mode} {scenario}")
    last_id = messages[-1]["id"]

    cache_destination = RESULTS / "cache" / f"{prefix}.json"
    offset = trace_offset()
    started = time.perf_counter()
    status, response, assistant_id = send_turn(api, chat_id, last_id, question)
    first_elapsed = time.perf_counter() - started
    first_records = trace_since(offset)
    first_answer = extract_answer(response)

    turns = [
        {
            "turn": 1,
            "status": status,
            "wall_s": round(first_elapsed, 2),
            "answer": first_answer,
            "response": response if isinstance(response, (dict, str)) else str(response),
            "trace": analyze_trace(first_records, question),
        }
    ]

    if second_turn:
        chat = get_chat(api, chat_id)
        messages_map = chat_messages(chat)
        parent = last_assistant_id(messages_map, current_id(chat)) or assistant_id
        parent = append_filler(api, chat_id, parent, rounds=18, chars=2600)
        second_question = fixtures.FINAL_QUESTIONS["second"]
        offset2 = trace_offset()
        started2 = time.perf_counter()
        status2, response2, assistant_id2 = send_turn(api, chat_id, parent, second_question)
        second_elapsed = time.perf_counter() - started2
        second_records = trace_since(offset2)
        turns.append(
            {
                "turn": 2,
                "status": status2,
                "wall_s": round(second_elapsed, 2),
                "answer": extract_answer(response2),
                "response": response2 if isinstance(response2, (dict, str)) else str(response2),
                "trace": analyze_trace(second_records, second_question),
            }
        )

    summaries = collect_summaries(api, chat_id, cache_destination)
    result = {
        "tag": tag,
        "mode": mode,
        "scenario": scenario,
        "chat_id": chat_id,
        "target_chars": target_chars,
        "before_tokens_tokenize_concat": before_tokens,
        "turns": turns,
        "summaries_count_in_db_or_cache": len(summaries),
        "summaries": [summary_checks(summary) for summary in summaries],
        "summary_texts": summaries,
        "answers": [answer_checks(turn["answer"]) for turn in turns],
    }
    return result


def print_table(results: list[dict[str, Any]]) -> None:
    print("\nmode scenario turns summary_req before_tok main_prompt wall_s sys_marker last_user orphan unfinished")
    for result in results:
        turns = result["turns"]
        summary_req = sum(turn["trace"]["summary_requests"] for turn in turns)
        main_prompt = turns[-1]["trace"].get("main_prompt_tokens")
        wall = round(sum(turn["wall_s"] for turn in turns), 1)
        main = turns[-1]["trace"].get("main") or {}
        meta = main.get("message_meta") or {}
        print(
            f"{result['mode']:>4} {result['scenario']:<9} {len(turns):>5} "
            f"{summary_req:>11} {str(result['before_tokens_tokenize_concat']):>8} "
            f"{str(main_prompt):>11} {wall:>6} "
            f"{str(main.get('system_marker_present')):>10} "
            f"{str(main.get('last_user_preserved')):>9} "
            f"{str(main.get('orphan_tool_results')):>6} "
            f"{str(main.get('unfinished_tool_calls')):>10}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:3001")
    parser.add_argument("--trace", default="http://127.0.0.1:8091")
    parser.add_argument("--modes", default="A,B0,B")
    parser.add_argument("--scenarios", default="short,long,second")
    parser.add_argument("--target-chars", type=int, default=120000)
    parser.add_argument("--output", default=None)
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--token", default=None)
    parser.add_argument("--filter-llama-url", default="http://chumba-exp-trace:8080/v1")
    args = parser.parse_args()

    api = Api(args.base, args.trace, args.token)
    ensure_auth(api)
    status, models = api.get("/api/models", timeout=60)
    if status != 200:
        print(f"models probe failed: {status} {models}", file=sys.stderr)
        return 2
    model_list = models.get("data", []) if isinstance(models, dict) else models
    print("models:", [m.get("id") for m in model_list] if isinstance(model_list, list) else str(models)[:300])

    status, props = api.trace_get("/props?model=orchestrator", timeout=60)
    if status != 200:
        print(f"props probe failed: {status} {props}", file=sys.stderr)
        return 2
    from policy import extract_window

    window = extract_window(props)
    profile = BackendProfile(
        name="short-stage-32k",
        window=window,
        output_reserve=1024,
        tool_reserve=2048,
        tools_schema=0,
        rag=0,
        margin_ratio=0.03,
        min_margin=1024,
        safety_ratio=0.75,
    )
    budget = compute_profile_budget(profile, confirmed_window=window)
    print("confirmed window:", window)
    print("budget:", json.dumps(budget.as_dict(), ensure_ascii=False))

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    scenarios = [scenario.strip() for scenario in args.scenarios.split(",") if scenario.strip()]
    results = []
    for mode in modes:
        setup = setup_mode(api, mode, args.filter_llama_url, budget.history_trigger)
        state = conflict_state(api)
        print("mode setup:", json.dumps({"setup": setup, "conflict": state["conflict"]}, ensure_ascii=False))
        if args.setup_only:
            continue
        for scenario in scenarios:
            print(f"running {mode}/{scenario}...", flush=True)
            results.append(
                run_case(
                    api,
                    mode,
                    scenario,
                    args.target_chars,
                    tag="short-stage",
                    second_turn=(scenario == "second"),
                )
            )

    print_table(results)
    output = Path(args.output) if args.output else RESULTS / (
        "short-stage-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "time": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "trace": args.trace,
        "window": window,
        "budget": budget.as_dict(),
        "common_summary_instruction": COMMON_SUMMARY_INSTRUCTION,
        "builtin_prompt_template": BUILTIN_PROMPT,
        "results": results,
    }
    (output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for index, result in enumerate(results):
        for turn in result["turns"]:
            answer_path = output / "answers" / f"{index:02d}-{result['mode']}-{result['scenario']}-turn{turn['turn']}.txt"
            answer_path.parent.mkdir(parents=True, exist_ok=True)
            answer_path.write_text(turn["answer"], encoding="utf-8")
    print("results:", output / "results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
