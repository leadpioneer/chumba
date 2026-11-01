---
id: seed-chumba-map
title: "Chumba — карта проекта"
type: project
status: active
sources: ["README.md", "docker-compose.yml", "repo@6039cf4e795a3e7b50a5a82e35d469815eb87616 (dirty working tree, 2026-11-01)", "docs/experiments/mvp-results.md"]
---

# Chumba — карта проекта

> Стартовая точка для человека и агента. Это **не** автоматическая память: заметки читаются только явными tool calls.

**Статус:** рабочая конфигурация — 32K; 64K — эксперимент, не активен. Notes MVP и budget guard — экспериментальные.

## С чего начать

- [[seed-map-architecture|Карта: архитектура]]
- [[seed-map-operations|Карта: эксплуатация]]
- [[seed-map-experiments|Карта: эксперименты]]
- [[seed-map-memory|Карта: контекст и память]]

## Коротко о системе

Chumba — локальный LLM-стек на одной машине: Open WebUI → llama.cpp orchestrator (32K), плюс поиск/чтение, delegate к облачному worker, embeddings/RAG, MCP-сервисы и экспериментальный linked-notes service.

## Рабочее и экспериментальное

| Область | Рабочее | Экспериментальное |
|---|---|---|
| Backend | 32K llama.cpp router | 64K profile, не активен |
| OWUI | pinned 0.11.4 | patched image с `/memory`, не заменяет рабочий |
| Контекст | legacy rolling summary активен | built-in compaction + budget guard, не включены |
| Память | нет автоматической памяти | linked notes доступны через MCP tools и UI |

## Практические заметки

- [[seed-operations|Запуск, остановка, диагностика, откат]]
- [[seed-storage-volumes|Данные, volumes и backup]]
- [[seed-limitations|Ограничения и открытые задачи]]
- [[seed-decisions|Ключевые решения]]
