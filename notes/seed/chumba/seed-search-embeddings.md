---
id: seed-search-embeddings
title: "Поиск, чтение и эмбеддинги"
type: reference
status: active
sources: ["docker-compose.yml", "searxng/settings.yml.template", "scripts/embed_check.py", "scripts/kb_check.py", "README.md"]
---

# Поиск, чтение и эмбеддинги

**Статус:** реализовано.

- SearXNG (`:8888`) — JSON-поиск; шаблон `searxng/settings.yml.template`; init-контейнер рендерит конфиг; опционально Tor через `SEARXNG_PROXY`.
- `search-mcp` — MCP-обёртка; `web_url_read` через Byparr (stealth browser); лимит `URL_READ_MAX_CHARS=8000`.
- delegate — поиск + чтение + облачный worker; результаты и источники сжимаются.
- `embeddings` — CPU llama.cpp с `bge-m3-Q8_0.gguf` (MIT, multilingual), OpenAI `/v1/embeddings`.
- OWUI RAG: `RAG_EMBEDDING_ENGINE=openai`, `RAG_OPENAI_API_BASE_URL=http://embeddings:8080/v1`, `RAG_TOP_K=4`; Chroma в `open-webui-data`.
- Проверки: `scripts/embed_check.py`, `scripts/kb_check.py`.

Ограничения: качество retrieval зависит от документов/чанкинга; embeddings не используются для memory notes; notes MVP использует FTS5, не embeddings. См. [[seed-network-privacy|приватность]].
