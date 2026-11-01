from __future__ import annotations

import hmac
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from storage import Conflict, NotesError, NotesStore, NotFound

INTERNAL_TOKEN = os.environ.get("NOTES_INTERNAL_TOKEN", "")
AGENT_TOKEN = os.environ.get("NOTES_AGENT_TOKEN", "")
if not INTERNAL_TOKEN or not AGENT_TOKEN:
    raise SystemExit("NOTES_INTERNAL_TOKEN and NOTES_AGENT_TOKEN are required")

store = NotesStore(os.environ.get("NOTES_DATA_DIR", "/data"))
mcp = FastMCP("chumba-notes")
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["chumba-notes:8080", "chumba-notes", "127.0.0.1:8080", "localhost:8080", "127.0.0.1:8089", "localhost:8089"],
    allowed_origins=["http://chumba-notes:8080", "http://127.0.0.1:8080", "http://localhost:8080", "http://127.0.0.1:8089", "http://localhost:8089"],
)


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in {"/health", "/api/health"}:
            await self.app(scope, receive, send)
            return
        expected = AGENT_TOKEN if path.startswith("/mcp") else INTERNAL_TOKEN
        headers = Headers(scope=scope)
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token, expected):
            payload = json.dumps({"error": "unauthorized"}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        await self.app(scope, receive, send)


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _error(exc: NotesError) -> JSONResponse:
    payload = {"error": exc.code, "message": str(exc)}
    payload.update(getattr(exc, "details", {}) or {})
    return JSONResponse(payload, status_code=exc.status)


def _user(request) -> str:
    return request.headers.get("x-chumba-user") or "user"


async def _body(request) -> dict:
    try:
        value = await request.json()
    except Exception as exc:
        raise NotesError("invalid JSON body") from exc
    if not isinstance(value, dict):
        raise NotesError("JSON object required")
    return value


@mcp.custom_route("/api/health", methods=["GET"])
async def health(request):
    return _json({"ok": True})


@mcp.custom_route("/api/notes", methods=["GET", "POST"])
async def notes_collection(request):
    try:
        if request.method == "GET":
            query = request.query_params.get("q") or request.query_params.get("query")
            limit = int(request.query_params.get("limit", "50"))
            return _json({"notes": store.list(query, limit)})
        body = await _body(request)
        note = store.create(
            title=body.get("title", ""),
            type=body.get("type", "reference"),
            status=body.get("status", "active"),
            body=body.get("body", ""),
            sources=body.get("sources") or [],
            author=_user(request),
            idempotency_key=body.get("idempotency_key", ""),
            note_id=body.get("id"),
        )
        return _json(note)
    except NotesError as exc:
        return _error(exc)


@mcp.custom_route("/api/notes/{note_id}", methods=["GET", "PUT"])
async def note_item(request):
    try:
        note_id = request.path_params["note_id"]
        if request.method == "GET":
            return _json(store.get(note_id))
        body = await _body(request)
        changes = {
            key: body[key]
            for key in ("title", "type", "status", "body", "sources")
            if key in body
        }
        note = store.edit(
            note_id,
            expected_revision=body.get("expected_revision", ""),
            changes=changes,
            author=_user(request),
            idempotency_key=body.get("idempotency_key", ""),
        )
        return _json(note)
    except NotesError as exc:
        return _error(exc)


@mcp.custom_route("/api/notes/{note_id}/backlinks", methods=["GET"])
async def note_backlinks(request):
    try:
        return _json({"backlinks": store.get(request.path_params["note_id"])["backlinks"]})
    except NotesError as exc:
        return _error(exc)


@mcp.custom_route("/api/notes/{note_id}/graph", methods=["GET"])
async def note_graph(request):
    try:
        return _json(store.graph(request.path_params["note_id"]))
    except NotesError as exc:
        return _error(exc)


@mcp.custom_route("/api/notes/{note_id}/history", methods=["GET"])
async def note_history(request):
    try:
        return _json({"history": store.history(request.path_params["note_id"])})
    except NotesError as exc:
        return _error(exc)


@mcp.custom_route("/api/notes/{note_id}/diff", methods=["GET"])
async def note_diff(request):
    try:
        return _json(
            store.diff(
                request.path_params["note_id"],
                request.query_params.get("from"),
                request.query_params.get("to"),
            )
        )
    except NotesError as exc:
        return _error(exc)


@mcp.custom_route("/api/notes/{note_id}/restore", methods=["POST"])
async def note_restore(request):
    try:
        body = await _body(request)
        return _json(
            store.restore(
                request.path_params["note_id"],
                revision=body.get("revision", ""),
                expected_revision=body.get("expected_revision", ""),
                author=_user(request),
                idempotency_key=body.get("idempotency_key", ""),
            )
        )
    except NotesError as exc:
        return _error(exc)


@mcp.tool()
def search_notes(query: str, limit: int = 10) -> str:
    """Search notes by text. Returns note id, title, type, status and revision."""
    return json.dumps(store.list(query, limit), ensure_ascii=False)


@mcp.tool()
def read_note(id: str) -> str:
    """Read a note, including body, revision, links and backlinks."""
    return json.dumps(store.get(id), ensure_ascii=False)


@mcp.tool()
def create_note(
    title: str,
    type: str,
    body: str,
    sources: list[str],
    idempotency_key: str,
) -> str:
    """Create a note. The idempotency key is required for safe retries."""
    try:
        note = store.create(
            title=title,
            type=type,
            body=body,
            sources=sources,
            author="agent",
            idempotency_key=idempotency_key,
        )
        return json.dumps({"ok": True, "note": note}, ensure_ascii=False)
    except NotesError as exc:
        return json.dumps({"ok": False, "error": exc.code, "message": str(exc), **exc.details}, ensure_ascii=False)


@mcp.tool()
def edit_note(
    id: str,
    expected_revision: str,
    idempotency_key: str,
    body: str | None = None,
    title: str | None = None,
    type: str | None = None,
    status: str | None = None,
    sources: list[str] | None = None,
) -> str:
    """Edit a note if expected_revision still matches. On conflict, reread and retry."""
    changes = {}
    for key, value in (("body", body), ("title", title), ("type", type), ("status", status), ("sources", sources)):
        if value is not None:
            changes[key] = value
    try:
        note = store.edit(
            id,
            expected_revision=expected_revision,
            changes=changes,
            author="agent",
            idempotency_key=idempotency_key,
        )
        return json.dumps({"ok": True, "note": note}, ensure_ascii=False)
    except NotesError as exc:
        return json.dumps({"ok": False, "error": exc.code, "message": str(exc), **exc.details}, ensure_ascii=False)


@mcp.tool()
def get_backlinks(id: str) -> str:
    """Return notes that link to this note."""
    try:
        return json.dumps({"ok": True, "backlinks": store.get(id)["backlinks"]}, ensure_ascii=False)
    except NotesError as exc:
        return json.dumps({"ok": False, "error": exc.code, "message": str(exc), **exc.details}, ensure_ascii=False)


app = mcp.streamable_http_app()
app.add_middleware(BearerAuthMiddleware)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("NOTES_PORT", "8080")), log_level="info")
