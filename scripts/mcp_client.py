#!/usr/bin/env python3
"""Общий мини-клиент MCP Streamable HTTP (только stdlib) для скриптов chumba.

Один клиент на все проверочные скрипты: load_env, HTTP-POST с парсингом
SSE/JSON-RPC, вызов и листинг инструментов. search-mcp, delegate и study
отвечают по одному протоколу — не дублируем его в каждом скрипте.
"""

import json
import urllib.error
import urllib.request

PROTO = "2025-06-18"


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
        return (
            resp.status,
            resp.read().decode("utf-8", "replace"),
            resp.headers.get("Mcp-Session-Id"),
        )
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            "HTTP %s %s: %s"
            % (exc.code, url, exc.read().decode("utf-8", "replace")[:500])
        )


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


def _pick(msgs, rid):
    for msg in msgs:
        if msg.get("id") == rid:
            return msg
    return None


def _open_session(url, token):
    _, body, sid = http_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTO,
                "capabilities": {},
                "clientInfo": {"name": "chumba-scripts", "version": "0.1"},
            },
        },
        token,
    )
    init = _pick(parse_rpc(body), 1)
    if not init or "result" not in init:
        raise SystemExit("initialize failed: %s" % body[:300])
    http_post(
        url,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        token,
        session=sid,
    )
    return sid


def mcp_call(url, token, tool, args, fail_on_error=False):
    """Один вызов инструмента MCP; возвращает текстовый результат."""
    sid = _open_session(url, token)
    _, body, _ = http_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        token,
        session=sid,
    )
    res = _pick(parse_rpc(body), 2)
    if not res or "result" not in res:
        raise SystemExit("tools/call failed: %s" % body[:300])
    parts = [
        c.get("text", "")
        for c in res["result"].get("content", [])
        if c.get("type") == "text"
    ]
    if fail_on_error and res["result"].get("isError"):
        raise SystemExit("MCP tool error: " + "\n".join(parts)[:300])
    return "\n".join(parts)


def mcp_list_tools(url, token):
    """Список инструментов MCP-сервера."""
    sid = _open_session(url, token)
    _, body, _ = http_post(
        url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        token,
        session=sid,
    )
    res = _pick(parse_rpc(body), 2)
    if not res or "result" not in res:
        raise SystemExit("tools/list failed: %s" % body[:300])
    return res["result"].get("tools", [])
