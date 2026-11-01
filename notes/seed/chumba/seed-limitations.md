---
id: seed-limitations
title: "Ограничения и открытые задачи"
type: issue
status: active
sources: ["docs/experiments/context-memory-policy.md", "docs/experiments/context-memory-guards.md", "docs/experiments/mvp-results.md", "docs/decisions/0001-linked-notes.md"]
---

# Ограничения и открытые задачи

**Рабочая конфигурация vs экспериментальная**

- 32K — рабочее окно; 64K — эксперимент, не активен.
- Legacy rolling summary активен и имеет подтверждённые дефекты (system/раннее состояние); built-in compaction — кандидат, не включён.
- Budget guard — экспериментальный `request` filter; summary preflight/reduction отсутствуют.
- Patched OWUI `/memory` — experimental image; рабочий OWUI 0.11.4 без patch.
- Notes доступны через MCP tools, **не** автоматически загружаются в контекст.

**Notes**

- single-user; нет delete/bulk/multi-user; external file editing не поддерживается.
- Chat deep link открывает новую вкладку.
- Depth-1 graph; typed relations and global graph не реализованы.
- `chumba-notes-data` содержит demo/test заметки; не удалены.

**Context**

- Guard estimate, не exact template tokens; `/props` load failure → safe 503.
- 64K/full matrix не запускались в этом этапе.
- Summary semantics/required markers — fixture oracle, не общая гарантия.

См. [[seed-context|context]], [[seed-compaction-guard|guard]], [[seed-notes-service|notes]].
