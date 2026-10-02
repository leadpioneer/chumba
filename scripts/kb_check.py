#!/usr/bin/env python3
"""Сквозная проверка RAG: knowledge -> загрузка файла -> эмбеддинги -> retrieval."""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = "http://127.0.0.1:3000"
TEST_FILE = Path("testdata/kb-test.txt")
MARKER = "сиреневый кирпич"


def req(method, path, token=None, body=None, timeout=180):
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


def upload(path, token, filepath):
    boundary = "----chumba" + uuid.uuid4().hex
    content = filepath.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"\r\n'.encode(),
            b"Content-Type: text/plain\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(BASE + path, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=300)
        return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
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

    name = "chumba-test-" + uuid.uuid4().hex[:6]
    status, kb = req("POST", "/api/v1/knowledge/create", token, {"name": name, "description": "autotest"})
    print("create:", status, json.dumps(kb, ensure_ascii=False)[:200] if isinstance(kb, dict) else kb)
    if status != 200 or not isinstance(kb, dict) or not kb.get("id"):
        sys.exit("knowledge create failed")
    kid = kb["id"]

    status, up = upload("/api/v1/files/", token, TEST_FILE)
    print("upload:", status, json.dumps(up, ensure_ascii=False)[:200] if isinstance(up, (dict, list)) else up)
    if status != 200 or not isinstance(up, dict) or not up.get("id"):
        sys.exit("file upload failed")
    file_id = up["id"]

    print("waiting for file processing...")
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(3)
        status, fdet = req("GET", f"/api/v1/files/{file_id}", token)
        if isinstance(fdet, dict) and (fdet.get("data") or fdet.get("status") in ("completed", "processed")):
            print("file processed:", fdet.get("status"))
            break

    status, add = req("POST", f"/api/v1/knowledge/{kid}/file/add", token, {"file_id": file_id})
    print("file/add:", status, json.dumps(add, ensure_ascii=False)[:200] if isinstance(add, (dict, list)) else add)

    print("waiting for embedding...")
    deadline = time.time() + 180
    file_status = ""
    while time.time() < deadline:
        time.sleep(4)
        status, details = req("GET", f"/api/v1/knowledge/{kid}", token)
        files = (details or {}).get("files") if isinstance(details, dict) else None
        if files:
            file_status = files[0].get("status") or files[0].get("data", {}).get("status") or "?"
            if file_status in ("completed", "processed", "success"):
                break
    print("file_status:", file_status)

    status, q1 = req(
        "POST",
        "/api/v1/retrieval/query/collection",
        token,
        {"collection_names": [kid], "query": MARKER, "k": 3},
    )
    text1 = json.dumps(q1, ensure_ascii=False)
    print("query(marker):", status, "hit=", MARKER in text1, "| keys:", list(q1)[:8] if isinstance(q1, dict) else text1[:200])

    status, q2 = req(
        "POST",
        "/api/v1/retrieval/query/collection",
        token,
        {"collection_names": [kid], "query": "на каком порту работает SearXNG?", "k": 3},
    )
    text2 = json.dumps(q2, ensure_ascii=False)
    print("query(port):", status, "hit_8888=", "8888" in text2)
    ok = MARKER in text1 and "8888" in text2
    print("KB_OK=" + str(ok))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
