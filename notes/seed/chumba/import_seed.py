#!/usr/bin/env python3
"""Import/update managed seed notes through the notes HTTP API.

Default is dry-run.  A seed note is updated only when its current revision
equals the revision recorded by the previous seed import; a user edit therefore
turns into a reported conflict, never a blind overwrite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

GIT_REV = "6039cf4e795a3e7b50a5a82e35d469815eb87616"
LINK_RE = re.compile(r"\[\[([A-Za-z0-9_-]{1,64})(?:\|[^\]]+)?\]\]")
SECRET_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{16,}|bearer\s+[A-Za-z0-9._-]{20,}|(?:api[_-]?key|secret|password|token)\s*=\s*[^\s]+)")
ROOT_ID = "seed-chumba-map"
BASE_DEFAULT = "http://127.0.0.1:3001"


def parse_seed(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        raise ValueError(f"{path}: malformed frontmatter")
    meta = {}
    for line in parts[1].splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"title"}:
            meta[key] = json.loads(value) if value.startswith('"') else value
        elif key == "sources":
            meta[key] = json.loads(value)
        else:
            meta[key] = value
    body = parts[2].lstrip("\n").rstrip("\n")
    meta["body"] = body
    meta["file"] = str(path.relative_to(path.parent))
    return meta


def load_seeds(seed_dir: Path) -> list[dict]:
    notes = []
    for path in sorted(seed_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        note = parse_seed(path)
        if not str(note.get("id", "")).startswith("seed-"):
            raise ValueError(f"{path}: id must start with seed-")
        notes.append(note)
    ids = [note["id"] for note in notes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate seed ids")
    return notes


def validate(notes: list[dict], seed_dir: Path) -> list[str]:
    errors = []
    ids = {note["id"] for note in notes}
    titles = {}
    for note in notes:
        for link in LINK_RE.findall(note["body"]):
            if link not in ids:
                errors.append(f"{note['id']}: broken link -> {link}")
        titles.setdefault(note.get("title"), []).append(note["id"])
        text = note["body"] + "\n" + "\n".join(note.get("sources") or [])
        if SECRET_RE.search(text):
            errors.append(f"{note['id']}: possible secret-like value in seed")
    for title, values in titles.items():
        if len(values) > 1:
            errors.append(f"duplicate title {title!r}: {values}")
    if ROOT_ID not in ids:
        errors.append(f"missing root note {ROOT_ID}")
        return errors
    graph = {note["id"]: set(LINK_RE.findall(note["body"])) for note in notes}
    reachable = set()
    stack = [ROOT_ID]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(graph.get(current, set()) - reachable)
    for note in notes:
        if note["id"] not in reachable:
            errors.append(f"unreachable from root: {note['id']}")
    return errors


def request(base: str, token: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"message": raw[:500]}


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(args) -> int:
    seed_dir = Path(args.seed_dir).resolve()
    notes = load_seeds(seed_dir)
    errors = validate(notes, seed_dir)
    if errors:
        print("Seed validation failed:")
        for error in errors:
            print(" -", error)
        return 2
    print(f"Seed notes: {len(notes)}; links: {sum(len(LINK_RE.findall(n['body'])) for n in notes)}; validation OK")
    manifest_path = seed_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    base = args.url.rstrip("/")
    token = args.token or os.environ.get("OWUI_TOKEN", "")
    if not token or token.startswith("<") or token.count(".") != 2:
        print("A real OWUI bearer token is required. Sign in and set OWUI_TOKEN; placeholders are rejected.")
        return 2

    status, listing = request(base, token, "GET", "/api/v1/memory/notes?limit=200")
    if status in (401, 403):
        print("Authentication failed: OWUI_TOKEN is missing, expired or not a real token; no writes performed.")
        return 2
    if status != 200 or not isinstance(listing, dict):
        print(f"Cannot list notes: HTTP {status}: {listing}")
        return 2
    existing_by_id = {item["id"]: item for item in listing.get("notes", [])}
    actions = []
    next_manifest = dict(manifest)
    for note in notes:
        note_id = note["id"]
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "title": note["title"],
                    "type": note.get("type", "reference"),
                    "status": note.get("status", "active"),
                    "body": note["body"],
                    "sources": note.get("sources") or [],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        current = existing_by_id.get(note_id)
        if not current:
            actions.append((note_id, "create", "missing"))
            if args.apply:
                status, result = request(base, token, "POST", "/api/v1/memory/notes", {
                    "id": note_id,
                    "title": note["title"],
                    "type": note.get("type", "reference"),
                    "status": note.get("status", "active"),
                    "body": note["body"],
                    "sources": note.get("sources") or [],
                })
                if status != 200:
                    print(f"CREATE CONFLICT {note_id}: HTTP {status}: {result}")
                    continue
                next_manifest[note_id] = {"revision": result["revision"], "content_sha256": content_hash}
            continue

        previous = manifest.get(note_id) or {}
        if previous.get("content_sha256") == content_hash and previous.get("revision") == current.get("revision"):
            actions.append((note_id, "unchanged", "seed hash matches"))
            continue
        if previous.get("revision") != current.get("revision"):
            actions.append((note_id, "conflict", "current revision differs from last seed import; manual edit preserved"))
            continue
        actions.append((note_id, "update", f"seed revision {current['revision'][:12]}"))
        if args.apply:
            status, result = request(base, token, "PUT", f"/api/v1/memory/notes/{note_id}", {
                "title": note["title"],
                "type": note.get("type", "reference"),
                "status": note.get("status", "active"),
                "body": note["body"],
                "sources": note.get("sources") or [],
                "expected_revision": current["revision"],
                "idempotency_key": f"seed-{note_id}-{content_hash}",
            })
            if status != 200:
                print(f"UPDATE CONFLICT {note_id}: HTTP {status}: {result}")
                continue
            next_manifest[note_id] = {"revision": result["revision"], "content_sha256": content_hash}

    for note_id, action, detail in actions:
        print(f"{action:9} {note_id}: {detail}")
    if args.apply:
        write_manifest(manifest_path, next_manifest)
        print(f"manifest: {manifest_path}")
    else:
        print("dry-run: no API writes, no manifest update")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=BASE_DEFAULT)
    parser.add_argument("--token", default="")
    parser.add_argument("--seed-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        notes = load_seeds(Path(args.seed_dir))
        errors = validate(notes, Path(args.seed_dir))
        print("validation OK" if not errors else "\n".join(errors))
        return 0 if not errors else 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
