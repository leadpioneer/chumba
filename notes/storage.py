from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LINK_RE = re.compile(r"\[\[([A-Za-z0-9_-]{1,64})(?:\|([^\]]+))?\]\]")
TYPES = {"project", "decision", "experiment", "issue", "reference"}
STATUSES = {"active", "superseded", "archived"}


class NotesError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class NotFound(NotesError):
    status = 404
    code = "not_found"


class Conflict(NotesError):
    status = 409
    code = "conflict"


class StorageError(NotesError):
    status = 500
    code = "storage_error"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_links(body: str) -> list[dict[str, str]]:
    links = []
    seen = set()
    for match in LINK_RE.finditer(body or ""):
        target = match.group(1)
        if target in seen:
            continue
        seen.add(target)
        links.append({"id": target, "title": match.group(2) or target})
    return links


class NotesStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.notes_dir = self.root / "notes"
        self.revisions_dir = self.root / "revisions"
        self.db_path = self.root / "notes.db"
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.revisions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()
        self.reindex()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
                "USING fts5(id UNINDEXED, title, body)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS idempotency ("
                "key TEXT PRIMARY KEY, operation TEXT NOT NULL, payload_hash TEXT NOT NULL, "
                "note_id TEXT NOT NULL, result_revision TEXT, created_at TEXT NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def _path(self, note_id: str) -> Path:
        if not ID_RE.fullmatch(note_id or ""):
            raise NotesError("invalid note id")
        return self.notes_dir / f"{note_id}.md"

    def _normalize_body(self, body: str) -> str:
        return (body or "").rstrip("\n")

    def _canonical(self, meta: dict[str, Any], body: str) -> str:
        clean = {key: value for key, value in meta.items() if key != "revision"}
        return json.dumps(
            {"meta": clean, "body": self._normalize_body(body)},
            ensure_ascii=False,
            sort_keys=True,
        )

    def _revision(self, meta: dict[str, Any], body: str) -> str:
        return _sha(self._canonical(meta, body))

    def _render(self, meta: dict[str, Any], body: str) -> str:
        revision = self._revision(meta, body)
        lines = [
            "---",
            f"id: {meta['id']}",
            f"title: {_quote(meta['title'])}",
            f"type: {meta['type']}",
            f"status: {meta['status']}",
            f"created_at: {meta['created_at']}",
            f"updated_at: {meta['updated_at']}",
            f"updated_by: {_quote(meta['updated_by'])}",
            f"sources: {json.dumps(meta.get('sources') or [], ensure_ascii=False)}",
            f"schema_version: {meta.get('schema_version', 1)}",
            f"revision: {revision}",
            "---",
            "",
            self._normalize_body(body) + "\n",
        ]
        return "\n".join(lines)

    def _parse(self, text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---\n"):
            raise StorageError("note has no frontmatter")
        parts = text.split("---\n", 2)
        if len(parts) != 3:
            raise StorageError("malformed note frontmatter")
        meta: dict[str, Any] = {}
        for line in parts[1].splitlines():
            if not line.strip() or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key in {"title", "updated_by"}:
                try:
                    meta[key] = json.loads(value)
                except json.JSONDecodeError:
                    meta[key] = value.strip('"')
            elif key == "sources":
                try:
                    meta[key] = json.loads(value)
                except json.JSONDecodeError:
                    meta[key] = []
            elif key == "schema_version":
                meta[key] = int(value or 1)
            else:
                meta[key] = value
        body = self._normalize_body(parts[2].lstrip("\n"))
        if not isinstance(meta.get("sources"), list):
            meta["sources"] = []
        return meta, body

    def _read(self, note_id: str) -> tuple[dict[str, Any], str, str]:
        path = self._path(note_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise NotFound(f"note {note_id} not found") from exc
        meta, body = self._parse(text)
        expected = self._revision(meta, body)
        if meta.get("revision") != expected:
            raise StorageError(f"note {note_id} revision mismatch")
        return meta, body, text

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def _write_snapshot(self, meta: dict[str, Any], body: str, text: str) -> None:
        revision = meta["revision"]
        self._atomic_write(self.revisions_dir / meta["id"] / f"{revision}.md", text)

    def _index(self, note_id: str, title: str, body: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
            conn.execute(
                "INSERT INTO notes_fts(id, title, body) VALUES (?, ?, ?)",
                (note_id, title, body),
            )
            conn.commit()
        finally:
            conn.close()

    def reindex(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM notes_fts")
                count = 0
                for path in sorted(self.notes_dir.glob("*.md")):
                    try:
                        meta, body = self._parse(path.read_text(encoding="utf-8"))
                        if meta.get("revision") != self._revision(meta, body):
                            continue
                        conn.execute(
                            "INSERT INTO notes_fts(id, title, body) VALUES (?, ?, ?)",
                            (meta["id"], meta.get("title", ""), body),
                        )
                        count += 1
                    except Exception as exc:
                        print(f"notes: reindex skipped {path.name}: {exc}", flush=True)
                conn.commit()
                return count
            finally:
                conn.close()

    def _idempotency(self, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM idempotency WHERE key = ?", (key,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _set_idempotency(
        self, key: str, operation: str, payload_hash: str, note_id: str, revision: str
    ) -> None:
        if not key:
            return
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO idempotency"
                "(key, operation, payload_hash, note_id, result_revision, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, operation, payload_hash, note_id, revision, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _payload_hash(self, fields: dict[str, Any]) -> str:
        return _sha(json.dumps(fields, ensure_ascii=False, sort_keys=True))

    def _summary(self, meta: dict[str, Any], body: str) -> dict[str, Any]:
        return {
            "id": meta["id"],
            "title": meta.get("title", ""),
            "type": meta.get("type", "reference"),
            "status": meta.get("status", "active"),
            "updated_at": meta.get("updated_at"),
            "updated_by": meta.get("updated_by", ""),
            "revision": meta.get("revision"),
            "links": _parse_links(body),
        }

    def get(self, note_id: str) -> dict[str, Any]:
        with self._lock:
            meta, body, _ = self._read(note_id)
            note = self._summary(meta, body)
            note["body"] = body
            note["sources"] = meta.get("sources") or []
            note["created_at"] = meta.get("created_at")
            note["backlinks"] = self._backlinks(note_id)
            return note

    def _backlinks(self, note_id: str) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.notes_dir.glob("*.md")):
            try:
                meta, body, _ = self._read(path.stem)
            except Exception:
                continue
            if any(link["id"] == note_id for link in _parse_links(body)):
                result.append({"id": meta["id"], "title": meta.get("title", "")})
        return result

    def list(self, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        with self._lock:
            ids: list[str] = []
            if query:
                conn = self._connect()
                try:
                    rows = conn.execute(
                        "SELECT id FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
                        (query, limit),
                    ).fetchall()
                    ids = [row["id"] for row in rows]
                except sqlite3.Error:
                    ids = []
                finally:
                    conn.close()
                if not ids:
                    needle = query.lower()
                    for path in sorted(self.notes_dir.glob("*.md")):
                        try:
                            meta, body, _ = self._read(path.stem)
                        except Exception:
                            continue
                        if needle in (meta.get("title", "") + "\n" + body).lower():
                            ids.append(meta["id"])
                            if len(ids) >= limit:
                                break
            else:
                ids = [path.stem for path in sorted(self.notes_dir.glob("*.md"))]
            notes = []
            for note_id in ids[:limit]:
                try:
                    meta, body, _ = self._read(note_id)
                    notes.append(self._summary(meta, body))
                except Exception:
                    continue
            notes.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
            return notes

    def create(
        self,
        *,
        title: str,
        type: str = "reference",
        status: str = "active",
        body: str = "",
        sources: list[str] | None = None,
        author: str = "user",
        idempotency_key: str = "",
        note_id: str | None = None,
    ) -> dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise NotesError("title is required")
        if type not in TYPES:
            raise NotesError(f"invalid type {type!r}")
        if status not in STATUSES:
            raise NotesError(f"invalid status {status!r}")
        fields = {
            "title": title,
            "type": type,
            "status": status,
            "body": body or "",
            "sources": list(sources or []),
        }
        payload_hash = self._payload_hash(fields)
        with self._lock:
            if note_id is not None:
                self._path(note_id)
                if self._path(note_id).exists():
                    raise Conflict("note id already exists")
            elif idempotency_key:
                previous = self._idempotency(idempotency_key)
                note_id = "n" + _sha(idempotency_key)[:24]
                if previous:
                    if previous["operation"] != "create" or previous["payload_hash"] != payload_hash:
                        raise Conflict("idempotency key already used with a different payload")
                    try:
                        existing = self.get(previous["note_id"])
                        if existing.get("revision") == previous.get("result_revision"):
                            return existing
                        raise Conflict("idempotent create has a conflicting published revision")
                    except NotFound:
                        pass
                    # A pending record without the published file can be retried.
                elif self._path(note_id).exists():
                    existing = self.get(note_id)
                    same = (
                        existing.get("title") == fields["title"]
                        and existing.get("type") == fields["type"]
                        and existing.get("status") == fields["status"]
                        and existing.get("body") == fields["body"]
                        and existing.get("sources") == fields["sources"]
                    )
                    if same:
                        return existing
                    raise Conflict("note id already exists for this idempotency key")
            else:
                note_id = uuid.uuid4().hex

            now = _now()
            meta = {
                "id": note_id,
                "title": fields["title"],
                "type": fields["type"],
                "status": fields["status"],
                "sources": fields["sources"],
                "created_at": now,
                "updated_at": now,
                "updated_by": author,
                "schema_version": 1,
            }
            note_body = fields["body"]
            revision = self._revision(meta, note_body)
            meta["revision"] = revision
            text = self._render(meta, note_body)
            self._set_idempotency(idempotency_key, "create", payload_hash, note_id, revision)
            self._write_snapshot(meta, note_body, text)
            self._atomic_write(self._path(note_id), text)
            self._index(note_id, title, note_body)
            return self.get(note_id)

    def edit(
        self,
        note_id: str,
        *,
        expected_revision: str,
        changes: dict[str, Any],
        author: str = "user",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not expected_revision:
            raise NotesError("expected_revision is required")
        with self._lock:
            meta, body, _ = self._read(note_id)
            fields = {
                "title": changes.get("title", meta.get("title", "")),
                "type": changes.get("type", meta.get("type", "reference")),
                "status": changes.get("status", meta.get("status", "active")),
                "body": changes.get("body", body),
                "sources": changes.get("sources", meta.get("sources") or []),
            }
            payload_hash = self._payload_hash(fields)
            previous = self._idempotency(idempotency_key)
            if previous:
                if previous["operation"] != "edit" or previous["payload_hash"] != payload_hash:
                    raise Conflict("idempotency key already used with a different payload")
                if meta.get("revision") == previous.get("result_revision"):
                    return self.get(note_id)
                if meta.get("revision") != expected_revision:
                    raise Conflict(
                        "idempotent edit was already applied; the note has a newer revision",
                        {"current_revision": meta.get("revision")},
                    )
            if meta.get("revision") != expected_revision:
                raise Conflict(
                    "revision conflict",
                    {"current_revision": meta.get("revision"), "expected_revision": expected_revision},
                )
            if fields["type"] not in TYPES or fields["status"] not in STATUSES:
                raise NotesError("invalid type or status")
            if not str(fields["title"]).strip():
                raise NotesError("title is required")
            now = _now()
            new_meta = {
                **meta,
                "title": fields["title"],
                "type": fields["type"],
                "status": fields["status"],
                "sources": fields["sources"],
                "updated_at": now,
                "updated_by": author,
                "schema_version": meta.get("schema_version", 1),
            }
            new_meta.pop("revision", None)
            new_body = fields["body"]
            revision = self._revision(new_meta, new_body)
            new_meta["revision"] = revision
            text = self._render(new_meta, new_body)
            self._set_idempotency(idempotency_key, "edit", payload_hash, note_id, revision)
            self._write_snapshot(new_meta, new_body, text)
            self._atomic_write(self._path(note_id), text)
            self._index(note_id, new_meta["title"], new_body)
            return self.get(note_id)

    def history(self, note_id: str) -> list[dict[str, Any]]:
        with self._lock:
            meta, body, _ = self._read(note_id)
            entries = []
            for path in sorted((self.revisions_dir / note_id).glob("*.md")):
                try:
                    old_meta, old_body = self._parse(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                entries.append(
                    {
                        "revision": old_meta.get("revision"),
                        "title": old_meta.get("title", ""),
                        "updated_at": old_meta.get("updated_at"),
                        "updated_by": old_meta.get("updated_by", ""),
                        "body": old_body,
                    }
                )
            if not any(item["revision"] == meta.get("revision") for item in entries):
                entries.append(
                    {
                        "revision": meta.get("revision"),
                        "title": meta.get("title", ""),
                        "updated_at": meta.get("updated_at"),
                        "updated_by": meta.get("updated_by", ""),
                        "body": body,
                    }
                )
            entries.sort(key=lambda item: item.get("updated_at") or "")
            return entries

    def diff(self, note_id: str, from_revision: str | None = None, to_revision: str | None = None) -> dict[str, Any]:
        with self._lock:
            meta, body, _ = self._read(note_id)
            history = self.history(note_id)
            if not history:
                raise NotFound("no history")
            from_rev = from_revision or history[0]["revision"]
            to_rev = to_revision or meta.get("revision")
            old = next((item["body"] for item in history if item["revision"] == from_rev), None)
            new = next((item["body"] for item in history if item["revision"] == to_rev), None)
            if old is None or new is None:
                raise NotFound("revision not found")
            diff = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile=from_rev[:12],
                    tofile=to_rev[:12],
                )
            )
            return {"from": from_rev, "to": to_rev, "diff": diff}

    def restore(
        self,
        note_id: str,
        *,
        revision: str,
        expected_revision: str,
        author: str = "user",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            current_meta, _, _ = self._read(note_id)
            if current_meta.get("revision") != expected_revision:
                raise Conflict("revision conflict", {"current_revision": current_meta.get("revision")})
            snapshot = self.revisions_dir / note_id / f"{revision}.md"
            if not snapshot.exists():
                raise NotFound("revision not found")
            old_meta, old_body = self._parse(snapshot.read_text(encoding="utf-8"))
            fields = {
                "title": old_meta.get("title", ""),
                "type": old_meta.get("type", "reference"),
                "status": old_meta.get("status", "active"),
                "body": old_body,
                "sources": old_meta.get("sources") or [],
            }
            payload_hash = self._payload_hash({"restore": revision, **fields})
            previous = self._idempotency(idempotency_key)
            if previous:
                if previous["operation"] != "restore" or previous["payload_hash"] != payload_hash:
                    raise Conflict("idempotency key already used with a different payload")
                if current_meta.get("revision") == previous.get("result_revision"):
                    return self.get(note_id)
                if current_meta.get("revision") != expected_revision:
                    raise Conflict("idempotent restore was already applied")
            now = _now()
            new_meta = {
                **current_meta,
                "title": fields["title"],
                "type": fields["type"],
                "status": fields["status"],
                "sources": fields["sources"],
                "updated_at": now,
                "updated_by": author,
                "schema_version": current_meta.get("schema_version", 1),
            }
            new_meta.pop("revision", None)
            new_body = fields["body"]
            new_revision = self._revision(new_meta, new_body)
            new_meta["revision"] = new_revision
            text = self._render(new_meta, new_body)
            self._set_idempotency(idempotency_key, "restore", payload_hash, note_id, new_revision)
            self._write_snapshot(new_meta, new_body, text)
            self._atomic_write(self._path(note_id), text)
            self._index(note_id, new_meta["title"], new_body)
            return self.get(note_id)

    def graph(self, note_id: str) -> dict[str, Any]:
        with self._lock:
            meta, body, _ = self._read(note_id)
            out_links = _parse_links(body)
            backlinks = self._backlinks(note_id)
            nodes = {note_id: {"id": note_id, "title": meta.get("title", "")}}
            edges = []
            for link in out_links:
                nodes.setdefault(link["id"], {"id": link["id"], "title": link["title"]})
                edges.append({"source": note_id, "target": link["id"], "type": "link"})
            for link in backlinks:
                nodes.setdefault(link["id"], {"id": link["id"], "title": link["title"]})
                edges.append({"source": link["id"], "target": note_id, "type": "backlink"})
            return {"nodes": list(nodes.values()), "edges": edges}
