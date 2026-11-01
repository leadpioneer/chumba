---
id: seed-routing
title: "Маршрутизация и внешние провайдеры"
type: reference
status: active
sources: ["litellm/config.yaml", "docker-compose.yml", ".env.example", "delegate/app.py"]
---

# Маршрутизация и внешние провайдеры

**Статус:** реализовано.

LiteLLM (`litellm/config.yaml`) предоставляет OpenAI-совместимые алиасы:

- `worker` — облачный OpenAI-совместимый роутер; URL/ключ/модель только из `.env`.
- `worker-local` — тот же llama-server; переключение delegate без правок кода.
- `orchestrator` — локальный llama-server.

`delegate` по умолчанию ходит в `litellm:4000/v1` с `DELEGATE_WORKER_MODEL`; `worker-local` — документированная замена для локального worker. Внешний ключ видит только контейнер `litellm`, не OWUI/llama.

Секреты не документируются значениями: имена — `WORKER_*`, `LITELLM_MASTER_KEY`. См. [[seed-network-privacy|приватность]], [[seed-tools-mcp|delegate]].
