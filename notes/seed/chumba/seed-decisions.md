---
id: seed-decisions
title: "Ключевые решения"
type: decision
status: active
sources: ["docs/decisions/0001-linked-notes.md", "docs/experiments/context-memory-policy.md", "docs/experiments/context-memory-guards.md", "notes/seed/chumba/import_seed.py"]
---

# Ключевые решения

Статусы: **accepted/implemented experimental**, **candidate**, **not active**.

- **Compaction:** built-in `chat.context_compaction` — кандидат вместо legacy filter; рабочая политика не переключена. См. [[seed-compaction-guard|compaction/guard]].
- **Notes storage:** own Markdown + SQLite FTS5, а не Basic Memory; причина — файлы, контроль revisions/idempotency, без AGPL/тяжёлого MCP-стека. См. [[seed-notes-service|notes service]].
- **Notes UI:** native Svelte route `/memory`, а не chat-iframe; same-origin OWUI proxy, notes не получает `WEBUI_SECRET_KEY`. См. [[seed-ui-patch|UI patch]].
- **Agent tools:** один MCP-интерфейс для notes; no parallel OpenAPI layer. См. [[seed-tools-mcp|tools]].
- **Guard:** experimental request filter, fail-closed on unknown/mismatched window; no truncation. Summary preflight/reduction — отдельное решение. См. [[seed-limitations|ограничения]].

Не считать решения из этого списка работающими в рабочем стеке, если это явно не указано.
