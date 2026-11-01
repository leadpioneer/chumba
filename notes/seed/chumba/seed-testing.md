---
id: seed-testing
title: "Тестирование"
type: reference
status: active
sources: ["notes/test_storage.py", "experiments/context-memory/test_*.py", "scripts/bench_context.py", "docs/experiments/mvp-results.md"]
---

# Тестирование

**Статус:** unit/mock и runtime проверки выполнены для experimental scope; полная матрица не запускалась.

- Context/guard: 42 unit tests (`experiments/context-memory/test_*.py`).
- Notes storage: 13 tests (`notes/test_storage.py`) — revisions, conflict, idempotency, crash recovery, reindex, path traversal.
- Runtime: 32K tool loop, guard oversized/tool overflow, two checkpoints, old tool group, notes agent E2E, restart, unauthorized 401.
- Browser: `/memory`, search? (UI search field present), note edit, diff/restore, backlinks, graph, deep link, screenshots.
- Benchmarks: `scripts/bench_context.py`, `testdata/context_bench`.
- Notes seed: validation/import idempotency; run `python notes/seed/chumba/import_seed.py --validate-only`.

Не запускались: 64K в этом этапе, full matrix, multi-user, adversarial summary. См. [[seed-experiment-regression|regression]], [[seed-experiment-notes-mvp|notes E2E]].
