# Chumba project knowledge seeds

Managed documentation notes for the experimental linked-notes MVP. The Markdown
files here are the reviewable source; `manifest.json` records the last seed
revision and content hash for each note.

## Import

The importer uses only the same-origin notes HTTP API (via the experimental OWUI)
and never writes storage or revisions directly.

```powershell
# validate links, reachability, duplicate titles and secret-like values
python notes/seed/chumba/import_seed.py --validate-only

# show planned actions; no writes
# Set OWUI_TOKEN in the environment (do not commit it)
python notes/seed/chumba/import_seed.py --url http://127.0.0.1:3001

# apply creates/updates and writes manifest.json
python notes/seed/chumba/import_seed.py --url http://127.0.0.1:3001 --apply
```

Rules:

- IDs are stable `seed-*` IDs; reruns do not duplicate notes.
- A seed note is updated only if its current revision equals the revision in
  `manifest.json`.
- A manual edit changes the revision; the importer reports a conflict and leaves
  the manual edit untouched. Resolve by reviewing the note and updating the seed
  file (there is no blind overwrite).
- Managed seed notes are marked by the `seed-` ID prefix and a UI badge. Demo/test
  notes from earlier experiments are not seed notes and are not linked from the
  project maps.
- Do not put secrets, tokens, credentials or personal chat contents in seeds.
  Use repository-relative paths and status labels.

## Scope and status

Every substantive note distinguishes: implemented, runtime verified, unit/mock,
experimental, proposed, not verified, or superseded. Working configuration is
32K and pinned OWUI 0.11.4; 64K, built-in compaction, the budget guard and the
patched `/memory` image are experimental and not active in the working stack.

The root map is `seed-chumba-map.md`; start there.
