# Budget-guard runtime verification plan (approval required)

Scope: verify that the experimental `request` guard produces a usable refusal on
the real streaming UI path, and does not break normal requests. No 64K, no full
matrix, no working-policy change, no image upgrade.

## Resources

| Resource | Image/build | Ports | Volume |
|---|---|---|---|
| `chumba-llama-server` | existing pinned image; no build | none to host | working volume untouched |
| `chumba-exp-trace` | existing OWUI 0.11.4 digest; no build | `127.0.0.1:8091` | trace logs in `benchmark-results/context-memory/traces` (ignored) |
| `chumba-exp-owui` | existing OWUI 0.11.4 digest; no build | `127.0.0.1:3001` | temporary `chumba-exp-owui-data` |
| guard filter | installed via supported functions API into temp OWUI | — | temporary DB/volume |

No image downloads/builds, no new images, no 64K.

## Guard setup

Install `experiments/context-memory/budget_guard_filter.py` as an active/global
`request` filter in the temp OWUI. Valves:

```text
window=32768
output_reserve=1024
tool_reserve=1024
margin=1024
props_url=http://llama-server:8080
tokenizer_url=http://llama-server:8080
verify_backend_window=true
model=orchestrator
template_overhead_per_message=8
template_overhead_fixed=256
fallback_chars_per_token=0
```

Keep built-in compaction enabled through the helper. Do not enable
`chumba_summary`.

## Test sequence

1. **Normal near-boundary request**: history sized just below
   `window - output_reserve - tool_reserve - margin`; expect a normal answer.
2. **Oversized current message**: send one message that exceeds the budget.
   Expected: the guard refuses before inference and the user sees a clear reason;
   no HTTP 200 with an empty answer. Record the exact UI rendering and HTTP
   payload.
3. **Large tool result in a real tool loop**: deterministic probe tool returns a
   large result; first inference executes the tool, second inference is refused
   by the guard. Verify the tool executed once and is not reported as rolled
   back; the user sees a clear refusal.
4. **Normal streaming tool loop**: basic, multi and error/recovery under budget;
   verify calls/results and final answer still work.
5. **Old completed tool group**: a prior tool group is compacted, then a new
   request passes; verify no split/conflict.
6. **Two checkpoints**: summary/system/last user remain correct with guard
   enabled.
7. **Regenerate**: use the UI regenerate action after a successful turn; verify
   it is not blocked by the guard and does not duplicate side effects.
8. **Secret/content non-disclosure**: error text must contain only counts,
   window, reserves, reason and method; no message content or token.

## Decision gate

The guard is acceptable only if the user sees a clear refusal (not a generic
silent failure). If OWUI converts the filter exception to a generic 500 or an
empty success, the in-process guard is not sufficient; document the gap and
evaluate a small external proxy or a minimal error-contract change separately.
Do not silently add a proxy in this step.

## Summary preflight/reduction

Not part of this runtime:

- no in-process hook before built-in compaction;
- no runtime reduction check;
- a persistent proxy is a separate cost/decision.

## Duration and cleanup

- setup: 5–10 min;
- tests: 15–25 min;
- cleanup: 2–3 min;
- total: ~25–40 min.

Stop `chumba-exp-owui`/`chumba-exp-trace`, remove `chumba-exp-owui-data` and the
temporary network, stop `chumba-llama-server`; verify no running containers.
Working `.env`, compose, DB, volumes and policy remain unchanged; no commit.
