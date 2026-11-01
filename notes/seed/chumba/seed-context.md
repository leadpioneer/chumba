---
id: seed-context
title: "Управление контекстом"
type: reference
status: active
sources: ["openwebui/filters/chumba_summary_filter.py", "docs/experiments/context-memory-policy.md", "docs/experiments/context-expansion.md", "docs/experiments/context-regression-results.md"]
---

# Управление контекстом

**Рабочая конфигурация:** 32K backend + legacy `chumba_summary` filter. Valves рабочего фильтра: `threshold_tokens=12000`, `keep_last_messages=6`, `summary_max_tokens=400`, `max_input_chars=30000`, fail-open.

Важное уточнение аудита: `threshold_tokens` — не точные токены; в router-режиме `/tokenize` вызывается без `model`, поэтому фактически используется `len(text)//4`; summary-вход обрезается до 30K символов. Legacy filter теряет system prompt и раннее состояние — **не рекомендуется** как безопасная политика.

**Кандидат:** встроенное `chat.context_compaction` OWUI, но оно **выключено** в рабочей конфигурации и не включено после экспериментов. Экспериментальная политика и guard — отдельно; см. [[seed-compaction-guard|compaction/guard]].

Слои контекста: 32K окно, summary, RAG top-k, delegate-сжатие. Backend не резервирует output budget; при переполнении — HTTP 400, без тихого усечения.

Ограничения: summary preflight/reduction не реализованы; 64K не активен. См. [[seed-limitations|ограничения]].
