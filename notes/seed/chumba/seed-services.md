---
id: seed-services
title: "Сервисы и порты"
type: reference
status: active
sources: ["docker-compose.yml", "README.md", "repo@6039cf4e795a3e7b50a5a82e35d469815eb87616 (dirty working tree, 2026-11-01)"]
---

# Сервисы и порты

**Статус:** реализовано; таблица отражает текущий `docker-compose.yml`.

| Сервис | Роль | Host-порт |
|---|---|---|
| `open-webui` | интерфейс, чаты, RAG, MCP | `127.0.0.1:3000` |
| `llama-server` | orchestrator, router | `127.0.0.1:8080` |
| `searxng` | поиск | `127.0.0.1:8888` |
| `litellm` | OpenAI-фасад `worker`, `worker-local`, `orchestrator` | `127.0.0.1:4000` |
| `search-mcp` | `searxng_web_search`, `web_url_read` | `127.0.0.1:8085` |
| `delegate` | `delegate_to_worker` | `127.0.0.1:8086` |
| `embeddings` | bge-m3, `/v1/embeddings` | `127.0.0.1:8087` |
| `study` | учебный прогресс, SQLite | `127.0.0.1:8088` |
| `byparr` | stealth-браузер для чтения | нет |
| `tor` | SOCKS для SearXNG, профиль `tor` | `127.0.0.1:9050` |

**Экспериментально, не в рабочем compose:** `chumba-notes` (8089), patched `chumba-notes-owui` (3001), trace (8091). Это отдельный `experiments/notes-mvp/docker-compose.yml`.

Все host-порты — loopback; сервисы в `llm_internal` не имеют прямого интернета. См. [[seed-storage-volumes|volumes]] и [[seed-operations|runbook]].
