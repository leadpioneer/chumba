#!/usr/bin/env python3
"""Добавляет MCP-подключения (search + delegate + study) в Open WebUI через админ-API.

Читает .env: WEBUI_ADMIN_EMAIL/PASSWORD, SEARCH_MCP_TOKEN, DELEGATE_MCP_TOKEN,
STUDY_MCP_TOKEN. Печатает результат GET /api/v1/configs/tool_servers.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import load_env  # noqa: E402

BASE = os.environ.get("WEBUI_BASE", "http://127.0.0.1:3000")


def req(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=120)
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(text or "{}")
        except json.JSONDecodeError:
            return resp.status, text[:500]
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

    connections = [
        {
            "id": "mcp-search",
            "type": "mcp",
            "name": "search",
            "url": "http://search-mcp:8080/mcp",
            "path": "/mcp",
            "auth_type": "bearer",
            "key": cfg.get("SEARCH_MCP_TOKEN", ""),
            "config": {"enable": True},
            "info": {
                "id": "search",
                "name": "search",
                "description": "SearXNG: веб-поиск и чтение страниц",
            },
        },
        {
            "id": "mcp-delegate",
            "type": "mcp",
            "name": "delegate",
            "url": "http://delegate:8000/mcp",
            "path": "/mcp",
            "auth_type": "bearer",
            "key": cfg.get("DELEGATE_MCP_TOKEN", ""),
            "config": {"enable": True},
            "info": {
                "id": "delegate",
                "name": "delegate",
                "description": "Делегирование узкой задачи облачному воркеру (JSON)",
            },
        },
        {
            "id": "mcp-study",
            "type": "mcp",
            "name": "study",
            "url": "http://study:8000/mcp",
            "path": "/mcp",
            "auth_type": "bearer",
            "key": cfg.get("STUDY_MCP_TOKEN", ""),
            "config": {"enable": True},
            "info": {
                "id": "study",
                "name": "study",
                "description": "Учебный прогресс и повторение репетитора (SQLite, локально)",
            },
        },
    ]
    status, res = req("POST", "/api/v1/configs/tool_servers", token, {"TOOL_SERVER_CONNECTIONS": connections})
    print("POST tool_servers:", status, json.dumps(res, ensure_ascii=False)[:600] if isinstance(res, (dict, list)) else res)

    status, res = req("GET", "/api/v1/configs/tool_servers", token)
    print("GET tool_servers:", status, json.dumps(res, ensure_ascii=False)[:1200] if isinstance(res, (dict, list)) else res)


if __name__ == "__main__":
    main()
