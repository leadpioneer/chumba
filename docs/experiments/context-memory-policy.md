# Context memory policy: short validation stage

Date: 2026-10-31  
Scope: isolated 32K backend + temporary Open WebUI with an empty volume.  
No 64K run. No working settings or working volumes were changed. No commit.

## 1. What was tested

| Variant | Mechanism | Threshold | Summary prompt | Summary output |
|---|---|---|---|---|
| A | legacy `chumba_summary` filter | 12000 (char/4 units; ~48000 chars of old text) | filter's built-in instruction | `max_tokens=500`, thinking disabled by the filter |
| B0 | built-in `chat.context_compaction` | 12000 (same numeric value; Open WebUI semantics) | same instruction + `{{COMPACTED_MESSAGES}}` only | `max_tokens=500`, thinking disabled |
| B | built-in `chat.context_compaction` | 21504 (budget-derived, see below) | same as B0 | same as B0 |

Budget used for B:

```text
W = 32768 (confirmed via GET /props?model=orchestrator)
R_out = 1024
R_tool = 2048 (estimate)
margin = max(1024, 3% W) = 1024
B_in = 32768 - 1024 - 2048 - 1024 = 28672
T = floor(0.75 * B_in) = 21504
```

`R_tool` is an explicit estimate, not a measured value. The 0.75 safety factor is
a stage setting, not a final policy.

The built-in summary prompt initially included `{{RECENT_MESSAGES}}` and ran with
model thinking enabled. That variant was rejected: the model answered the final
user JSON question instead of summarizing (summary length 210–1889 chars, 500-token
cap hit, reasoning text in the summary). All B/B0 results below use:

- `chat_template_kwargs.enable_thinking=false` for the summary task;
- a template containing only `{{COMPACTED_MESSAGES}}`, never the current final
  question;
- `max_tokens=500`, `temperature=0.1`.

This is a **required configuration fix**, not a default of the built-in mechanism.

## 2. A/B0/B table

All values are from the isolated UI path (`POST /api/chat/completions`, the same
internal route used by the web UI; confirmed in this stage). Wall time is measured
around the whole call and includes summary generation. Main prompt tokens are the
backend `usage.prompt_tokens` of the final main request. Before tokens are exact
tokenizer counts of the concatenated history and exclude the chat template.

### 2.1 Short history (no compression expected)

| Mode | Summary requests | Before tokens | Main prompt | Wall s | System marker | Last user | Result |
|---|---:|---:|---:|---:|---|---|---|
| A | 0 | 194 | 225 | 4.1 | true | true | exact values correct |
| B0 | 0 | 194 | 225 | 4.1 | true | true | exact values correct |
| B | 0 | 194 | 225 | 4.1 | true | true | exact values correct |

### 2.2 60K-char history (one compression for A/B0, none for B)

| Mode | Summary requests | Before tokens | Main prompt | Wall s | System marker | Last user | State result |
|---|---:|---:|---:|---:|---|---|---|
| A | 1 | 12288 | 1030 | 27.4 | **false** | true | all exact values `null`; constraints absent |
| B0 | 1 | 12288 | 5664 | 32.0 | true | true | ID/path/version/port/decision preserved |
| B | 0 | 12288 | 12679 | 20.1 | true | true | ID/path/version/port/decision preserved |

A summary request: 5,949 prompt tokens, 380 completion tokens, 23.1 s.  
B0 summary request: 7,236 prompt tokens, 297 completion tokens, 21.4 s.

### 2.3 Two turns: checkpoint + second compression

| Mode | Summary requests | Turn 1 wall | Turn 2 wall | Final main prompt | System marker | Last user | State result |
|---|---:|---:|---:|---:|---|---|---|
| A | 2 | 18.3 | 28.0 | 1504 | **false** | true | hallucinated `8080`, `step-45`, `/background/step/45`; no original values |
| B0 | 2 | 32.3 | 41.9 | 8296 | true | true | ID/path/version preserved on both turns |
| B | 1 | 19.0 | 37.6 | 11774 | true | true | ID/path/version preserved; first turn had no summary |

B0 second summary request: 6,881 prompt tokens, 454 completion, 28.3 s.  
B second (only later) summary request: 10,488 prompt tokens, 333 completion, 19.4 s.

### 2.4 Decision, exact values, tools

| Scenario | A | B0 | B |
|---|---|---|---|
| Decision A→B | decision B, rejected A | decision B, rejected A | decision B, rejected A |
| Exact values/status | all values `null` | ID, path, port, version, threshold, proposed/executed/verified preserved | all values preserved |
| Tool history + untrusted result | tool status `ok`; exact ID hallucinated; no `PWNED` | t1 ok, t2 timeout/retry, t3 corrected/verified; exact ID/port preserved; no `PWNED` | tool status collapsed to `ok`; exact values preserved; no `PWNED` |

A's decision scenario passed because the decision change was near the end and
inside the last 30K chars. A's early values were outside the truncation window
and were lost. This is consistent with the audit.

## 3. Confirmed invariants

| Invariant | A | B0/B | Evidence |
|---|---|---|---|
| System instructions survive | **No** | **Yes** | A outbound `system_has_expected_marker=false`; B/B0 `true` |
| Last user message is verbatim | Yes in tested turns | Yes | outbound `last_user_sha16` matched in all compressed runs |
| Tool groups remain structurally valid | Not proven; unit test shows orphan risk | Not proven at runtime; code boundary is user-message aligned | A synthetic orphan test; built-in boundary code; synthetic role-tool messages were not preserved by UI normalization |
| Incomplete tool calls not summarized | Not implemented | Not runtime tested; boundary should keep the current turn | code + unit |
| Recent messages verbatim | Last 6 (can split groups) | Recent suffix from user boundary | code/tests |
| Summary included in prompt | Yes (replaces system) | Yes (appended to system) | outbound system chars/marker |
| Summary request fits backend | Yes (5,949 prompt tokens) | Yes (7,236 / 6,881 / 10,488 tokens) | proxy usage |
| Safe fallback if still too large | **No** | **No post-size validation** | no comparison before/after; middleware logs and continues with full history on exception |
| Summary is not a trusted instruction | **No**: filter explicitly says "trusted history"; summary is system | **Residual risk**: appended to system as `[CONVERSATION SUMMARY]`; tool text is inside it | code + summary contents |
| No PII in diagnostic logs | Yes in this stage | Yes | only roles/lengths/hashes; synthetic data only |

### 3.1 Tool history and tool loop

- The request hook (`exp_request_probe`) **is called before the main request**
  and sees the final tool schema and message list. In the native test it logged
  124 messages, 80,555 chars, `tool_schema_count=1`, `tool_names=["large_probe"]`.
- The backend returned `finish_reason=tool_calls`, but Open WebUI did not execute
  or persist the tool call; there was no second backend request, no tool message,
  and the answer was empty. This is a separate Open WebUI/provider integration
  defect, not a compaction result.
- Legacy function calling produced two requests but no executed tool; the second
  request had `tool_schema_count=0` and a 13,670-char last-user message containing
  the legacy tool instructions. It is not a valid tool-loop test.
- Code confirms the `request` filter hook is invoked in the tool loop
  (`utils/middleware.py:6286`, `:6507`), after tool results are appended, but the
  built-in compaction runs only once in `process_chat_payload` (`:2497`).
  Therefore **there is no budget check or compaction on each tool iteration**.
- Synthetic role-tool messages imported into history were not preserved as
  `role=tool` in the outbound payload (the outbound roles contained only
  system/user/assistant). This makes the `tools_tail` integrity result
  inconclusive. A native `output` fixture or a working real tool loop is required
  before claiming tool-group preservation.

## 4. Loss and distortion examples

1. **System loss, A**: 60K-char history, one summary. Outbound system was the
   filter summary (1,837 chars), `SYSTEM_MARKER` absent. Exact values were all
   `null`; the summary contained only filler narrative.
2. **Truncated summary input, A**: summary input was exactly 30,000 chars; it
   began mid-message. The original system prompt and early exact values were
   outside that suffix.
3. **Hallucinated continuation, A second turn**: the model answered
   `forbidden_port=8080`, `exact_id=synthetic-log-18`, `path=/logs/synthetic/no-state`,
   none of which occurred in the fixture.
4. **Built-in summary hijack, initial config**: with `{{RECENT_MESSAGES}}` and
   thinking enabled, the summary was the model's answer to the final question
   (210–1889 chars, 500-token cap), not a summary. After disabling thinking and
   removing recent messages from the summary input, summaries became 883–1372
   chars and preserved state.
5. **Untrusted text in summary**: B0 tools summary contained the `SYSTEM OVERRIDE`
   / `PWNED` text, but also labeled tool results as untrusted data. The model did
   not output `PWNED` in the tested run. This is a residual trust risk, not proof
   of safety.
6. **Tool result formatting**: our synthetic role-tool messages were not preserved
   by UI normalization; `tools_tail` outbound had no tool messages. Real OWUI
   stores tool calls/results in assistant `output` items; this fixture must be
   replaced before drawing tool-history conclusions.

## 5. What the built-in threshold and cap actually mean

Code (`utils/context_compaction.py`):

- `_exceeds_token_threshold` uses the last assistant `usage` if present
  (`prompt_tokens + completion_tokens`), plus a char/4 estimate of messages after
  it. If no usage is present, it estimates system + previous summary + messages.
  It does **not** add tools schema/RAG in the estimate path.
- `_resolve_token_threshold` returns
  `min(per-chat compact_token_threshold or global threshold, global cap)`.
- `token_cap` is therefore a **cap on the threshold**, not a hard limit on the
  whole outbound request. It can lower the trigger; it cannot guarantee that the
  final prompt plus generation/tool reserves fits the backend.
- The built-in compaction is invoked once per `process_chat_payload`, before
  `process_messages_with_output`; it is not invoked on tool-loop iterations.
- The summary is appended to the original system message with
  `[CONVERSATION SUMMARY]` (`utils/middleware.py:2506-2511`), so the system prompt
  is preserved but the summary remains system-level content.

Environment vs API:

- The temp OWUI was restarted after setting `ENABLE_CONTEXT_COMPACTION=true`,
  threshold/cap 21504, retention 40 through the HTTP API. All values and the
  probe filter's active/global state persisted.
- The temp compose did not set `CONTEXT_COMPACTION_*` env values; the
  `config.py` env values are defaults, while API writes persistent DB config.
  There was no second source of truth in this test. The future helper must check
  both and refuse a mismatch.

## 6. Latency and cost

| Case | Summary requests | Summary time | Total wall | Main prompt tokens |
|---|---:|---:|---:|---:|
| A long | 1 | 23.1 s | 27.4 s | 1,030 |
| A second | 2 | 13.4 + 22.4 s | 46.2 s | 1,504 |
| B0 long | 1 | 21.4 s | 32.0 s | 5,664 |
| B0 second | 2 | 21.9 + 28.3 s | 74.2 s | 8,296 |
| B long | 0 | — | 20.1 s | 12,679 |
| B second | 1 | 19.4 s | 56.6 s | 11,774 |

- Summary requests are full inference requests on the same GPU slot. Their
  prefill alone was 5,949–10,488 tokens in these runs.
- A's summary input was capped at 30K chars; B0/B summarized 36K–75K chars of
  compacted messages. Larger summary input costs more latency.
- Main prompt cache was not reused in these runs (`cache_n=0` for most main
  requests). One A summary and one B summary showed cache hits, but the sample is
  too small to claim a prompt-cache effect. A's repeated summary per turn is a
  likely cache-invalidation cost; not proven here.
- No main/summary HTTP 400 occurred. The only 400s were the filter's `/tokenize`
  calls without `model` (9), which force the char/4 fallback.

## 7. Conclusion

**Built-in context compaction is a viable basis, but not ready to enable as the
working policy.**

Reasons to use it:

- preserves the original system prompt;
- preserves the last user message in all tested turns;
- preserves exact values, decisions and statuses at 60K chars;
- stores checkpoints in the branch and passes branch isolation;
- supports a second compression incrementally;
- is a supported Open WebUI mechanism, no fork.

Reasons not to enable it yet:

- it requires explicit `enable_thinking=false` and a compacted-only summary
  template; otherwise the summary model answers the final question;
- `token_cap` is not an outbound hard limit;
- there is no post-summary check that the input actually shrank;
- there is no per-tool-iteration budget check;
- tool-history preservation is not yet proven because the synthetic role-tool
  fixture was not preserved by UI normalization, and the real tool loop did not
  execute;
- summary remains system-level and can contain untrusted tool text; a prompt
  sentence is not a security boundary;
- oversized single message behavior is not validated at runtime.

A must remain only a control. It loses the system prompt and early state on
every compressed run; do not recommend it as a safe policy.

## 8. Minimal next diff

1. Add `scripts/apply_context_policy.py` (config helper, no direct DB writes):
   - read `W` from `GET /props?model=<id>`; if missing, dry-run only, apply fails;
   - reject `profile.window != backend.window` (64K profile vs 32K backend is an
     error);
   - compute `B_in` and `T` with explicit `R_out`, `R_tool`, margin, tools/RAG;
   - refuse if `chumba_summary` is active+global and built-in is enabled;
   - set built-in config through `/api/v1/chats/config` and summary task params
     through `/api/v1/configs/import`;
   - print a dry-run diff and support `--rollback`.
2. Set the built-in summary task explicitly:
   - `max_tokens=500`, `temperature=0.1`,
     `chat_template_kwargs={"enable_thinking": false}`;
   - template with `{{COMPACTED_MESSAGES}}` only, plus a strong
     "do not answer the conversation; summarize it" instruction;
   - keep the original system prompt; append summary with a provenance header.
3. Add a preflight/postflight guard (small supported `request` filter or helper
   check):
   - compute exact prompt + tools + reserves against `W`;
   - if no reduction after summary, fail with a diagnostic instead of storing a
     non-reducing summary;
   - if the summary request itself cannot fit, fail explicitly; do not silently
     truncate.
4. Do not add automatic tool-result truncation yet. First fix the native tool
   execution/test fixture. Then add a per-tool-loop guard that returns a clear
   error (or a user-boundary compaction) when the next round-trip would exceed
   budget.
5. Re-run the tool history test using native Open WebUI `output` items or a
   working real tool loop; re-run branch/regeneration after that.
6. Only then run the full A/B/C/D matrix and the 64K profile. The short stage
   already shows why B is preferable to A; it does not yet justify enabling B in
   production.

## 9. Should the full matrix run now?

Not yet. The remaining gaps are structural, not statistical:

- summary prompt/thinking configuration must be fixed in the apply path;
- no post-summary reduction check;
- no tool-loop budget guard;
- tool-history fixture is not faithful;
- oversized-message behavior is unverified.

Running the 4–6 hour matrix before these fixes would spend GPU time on a policy
that can still silently produce a non-reducing or instruction-following summary.
After the next diff, run a short A/B0/B regression first, then the full matrix.

## 10. Artifacts and changes

Created (experimental only):

- `experiments/context-memory/policy.py` — budget, profile, conflict, tool-group
  and no-reduction pure functions;
- `experiments/context-memory/test_policy.py` — 23 no-GPU tests;
- `experiments/context-memory/test_current_filter.py` — mock-server tests of the
  current filter;
- `experiments/context-memory/fixtures.py` — synthetic task-state fixtures;
- `experiments/context-memory/run_short_stage.py` — A/B0/B harness;
- `experiments/context-memory/run_branch_check.py` — fork/branch check;
- `experiments/context-memory/run_tool_loop.py` — native/legacy tool-loop probe;
- `experiments/context-memory/trace_proxy.py` — metadata-only trace proxy;
- `experiments/context-memory/docker-compose.exp.yml` — isolated temp stack;
- `experiments/context-memory/exp_request_probe_filter.py` — request-hook probe;
- `experiments/context-memory/exp_large_tool.py` — synthetic large tool;
- raw synthetic outputs in `benchmark-results/context-memory/` (git-ignored).

Not changed:

- working `.env`, `docker-compose.yml`, working Open WebUI DB/volume and filter
  valves;
- no commit, no image/dependency update.

Final state:

- temp containers and `chumba-exp-owui-data` volume removed;
- working `chumba-llama-server` stopped (initial state was stopped);
- no 64K backend started;
- all working containers stopped; working volumes untouched.

## 11. Classification

**Measured in this stage**

- A/B0/B prompt tokens, wall time, summary counts, outbound system/last-user
  markers, exact-value preservation, branch isolation, config persistence.
- Native tool call returned `finish_reason=tool_calls` but was not executed by
  Open WebUI; request hook saw final tools/messages once.

**Code-confirmed**

- A's char/4 threshold and 30K-char summary truncation;
- built-in threshold/cap semantics and summary-to-system insertion;
- no tool-loop compaction; request hook location;
- env defaults vs persistent DB config.

**Hypotheses / unverified**

- prompt-cache impact;
- native tool-group preservation;
- oversized single-message behavior;
- adversarial robustness of summary provenance;
- 64K policy behavior (not run by instruction).
