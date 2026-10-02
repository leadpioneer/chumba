#!/usr/bin/env python3
"""Бенч контекста: промпт ~N токенов -> llama-server, печатает timings/truncated."""

import json
import sys
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"
FILLER = "Это проверочный текст для замера длины контекста. " * 8


def build_prompt(approx_tokens: int) -> str:
    # ~2.4 символа на токен для русского текста (оценка; факт виден в prompt_n)
    target_chars = int(approx_tokens * 2.4)
    text = FILLER
    while len(text) < target_chars:
        text += FILLER
    return text[:target_chars]


def main():
    approx = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    prompt = build_prompt(approx)
    body = {
        "model": "orchestrator",
        "messages": [
            {"role": "system", "content": "Отвечай очень кратко."},
            {"role": "user", "content": prompt + "\n\nСкажи одним словом: ок"},
        ],
        "max_tokens": 32,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    started = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=600)
        out = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
        return 1
    elapsed = time.time() - started
    timings = out.get("timings") or {}
    print("timings:", json.dumps(timings, ensure_ascii=False))
    print(
        "approx=%d prompt_n=%s prompt_tps=%s predicted_n=%s predicted_tps=%s truncated=%s elapsed=%.1fs content=%r"
        % (
            approx,
            timings.get("prompt_n"),
            round(timings.get("prompt_per_second") or 0, 1),
            timings.get("predicted_n"),
            round(timings.get("predicted_per_second") or 0, 1),
            timings.get("truncated"),
            elapsed,
            (out["choices"][0]["message"].get("content") or "")[:60],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
