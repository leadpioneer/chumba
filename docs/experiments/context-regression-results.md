# Short 32K regression and notes UI-spike results

Date: 2026-10-31  
Environment: 32K `llama-server`, temporary Open WebUI 0.11.4, empty temp volume, metadata-only trace proxy.  
No 64K, no full matrix, no working-policy change, no commit, no image update.

## 1. Tool loop: root cause and runtime result

Root cause of the previous failure: the test used `stream=false`. Open WebUI's
tool loop is inside `streaming_chat_response_handler`
(`utils/middleware.py:4431`, loop `:5891–:6155`); the non-streaming handler
(`:4211`) does not execute tools. This exactly explains the prior
`finish_reason=tool_calls` response with no second request.

The corrected harness uses `stream=true`, `tool_approval_mode=full`, and a real
deterministic tool. Runtime results:

| Scenario | Backend requests | Executed calls | Tool results | Request-hook calls | Second request after result | Final answer | Wall |
|---|---:|---:|---:|---:|---|---|---:|
| basic | 2 | 1 | 1 | 2 | yes | DONE | 9.6 s / 38.5 s after compaction |
| multi (2 calls) | 2 | 2 | 2 | 2 | yes | DONE | 3.1 s |
| error/recovery | 3 | 2 | 2 | 3 | yes | DONE | 3.4 s |
| basic after compaction | 2 | 1 | 1 | 2 | yes | DONE | 38.5 s |

Evidence:

- The `request` filter ran on every real backend request (2/2, 2/2, 3/3).
- On the second request it saw the tool result IDs and the final tools schema.
- Outbound tool IDs and result IDs matched; `orphan_tool_results=[]`.
- After compaction, the probe still saw `SYSTEM_MARKER`, the conversation
  summary, the tool schema, and the tool result; no group was split.
- The first basic run's JSON had an empty `answer` due to the harness reading
  the SSE envelope instead of the saved chat message; the saved assistant
  content was `DONE`. The harness now reads the final chat message.

Tool history is now **runtime-confirmed for the tested scenarios**, not just
unit/mock.

## 2. Structural regression

| Check | Result | Evidence level |
|---|---|---|
| Permanent system instructions survive | Yes | runtime outbound `system_has_expected_marker=true` |
| Last user message survives | Yes | runtime outbound `last_user_sha16` matched |
| Exact values/statuses survive two checkpoints | Yes | B0 values: ID, path, port, version, threshold, proposed/executed/verified preserved |
| Two sequential checkpoints | Yes | 1 summary + 1 summary across two turns; previous summary included in the new prompt |
| Tool groups after compaction | Yes in tested loop | real streaming loop, 2 requests, matching call/result, no orphan |
| Branch isolation | Yes | original summary contains only original marker; fork contains only fork marker |
| Regeneration | Not separately exercised | branch isolation covered; regeneration UI action not rerun |
| Oversized input | **No clear failure** | 200K-char user message returned HTTP 200/empty answer; no user-facing error |
| Non-reducing summary | **Not runtime-tested** | only `ensure_summary_reduces` unit/mock; built-in has no post-check |
| Budget refusal | Yes | 64K profile against 32K backend refused; config unchanged |

The oversized test is the main structural failure: the backend prompt was
200,830 chars; the request returned 200 with no answer and no visible error. The
user sees an empty response rather than a budget explanation. Do not repeat this
expensive case until a request/preflight guard is designed.

## 3. Config and helper

- `scripts/apply_context_policy.py` is implemented and tested.
- Dry-run is the default; `--apply` and `--rollback FILE` are explicit.
- It reads `W` only from `/props?model=...`; unknown window prohibits apply;
  64K profile against 32K backend is an error; active legacy filter blocks apply.
- It saves only compaction keys and task params in the rollback file; no tokens.
- It verifies settings by reading them back; on verification failure it restores.
- Real temp apply was verified; the rollback file was created under
  `benchmark-results/context-memory/`.
- Real 64K-profile apply against the 32K backend failed as intended and left the
  temp config unchanged.
- Settings written via API persisted across a temp OWUI restart; env values were
  not a second active source in this test.

Summary contract:

- `enable_thinking=false`, `temperature=0.1`, `max_tokens=500`.
- Template uses `{{COMPACTED_MESSAGES}}` and `{{PREVIOUS_SUMMARY}}`; it excludes
  `{{RECENT_MESSAGES}}` so the summarizer does not answer the final question.
- The previous summary is now carried into the next checkpoint.
- `missing_required_markers()` reports lost required facts instead of silently
  treating a lossy summary as success.

## 4. Is built-in compaction ready for a limited pilot?

**Conditionally, but not yet as a general pilot.**

It does provide:

- system prompt preservation;
- last user message preservation;
- exact values and statuses after two checkpoints;
- branch isolation;
- real tool-loop integrity after compaction;
- summary request fit in the tested 32K cases;
- a supported configuration path without a fork.

It does not yet provide:

- a preflight for the summary request (no in-process hook before compaction);
- a post-check that the summary actually reduced the input;
- a user-facing oversized-input error (observed silent empty answer);
- a final/request budget guard (not implemented);
- tool-loop compaction/truncation (only the request hook can reject);
- proof for 64K, adversarial injection, or multi-user access.

For a pilot, the minimum gate should be a `request`-level final/oversized guard
with a clear error and no automatic truncation. Until that exists, a user can
send an oversized message and receive an empty answer, which is not acceptable.
Summary content remains system-level and may contain tool text; a prompt sentence
is not a security boundary.

## 5. Guards: what is possible where

See `docs/experiments/context-memory-guards.md` for the full map.

- Summary preflight: no supported in-process point before built-in compaction;
  only an external proxy or a pre-turn helper/manual check.
- Reduction post-check: no in-process hook with pre-compaction input; a proxy or
  a post-hoc checkpoint check is required.
- Final outbound: `request` filter (`middleware.py:3263`) sees final messages and
  tools; it can stop by raising, but the HTTP error shape is not yet verified.
- Each tool iteration: `request` filter (`:6286`, `:6507`) is runtime-confirmed
  and sees tool results; it can reject but not compact.
- Oversized current message: `request`/proxy can reject; built-in compaction
  cannot summarize the current user message.
- `token_cap` is a threshold cap, not a final outbound limit.

No monkey patch and no proxy product were added.

## 6. Notes UI-spike

Mock page: `http://127.0.0.1:8091/spike/`, served by the temporary trace
container; two notes, internal links, textarea, Save to localStorage. Mock only,
no storage backend and no real auth.

| Check | Result |
|---|---|
| Native Notes iframe | **Stripped**: note data contained the iframe, but the Tiptap page showed 0 words and no iframe |
| Native Notes normal Markdown | Works (heading/paragraph rendered) |
| Chat Markdown iframe | Iframe renders, but generic iframe sandbox has no `allow-scripts`; console: "Blocked script execution ... sandboxed and 'allow-scripts' permission is not set" |
| Embedded save/navigation/keyboard | Not usable in the chat iframe because scripts are blocked |
| Static page standalone | Works: textarea focus, save, reload persistence, note A↔B link |
| Chat link to note | Opens a new browser tab, not the embedded panel |
| Save/reload inside chat iframe | No script execution; no save state |
| Loopback-only exposure | Yes, 8091; no CORS wildcard; no OWUI token in URL |
| Sanitization/CSP disabled globally | No |
| Privileged/unsafe plugin | No |

Artifacts:

- `benchmark-results/context-memory/ui-spike/native-note-iframe-stripped.png`
- `benchmark-results/context-memory/ui-spike/chat-iframe.png`
- `benchmark-results/context-memory/ui-spike/static-notes-standalone.png`

### Gate result

**B — possible only in a degraded form, not a good basis for the target UX.**

- Native Notes cannot host the iframe.
- A chat iframe renders but cannot run a notes app because scripts are sandboxed;
  it cannot save or switch notes.
- A normal chat link opens a new tab, which is explicitly not the target.
- A mock with no scripts could display static content, but not edit/save/backlinks.

Recommendation: a **minimal frontend route/sidebar patch** (Option C) is the
only way to get a real in-Chumba notes screen without relying on a new tab. Do
not build the patch yet; it requires reusing the pinned Svelte build, adding one
route/sidebar entry and an iframe/panel, and accepting upgrade/maintenance work.
Alternative to test before C: a supported file-content iframe
(`/api/v1/files/<id>/content/html`) with the OWUI iframe settings may allow
scripts, but it is still a chat/artifact surface, not a notes route, and was not
tested here.

## 7. Storage cost (not fixed)

Own Markdown + SQLite FTS5 is still a candidate, not automatically simpler:

- file write + atomic rename + index rebuild;
- index and file consistency; rebuild path if Markdown is the source of truth;
- revision snapshots and restore;
- conflict detection and idempotency;
- schema migrations;
- auth and access control;
- agent tool adapter (choose one interface: MCP or OpenAPI, not both without a
  concrete need).

Basic Memory remains an option: AGPL-3.0-or-later, Python ≥3.12, FastMCP 4.1.3
prerelease, SQLite/Postgres, optional semantic dependencies, no UI, auto-update.
It still needs the UI and revision/idempotency layer.

## 8. Files and commands

Changed/created for this stage:

- `scripts/apply_context_policy.py`;
- `experiments/context-memory/summary_policy.py`;
- `experiments/context-memory/test_apply_context_policy.py`;
- `experiments/context-memory/trace_proxy.py` (metadata proxy + mock spike page);
- `experiments/context-memory/exp_probe_tool.py`;
- `experiments/context-memory/run_tool_loop.py` (streaming, probe tool);
- `experiments/context-memory/run_short_stage.py` (summary contract, stream/tool support);
- `experiments/context-memory/fixtures.py` (tools_tail fixture);
- `docs/experiments/context-memory-guards.md`;
- `docs/experiments/context-regression-plan.md`;
- `docs/experiments/context-regression-results.md`;
- `docs/experiments/linked-notes-design.md`.

Commands executed (synthetic temp only):

- `python -m unittest discover -s experiments/context-memory -p 'test_*.py'` — 33 tests OK;
- `scripts/apply_context_policy.py --profile 32k ...` dry-run/apply, and 64K mismatch refusal;
- `run_tool_loop.py --scenario basic|multi|error`, plus basic after 120K-char history;
- `run_short_stage.py --modes B0 --scenarios second|values`;
- `run_branch_check.py`;
- Playwright UI checks for native Notes, chat iframe, standalone mock, chat link.

## 9. Cleanup and final state

- `chumba-exp-owui`, `chumba-exp-trace`, `chumba-exp-owui-data` removed;
- temporary network `chumba-context-memory_host_access` removed;
- `chumba-llama-server` stopped;
- no running containers;
- working `.env`, compose, DB, volumes and policies unchanged;
- 64K not started;
- no commit;
- Open WebUI image not updated; working UI remains pinned 0.11.4, so the v0.12.5
  banner remains until a separate update is approved.

## 10. Next minimal diff

1. Add a `request` filter guard that rejects oversized/final requests with a
   structured, user-visible error containing window, budget, estimated/actual
   tokens and reason. No automatic truncation or tool-result deletion.
2. Test whether `request` exceptions produce a useful HTTP error; if not, use a
   small external proxy for the error contract and document the tradeoff.
3. Add an explicit summary reduction check (post-checkpoint or proxy); do not
   claim the built-in has one.
4. Add a native tool-loop regression to the short test suite (now that the
   streaming path works) and test a previous tool group crossing a checkpoint.
5. For notes: approve a small frontend route/sidebar patch only after reviewing
   the maintenance cost; do not enable a chat-iframe workaround as the product.
