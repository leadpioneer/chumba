#!/usr/bin/env python3
"""Configure the notes MCP connection and import seed docs in the main stack.

Idempotent. Uses the OWUI admin API and the notes seed importer; it does not
enable the experimental compaction/guard policy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OWUI = os.environ.get("OWUI_URL", "http://open-webui:8080").rstrip("/")
EMAIL = os.environ.get("WEBUI_ADMIN_EMAIL", "")
PASSWORD = os.environ.get("WEBUI_ADMIN_PASSWORD", "")
AGENT_TOKEN = os.environ.get("NOTES_AGENT_TOKEN", "")
MCP_URL = os.environ.get("NOTES_MCP_URL", "http://notes:8080/mcp")
SEED_DIR = Path(os.environ.get("SEED_DIR", "/seed"))


def req(method: str, path: str, token: str | None = None, body=None) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(OWUI + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"message": raw[:500]}
    except Exception as exc:
        return 0, {"message": type(exc).__name__}


def wait_health() -> None:
    last = None
    for _ in range(90):
        status, _ = req("GET", "/health")
        if status == 200:
            return
        last = status
        time.sleep(2)
    raise SystemExit(f"OWUI did not become healthy: {last}")


def login() -> str | None:
    wait_health()
    if not EMAIL or not PASSWORD:
        raise SystemExit("WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD are required")
    last = None
    for _ in range(20):
        status, data = req("POST", "/api/v1/auths/signin", body={"email": EMAIL, "password": PASSWORD})
        if status == 200 and isinstance(data, dict) and data.get("token"):
            return data["token"]
        # Первый запуск на свежем томе: админ ещё не создан — создаём.
        status, data = req(
            "POST",
            "/api/v1/auths/signup",
            body={"name": "Admin", "email": EMAIL, "password": PASSWORD},
        )
        if status == 200 and isinstance(data, dict) and data.get("token"):
            return data["token"]
        # Аккаунт уже существует, а пароль из .env не подходит: пароль был
        # сменён в UI после первого запуска, тогда же применился и конфиг.
        if status in (400, 409) and ("already" in str(data).lower() or "exist" in str(data).lower()):
            print(
                "OWUI admin already exists, but the password from .env does not match. "
                "The password was likely changed in the UI after first boot; the notes "
                "config was applied then. Skipping."
            )
            return None
        last = (status, data)
        time.sleep(2)
    raise SystemExit(f"admin auth failed: {last}")


def configure_mcp(token: str) -> None:
    if not AGENT_TOKEN:
        raise SystemExit("NOTES_AGENT_TOKEN is empty")
    status, data = req("GET", "/api/v1/configs/tool_servers", token)
    if status != 200 or not isinstance(data, dict):
        raise SystemExit(f"cannot read tool server config: {status} {data}")
    connections = data.get("TOOL_SERVER_CONNECTIONS") or []
    connection = {
        "id": "mcp-notes",
        "type": "mcp",
        "name": "notes",
        "url": MCP_URL,
        "path": "/mcp",
        "auth_type": "bearer",
        "key": AGENT_TOKEN,
        "config": {"enable": True},
        "info": {
            "id": "notes",
            "name": "notes",
            "description": "Linked Markdown notes shared with the user",
        },
    }
    found = False
    updated = []
    for item in connections:
        if isinstance(item, dict) and item.get("id") == "mcp-notes":
            updated.append(connection)
            found = True
        else:
            updated.append(item)
    if not found:
        updated.append(connection)
    status, response = req(
        "POST",
        "/api/v1/configs/tool_servers",
        token,
        {"TOOL_SERVER_CONNECTIONS": updated},
    )
    if status != 200:
        raise SystemExit(f"cannot write tool server config: {status} {response}")
    print("notes MCP configured")


def import_seeds(token: str) -> None:
    script = SEED_DIR / "import_seed.py"
    if not script.exists():
        print(f"seed importer not mounted at {script}; skipped")
        return
    env = dict(os.environ)
    env["OWUI_TOKEN"] = token
    proc = subprocess.run(
        [sys.executable, str(script), "--url", OWUI, "--apply"],
        cwd=str(SEED_DIR),
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    print(proc.stdout[-3000:])
    if proc.stderr:
        print(proc.stderr[-2000:], file=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(f"seed import failed with code {proc.returncode}")


def main() -> int:
    token = login()
    if token is None:
        return 0
    configure_mcp(token)
    import_seeds(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
