---
id: seed-request-path
title: "Путь запроса"
type: reference
status: active
sources: ["README.md", "docker-compose.yml", "openwebui/filters/chumba_summary_filter.py", "docs/experiments/context-regression-results.md"]
---

# Путь запроса

**Статус:** реализовано; notes proxy — только в экспериментальном image.

Обычный чат:

1. Браузер → `127.0.0.1:3000` Open WebUI.
2. OWUI собирает историю, system prompt, RAG/tools; при длинной истории работает legacy `chumba_summary`.
3. Запрос идёт в `llama-server:8080/v1` (router, модель `orchestrator`).
4. Модель может вызвать MCP tools (`search`, `delegate`, `study`) или notes tools в экспериментальном image.
5. Tool results добавляются в историю; OWUI делает следующий inference-запрос.
6. Ответ возвращается в UI.

Важно:

- OWUI и llama-server не имеют egress.
- delegate локально ищет/читает и отправляет в облако только обезличенную задачу; см. [[seed-network-privacy|приватность]].
- RAG использует локальный bge-m3; см. [[seed-search-embeddings|поиск]].
- Экспериментальный notes path: браузер → `/api/v1/memory/...` в OWUI → same-origin proxy → notes service. OWUI проверяет пользователя; notes получает внутренний token, не `WEBUI_SECRET_KEY`.

Ограничение: в рабочем OWUI нет `/memory`; он появляется только в patched experimental image.
