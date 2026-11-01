# Notes frontend patch plan (not implemented)

Status: implemented in the experimental MVP. Patch and image are experimental;
the working image was not replaced.

Upstream:

- repository: `open-webui/open-webui`
- revision: `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`
- image digest: `sha256:8b432fe0a65b91116afc7961365c6cca5379cc923171386a96691f3471f3cae9`
- version: 0.11.4

## Chosen integration

Native Svelte route/component (ADR 0001), not an iframe.

Files expected to change (final diff must be checked against the pinned tree):

- `src/lib/components/layout/Sidebar.svelte` — add one `memory` menu item and
  path prefix, guarded by the same user/permission pattern as Notes;
- `src/routes/(app)/memory/+page.svelte` — list/search;
- `src/routes/(app)/memory/[id]/+page.svelte` — note view/edit/backlinks/diff;
- `src/lib/components/memory/MemoryPanel.svelte` — minimal editor and lists;
- `src/lib/apis/memory/index.ts` — same-origin JSON API client; the browser
  sends its normal OWUI token to OWUI, never to the notes service.

No changes to chat/tool execution, no CSP/sanitization relaxation, no iframe.

## Auth and network

- Browser UI calls same-origin `/api/v1/memory/...`; OWUI verifies the user and
  the new backend proxy forwards with `NOTES_INTERNAL_TOKEN`.
- `WEBUI_SECRET_KEY` is not sent to the notes service.
- MCP is internal-only; agent uses a separate `NOTES_AGENT_TOKEN`.
- No OWUI token in URLs or the notes service, no cookies for the notes API, no
  direct browser access to the note directory.
- The direct loopback 8089 port is diagnostic and still requires the internal
  token.

## Deep links

`/memory/<ULID>` is the stable browser route. Chat answers link to this route,
not to a custom `note://` scheme. Renaming a note changes title only; the ID and
links remain stable.

## Build and reproducibility

1. Extract a clean pinned source tree at the revision above.
2. Apply `../notes-mvp/owui-memory.patch` with `git apply --check` first, then
   `git apply`. The patch checksum is recorded in
   `docs/experiments/pilot-final-report.md`.
3. Build the existing frontend toolchain from the pinned lockfile; do not add
   new frontend dependencies.
4. Build an experimental image tagged `chumba-open-webui-notes:exp`; do not
   replace the working image.
5. Verify the sidebar entry, `/memory`, `/memory/<id>`, reload/deep link, API
   auth, and that chat/tool behavior is unchanged.
6. The patch and checksum are stored under `experiments/notes-mvp/`.

Rollback: stop the experimental UI and use the upstream digest from `.env`; no
working volume or image is modified by the patch build itself.

## Not in this patch

- iframe/Artifacts workarounds;
- graph canvas, typed relation extraction, embeddings;
- automatic memory injection;
- deletion/bulk operations;
- OWUI 0.12.5 upgrade.

## Runtime prerequisites (separate approval)

- notes service container and image;
- notes data volume;
- loopback port 8089;
- experimental OWUI image build;
- CORS/auth smoke test.
