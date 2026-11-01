---
id: seed-compaction-guard
title: "Compaction и budget guard"
type: reference
status: active
sources: ["docs/experiments/context-memory-policy.md", "docs/experiments/context-memory-guards.md", "docs/experiments/context-regression-results.md", "experiments/context-memory/budget_guard_filter.py"]
---

# Compaction и budget guard

**Статус:** experimental; рабочая политика не переключена.

- **Built-in `chat.context_compaction`** — выбранный кандидат. Проверено: сохраняет system, последний user, точные значения/статусы, branch isolation, реальный tool loop. Требует `enable_thinking=false`, summary template только с `{{COMPACTED_MESSAGES}}` + `{{PREVIOUS_SUMMARY}}`; `token_cap` ограничивает threshold, а не весь outbound.
- **Budget guard** — экспериментальный `request` filter: проверяет финальные messages/tools перед основным запросом и каждой итерацией tool loop; требует explicit window и сверяет `/props?model`; не усекает, не суммаризирует; при превышении даёт видимый `CONTEXT_BUDGET_EXCEEDED`.
- Проверено runtime: oversized, tool-result overflow, normal tool loop, два checkpoint, старая tool-группа, regenerate через same-user-message.
- Не реализовано: summary preflight, reduction-check. Guard считает text parts + estimated overhead, не exact chat template. `/props` failure даёт безопасный 503.

См. [[seed-experiment-regression|эксперимент]], [[seed-decisions|решения]], [[seed-limitations|ограничения]].
