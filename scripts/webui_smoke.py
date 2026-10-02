#!/usr/bin/env python3
"""Смоук-тест Open WebUI: вход админом, список моделей, чтение конфига tool_servers.

Читает WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD из .env, BASE из WEBUI_BASE.
"""

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


def req(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=90)
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, json.loads(text or "{}")
        except json.JSONDecodeError:
            return resp.status, text[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:500].strip()


def main():
    cfg = load_env()
    email = cfg.get("WEBUI_ADMIN_EMAIL", "")
    password = cfg.get("WEBUI_ADMIN_PASSWORD", "")
    if not email or not password:
        sys.exit("WEBUI_ADMIN_EMAIL/WEBUI_ADMIN_PASSWORD не заданы в .env")

    status, res = req("POST", "/api/v1/auths/signin", body={"email": email, "password": password})
    if status != 200 or not isinstance(res, dict) or not res.get("token"):
        sys.exit(f"signin failed: {status} {res}")
    print("signin: OK, role =", res.get("role"))
    token = res["token"]

    for path in ("/api/models", "/api/v1/models"):
        status, res = req("GET", path, token)
        if status == 200 and isinstance(res, dict):
            ids = [m.get("id") for m in res.get("data", [])]
            print(f"models ({path}): {ids}")
            break
    else:
        print("models: FAILED", status, res)

    for path in ("/api/v1/configs/tool_servers", "/api/configs/tool_servers"):
        status, res = req("GET", path, token)
        if status == 200:
            print(f"tool_servers ({path}): {json.dumps(res, ensure_ascii=False)[:700]}")
            break
    else:
        print("tool_servers: FAILED", status, res)


if __name__ == "__main__":
    main()
