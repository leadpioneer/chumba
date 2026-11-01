"""
title: Experiment Request Probe
author: chumba experiment
version: 0.1.0
required_open_webui_version: 0.11.4
"""

import json
import time
import uuid
from pathlib import Path


class Filter:
    class Valves:
        log_file: str = "/app/backend/data/exp_request_probe.jsonl"

    def __init__(self):
        self.valves = self.Valves()

    async def request(self, body, __metadata__=None):
        if not isinstance(body, dict):
            return body
        messages = body.get("messages") or []
        roles = []
        content_chars = 0
        last_user_chars = 0
        tool_call_ids = []
        tool_result_ids = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role") or "?"
            roles.append(role)
            content = message.get("content")
            if isinstance(content, str):
                content_chars += len(content)
                if role == "user":
                    last_user_chars = len(content)
            elif content is not None:
                content_chars += len(str(content))
            for call in message.get("tool_calls") or []:
                if call.get("id"):
                    tool_call_ids.append(call.get("id"))
            if role == "tool" and message.get("tool_call_id"):
                tool_result_ids.append(message.get("tool_call_id"))

        tools = body.get("tools") or []
        tool_names = []
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    function = tool.get("function") or {}
                    tool_names.append(function.get("name") or tool.get("name") or "?")
        system = "\n".join(
            message.get("content") or ""
            for message in messages
            if isinstance(message, dict) and message.get("role") == "system"
        )
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "id": uuid.uuid4().hex,
            "metadata_chat_id": (__metadata__ or {}).get("chat_id") if isinstance(__metadata__, dict) else None,
            "message_count": len(messages),
            "roles": roles,
            "content_chars_total": content_chars,
            "last_user_chars": last_user_chars,
            "tool_schema_count": len(tool_names),
            "tool_names": tool_names,
            "tool_call_ids": tool_call_ids,
            "tool_result_ids": tool_result_ids,
            "orphan_tool_results": [value for value in tool_result_ids if value not in tool_call_ids],
            "unfinished_tool_calls": [value for value in tool_call_ids if value not in tool_result_ids],
            "system_chars": len(system),
            "has_conversation_summary": "[CONVERSATION SUMMARY]" in system,
            "has_system_marker": "SYSTEM_MARKER" in system,
        }
        log_file = Path(self.valves.log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return body
