---
id: seed-architecture
title: "Архитектура Chumba"
type: reference
status: active
sources: ["README.md", "docker-compose.yml", "repo@6039cf4e795a3e7b50a5a82e35d469815eb87616 (dirty working tree, 2026-11-01)"]
---

# Архитектура Chumba

**Статус:** реализовано и используется; 64K/patched notes — отдельные эксперименты.

Chumba разделён на четыре слоя:

1. **Интерфейс:** Open WebUI на `127.0.0.1:3000` (рабочая версия pinned 0.11.4).
2. **Локальный inference:** llama.cpp router на `llama-server:8080`, модель `orchestrator`; 32K — рабочее окно.
3. **Инструменты:** MCP search/read, delegate, study; embeddings для RAG.
4. **Память:** экспериментальный notes-сервис с Markdown + SQLite FTS5, доступный через UI и MCP.

Изоляция: `llm_internal` — internal-only; `egress` — только сервисы, которым нужен интернет; host-порты публикуются через socat на loopback. Подробнее: [[seed-network-privacy|Сетевые границы]].

Связанные заметки:

- [[seed-services|Сервисы и порты]]
- [[seed-request-path|Путь запроса]]
- [[seed-inference|Локальный inference]]
- [[seed-tools-mcp|Инструменты и MCP]]
- [[seed-context|Управление контекстом]]
- [[seed-notes-service|Linked-notes service]]
