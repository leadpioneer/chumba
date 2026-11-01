#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_context_policy as policy_helper

BASE = os.environ.get("EXP_OWUI_URL", "http://127.0.0.1:3001")
BACKEND = os.environ.get("EXP_BACKEND_URL", "http://127.0.0.1:8091")
EMAIL = os.environ.get("EXP_OWUI_EMAIL", "exp@example.invalid")
PASSWORD = os.environ.get("EXP_OWUI_PASSWORD", "exp-only-password-123")
AGENT_TOKEN = os.environ.get("EXP_NOTES_AGENT_TOKEN", "")
if not AGENT_TOKEN:
    raise SystemExit("EXP_NOTES_AGENT_TOKEN is required")


def req(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:1000]


def wait_health():
    last = None
    for _ in range(60):
        try:
            status, _ = req("GET", "/health")
            if status == 200:
                return
            last = status
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(2)
    raise SystemExit(f"OWUI did not become ready: {last}")


def auth():
    wait_health()
    last = None
    for _ in range(15):
        try:
            status, data = req("POST", "/api/v1/auths/signin", body={"email": EMAIL, "password": PASSWORD})
            if status == 200 and isinstance(data, dict) and data.get("token"):
                return data["token"]
            status, data = req(
                "POST",
                "/api/v1/auths/signup",
                body={"name": "exp", "email": EMAIL, "password": PASSWORD},
            )
            if status == 200 and isinstance(data, dict) and data.get("token"):
                return data["token"]
            last = (status, data)
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(2)
    raise SystemExit(f"auth failed after OWUI became healthy: {last}")


def install_guard(token):
    content = (ROOT / "experiments" / "context-memory" / "budget_guard_filter.py").read_text(encoding="utf-8")
    form = {
        "id": "context_budget_guard",
        "name": "Context Budget Guard",
        "type": "filter",
        "content": content,
        "meta": {"description": "Experimental final-request budget guard", "manifest": {"title": "Context Budget Guard", "version": "0.1.0"}},
    }
    status, _ = req("GET", "/api/v1/functions/id/context_budget_guard", token)
    if status == 200:
        status, data = req("POST", "/api/v1/functions/id/context_budget_guard/update", token, form)
    else:
        status, data = req("POST", "/api/v1/functions/create", token, form)
    if status != 200:
        raise SystemExit(f"guard install failed: {status} {data}")
    for action in ("toggle", "toggle/global"):
        status, function = req("GET", "/api/v1/functions/id/context_budget_guard", token)
        if status != 200:
            raise SystemExit(f"guard read failed: {status} {function}")
        if action == "toggle" and function.get("is_active"):
            continue
        if action == "toggle/global" and function.get("is_global"):
            continue
        status, data = req("POST", f"/api/v1/functions/id/context_budget_guard/{action}", token, {})
        if status != 200:
            raise SystemExit(f"guard {action} failed: {status} {data}")
    valves = {
        "enabled": True,
        "window": 32768,
        "output_reserve": 1024,
        "tool_reserve": 1024,
        "margin": 1024,
        "tokenizer_url": "http://llama-server:8080",
        "props_url": "http://llama-server:8080",
        "verify_backend_window": True,
        "model": "orchestrator",
        "tokenizer_timeout": 30,
        "template_overhead_per_message": 8,
        "template_overhead_fixed": 256,
        "fallback_chars_per_token": 2.5,
    }
    status, data = req("POST", "/api/v1/functions/id/context_budget_guard/valves/update", token, valves)
    if status != 200:
        raise SystemExit(f"guard valves failed: {status} {data}")


def configure_mcp(token):
    connections = [{
        "id": "mcp-notes",
        "type": "mcp",
        "name": "notes",
        "url": "http://chumba-notes:8080/mcp",
        "path": "/mcp",
        "auth_type": "bearer",
        "key": AGENT_TOKEN,
        "config": {"enable": True},
        "info": {"id": "notes", "name": "notes", "description": "Linked Markdown notes shared with the user"},
    }]
    status, data = req("POST", "/api/v1/configs/tool_servers", token, {"TOOL_SERVER_CONNECTIONS": connections})
    if status != 200:
        raise SystemExit(f"tool servers failed: {status} {data}")
    status, data = req("GET", "/api/v1/configs/tool_servers", token)
    if status != 200:
        raise SystemExit(f"tool servers read failed: {status} {data}")
    return data


def main():
    token = auth()
    client = policy_helper.OwuiClient(BASE, BACKEND, token)
    plan = policy_helper.build_plan(client, profile_name="32k", allow_hypothesis=False)
    if not plan.apply_allowed:
        raise SystemExit("policy apply not allowed: " + "; ".join(plan.reasons))
    rollback = ROOT / "benchmark-results" / "context-memory" / "notes-mvp-policy-rollback.json"
    policy_helper.apply_plan(client, plan, rollback)
    install_guard(token)
    mcp = configure_mcp(token)
    print(json.dumps({"policy": plan.public(), "guard": "context_budget_guard", "mcp": mcp}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
