---
id: seed-experiment-context-expansion
title: "Эксперимент: расширение контекста"
type: experiment
status: active
sources: ["docs/experiments/context-expansion.md", "experiments/context-profiles/README.md"]
---

# Эксперимент: расширение контекста

**Статус:** завершён; рабочая конфигурация не изменена.

- Рабочее окно: 32K (`ORCHESTRATOR_CTX=32768`), не 16K.
- 32K стабилен: retrieval 9/9, tools 2/2, история 28.4K.
- 64K стабилен на тестах: retrieval 3/3 на 25/50/85%, история 56K, tools 2/2; +~720 MiB VRAM, prefill/decode ниже на длинном конце.
- 64K не активен; отдельный профиль 8090.
- Basalt/Strata не подходят текущей dense/hybrid модели; повторно не исследовать.
- Главное ограничение — legacy summary, а не backend.

Вывод: 64K — подтверждённый экспериментальный профиль, не рабочая политика. См. [[seed-inference|inference]], [[seed-context|context]].
