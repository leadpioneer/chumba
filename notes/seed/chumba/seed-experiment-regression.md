---
id: seed-experiment-regression
title: "Эксперимент: context regression + guard"
type: experiment
status: active
sources: ["docs/experiments/context-regression-results.md", "docs/experiments/context-memory-guards.md", "docs/experiments/context-memory-policy.md"]
---

# Эксперимент: context regression + guard

**Статус:** experimental; рабочая политика не включена.

Проверено:

- реальный streaming tool loop (basic/multi/error) — tool call → result → следующий запрос;
- built-in compaction: system/last user, точные значения/статусы, два checkpoint, branch isolation, tool group after compaction;
- старая tool-группа в checkpoint сохраняется в summary;
- guard: oversized → видимый 413, tool overflow → видимый отказ, normal loop проходит;
- regenerate via same-user-message создаёт новый ответ;
- legacy A как control теряет system/ранние значения; не рекомендуется.

Не реализовано: summary preflight, reduction-check, exact template token count; guard — conservative estimate. См. [[seed-compaction-guard|compaction/guard]], [[seed-limitations|ограничения]].
