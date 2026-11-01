# Context guards: extension-point map

Pinned Open WebUI: `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`.

This map is intentionally conservative: an extension point is only listed as a
guard if it can see the relevant data and stop the request. "Can stop" is based
on code; the HTTP error shape still needs runtime verification.

## 1. Summary preflight

| Check | Supported point | Visible data | Can stop? | Limitations |
|---|---|---|---|---|
| Summary request fits `W - summary_max - margin` | none inside OWUI | The built-in compaction runs in `process_chat_payload` before `inlet`/`request`; no pre-compaction filter hook | No | Cannot be implemented as a normal filter. A preflight must be an external proxy or a config/helper check before the turn. |
| Exact summary input size | trace proxy | Full HTTP body of the summary request (`POST /v1/chat/completions` with the compaction template) | Yes, 413 before the backend | External service in the request path; it must know the summary/model and must not log bodies. |
| Estimated summary input size | helper | Config + compacted message estimate | No, only before the turn | The compacted set is chosen inside OWUI; helper cannot know the boundary. |
| Lower trigger to avoid oversize | `compact_token_threshold` / `token_cap` | Threshold config | Yes, indirectly | `token_cap` caps the threshold only, not the final prompt. It cannot guarantee a summary request fits. |

Conclusion: there is **no supported in-process preflight hook** for the built-in
summary request. A real preflight is either an external HTTP proxy or a user-
triggered manual compact with a precheck. Do not claim an `inlet` filter can do it.

## 2. Summary reduction post-check

| Check | Supported point | Visible data | Can stop? | Limitations |
|---|---|---|---|---|
| Summary input/output tokens | trace proxy | Summary request prompt + response usage/timings | Yes, for the summary HTTP call; cannot prevent the later main call unless it also blocks it | External; needs request classification. |
| Main prompt reduced | `request` filter or proxy | Final outbound messages/system summary | `request` can raise; proxy can return 413 | `request` sees only the post-compaction system+recent, not the pre-compaction input, so it cannot prove reduction by itself. Proxy can compare tracked prompt tokens. |
| Summary contains required facts | none automatic | Summary checkpoint via chat API/DB | No inside OWUI | A checker can run after the fact and flag loss; it cannot undo the stored checkpoint. |
| Summary is not longer than compacted input | none inside OWUI | — | No | Built-in compaction stores the summary without a size comparison. A proxy can compare prompt/response sizes. |

Conclusion: reduction validation is a **post-hoc/external** property. The only
supported in-process response is to reject the final request if it is over
budget, not to prove the summary was good.

## 3. Final outbound budget

| Check | Supported point | Visible data | Can stop? | Limitations |
|---|---|---|---|---|
| Path/endpoint | `chumba_summary_filter.py:117` `inlet` | `body["messages"]` before tools/RAG | Yes: exceptions are re-raised (`utils/filter.py:200-205`) | Does not see tools schema, RAG, web search, memory. Not the final prompt. |
| Path/endpoint | `request` filter, `utils/middleware.py:3263` and tool-loop `:6286`, `:6507` | Final `form_data` messages and `tools` after RAG/tools; tool results on each iteration | Yes: filter exception is re-raised | Library/utility errors may surface as a generic request error; no structured 413 unless the filter raises an accepted HTTP error type. Exact count requires `/tokenize`; otherwise estimate. |
| Path/endpoint | trace proxy | Exact HTTP request body and backend response usage | Yes, return 413/400 with explicit detail | External service; must be in the path; must not log content. |
| Path/endpoint | `outlet` filter | Response only | No, too late | Cannot prevent the request. |

Conclusion: `request` is the correct in-process guard for the **final outbound**
and for **each tool iteration**. The proxy is the only point that sees both the
summary request and the final request exactly.

## 4. Tool loop

| Check | Supported point | Visible data | Can stop? | Limitations |
|---|---|---|---|---|
| Per-iteration messages/tools | `request` filter at `:6286`, `:6507` | New form data after tool results are appended; final tools schema | Yes, by raising | Must be verified through the streaming UI path; previous `stream=false` test did not execute tools (`non_streaming_chat_response_handler` has no tool loop). |
| Mid-loop compaction | none | — | No | Built-in compaction runs once in `process_chat_payload` (`:2497`). No automatic compaction inside the loop. |
| Tool result size limits | tool server / MCP | Tool return value | Yes, at the tool boundary | Applies only to tools that enforce it; not a general loop budget. OWUI's loop does not truncate arbitrary tool results. |
| Approval pause | `tool_approval_mode=ask` | Tool calls before execution | Yes, pauses | Not a budget guard; `full` executes without approval. |

Conclusion: a per-iteration budget guard is possible as a `request` filter, but
it can only **reject**; it cannot compact or truncate tool results. A separate
approval/truncation policy would be required for automatic recovery.

## 5. Oversized single message

| Check | Supported point | Visible data | Can stop? | Limitations |
|---|---|---|---|---|
| One message alone exceeds budget | `request` filter / proxy | Last user message / final messages | Yes, explicit error | Built-in compaction cannot summarize the current user message; backend would otherwise return 400. |
| Old message exceeds summary input | none inside OWUI | — | No | Built-in compaction has no pre-summary size check; a proxy could reject the summary request. |

Conclusion: oversized **current** input can be rejected by `request`/proxy.
Oversized **compaction input** cannot be preflighted in-process.

## 6. Unknown window / profile mismatch

| Check | Supported point | Visible data | Can stop? | Limitations |
|---|---|---|---|---|
| `W` from `/props?model=` | helper/apply | Backend JSON | Yes, apply refuses | Must use `?model=`; router `/props` without model returns a dummy `n_ctx=0`. |
| Profile/backed mismatch | helper/apply | Profile config + backend JSON | Yes, error | Does not affect an already-running OWUI that has no budget check. |
| Conflict legacy filter + built-in | helper/apply + API readback | Function state + chat config | Yes, apply refuses | Must also be checked at runtime before enabling; helper is the gate. |

## 7. Error semantics

- A guard must return a visible error with: configured window, budget, observed
  or estimated tokens, reason, and whether the count is exact or estimated.
- No silent truncation, no automatic removal of tool results, no retrying the
  same oversized request.
- `request` filter exceptions are re-raised, but the resulting HTTP shape must be
  tested. If OWUI returns a generic 500, the guard should be documented as
  "stops but not user-friendly" and a proxy/middleware response may be needed.
- The proxy can return a structured 413 with a JSON body; that is the only point
  with a clear error contract without frontend work.

## 8. Implemented vs proposed

Implemented locally (no services):

- pure budget/profile/conflict/tool-group/no-reduction checks in
  `experiments/context-memory/policy.py`;
- helper `scripts/apply_context_policy.py` with dry-run/apply/rollback and
  verification;
- summary contract in `experiments/context-memory/summary_policy.py`.

Not implemented:

- runtime verification of the experimental guard's user-visible error;
- a production proxy guard;
- automatic compaction inside the tool loop;
- any monkey patch of OWUI internals.

## 9. Implemented experimental request guard

File: `experiments/context-memory/budget_guard_filter.py`.

It is a supported `request` filter, not a proxy and not a monkey patch. It runs
before the main request and before each tool-loop iteration, sees the final
`messages` and `tools`, and only rejects; it never truncates or re-summarizes.

Valves:

- `window` — explicit, required;
- `output_reserve`, `tool_reserve`, `margin` — explicit;
- `props_url` / `verify_backend_window` — calls `/props?model=...` and refuses if
  the confirmed backend window differs from `window`;
- `tokenizer_url` / `model` — tokenizes text parts;
- `template_overhead_per_message`, `template_overhead_fixed` — explicitly
  estimated chat-template overhead;
- `fallback_chars_per_token` — disabled by default; if enabled, the error names
  it as a conservative fallback.

Counting method: **tokenizer over text parts plus estimated overhead**, not an
exact serialized chat-template count. The error string says this explicitly.
Unknown window or unavailable tokenizer fails closed. The error includes only
counts/window/reserves/reason, never message content or tokens. It notes that
already executed tools are not rolled back.

Unit/mock coverage exists. Real UI error rendering and HTTP shape are **not yet
verified**; that requires the runtime step. The `request` filter exception is
re-raised in code, but the visible message must be checked before claiming a
usable refusal.

## 10. Summary preflight and reduction: minimal options

No supported in-process hook exists before built-in compaction. Therefore:

- preflight summary size: only an external HTTP proxy (or a user-triggered manual
  precheck) can see and reject the summary request before the backend;
- reduction check: the `request` filter sees the post-compaction system+recent
  messages but not the pre-compaction input, so it cannot prove reduction; a
  proxy or a post-hoc checkpoint inspection is required;
- a persistent proxy adds a new request-path component, summary/main
  classification, auth, streaming, failure handling and rollback. It is not
  implemented and should be a separate decision with cost/alternatives;
- the final-request guard covers the outbound overflow risk, but not a wasted or
  non-reducing summary that still fits.

`REQUIRED_STATE_MARKERS` / `missing_required_markers` are a **synthetic fixture
oracle**, not a general semantic guarantee for user conversations. Do not use
them as a production acceptance criterion.


## 11. Runtime result (2026-11-01)

The guard was installed in the patched experimental OWUI and exercised through
the real `/api/chat/completions` UI path:

- normal request: estimated 461 tokens, budget 29696, passed;
- oversized current message: estimated 204363, budget 29696, HTTP 413 visible
  in the chat as `CONTEXT_BUDGET_EXCEEDED` with window/reserve/reason; no empty
  200;
- tool-result overflow: first request executed `huge_probe`; the next request
  was refused with estimated 36945 > budget 29696; the chat shows the error and
  the tool result remains visible (tool work is not rolled back);
- normal streaming tool loop: 2 backend requests, 1 call/1 result, DONE;
- two checkpoints under guard: 2 summaries, system/last user preserved, no
  orphan;
- regeneration via the same user message created a new assistant response
  without a guard error.

The guard is a conservative text-parts + estimated-overhead check, not an exact
serialized template count. Summary preflight/reduction remains outside this
implementation.
