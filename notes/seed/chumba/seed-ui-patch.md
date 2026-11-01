---
id: seed-ui-patch
title: "UI и OWUI patch"
type: reference
status: active
sources: ["experiments/notes-mvp/owui-memory.patch", "experiments/notes-frontend/README.md", "docs/experiments/mvp-results.md"]
---

# UI и OWUI patch

**Статус:** experimental image; рабочий image не заменён. База — OWUI 0.11.4, revision `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`.

Patch добавляет:

- `/memory` и `/memory/<id>`;
- sidebar entry;
- same-origin proxy `/api/v1/memory` с проверкой OWUI user;
- список/поиск, edit/preview, links, backlinks, history/diff/restore, depth-1 graph;
- seed badge and overview map (этот этап).

Patch: `experiments/notes-mvp/owui-memory.patch`, SHA-256 `474fc38b...b13df8`; `git apply --check` OK. Experimental image `chumba-open-webui-notes:exp`.

Рабочий OWUI 0.11.4 остаётся без patch; обновление до 0.12.5 не выполнялось. Deep link из чата в рабочем UI не существует; в experimental открывается в новой вкладке (или same-tab после финального patch).
