# Short 32K regression plan (approval required before starting)

Scope: fix and verify the real tool loop, exercise the policy helper, and run a
short structural regression. No 64K, no full matrix, no working-policy change.

## Services, ports, volumes

| Resource | Purpose | Exposure | Persistent? |
|---|---|---|---|
| `chumba-llama-server` (existing working compose service) | 32K backend, `orchestrator` | internal `chumba_llm_internal` only; no host 8080 port | working volume untouched; container stopped after |
| `chumba-exp-trace` (temporary) | metadata-only HTTP trace; classifies summary/main; records usage/timings | `127.0.0.1:8091` + experimental network | host `benchmark-results/context-memory/traces` (git-ignored) |
| `chumba-exp-owui` (temporary) | isolated Open WebUI, empty volume, synthetic admin | `127.0.0.1:3001` + experimental network | temporary named volume `chumba-exp-owui-data` |
| probe filter + probe tool | request-hook log and deterministic `probe`/`probe_fail` | installed via supported APIs into temp OWUI only | removed with temp volume |

No 64K container, no working Open WebUI DB, no working `.env`/compose edits, no
new images/dependencies.

## Test sequence

1. Start only `llama-server`; confirm `GET /props?model=orchestrator` returns
   `n_ctx=32768`.
2. Start temporary trace + OWUI; synthetic signup; verify `/api/models`.
3. Run `scripts/apply_context_policy.py` in dry-run against the temp instance,
   then apply. Verify readback and inspect the rollback file. This tests the
   helper against real supported APIs, not just mocks.
4. Install the probe filter/tool via supported APIs. Confirm no legacy filter is
   active and built-in compaction is enabled (no conflict).
5. **Tool loop first:** send with `stream=true` (the UI path). Required trace:
   `user -> assistant tool_calls -> tool execution -> tool result -> second
   backend request -> final answer`. Test basic, two calls, and
   error/correction. Inspect saved assistant `output` items, probe JSONL, and
   trace requests. If no execution, stop and diagnose; do not run the rest.
6. Short structural regression (synthetic only):
   - system instructions survive;
   - last user message survives;
   - exact IDs/values and `proposed/executed/verified` statuses;
   - two sequential checkpoints (including previous summary merge);
   - real tool loop and tool-group integrity after compaction;
   - branch and regeneration isolation;
   - oversized single message: explicit error, no silent truncation;
   - non-reducing summary: diagnostic/failure, not success;
   - insufficient budget: clear refusal with window/reserve/reason;
   - legacy A only as an isolated control if needed.
7. Stop temp compose with `down -v` (removes only the temporary volume),
   stop `llama-server`, verify no running containers. Keep the working volumes
   and settings untouched.

## Duration

| Step | Estimate |
|---|---:|
| Start backend + temp services | 5–10 min |
| Helper dry-run/apply/verify | 2–3 min |
| Tool-loop reproduction (3 variants) | 5–10 min |
| Structural regression (8–12 turns) | 15–25 min |
| Stop/cleanup | 2–3 min |
| **Total** | **30–50 min** |

Summary turns at ~60K chars previously took ~20–35 s each; the estimate includes
model load and possible retries.

## Stop conditions

Stop and report diagnostics if:

- built-in compaction loses permanent instructions;
- the last user request disappears;
- a tool group is split;
- a branch/regression mixes checkpoints;
- a tool instruction is promoted to a user requirement;
- the same HTTP 400 recurs for an identified structural reason;
- swap/memory pressure appears or a container OOM/restarts.

Do not repeat an expensive case until the identified structural cause is fixed.

## Approvals needed

1. Start `chumba-llama-server` and the temporary trace/OWUI containers.
2. Publish only `127.0.0.1:3001` and `127.0.0.1:8091`; use the existing
   experimental Docker network.
3. Create/remove `chumba-exp-owui-data` and the temporary compose project.
4. Apply the experimental policy to the **temp** OWUI via the helper and install
   the probe filter/tool via supported APIs.
5. Run the ~30–50 minute regression, then stop and remove temp resources.
