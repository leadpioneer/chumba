# Context guard + linked-notes MVP: results

Date: 2026-11-01  
Environment: 32K backend, patched experimental Open WebUI 0.11.4, notes service, loopback trace.  
No 64K, no working image replacement, no working policy enablement, no commit.

## 1. What works

### Context guard

- Real streaming tool loop under guard: `user → tool_call → execution → tool result →
  second request → DONE` (2 backend requests, 1 call/1 result).
- Oversized current message: `CONTEXT_BUDGET_EXCEEDED`, window 32768, estimated
  204,363 tokens, budget 29,696; the chat displays HTTP 413 with the reason.
- Tool-result overflow: `huge_probe` executed once, then the next request was
  refused with estimated 36,945 > budget 29,696; the tool result remains visible
  and the error says executed tools are not rolled back.
- Two checkpoints under guard: 2 summaries, system marker and last user preserved,
  no orphan tool results.
- Old completed tool group crossing a checkpoint: the summary contains the old
  probe marker and the tool group remains intact in history.
- Regeneration via the same user message created a new assistant response without
  a guard error. The UI “Перегенерировать” button was clicked but did not create a
  separate message in this test; the API-path regeneration above is the verified
  check.
- Guard is conservative: tokenizer over text parts plus estimated overhead; it is
  not an exact serialized chat-template count. `/props?model=...` must match the
  configured window, otherwise it fails closed.

### Notes MVP

- `/memory` is a native OWUI route with a sidebar entry; `/memory/<id>` deep links.
- List/search, Markdown edit/preview, create, internal `[[id|title]]` links,
  backlinks, history, diff, restore, and depth-1 local graph.
- Markdown files are the source of truth in the preserved volume; SQLite FTS5 is
  rebuildable; revisions are immutable snapshots; restore creates a new revision.
- Optimistic concurrency with `expected_revision`; idempotency key + payload hash;
  path traversal rejected; unauthorized direct and proxy access return 401.
- Agent MCP tools: `search_notes`, `read_note`, `create_note`, `edit_note`,
  `get_backlinks`.
- Real agent E2E:
  1. user created a project note;
  2. agent searched/read it and created a linked experiment note;
  3. user edited the experiment value;
  4. agent used the old revision, received a structured `conflict` with the current
     revision;
  5. agent re-read and applied the edit with the current revision;
  6. project backlinks included the experiment; graph contained both nodes;
  7. history showed agent → user → agent revisions;
  8. notes service restart preserved data and history;
  9. unauthorized direct and proxy calls were rejected.
- Chat link to `/memory/<id>` opens the note in a new Chumba tab. It is not a
  separate application, but it is a new tab rather than an in-place navigation.

## 2. Screenshots

- `benchmark-results/notes-mvp/ui/memory-list.png`
- `benchmark-results/notes-mvp/ui/memory-project-backlinks-graph.png`
- `benchmark-results/notes-mvp/ui/memory-note-diff.png`
- `benchmark-results/notes-mvp/ui/memory-note-restored.png`
- `benchmark-results/notes-mvp/ui/memory-ui-created.png`
- `benchmark-results/context-memory/ui-spike/guard-oversized-error.png`
- `benchmark-results/notes-mvp/ui/guard-tool-overflow.png`

## 3. Tests and evidence

- `python -m unittest discover -s experiments/context-memory -p 'test_*.py'` — **42 OK**.
- `python -m unittest notes/test_storage.py` — **13 OK**.
- Real notes E2E chat: `44bdc846-c617-42f8-a0d8-52a2848cf5af`.
- Notes created: project `n68de1905c9d6e5783bc4b167`, experiment
  `n6cf18222541a8c12801e1aad`, UI note `n8e19383aa975898d2f4feae6`.
- Agent conflict output:
  `ok=false, error=conflict, current_revision=b106d70a..., expected_revision=8530c4dd...`.
- Agent retry succeeded with revision `34d181f1...`.
- Restored note created a new revision `bdfbe552...`; history retained all previous
  revisions.
- Direct `GET http://127.0.0.1:8089/api/notes` without token: 401.
- OWUI proxy without token: 401.

## 4. Reproducible artifacts

- Notes service: `notes/storage.py`, `notes/app.py`, `notes/Dockerfile`,
  `notes/requirements.txt`.
- OWUI patch: `experiments/notes-mvp/owui-memory.patch`.
  - upstream revision: `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`
  - upstream image digest: `sha256:8b432fe0a65b91116afc7961365c6cca5379cc923171386a96691f3471f3cae9`
  - patch SHA-256: `29c4e0be9d40a0c990da2dbf54f7cbf6c3dfd2d36d83cf1bb0f4672d7932bb69`
  - `git apply --check` against a clean pinned tree: OK
- Experimental images:
  - `chumba-notes:exp`
  - `chumba-open-webui-notes:exp`
- Compose: `experiments/notes-mvp/docker-compose.yml`.
- Setup: `experiments/notes-mvp/configure_temp.py`.
- Guard cases: `experiments/notes-mvp/run_guard_cases.py`.
- Agent E2E: `experiments/notes-mvp/e2e_notes.py`.
- Patch README: `experiments/notes-frontend/README.md`.

## 5. Start and rollback commands

Start backend only:

```powershell
docker compose up -d llama-server
```

Build and start the notes MVP:

```powershell
docker build -t chumba-notes:exp notes
# Apply experiments/notes-mvp/owui-memory.patch to the pinned OWUI source tree.
$OWUI_SRC = "C:\path\to\patched-owui"
docker build --build-arg USE_SLIM=true -t chumba-open-webui-notes:exp $OWUI_SRC
docker compose -f experiments/notes-mvp/docker-compose.yml up -d
python experiments/notes-mvp/configure_temp.py
```

Rollback / stop:

```powershell
docker compose -f experiments/notes-mvp/docker-compose.yml down
docker volume rm chumba-notes-owui-data
# keep chumba-notes-data for the synthetic demonstration
docker compose stop llama-server
```

The working image and working `.env`/compose/DB/volumes are untouched. The
experimental OWUI image is not used by the working stack.

## 6. Preserved demo volume

`chumba-notes-data` is intentionally kept. It contains synthetic notes:

- two `Project Chumba` notes (one is a duplicate from the initial failed agent
  attempt; deletion is outside the MVP contract);
- `Context memory experiment` with four revisions;
- `UI created note`.

Files are under `notes/` and revisions under `revisions/`; `notes.db` is the FTS
index and can be rebuilt.

## 7. Known limitations

- Summary preflight and reduction-check are **not implemented**: no supported
  in-process hook before built-in compaction; a proxy or a separate decision is
  needed. The final request guard covers outbound overflow only.
- The guard’s count is conservative text-parts + estimated overhead, not an exact
  chat-template count.
- A guard `/props` failure during model load produces a safe 503; it is not
  retried automatically.
- Chat deep links open a new Chumba tab, not an in-place navigation.
- The OWUI “Перегенерировать” button did not produce a separate message in this
  test; regeneration was verified through the same-user-message API path.
- Notes are single-user MVP; no deletion, bulk operations, multi-user
  collaboration, typed-relation extraction, embeddings, automatic memory
  injection or global graph.
- External file editing while the service runs is unsupported; a manual reindex
  path is the documented recovery.
- The synthetic volume contains a duplicate project note; no delete endpoint was
  implemented.
- 64K and the full matrix were not run. No automatic policy was enabled.

## 8. Final state

- `chumba-notes`, `chumba-notes-owui`, `chumba-exp-trace` stopped and removed;
- temporary `chumba-notes-owui-data` volume removed;
- `chumba-notes-data` volume preserved;
- `chumba-llama-server` stopped;
- no running containers;
- working `.env`, compose, DB, volumes and policies unchanged;
- no commit.
