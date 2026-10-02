#!/usr/bin/env python3
"""Проверка Open WebUI: список tool-серверов и чат с включёнными MCP-инструментами."""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("WEBUI_BASE", "http://127.0.0.1:3000")


def load_env(path=".env"):
    cfg = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key] = value
    return cfg


def req(method, path, token=None, body=None, timeout=420):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=timeout)
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(text or "{}")
        except json.JSONDecodeError:
            return resp.status, text[:800]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:800].strip()


def main():
    cfg = load_env()
    status, res = req(
        "POST",
        "/api/v1/auths/signin",
        body={"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200 or not isinstance(res, dict) or not res.get("token"):
        sys.exit(f"signin failed: {status} {res}")
    token = res["token"]

    print("=== probe endpoints ===")
    for path in ("/api/v1/tools/servers", "/api/v1/tools/", "/api/v1/tools", "/api/v1/mcp/servers"):
        status, res = req("GET", path, token, timeout=60)
        body = json.dumps(res, ensure_ascii=False)[:400] if isinstance(res, (dict, list)) else str(res)[:300]
        print(f"{path}: {status} {body}")

    print("=== chat with MCP tools (legacy server-side loop) ===")
    payload = {
        "model": "orchestrator",
        "messages": [
            {
                "role": "user",
                "content": "Найди в интернете и кратко изложи, что такое llama.cpp. Итог дай с источниками.",
            }
        ],
        "tool_ids": ["server:mcp:search", "server:mcp:delegate"],
        "metadata": {
            "tool_ids": ["server:mcp:search", "server:mcp:delegate"],
            "params": {"function_calling": "legacy"},
        },
        "stream": False,
    }
    status, res = req("POST", "/api/chat/completions", token, payload)
    if status != 200:
        print("chat:", status, res)
        return
    if isinstance(res, dict):
        choices = res.get("choices") or []
        if choices:
            message = choices[0].get("message", {})
            print("finish:", choices[0].get("finish_reason"))
            print("content:", (message.get("content") or "")[:1800])
            if message.get("tool_calls"):
                print("tool_calls:", json.dumps(message["tool_calls"], ensure_ascii=False)[:400])
        else:
            print("chat response:", json.dumps(res, ensure_ascii=False)[:800])
    else:
        print("chat:", status, str(res)[:800])


if __name__ == "__main__":
    main()
