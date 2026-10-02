#!/usr/bin/env python3
"""Установка фильтра Chumba Rolling Summary в Open WebUI (create/update + activate)."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = "http://127.0.0.1:3000"
FILTER_ID = "chumba_summary"
FILTER_FILE = Path("openwebui/filters/chumba_summary_filter.py")


def req(method, path, token=None, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=timeout)
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, (json.loads(text) if text.strip() else {})
        except json.JSONDecodeError:
            return resp.status, text[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:600]


def main():
    cfg = load_env()
    status, res = req(
        "POST",
        "/api/v1/auths/signin",
        body={"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200 or not isinstance(res, dict):
        sys.exit(f"signin failed: {status} {res}")
    token = res["token"]

    content = FILTER_FILE.read_text(encoding="utf-8")
    form = {
        "id": FILTER_ID,
        "name": "Chumba Rolling Summary",
        "meta": {"description": "Сжатие старой истории чата в конспект локальной моделью"},
        "content": content,
    }

    status, existing = req("GET", f"/api/v1/functions/id/{FILTER_ID}", token)
    if status == 200:
        status, res2 = req("POST", f"/api/v1/functions/id/{FILTER_ID}/update", token, form)
        print("update:", status, json.dumps(res2, ensure_ascii=False)[:200] if isinstance(res2, dict) else res2)
    else:
        status, res2 = req("POST", "/api/v1/functions/create", token, form)
        print("create:", status, json.dumps(res2, ensure_ascii=False)[:300] if isinstance(res2, dict) else res2)

    status, fn = req("GET", f"/api/v1/functions/id/{FILTER_ID}", token)
    if isinstance(fn, dict):
        print("is_active=", fn.get("is_active"), "is_global=", fn.get("is_global"), "type=", fn.get("type"))
        if not fn.get("is_active"):
            status, t = req("POST", f"/api/v1/functions/id/{FILTER_ID}/toggle", token)
            print("toggle:", status, (t or {}).get("is_active") if isinstance(t, dict) else t)
        if not fn.get("is_global"):
            status, t = req("POST", f"/api/v1/functions/id/{FILTER_ID}/toggle/global", token)
            print("toggle/global:", status, (t or {}).get("is_global") if isinstance(t, dict) else t)

    if "--low-threshold" in sys.argv:
        status, current = req("GET", f"/api/v1/functions/id/{FILTER_ID}/valves", token)
        valves = dict(current) if isinstance(current, dict) else {}
        valves["threshold_tokens"] = 500
        status, res3 = req("POST", f"/api/v1/functions/id/{FILTER_ID}/valves/update", token, valves)
        print("valves(min):", status, json.dumps(res3, ensure_ascii=False)[:150] if isinstance(res3, dict) else res3)
    if "--restore-threshold" in sys.argv:
        status, current = req("GET", f"/api/v1/functions/id/{FILTER_ID}/valves", token)
        valves = dict(current) if isinstance(current, dict) else {}
        valves["threshold_tokens"] = 12000
        status, res3 = req("POST", f"/api/v1/functions/id/{FILTER_ID}/valves/update", token, valves)
        print("valves(restore):", status, json.dumps(res3, ensure_ascii=False)[:150] if isinstance(res3, dict) else res3)

    status, vals = req("GET", f"/api/v1/functions/id/{FILTER_ID}/valves", token)
    print("valves:", json.dumps(vals, ensure_ascii=False)[:300] if isinstance(vals, dict) else vals)


if __name__ == "__main__":
    main()
