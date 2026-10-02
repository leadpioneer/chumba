#!/usr/bin/env python3
"""Мини-клиент MCP Streamable HTTP (только stdlib) для проверок стека chumba.

Использование:
  MCP_TOKEN=... python check_mcp.py <url> list
  MCP_TOKEN=... python check_mcp.py <url> call <tool> '<json-args>'
"""

import json
import os
import sys
import urllib.error
import urllib.request

PROTO = "2025-06-18"


def rpc(url, token, payload, session=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if session:
        req.add_header("Mcp-Session-Id", session)
    try:
        resp = urllib.request.urlopen(req, timeout=240)
    except urllib.error.HTTPError as exc:
        raise SystemExit("HTTP %s: %s" % (exc.code, exc.read().decode("utf-8", "replace")[:800]))
    sid = resp.headers.get("Mcp-Session-Id")
    body = resp.read().decode("utf-8", "replace")
    ctype = resp.headers.get("Content-Type", "")
    msgs = []
    if "text/event-stream" in ctype:
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    msgs.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    elif body.strip():
        try:
            msgs.append(json.loads(body))
        except json.JSONDecodeError:
            pass
    return sid, msgs


def pick(msgs, rid):
    for msg in msgs:
        if msg.get("id") == rid:
            return msg
    return None


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    url, mode = sys.argv[1], sys.argv[2]
    token = os.environ.get("MCP_TOKEN", "")

    sid, msgs = rpc(
        url,
        token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTO,
                "capabilities": {},
                "clientInfo": {"name": "chumba-check", "version": "0.1"},
            },
        },
    )
    init = pick(msgs, 1)
    if not init or "result" not in init:
        raise SystemExit("initialize failed: %s" % json.dumps(msgs)[:600])
    rpc(url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)

    if mode == "list":
        _, msgs = rpc(url, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
        res = pick(msgs, 2)
        tools = (res or {}).get("result", {}).get("tools", [])
        print("tools:", ", ".join(t["name"] for t in tools))
        for tool in tools:
            desc = (tool.get("description") or "").split("\n")[0][:120]
            print("-", tool["name"], "::", desc)
    elif mode == "call":
        if len(sys.argv) < 4:
            raise SystemExit(__doc__)
        tool = sys.argv[3]
        raw_args = sys.argv[4] if len(sys.argv) > 4 else "{}"
        if raw_args.startswith("@"):
            with open(raw_args[1:], "r", encoding="utf-8") as fh:
                args = json.load(fh)
        else:
            args = json.loads(raw_args)
        _, msgs = rpc(
            url,
            token,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
            sid,
        )
        res = pick(msgs, 3)
        if not res or "result" not in res:
            raise SystemExit("call failed: %s" % json.dumps(msgs)[:800])
        for chunk in res["result"].get("content", []):
            if chunk.get("type") == "text":
                print(chunk["text"][:4000])
        if res["result"].get("isError"):
            sys.exit(2)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
