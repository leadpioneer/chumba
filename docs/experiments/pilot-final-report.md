# Chumba pilot: final report

Date: 2026-11-01  
Repository HEAD: `6039cf4e795a3e7b50a5a82e35d469815eb87616`, dirty working tree.  
No commit, push, OWUI upgrade, 64K run, or working-policy change.

## 1. Completed

### Context guard

- Experimental `request` guard is installed only in the experimental OWUI.
- It runs before the main request and each tool-loop iteration, checks final
  messages/tools, verifies the configured window against `/props?model=...`, and
  never truncates or re-summarizes.
- Runtime verified: normal streaming, oversized current message (visible 413),
  tool-result overflow (visible refusal; executed tool not rolled back), two
  checkpoints, old tool group through checkpoint, regeneration via same user
  message.
- Counting is a conservative text-parts + estimated-overhead check. It uses the
  tokenizer when available and falls back to an explicitly named 2.5 chars/token
  estimate; the confirmed window is cached for 300 seconds after a successful
  probe. This is not an exact serialized chat-template count.
- Summary preflight and summary reduction-check remain **unimplemented**; they
  still require a proxy or a separate supported design.

### Linked notes inside Chumba

- Native Svelte routes: `/memory`, `/memory/<id>`, `/memory/graph` in the
  experimental image.
- Sidebar entry, same-tab internal `/memory` links from chat, list/search,
  Markdown edit/preview, create, internal links, backlinks, depth-1 local graph,
  project overview graph with type filters, history/diff/restore, sources.
- Obsidian-style full graph at `/memory/graph`: global/local modes, depth
  selector, force layout, zoom/pan, node drag, hover connection highlight,
  arrow markers, labels, type/status color groups, search/status/type filters,
  force sliders. Node radius reflects incoming links. Dragging a node starts the
  force simulation so connected nodes are pulled by the link springs.
- Full-screen graph first; selecting a node opens a 2/3-width read-only note pane
  with links/sources/backlinks while the interactive graph stays on the right.
  The full note opens via `/memory/<id>`.
- `/memory` overview is now a launcher to the interactive graph, not a static
  graph; the note page's static local graph is replaced by a link to the local
  interactive graph.
- Stable IDs, optimistic concurrency (`expected_revision`), idempotency key +
  payload hash, path-traversal protection, immutable revisions, new-revision
  restore.
- Agent MCP tools: `search_notes`, `read_note`, `create_note`, `edit_note`,
  `get_backlinks`.
- Same-origin OWUI proxy: OWUI checks the user and sends a separate internal token
  to notes; the notes service never receives `WEBUI_SECRET_KEY`.
- Real agent E2E proved stale-edit conflict → reread → successful update.

### Project knowledge map

- 25 managed seed notes, 81 wiki-links.
- Root map: `Chumba — карта проекта` (`seed-chumba-map`).
- Four thematic maps: architecture, operations, experiments, context/memory.
- Component notes: services, request path, inference profiles, routing/delegate,
  MCP tools, search/embeddings, network/privacy, context, compaction/guard,
  notes service, UI patch, storage/backup, operations, testing.
- Decision, experiment, and limitation notes.
- Total notes in the preserved volume: 29 (25 seeds + 4 demo/test notes marked
  `[demo]`). Demo notes are not linked from the project maps.

Covered areas:

- purpose/architecture;
- services/ports/request path;
- local inference 32K and experimental 64K;
- LiteLLM/external worker;
- tools/MCP/delegate;
- search/read/embeddings/RAG;
- network boundaries/privacy;
- context management;
- built-in compaction candidate and budget guard;
- linked-notes service, UI patch, storage/volumes/backup;
- operations/diagnostics/rollback;
- testing;
- three experiments;
- decisions and open limitations.

Not covered as separate notes: individual source files, generated images, every
MCP submodule, historical plans that were not implemented, and code paths not
confirmed by source/runtime. The graph is a navigation graph, not a complete
semantic or dependency graph; typed relations are not implemented.

## 2. Note/link counts

- Seed notes: **25**
- Seed links: **81**
- Validation: no broken internal links, no duplicate seed IDs/titles, all notes
  reachable from the root, no secret-like values in seed notes.
- Re-import: all 25 unchanged; no duplicate notes; manifest revisions matched.
- Demo/test notes: 4, all marked `[demo]` and “DEMO/TEST” in the body.

## 3. Tests and browser checks

Local tests:

- `python -m unittest discover -s experiments/context-memory -p 'test_*.py'` — **42 OK**
- `python -m unittest notes/test_storage.py` — **13 OK**

Runtime/browser:

- real streaming tool loop (basic/multi/error) and after compaction;
- guard oversized and tool-overflow errors visible in chat; normal requests pass;
- notes UI: overview graph, seed filter, type filter, search (`inference`), graph
  node click, root map → architecture map → component, backlinks, sources,
  history/diff/restore on demo note;
- chat link `/memory/seed-chumba-map` opens in the **current tab**; ctrl/meta/
  middle click is left to the browser for explicit new-tab behavior;
- notes service restart preserved 29 notes and history;
- unauthorized direct 8089 and OWUI proxy access rejected (401).

Agent questions (`agent-questions-3.json`, chat
`f9286223-c8fe-463a-bf3b-0c14e2cce7ee`):

1. Request path → cited `/memory/seed-request-path`.
2. Outbound protection → cited `/memory/seed-network-privacy`.
3. Working vs 64K → cited inference + context-expansion notes and distinguished
   experimental from active.
4. Context overflow → cited context, compaction/guard and limitations; a second
   run with a larger output budget produced a complete answer
   (`agent-overflow-answer.json`, chat `b46f1d24-...`).
5. Revisions → cited `/memory/seed-notes-service` and described snapshots,
   conflict, idempotency, restore.

The agent’s first four questions initially hit guard `413/503` because `/tokenize`
and `/props` timed out while the backend was loading. The guard was corrected to
use a conservative 2.5 chars/token fallback and a cached confirmed window; the
re-run completed all five questions without errors. This fallback is an
approximation, not an exact template count.

## 4. Screenshots

- `docs/experiments/screenshots/memory-page-launcher.png` — memory list and
  interactive graph launcher.
- `docs/experiments/screenshots/memory-graph-fit-final.png` — fitted global
  graph after the width, layout and auto-fit fixes.
- `docs/experiments/screenshots/memory-graph-split-final-2.png` — selected node,
  note at 2/3, interactive graph at 1/3, settings auto-hidden.
- `benchmark-results/notes-mvp/ui/memory-graph-local.png` — local depth-1 graph.
- `docs/experiments/screenshots/memory-root-map.png` — root project map.
- `benchmark-results/notes-mvp/ui/memory-inference-sources.png` — component note,
  sources, links.
- `docs/experiments/screenshots/memory-project-backlinks-graph.png` — backlinks
  and local graph.
- `docs/experiments/screenshots/memory-note-diff.png` — history diff.
- `benchmark-results/notes-mvp/ui/memory-note-restored.png` — restored note.
- `benchmark-results/notes-mvp/ui/memory-ui-created.png` — UI-created test note.
- `docs/experiments/screenshots/guard-oversized-error.png` — visible
  guard refusal.
- `docs/experiments/screenshots/guard-tool-overflow.png` — tool-result overflow
  refusal with preserved tool result.

## 5. Build and start commands

```powershell
# working backend only for context experiments
docker compose up -d llama-server

# notes service image
docker build -t chumba-notes:exp notes

# experimental OWUI image from a clean pinned source tree with the patch
git apply --check experiments/notes-mvp/owui-memory.patch
git apply experiments/notes-mvp/owui-memory.patch
# Set this to the patched source directory; do not paste angle brackets in PowerShell.
$OWUI_SRC = "C:\path\to\patched-owui"
docker build --build-arg USE_SLIM=true -t chumba-open-webui-notes:exp $OWUI_SRC

# experimental stack
docker compose -f experiments/notes-mvp/docker-compose.yml up -d

# one-time temp OWUI user/policy/guard/MCP setup
python experiments/notes-mvp/configure_temp.py

# seed documentation
# Sign in to the experimental OWUI and set the real JWT in the shell.
$env:OWUI_TOKEN = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3001/api/v1/auths/signin `
  -ContentType application/json -Body '{"email":"exp@example.invalid","password":"exp-only-password-123"}').token
python notes/seed/chumba/import_seed.py --validate-only
python notes/seed/chumba/import_seed.py --url http://127.0.0.1:3001          # dry-run
python notes/seed/chumba/import_seed.py --url http://127.0.0.1:3001 --apply  # update
```

Start URL after the experimental stack is running:

- `http://127.0.0.1:3001/memory`
- root map: `http://127.0.0.1:3001/memory/seed-chumba-map`

Patch reproducibility:

- upstream `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`
- upstream image digest `sha256:8b432fe0a65b91116afc7961365c6cca5379cc923171386a96691f3471f3cae9`
- patch `experiments/notes-mvp/owui-memory.patch`
- SHA-256 `c3fe2f0b2b3a6d8a1dd4e28a067a12171ad0b424c74e2fb1ca9e3ac08ccabad7`
- `git apply --check` against a clean pinned tree: OK
- experimental image: `chumba-open-webui-notes:exp`
- working OWUI 0.11.4 and its image remain unchanged.

## 6. Import/update semantics

- IDs are stable `seed-*` IDs; reruns do not duplicate notes.
- `manifest.json` stores the last seed revision and content hash for each note.
- A seed note is updated only when the current revision equals the manifest
  revision. A manual edit changes the revision, so the importer reports a conflict
  and leaves the manual edit untouched.
- `--validate-only` checks broken links, duplicate IDs/titles, reachability and
  secret-like values.
- No direct storage/index/revision writes; the importer uses the notes HTTP API.

## 7. Backup and restore

Preserved volume: `chumba-notes-data`.

Backup files (non-empty, 99 tar entries):

- `benchmark-results/notes-mvp/backups/chumba-notes-data-final-20261101.tar.gz`
  SHA-256 `CEB496F887D4DD14866AE21EF85846C68EA6CE238D9EDBBB2A5FDA43A1A2759B`
- earlier copy: `chumba-notes-data-20261101.tar.gz`.

Restore (to a new/empty volume, with notes service stopped):

```powershell
docker volume create chumba-notes-data-restore
docker run --rm -v chumba-notes-data-restore:/data -v "${PWD}\benchmark-results\notes-mvp\backups:/backup" `
  --entrypoint tar chumba-notes:exp -xzf /backup/chumba-notes-data-final-20261101.tar.gz -C /data
```

The archive contains Markdown notes, immutable revisions and the SQLite FTS index.
Keep both the volume and the archive for restoration.

## 8. Files/volumes and unchanged state

Created/updated in this stage:

- `notes/seed/chumba/` — 25 seed notes, `manifest.json`, import script, README;
- `experiments/notes-mvp/` — notes compose, OWUI patch, configure/agent/guard scripts;
- notes service/API/MCP and storage tests under `notes/`;
- UI improvements: overview graph, same-tab links, sources, seed badge;
- `docs/experiments/pilot-final-report.md`, updated ADR and notes docs;
- screenshots and backups under `benchmark-results/notes-mvp/` (git-ignored).

Preserved:

- `chumba-notes-data` (synthetic demo + 25 documentation notes);
- final backup archive;
- experimental images `chumba-notes:exp`, `chumba-open-webui-notes:exp`.

Current state after the graph follow-up:

- experimental `chumba-notes`, `chumba-notes-owui`, `chumba-exp-trace` and
  `chumba-llama-server` are running for interactive inspection;
- temporary `chumba-notes-owui-data` was recreated after the restart; the
  preserved `chumba-notes-data` volume is intact (29 notes);
- stop with `docker compose -f experiments/notes-mvp/docker-compose.yml down`
  and `docker compose stop llama-server`; remove `chumba-notes-owui-data` only;
- `chumba-notes-data` must remain.

Not changed:

- working `.env`, `docker-compose.yml`, working DB, volumes, active compaction
  policy;
- working image not replaced;
- no OWUI update, no 64K, no commit/push.

## 9. Remaining limitations

- Built-in compaction is a candidate, not the active policy; legacy `chumba_summary`
  remains active and has known state-loss defects.
- Summary preflight/reduction-check are absent.
- Guard is conservative, not exact; 5-minute window cache can mask a backend
  profile change inside that window; it fails closed on unknown window.
- Notes MVP is single-user; no delete/bulk/multi-user, no external file edits, no
  automatic memory injection, no embeddings, no typed relations/global graph.
- Chat link opens same tab; explicit modifier-click new-tab behavior is preserved.
- Agent answers are model-generated; citations were checked against notes, but
  this is not a formal semantic proof of every statement.
- The demo volume contains 4 synthetic demo notes; they are intentionally retained
  and marked `[demo]`.
- 64K/full matrix were not run in this stage.


## 10. Post-report troubleshooting

After the first report, the experimental stack was restarted manually. The OWUI
temp volume was newly created, so the first `configure_temp.py` attempt raced
OWUI startup and the seed import received a placeholder token. Both scripts were
hardened:

- `configure_temp.py` waits for `/health` and retries auth/signup;
- `import_seed.py` rejects placeholder tokens, aborts on 401 before writing and
  reports unchanged seeds correctly.

The existing `chumba-notes-data` volume was intact (29 notes). The real JWT was
used, `configure_temp.py` completed, and the seed import returned all 25 notes
unchanged. A post-fix notes-agent smoke test answered with citations and no
errors.

The original delayed sleep was not retried while the interactive troubleshooting
session was active. The experimental stack is running for the user to inspect;
the working stack remains stopped. No power settings were changed and no forced
shutdown was used.
