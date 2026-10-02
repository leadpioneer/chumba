#!/usr/bin/env python3
"""Длинный чат через Open WebUI: проверка rolling summary (по логам фильтра)."""

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = "http://127.0.0.1:3000"


def req(method, path, token=None, body=None, timeout=600):
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


def build_messages():
    messages = [{"role": "system", "content": "Ты продолжаешь длинную беседу. Отвечай кратко."}]
    for i in range(1, 21):
        messages.append(
            {
                "role": "user",
                "content": f"Сообщение {i}: обсуждаем план проекта, шаг {i}, важные детали и договорённости номер {i}.",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"Принято по шагу {i}: записал решение и детали, продолжаем по плану проекта.",
            }
        )
    messages.append({"role": "user", "content": "Напомни одним предложением, что мы уже решили."})
    return messages


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

    chat_id = "chumba-test-" + uuid.uuid4().hex[:8]
    messages = build_messages()
    approx_chars = sum(len(m["content"]) for m in messages)
    print(f"chat_id={chat_id} messages={len(messages)} chars={approx_chars}")

    for attempt in (1, 2):
        payload = {
            "model": "orchestrator",
            "messages": messages,
            "metadata": {"chat_id": chat_id},
            "stream": False,
            "max_tokens": 64,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        status, out = req("POST", "/api/chat/completions", token, payload)
        if status != 200 or not isinstance(out, dict):
            print(f"attempt {attempt}: failed {status} {str(out)[:300]}")
            continue
        content = (out.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        print(f"attempt {attempt}: ok, answer={content[:120]!r}")


if __name__ == "__main__":
    main()
