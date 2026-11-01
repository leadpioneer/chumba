---
id: seed-experiment-notes-mvp
title: "Эксперимент: notes MVP"
type: experiment
status: active
sources: ["docs/experiments/mvp-results.md", "docs/decisions/0001-linked-notes.md", "experiments/notes-mvp/e2e_notes.py"]
---

# Эксперимент: notes MVP

**Статус:** experimental MVP, проверен runtime; рабочий image/стек не заменены.

E2E:

1. user создал project note;
2. agent через MCP notes tools нашёл/прочитал и создал связанную experiment note;
3. user изменил значение;
4. agent со stale revision получил `conflict` с current revision;
5. agent перечитал и корректно обновил;
6. backlinks/graph соответствуют ссылкам; history/diff/restore работают;
7. notes service restart сохранил данные;
8. unauthorized direct/proxy access → 401.

UI: `/memory`, sidebar, edit/preview, links, backlinks, graph, history/diff/restore; deep link из чата открывается в новой вкладке. Синтетический volume `chumba-notes-data` сохранён.

Ограничения: single-user, delete/bulk нет, external file editing не поддерживается, auto memory нет. См. [[seed-notes-service|notes service]], [[seed-ui-patch|UI patch]].
