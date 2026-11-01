# Linked notes in Chumba: architecture assessment and MVP design

Status: research document; superseded for the decision by
`docs/decisions/0001-linked-notes.md`. No implementation started. No fork/build
of Open WebUI. Pinned Open WebUI: `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`.

## 1. What the pinned Open WebUI already provides

### Notes (native)

`src/routes/(app)/notes`, `src/lib/components/notes/*`, backend
`routers/notes.py`, `models/notes.py`.

- Markdown content stored in `Note.data.content.md` (plus editor JSON/HTML),
  not as ordinary files on disk.
- Note list, pinned notes, search (`/api/v1/notes/search`), import/export,
  per-note access grants, read/write access flags.
- Tiptap editor with undo/redo and client-side `data.versions` snapshots; version
  navigation/restore exists in the editor, but versions are stored in the note
  JSON and are not a server-enforced revision/audit log.
- "AI edit"/"Chat" menu and hidden note-chat endpoints; the UI can ask an agent
  to view/replace note content. There is no generic revision conflict check.
- Chat markdown recognizes `/notes/<id>` links and opens the note.
- No wiki-link `[[...]]` handling, no backlinks, no local graph, no typed
  relations, no stable frontmatter ID/checksum, no plain-file source of truth.

### Other native capabilities

- Knowledge collections/files: RAG sources and citations, not an editable linked
  note workspace.
- Memories: extracted user memory, not a human-readable linked notebook.
- Functions/Filters/Tools: request logic and tool calls; not a persistent
  workspace screen.
- Pipelines: external OpenAI-compatible model provider; not a UI extension.
- Chat Markdown/HTML and Artifacts can render iframes (`FullHeightIframe`,
  `HTMLToken`, `Artifacts.svelte`), but this is chat/artifact content, not a
  native notes route.
- The Notes editor is a Tiptap document; raw iframe authoring is not part of its
  schema. Whether a crafted `html` field survives reload must be tested before
  claiming an embedded panel.

## 2. UI integration options

| Option | What the user sees | Integration degree | Gaps |
|---|---|---|---|
| A. Reuse native Notes | Notes list/editor inside Chumba; `/notes/<id>` links from chat | Native, no fork | No files, wiki-links, backlinks, graph, typed relations, revision conflict; search is basic; agent edits are chat-driven and not revision-safe |
| B. Separate notes service | Service UI in an iframe or a dedicated tab | Depends on embedding | If only a link/tab: user switches apps, not target UX. If iframe in a Notes/Artifact page: acceptable but not native navigation; auth/CORS work. |
| C. Minimal frontend change | Native route/sidebar item/panel for the notes service | Native UX | Requires building a modified Open WebUI frontend (fork/patch); maintenance cost |

There is no supported "add a custom screen" API for Open WebUI: functions,
filters, tools and pipelines do not add a persistent route/panel. A true
in-Chumba screen therefore needs either an iframe host (B) or a frontend change
(C).

**Recommendation gate:** first test whether the Notes page can render an iframe
to a local notes service. If it can, use B with the native Notes shell. If it
cannot, C is the only option that meets the "inside Chumba" UX; use the smallest
possible frontend addition (one route/entry) only after approval. Do not call a
plain external link a full embedding.

## 3. Storage comparison

### Basic Memory

Current upstream (read from `basicmachines-co/basic-memory` main):

- AGPL-3.0-or-later; Python >= 3.12.
- Markdown files + SQLite/Postgres index; MCP server/API; wikilinks, entities,
  observations, typed relations, `bm_context` graph; search; semantic search is
  optional but brings FastEmbed/sqlite-vec/OpenAI deps.
- `fastmcp==4.1.3` prerelease; local CLI auto-updates by default; cloud is
  optional. No user-facing notes UI in the OSS package.
- Provides explicit edit tools and overwrite guards, but not the multi-user
  revision/audit and UI model this project needs.

Assessment: strong prior art and a good later backend if graph/search tooling
grows; for the MVP it is heavier than needed (AGPL review, Python 3.12,
prerelease FastMCP, optional semantic stack) and still requires a UI and
revision/idempotency layer. Do not adopt it just for graph lines.

### Minimal own store

- Markdown files on disk (`notes/<id>.md`) with YAML frontmatter.
- SQLite FTS5 index (stdlib `sqlite3`) for search; no embeddings.
- Revisions as immutable Markdown snapshots under `.revisions/<id>/`; restore
  creates a new revision.
- Stable ID (ULID) in frontmatter and filename; renaming title does not change ID.
- Atomic write + per-note lock; `expected_revision` = SHA-256 of current file.
- No graph database: adjacency is computed from typed links, depth-1 for the
  local graph.
- Small Python service (stdlib `http.server` or a single FastAPI app using the
  existing image) serves a static vanilla-JS UI and JSON API; MCP/OpenAPI tool
  adapter exposes the same operations.

Assessment: more code to write, but no new runtime stack, files remain readable,
and revision/conflict/idempotency are first-class. Recommended for the MVP.

## 4. Minimal note model

```yaml
id: 01K...ULID
title: Context memory policy
type: experiment          # project | decision | experiment | issue | reference
status: active            # active | superseded | archived
created_at: ...
updated_at: ...
updated_by: user|agent:<id>
revision: sha256:...      # optimistic concurrency token
sources:
  - docs/experiments/context-memory-policy.md
schema_version: 1
```

Body:

- Markdown, human-readable.
- Navigation link: `[[01K...|Title]]` — only navigation.
- Typed relation: `relation: depends_on [[01K...]]`, `supersedes [[...]]`,
  `implements [[...]]` — these become graph edges.
- Fact line: a statement with origin label:
  `> [VERIFIED RESULT] ...`, `> [TOOL REPORT] ...`, `> [HYPOTHESIS] ...`.
- A plain link is **not** proof of a fact or a relationship; only typed relations
  are asserted edges. Both are indexed for backlinks.

Minimum fields are id/title/type/status/body/links/sources/revision/updated_at/
updated_by. `created_at`, `schema_version` and revision snapshots are kept for
safe evolution and restore.

## 5. UX MVP

Required:

- list and full-text search;
- Markdown read/edit with preview;
- wiki-link navigation;
- backlinks panel;
- depth-1 local graph around the open note (SVG, no global graph required);
- last change/diff and restore;
- agent answer links to the note it read or changed.

Not in MVP: global vault graph, embeddings/semantic search, automatic memory
injection, real-time multi-user collaboration, public sharing.

## 6. Agent tools and safety

Operations:

- `search_notes(query, type?, status?, limit?)`
- `read_note(id)` — returns body and revision;
- `create_note(title, type, body, links, sources, idempotency_key)`
- `edit_note(id, expected_revision, body_or_patch, idempotency_key)`
- `get_links(id)`, `get_backlinks(id)`

Safety:

- `expected_revision` mismatch → 409 with current revision; agent rereads/merges.
- Idempotency keys are stored for a bounded window; retry returns the prior result
  and does not duplicate a note/edit.
- IDs are validated; resolve paths under the notes root; reject `..`, absolute
  paths, symlink escapes.
- Tool output is returned as data; the note body is never inserted as a system
  instruction. The agent must explicitly search/read notes; no automatic memory
  injection in the MVP.
- Secrets are not auto-saved; a lint check warns on likely credentials.
- Access is limited to the authenticated user and the experimental notes space.
- Delete, bulk edits and protected notes are out of MVP and require separate
  approval.

## 7. Memory rules

Do not save every message. Save only:

- confirmed results;
- current decisions and their reasons;
- task constraints;
- open questions;
- links to reports/artifacts.

Each item carries provenance: `USER REQUIREMENT`, `ASSISTANT PROPOSAL`,
`TOOL REPORT`, `VERIFIED RESULT`, `HYPOTHESIS`, `SUPERSEDED`. Changing a decision
creates a new revision and marks the old statement `SUPERSEDED`; it does not
erase history. Notes must never silently override the current user request or
system instructions. The first prototype uses explicit search/read only.

## 8. Acceptance scenario

On synthetic data:

1. Agent creates an experiment note.
2. Agent adds a verified result.
3. Agent links it to a project note with a typed relation.
4. User sees both notes and backlinks inside Chumba.
5. User edits a value in the note.
6. Agent's stale edit gets a revision conflict.
7. Agent rereads, merges and updates successfully.
8. A new session finds the note and continues the task.
9. User views the diff and restores the previous revision.

No private chats or real secrets.

## 9. What changes in Chumba

If Option B with iframe works:

- new `chumba-notes` service/container and volume for Markdown files;
- one pinned native Note (or a chat artifact) that embeds the service;
- MCP/OpenAPI tool registration for the agent;
- no Open WebUI core files changed.

If Option C is required:

- a single frontend route/sidebar entry and an iframe/panel component;
- build/publish a patched Open WebUI image — this is a fork and needs explicit
  approval;
- backend Notes/chat files remain unchanged;
- keep the service behind the same auth boundary and local network.

## 10. MVP / non-MVP and fork position

MVP: one experimental notes space; Markdown + SQLite FTS5; list/search/edit;
wiki-links/backlinks; local graph; revisions/diff/restore; MCP tools with
revision/idempotency; agent links; optional iframe shell.

Not MVP: global graph, embeddings, automatic context assembly, multi-user
collaboration, deletion, bulk migration, public sharing, Basic Memory adoption.

No fork for storage/API/tools. Fork only if the iframe test fails and the
in-Chumba panel is mandatory; then keep the frontend diff to one route/entry and
document the upgrade burden.

## 11. UI-spike result (2026-10-31)

Tested on the isolated 0.11.4 instance:

- Native Notes strips an authored iframe: the note API stored `md`/`html`, but
  the Tiptap page rendered 0 words and no iframe. Normal Markdown rendered fine.
- A generic chat Markdown iframe renders, but its sandbox has no
  `allow-scripts`, so the notes JavaScript cannot run: no save, no note
  switching. Console: blocked script execution because the iframe is sandboxed.
- A chat link opens the notes page in a new browser tab, not inside Chumba.
- The standalone static mock works: focus, textarea save, reload persistence,
  and A↔B navigation; this only validates the page itself.

Gate: **B — possible only in a degraded form, not a good basis for the target
UX.** Native Notes cannot host it; chat iframe cannot run it; a new tab is not
the target. A minimal frontend route/sidebar patch (Option C) is the only path
to a real in-Chumba notes screen. Do not build it until the frontend maintenance
cost is accepted. A supported file-content iframe may allow scripts, but it is
still a chat/artifact surface and was not tested.

## 12. Open decisions

1. Approve a minimal frontend route/sidebar patch if native in-Chumba notes are
   required (Option C); no patch yet.
2. Confirm whether plain Markdown files are a hard requirement (if not, native
   Notes could be a faster A, but still lacks backlinks/graph/revisions).
3. Confirm the notes service runtime (existing Open WebUI image vs a new minimal
   Python image) and auth boundary for the pilot.
4. Confirm that the pilot may use a separate read/write notes volume and an
   experimental MCP tool registration.
