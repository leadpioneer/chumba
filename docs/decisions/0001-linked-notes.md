# ADR 0001: Linked notes MVP storage and UI integration

Status: **Accepted and implemented in the experimental MVP**  
Date: 2026-10-31; implementation verified 2026-11-01

## Context

We need a small vertical slice: readable linked Markdown notes, editable by the
user inside Chumba, searchable, with backlinks, revisions/diff/restore, and an
agent that reads/edits the same data through one tool interface. No graph
database, embeddings, automatic memory injection, or full Obsidian clone.

Pinned Open WebUI: `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d` (0.11.4). The
native Notes page cannot host a usable embedded app (iframe stripped), and a
chat iframe cannot run scripts. A minimal frontend route/sidebar patch is
allowed. Upgrade to 0.12.5 is out of scope.

## Decision

### Storage

Use the **minimal own Markdown + SQLite FTS5 store**.

- Markdown files are the single source of truth.
- File name is the stable ID: `notes/<ULID>.md`; title is metadata, so renaming
  does not break links.
- YAML frontmatter: `id`, `title`, `type`, `status`, `sources`, `revision`,
  `created_at`, `updated_at`, `updated_by`, `schema_version`.
- SQLite FTS5 is a derived index; it must be rebuildable from files (`reindex`).
- Revisions are immutable snapshots under `.revisions/<id>/`; a restore creates
  a new revision, never deletes history.
- Links are `[[<ULID>|Title]]`; backlinks are computed from current file bodies.
- No graph database, no embeddings.

### UI integration

Use **Option A: a small native Svelte route/component** in the patched OWUI
frontend, not an iframe.

- New route `/memory` (list/search) and `/memory/<id>` (note view/editor).
- One sidebar entry.
- Component calls the same-origin `/api/v1/memory/...` proxy. OWUI verifies the
  user with its normal auth dependency; the proxy forwards to the notes service
  with a separate internal token. The browser never sends the OWUI JWT to the
  notes service, and no token appears in a URL or frontend bundle.
- Deep links `/memory/<id>` work from chat markdown links.
- No chat/tool execution changes.
- Patch is based on the pinned revision, stored as a reproducible patch, and
  built into an experimental image; the working image stays upstream.

### Auth and access boundaries

- UI user: OWUI remains the auth boundary. The browser uses its normal OWUI
  session; the OWUI backend route `/api/v1/memory/{path}` checks the user and
  forwards with a separate internal service token. `WEBUI_SECRET_KEY` is not
  sent to the notes service.
- Agent: a separate `NOTES_AGENT_TOKEN` authenticates the MCP transport. It is
  scoped to the notes service and is not the OWUI user token.
- The notes API is reachable only through OWUI's same-origin proxy for the
  browser; the direct loopback port is diagnostic and still requires the
  internal token. MCP is internal-only. No `0.0.0.0` browser exposure.
- No CORS wildcard is needed because the browser path is same-origin. No cookies
  for the notes service; no mutations via GET.
- Credentials live in the experimental environment, never in URLs, logs or
  notes. The service does not log note bodies or tokens.

### Tool interface

Use **MCP only** (the existing Chumba integration), implemented with the same
`mcp>=1.2,<2` family already used by `study`. Do not build a parallel OpenAPI
tool layer. Tools:

- `search_notes`
- `read_note`
- `create_note`
- `edit_note(expected_revision)`
- `get_backlinks`

The UI's HTTP JSON API is not a second agent tool interface.

## Options considered

### Storage

| Criterion | Basic Memory | Own Markdown + SQLite |
|---|---|---|
| Markdown files | yes | yes |
| Search | yes (index) | FTS5 |
| Wiki-links/backlinks | yes | yes, computed |
| Revisions/diff/restore | not a first-class user audit | designed in MVP |
| Optimistic concurrency | not the primary contract | `expected_revision` SHA-256 |
| Idempotency | not the primary contract | key + payload hash |
| UI | none | ours |
| Dependencies | Python ≥3.12, FastMCP 4.1.3 prerelease, optional semantic stack | stdlib + SQLite + existing `mcp` |
| License | AGPL-3.0-or-later | project code; no copyleft dependency |
| Maintenance | upstream + adapter + UI | small code we own |

Basic Memory is technically credible and has useful graph/MCP prior art, but for
this MVP it still leaves the UI, revision/audit, concurrency and idempotency
layers to us, while adding a prerelease MCP stack and an AGPL review. This is a
technical choice, not a legal conclusion; counsel must review AGPL if the option
is revisited.

The own option is not free: file/index consistency, revisions, migrations, auth,
and agent tools are real work. They are bounded here because the MVP is
single-user and has no embeddings/graph DB.

### UI

| Option | Auth | Deep links | Build/update cost | Verdict |
|---|---|---|---|---|
| A. Native Svelte component | OWUI JWT via header; no token in URL | `/memory/<id>` | one route/sidebar patch; rebuild OWUI image | chosen |
| B. Route with iframe | iframe needs token handoff; sandbox/CSP | harder; iframe navigation | service UI already built, but auth bridge is complex | rejected for MVP |
| C. Chat/file iframe or Artifacts | sandbox without scripts; not a notes route | no | no native route | rejected |

A is more frontend code than an iframe, but removes the sandbox/token handoff
problem, keeps deep links native, and avoids a second embedded-origin auth model.
The patch is one route, one component, one sidebar entry, and an API client.

## Source of truth and crash recovery

- Source of truth: Markdown files, canonical bytes including frontmatter.
- `revision = sha256(canonical file bytes)`; editing requires the expected
  revision.
- Write order:
  1. validate expected revision and payload hash;
  2. write a new immutable revision snapshot (temp + fsync + atomic rename);
  3. write the note file (temp + fsync + atomic rename);
  4. update/replace the FTS row.
- Crash cases:
  - after snapshot, before note rename: orphan snapshot; current note/index
    remain old; startup reconciliation ignores unreferenced snapshots;
  - after note rename, before index update: startup `verify` compares file SHA
    with index metadata and reindexes;
  - index corruption: `reindex` rebuilds entirely from files.
- External editing while the service runs is **not supported** in the MVP. Files
  are readable, but manual edits are detected as a revision mismatch; an explicit
  `reindex`/import command can adopt them as a new revision later.
- Restore reads a snapshot and writes it as a new revision; history is not
  rewritten.
- `expected_revision` covers body plus significant metadata (title, type, status,
  sources, links). It does not cover purely derived index state.
- Idempotency: key + operation + canonical payload hash; same key with a
  different payload is a conflict; duplicate create/edit returns the prior
  result.

## Consequences and costs

Positive:

- files are readable and portable;
- links are stable by ID;
- index is disposable;
- revisions and conflicts are first-class;
- no new graph/embedding stack;
- one agent tool interface (MCP) matching Chumba.

Costs:

- we own file/index reconciliation and repair;
- we own revision storage and diff/restore;
- we own the small OWUI backend proxy and internal service token;
- we own the Svelte route and must re-apply the patch on OWUI upgrades;
- frontend build and experimental image are required for runtime;
- local loopback exposure is not remote multi-user access.

## MVP / non-MVP

MVP: list, text search, Markdown edit/preview, create, navigation links,
backlinks, chat links, optimistic concurrency, history/diff/restore, agent reads
and edits the same files, provenance/source fields.

Not MVP: depth-1 graph, global graph, typed relations extraction, embeddings,
automatic context injection, deletion, bulk operations, multi-user collaboration,
remote access, Basic Memory adoption.

## Implemented experimental MVP

1. Storage core: Markdown files, frontmatter, canonical SHA-256 revision,
   snapshots, FTS5 rebuild, conflict/idempotency and crash-recovery tests.
2. HTTP JSON API: same-origin OWUI proxy, list/search/read/create/edit/backlinks/
   graph/history/diff/restore, expected_revision, idempotency, path traversal.
3. MCP wrapper: `search_notes`, `read_note`, `create_note`, `edit_note`,
   `get_backlinks`; agent token and real conflict/re-read flow verified.
4. Frontend patch: `/memory` route, editor/preview, backlinks, history, diff,
   restore, depth-1 graph, sidebar entry, deep links; experimental image built
   from the pinned revision.
5. E2E: user create/edit; agent create/edit; user edit; stale agent conflict;
   re-read and successful update; backlinks/graph; diff/restore; restart;
   unauthorized access rejected.

The reproducible patch is `experiments/notes-mvp/owui-memory.patch` with its
checksum in the notes MVP results document. The working image was not replaced.
