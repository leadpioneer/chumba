#!/usr/bin/env python3
"""Сквозной E2E-тест: Open WebUI (оркестратор) -> MCP-инструменты -> ответ.

API Open WebUI работает как OpenAI-совместимый pass-through: tool_calls
исполняет клиент. Этот скрипт исполняет их через MCP и возвращает результаты
модели — ровно та цепочка, которую в браузерном UI Open WebUI делает сам:
оркестратор -> delegate_to_worker -> воркер (JSON) -> оркестратор.
"""

import json
import os
import sys

BASE = os.environ.get("WEBUI_BASE", "http://127.0.0.1:3000")
SEARCH_MCP_URL = os.environ.get("SEARCH_MCP_URL", "http://127.0.0.1:8085/mcp")
DELEGATE_MCP_URL = os.environ.get("DELEGATE_MCP_URL", "http://127.0.0.1:8086/mcp")


# Общий клиент (load_env/http_post/mcp_call) — переэкспортируем: другие скрипты
# импортируют их отсюда.
from mcp_client import http_post, load_env, mcp_call  # noqa: E402


def main():
    # Консоль Windows (cp1251) не осилит эмодзи в ответе модели.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_env()
    search_token = cfg.get("SEARCH_MCP_TOKEN", "")
    delegate_token = cfg.get("DELEGATE_MCP_TOKEN", "")

    status, body, _ = http_post(
        BASE + "/api/v1/auths/signin",
        {"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200:
        sys.exit(f"signin failed: {body[:300]}")
    token = json.loads(body)["token"]

    model = sys.argv[1] if len(sys.argv) > 1 else "orchestrator"
    user_prompt = (
        sys.argv[2]
        if len(sys.argv) > 2
        else (
            "Найди в интернете и кратко изложи, что такое llama.cpp. "
            "Обязательно используй инструмент delegate_delegate_to_worker. Итог дай с источниками."
        )
    )

    tool_ids = ["server:mcp:search", "server:mcp:delegate"]
    messages = [{"role": "user", "content": user_prompt}]

    for step in range(1, 9):
        payload = {
            "model": model,
            "messages": messages,
            "tool_ids": tool_ids,
            "metadata": {"tool_ids": tool_ids},
            "stream": False,
        }
        status, body, _ = http_post(BASE + "/api/chat/completions", payload, token)
        if status != 200:
            sys.exit(f"chat failed: {status} {body[:400]}")
        res = json.loads(body)
        message = res["choices"][0]["message"]
        finish = res["choices"][0].get("finish_reason")
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            print(f"[step {step}] finish={finish}")
            print(message.get("content") or "")
            break

        print(f"[step {step}] tool_calls: {len(tool_calls)}")
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"].get("arguments") or "{}")
            print(f"  -> {name} args={json.dumps(args, ensure_ascii=False)[:160]}")
            if name.startswith("search_"):
                result = mcp_call(SEARCH_MCP_URL, search_token, name[len("search_"):], args)
            elif name.startswith("delegate_"):
                result = mcp_call(DELEGATE_MCP_URL, delegate_token, name[len("delegate_"):], args)
            else:
                result = json.dumps({"error": f"unknown tool {name}"})
            print(f"     result_chars={len(result)}")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
    else:
        print("stopped: iteration limit")


if __name__ == "__main__":
    main()
