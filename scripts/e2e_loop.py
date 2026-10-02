#!/usr/bin/env python3
"""Сквозной E2E-тест: Open WebUI (оркестратор) -> MCP-инструменты -> ответ.

API Open WebUI работает как OpenAI-совместимый pass-through: tool_calls
исполняет клиент. Этот скрипт исполняет их через MCP и возвращает результаты
модели — ровно та цепочка, которую в браузерном UI Open WebUI делает сам:
оркестратор -> delegate_to_worker -> воркер (JSON) -> оркестратор.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("WEBUI_BASE", "http://127.0.0.1:3000")
SEARCH_MCP_URL = os.environ.get("SEARCH_MCP_URL", "http://127.0.0.1:8085/mcp")
DELEGATE_MCP_URL = os.environ.get("DELEGATE_MCP_URL", "http://127.0.0.1:8086/mcp")


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


def http_post(url, body, token=None, timeout=600, session=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if session:
        request.add_header("Mcp-Session-Id", session)
    try:
        resp = urllib.request.urlopen(request, timeout=timeout)
        return resp.status, resp.read().decode("utf-8", "replace"), resp.headers.get("Mcp-Session-Id")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} {url}: {exc.read().decode('utf-8', 'replace')[:500]}")


def parse_rpc(body):
    msgs = []
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                msgs.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass
    if not msgs and body.strip():
        try:
            msgs.append(json.loads(body))
        except json.JSONDecodeError:
            pass
    return msgs


def mcp_call(url, token, tool, args):
    """Один вызов MCP-инструмента по Streamable HTTP (JSON-RPC)."""
    _, body, sid = http_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "chumba-e2e", "version": "0.1"},
            },
        },
        token,
    )
    init = None
    for msg in parse_rpc(body):
        if msg.get("id") == 1:
            init = msg
    if not init or "result" not in init:
        raise SystemExit(f"initialize failed: {body[:300]}")
    http_post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token, session=sid)
    _, body, _ = http_post(
        url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}},
        token,
        session=sid,
    )
    result = None
    for msg in parse_rpc(body):
        if msg.get("id") == 2:
            result = msg
    if not result or "result" not in result:
        raise SystemExit(f"tools/call failed: {body[:300]}")
    parts = [c.get("text", "") for c in result["result"].get("content", []) if c.get("type") == "text"]
    return "\n".join(parts)


def main():
    cfg = load_env()
    search_token = cfg.get("SEARCH_MCP_TOKEN", "")

    status, body, _ = http_post(
        BASE + "/api/v1/auths/signin",
        {"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200:
        sys.exit(f"signin failed: {body[:300]}")
    token = json.loads(body)["token"]

    model = sys.argv[1] if len(sys.argv) > 1 else "orchestrator"
    user_prompt = (
        sys.argv[2]
        if len(sys.argv) > 2
        else (
            "Найди в интернете и кратко изложи, что такое llama.cpp. "
            "Обязательно используй инструмент delegate_delegate_to_worker. Итог дай с источниками."
        )
    )

    tool_ids = ["server:mcp:search", "server:mcp:delegate"]
    messages = [{"role": "user", "content": user_prompt}]

    for step in range(1, 9):
        payload = {
            "model": model,
            "messages": messages,
            "tool_ids": tool_ids,
            "metadata": {"tool_ids": tool_ids},
            "stream": False,
        }
        status, body, _ = http_post(BASE + "/api/chat/completions", payload, token)
        if status != 200:
            sys.exit(f"chat failed: {status} {body[:400]}")
        res = json.loads(body)
        message = res["choices"][0]["message"]
        finish = res["choices"][0].get("finish_reason")
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            print(f"[step {step}] finish={finish}")
            print(message.get("content") or "")
            break

        print(f"[step {step}] tool_calls: {len(tool_calls)}")
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"].get("arguments") or "{}")
            print(f"  -> {name} args={json.dumps(args, ensure_ascii=False)[:160]}")
            if name.startswith("search_"):
                result = mcp_call(SEARCH_MCP_URL, search_token, name[len("search_"):], args)
            elif name.startswith("delegate_"):
                result = mcp_call(DELEGATE_MCP_URL, "", name[len("delegate_"):], args)
            else:
                result = json.dumps({"error": f"unknown tool {name}"})
            print(f"     result_chars={len(result)}")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
    else:
        print("stopped: iteration limit")


if __name__ == "__main__":
    main()
