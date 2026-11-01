---
id: seed-storage-volumes
title: "Данные, volumes и backup"
type: reference
status: active
sources: ["docker-compose.yml", "notes/storage.py", "docs/experiments/mvp-results.md", "repo@6039cf4e795a3e7b50a5a82e35d469815eb87616 (dirty working tree, 2026-11-01)"]
---

# Данные, volumes и backup

**Рабочие данные:**

- `open-webui-data` — OWUI DB, настройки, RAG/Chroma, filters.
- `study-data` — SQLite учебного прогресса.
- `searxng-data` — кэш SearXNG.
- `logs/outbound` — журнал исходящего delegate (host bind, не коммитить).
- `models/` — GGUF (host bind, не коммитить).

**Экспериментальные:**

- `chumba-notes-data` — сохранённый синтетический notes-volume: `notes/*.md`, `revisions/<id>/*.md`, `notes.db` (FTS index).
- Временный `chumba-notes-owui-data` удалён после проверки.

Backup notes: остановить notes service → скопировать volume (Markdown + revisions + notes.db) → при восстановлении вернуть volume; `notes.db` можно пересобрать из Markdown. Backup OWUI/study — соответствующие volumes; не копировать `.env`/секреты в заметки.

См. [[seed-operations|runbook]], [[seed-limitations|ограничения]].
