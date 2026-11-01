---
id: seed-tools-mcp
title: "Инструменты, MCP и delegate"
type: reference
status: active
sources: ["docker-compose.yml", "delegate/app.py", "study/app.py", "experiments/notes-mvp/docker-compose.yml"]
---

# Инструменты, MCP и delegate

**Статус:** search/delegate/study — реализованы в рабочем стеке; notes MCP — экспериментальный.

- `search-mcp`: MCP Streamable HTTP; `searxng_web_search`, `web_url_read`; Bearer `SEARCH_MCP_TOKEN`; лимит чтения `URL_READ_MAX_CHARS`.
- `delegate`: MCP tool `delegate_to_worker(task, urls, max_sources)`. Локально ищет/читает, санитайзит задачу локальной моделью и делает один запрос worker; возвращает JSON ≤ `WORKER_MAX_RESULT_TOKENS`; пишет исходящее в `logs/outbound`.
- `study`: MCP tools для учебных тем/плана/повторений/прогресса; SQLite в `study-data`; egress нет.
- `notes` (экспериментальный): `search_notes`, `read_note`, `create_note`, `edit_note(expected_revision)`, `get_backlinks`; Bearer agent token; same service also serves `/api` for UI.

OWUI MCP connections добавляются администратором через `scripts/webui_add_mcp.py`; список зависит от окружения. В рабочем compose notes MCP отсутствует.

Связанные: [[seed-search-embeddings|поиск]], [[seed-network-privacy|сеть]], [[seed-notes-service|notes]].
