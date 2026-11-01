---
id: seed-network-privacy
title: "Сетевые границы и приватность"
type: reference
status: active
sources: ["docker-compose.yml", "delegate/app.py", "README.md", "repo@6039cf4e795a3e7b50a5a82e35d469815eb87616 (dirty working tree, 2026-11-01)"]
---

# Сетевые границы и приватность

**Статус:** реализовано; границы подтверждены compose и кодом.

| Сеть | Кто | Интернет |
|---|---|---|
| `llm_internal` (internal) | llama-server, OWUI, delegate, litellm, searxng, search-mcp, embeddings, study | нет |
| `edge` | socat-форвардеры host-портов | нет |
| `egress` | litellm, searxng, search-mcp, byparr, tor (profile) | да |

- Host-порты только `127.0.0.1`.
- llama-server/OWUI/delegate не имеют прямого egress.
- delegate санитайзит задачу локальной моделью; в облако уходят обезличенная задача и публичные материалы; journal `logs/outbound` (не коммитить).
- Секреты — только в `.env`/env, не в заметках; имена переменных документируются, значения нет.
- Экспериментальный notes: same-origin proxy; notes не получает `WEBUI_SECRET_KEY`; отдельные internal/agent tokens; loopback 8089 диагностический.

Ограничение: локальный loopback не является multi-user auth; notes MVP single-user.
