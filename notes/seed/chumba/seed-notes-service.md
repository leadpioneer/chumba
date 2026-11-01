---
id: seed-notes-service
title: "Linked-notes service"
type: reference
status: active
sources: ["notes/storage.py", "notes/app.py", "docs/decisions/0001-linked-notes.md", "docs/experiments/mvp-results.md"]
---

# Linked-notes service

**Статус:** experimental MVP, не в рабочем compose.

- Источник истины: Markdown-файлы в `chumba-notes-data`; имена `<id>.md`.
- Индекс: SQLite FTS5, восстановимый `reindex`.
- Revisions: immutable snapshots; restore создаёт новую revision.
- Concurrency: `expected_revision` SHA-256; idempotency key + payload hash.
- Links: wiki-link вида `ID|title`; backlinks вычисляются; depth-1 graph API.
- API: same-origin OWUI proxy `/api/v1/memory`; notes service internal token; MCP agent token.
- MCP: `search_notes`, `read_note`, `create_note`, `edit_note`, `get_backlinks`.
- UI: `/memory`, `/memory/<id>`; seed docs imported from `notes/seed/chumba`.

Ограничения: single-user, external file editing не поддерживается, delete/bulk нет, нет embeddings/auto memory. См. [[seed-ui-patch|UI patch]], [[seed-storage-volumes|storage]], [[seed-limitations|ограничения]].
