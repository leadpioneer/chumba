# Main launch: notes + reproducible OWUI patch build

Date: 2026-11-01  
Status: implemented in the main compose; working compaction/guard policy unchanged.

## What changed

- `notes` is now a main-compose service (Markdown + SQLite FTS5, volume
  `chumba-notes-data`, internal API + MCP; no host port).
- `open-webui` now builds from `openwebui/Dockerfile`:
  - pinned upstream source revision is downloaded;
  - `openwebui/owui-memory.patch` is applied automatically;
  - frontend is built with the pinned lockfile and `USE_SLIM=true`;
  - the built frontend and the two patched backend files are overlaid on the
    pinned upstream image.
- `notes-config` is a one-shot service that waits for OWUI, adds/updates the
  `notes` MCP connection and imports `notes/seed/chumba` idempotently. It does
  not enable compaction or the experimental budget guard.
- OWUI reaches notes through the same-origin backend proxy with
  `NOTES_INTERNAL_TOKEN`; it does not receive `WEBUI_SECRET_KEY` in notes.
- `NOTES_AGENT_TOKEN` is a separate MCP bearer token.

## Reproducible build/run

```powershell
docker compose up -d --build
```

This is the daily command. No manual `git apply`, source checkout or image
build command is required. The image tag is
`chumba-open-webui-memory:local`; the pinned base is
`sha256:8b432fe0...f3cae9`, source revision
`8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`.

Required `.env` values (see `.env.example`): `NOTES_INTERNAL_TOKEN`,
`NOTES_AGENT_TOKEN`, admin credentials, and optional
`OPENWEBUI_MEMORY_IMAGE` / `OPENWEBUI_BASE_IMAGE` / `OPENWEBUI_REV`.

URLs:

- `http://127.0.0.1:3000/memory`
- `http://127.0.0.1:3000/memory/seed-chumba-map`
- `http://127.0.0.1:3000/memory/graph`

## Verified

- `docker compose config`: OK.
- `docker compose build open-webui notes`: image built from `openwebui/Dockerfile`
  without manual patch application.
- Main stack started: `open-webui` healthy, `notes` healthy,
  `notes-config` exited 0.
- `notes-config` configured the notes MCP connection and imported all 25 seed
  notes as unchanged; memory proxy returned 29 notes (25 seed + 4 demo).
- Existing `open-webui-data` and `chumba-notes-data` volumes were reused; no
  working DB migration was needed beyond the image overlay.
- Existing compaction/guard settings were not changed.

## Rollback

```powershell
docker compose stop open-webui notes notes-config
docker compose stop
```

To return the UI image to upstream, set
`OPENWEBUI_MEMORY_IMAGE=ghcr.io/open-webui/open-webui@sha256:8b432fe0...f3cae9`
and run `docker compose up -d --no-build`; keep `chumba-notes-data` intact.
Remove only after explicit backup approval.

## Caveats

- `openwebui/owui-memory.patch` is a build-context copy of the canonical
  `experiments/notes-mvp/owui-memory.patch`; both must stay in sync.
- The first build downloads the pinned source and npm dependencies; later builds
  use Docker cache.
- `notes-config` requires valid OWUI admin credentials; if login fails it exits
  non-zero and the UI still works but MCP/seed initialization must be retried.
- Notes MCP is explicit-only; no automatic memory injection.
- Working compaction policy remains the legacy filter; built-in compaction and
  budget guard remain experimental/off.


## Final verification (2026-11-01)

- Main compose is running: `chumba-open-webui` healthy, `chumba-notes`
  healthy, `chumba-llama-server` healthy.
- `docker compose up -d --build open-webui notes notes-config` built both local
  images and started the one-shot `notes-config`; it exited 0.
- `notes-config` added/updated the `mcp-notes` tool server connection and
  imported all 25 seed notes unchanged.
- Authenticated proxy check returned 29 notes; tool server list contains
  `mcp-search`, `mcp-delegate`, `mcp-study`, `mcp-notes`.
- Commit with the complete pilot/migration: `80ac7ca18956e3e0da46eb6a3fadcfbe161d5284`;
  pushed to `origin/main`.
- Working compaction and guard policy were not changed. The experimental
  `chumba_summary` filter remains the active working policy; built-in
  compaction/guard remain off.
- Main UI URLs use port 3000; the notes service has no host port.
- `.env` (tokens), `benchmark-results`, volumes and backups are ignored by git.
- Docker may warn once that the existing `chumba-notes-data` volume was created
  by the old experimental compose project; the main stack reuses it. Do not run
  `docker compose down -v` if the notes volume must be preserved.

## Screenshots

- `docs/experiments/screenshots/memory-graph-fit-final.png`
- `docs/experiments/screenshots/memory-graph-split-final-2.png`
- `docs/experiments/screenshots/memory-page-launcher.png`
- `docs/experiments/screenshots/memory-root-map.png`
- `docs/experiments/screenshots/memory-note-diff.png`
- `docs/experiments/screenshots/memory-project-backlinks-graph.png`
- `docs/experiments/screenshots/guard-oversized-error.png`
- `docs/experiments/screenshots/guard-tool-overflow.png`
