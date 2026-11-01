---
id: seed-inference
title: "Локальный inference и профили"
type: reference
status: active
sources: ["llama/models.ini", "docker-compose.yml", ".env.example", "docs/experiments/context-expansion.md", "repo@6039cf4e795a3e7b50a5a82e35d469815eb87616 (dirty working tree, 2026-11-01)"]
---

# Локальный inference и профили

**Рабочая конфигурация:** `ORCHESTRATOR_CTX=32768`, router, `-np 1`, KV q4_0, `--sleep-idle-seconds` (значение в `.env`).

Модель: `Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS` (GGUF в `models/`), hybrid Qwen, tools через Jinja. Параметры и измерения: `docs/experiments/context-expansion.md`.

**Экспериментальный 64K:** отдельный `experiments/context-profiles/docker-compose.context-exp.yml`, порт 8090. В отчёте 64K проверен, стабилен, но **не активен** в рабочей конфигурации. Не выдавать его за рабочее окно.

Sleep: llama-server выгружает модель при простое; `/health`, `/props`, `/models` не будят. Первый запрос после сна ждёт загрузку (десятки секунд).

Проверки профиля:

- `GET /props?model=orchestrator` — подтверждённое окно и `is_sleeping`.
- `scripts/bench_context.py` — perf/retrieval/multi_turn/tools.
- только один GPU-backend одновременно.

Связанные: [[seed-context|контекст]], [[seed-operations|runbook]], [[seed-experiment-context-expansion|эксперимент]].
