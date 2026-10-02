#!/usr/bin/env python3
"""Применение RAG-настроек Open WebUI: движок эмбеддингов и TOP_K."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = "http://127.0.0.1:3000"
EMBEDDING_FORM = {
    "RAG_EMBEDDING_ENGINE": "openai",
    "RAG_EMBEDDING_MODEL": "bge-m3",
    "RAG_EMBEDDING_BATCH_SIZE": 8,
    "ENABLE_ASYNC_EMBEDDING": True,
    "RAG_EMBEDDING_CONCURRENT_REQUESTS": 2,
    "openai_config": {"url": "http://embeddings:8080/v1", "key": "none"},
}


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
        return exc.code, exc.read().decode("utf-8", "replace")[:500]


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

    status, before = req("GET", "/api/v1/retrieval/embedding", token)
    print("embedding before:", status, json.dumps(before, ensure_ascii=False)[:300] if isinstance(before, dict) else before)

    status, res2 = req("POST", "/api/v1/retrieval/embedding/update", token, EMBEDDING_FORM)
    print("embedding/update:", status, json.dumps(res2, ensure_ascii=False)[:200] if isinstance(res2, dict) else res2)

    status, after = req("GET", "/api/v1/retrieval/embedding", token)
    if isinstance(after, dict):
        print(
            "embedding after: engine=%s model=%s url=%s concurrent=%s"
            % (
                after.get("RAG_EMBEDDING_ENGINE"),
                after.get("RAG_EMBEDDING_MODEL"),
                (after.get("openai_config") or {}).get("url"),
                after.get("RAG_EMBEDDING_CONCURRENT_REQUESTS"),
            )
        )

    status, cfg_before = req("GET", "/api/v1/retrieval/config", token)
    print("config before: TOP_K =", cfg_before.get("TOP_K") if isinstance(cfg_before, dict) else cfg_before)
    status, res3 = req("POST", "/api/v1/retrieval/config/update", token, {"TOP_K": int(4)})
    print("config/update:", status, json.dumps(res3, ensure_ascii=False)[:200] if isinstance(res3, dict) else res3)
    status, cfg_after = req("GET", "/api/v1/retrieval/config", token)
    print("config after: TOP_K =", cfg_after.get("TOP_K") if isinstance(cfg_after, dict) else cfg_after)


if __name__ == "__main__":
    main()
