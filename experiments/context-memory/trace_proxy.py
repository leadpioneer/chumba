#!/usr/bin/env python3
"""Metadata-only OpenAI reverse proxy for the isolated context experiment.

It forwards requests to llama.cpp and writes JSONL records containing message
roles, lengths, hashes, token usage and timings.  It never writes message
bodies, prompts, summaries or tool result text to the log.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

UPSTREAM = os.environ.get("UPSTREAM", "http://llama-server:8080")
PORT = int(os.environ.get("PORT", "8080"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/data"))
LOG_PATH = LOG_DIR / "requests.jsonl"
EXPECTED_SYSTEM_MARKER = os.environ.get("EXPECTED_SYSTEM_MARKER", "SYSTEM_MARKER")
LOCK = threading.Lock()

UP = urlsplit(UPSTREAM)
UP_HOST = UP.hostname or "llama-server"
UP_PORT = UP.port or 80
UP_BASE_PATH = UP.path.rstrip("/")

SPIKE_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chumba notes UI spike</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; font:14px/1.45 system-ui, sans-serif; background:#f7f7f8; color:#18181b; }
  header { display:flex; gap:8px; align-items:center; padding:10px 12px; background:#fff; border-bottom:1px solid #ddd; }
  main { display:grid; grid-template-columns:180px 1fr; min-height:calc(100vh - 46px); }
  nav { border-right:1px solid #ddd; padding:8px; background:#fbfbfc; }
  nav button { display:block; width:100%; text-align:left; margin:4px 0; padding:7px; border:1px solid #d4d4d8; background:#fff; border-radius:6px; cursor:pointer; }
  nav button.active { background:#e8f0ff; border-color:#8ab4ff; }
  section { padding:12px; min-width:0; }
  textarea { width:100%; min-height:220px; box-sizing:border-box; font:inherit; padding:10px; border:1px solid #ccc; border-radius:6px; }
  .row { display:flex; gap:8px; align-items:center; margin:8px 0; flex-wrap:wrap; }
  .muted { color:#666; }
  a { color:#155eef; }
  @media (prefers-color-scheme: dark) { body{background:#111;color:#eee} header,nav{background:#1b1b1f;border-color:#333} textarea{background:#151518;color:#eee;border-color:#444} nav button{background:#202024;color:#eee;border-color:#444} nav button.active{background:#26344d;border-color:#5b8cff} .muted{color:#aaa} }
</style></head><body>
<header><strong>Notes UI spike</strong><span class="muted">mock, no backend</span><span id="status" class="muted"></span></header>
<main><nav id="list" aria-label="Notes list"></nav><section>
  <h2 id="title"></h2>
  <div class="row"><button id="save">Save</button><a id="other" href="?note=b">Open linked note</a><button id="reset">Reset</button></div>
  <textarea id="body" aria-label="Note body"></textarea>
  <p class="muted">Typing, focus and localStorage persistence are intentional. Links stay inside this iframe.</p>
</section></main>
<script>
const NOTES = {
  a: {title:"Project Chumba", body:"# Project Chumba | Goal: context correctness. | See Experiment A via the linked note button. | Status: PROPOSED."},
  b: {title:"Experiment A", body:"# Experiment A | Decision: try later summarization. | Constraint: never use port 9999. | Back to Project Chumba."}
};
const params = new URLSearchParams(location.search);
let current = NOTES[params.get("note")] ? params.get("note") : "a";
function load() {
  const note = NOTES[current];
  const saved = localStorage.getItem("spike:"+current);
  document.getElementById("title").textContent = note.title;
  document.getElementById("body").value = saved === null ? note.body : saved;
  document.getElementById("other").href = "?note=" + (current === "a" ? "b" : "a");
  document.getElementById("other").textContent = current === "a" ? "Open linked note: Experiment A" : "Back to Project Chumba";
  document.getElementById("list").innerHTML = "";
  for (const [id, item] of Object.entries(NOTES)) {
    const b=document.createElement("button"); b.type="button"; b.textContent=item.title; b.className=id===current?"active":"";
    b.onclick=()=>{current=id; history.replaceState(null,"","?note="+id); load();};
    document.getElementById("list").appendChild(b);
  }
  document.getElementById("body").focus();
}
document.getElementById("save").onclick=()=>{localStorage.setItem("spike:"+current,document.getElementById("body").value);document.getElementById("status").textContent="saved "+new Date().toLocaleTimeString();};
document.getElementById("reset").onclick=()=>{localStorage.removeItem("spike:"+current);load();document.getElementById("status").textContent="reset";};
load();
</script></body></html>"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _message_meta(messages) -> dict:
    roles = []
    lengths = []
    total = 0
    tool_calls = 0
    call_ids = []
    result_ids = []
    last_user = ""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "?"
        content = _text(message.get("content"))
        roles.append(role)
        lengths.append(len(content))
        total += len(content)
        for call in message.get("tool_calls") or []:
            if call.get("id"):
                call_ids.append(call.get("id"))
        if role == "tool" and message.get("tool_call_id"):
            result_ids.append(message.get("tool_call_id"))
        if role == "user":
            last_user = content
        tool_calls += len(message.get("tool_calls") or [])
    orphan_results = [value for value in result_ids if value not in call_ids]
    unfinished_calls = [value for value in call_ids if value not in result_ids]
    system = "\n".join(
        _text(m.get("content"))
        for m in (messages or [])
        if isinstance(m, dict) and m.get("role") == "system"
    )
    return {
        "message_count": len(roles),
        "roles": roles,
        "content_chars_total": total,
        "content_chars": lengths,
        "system_chars": len(system),
        "system_sha16": _sha(system),
        "tool_calls": tool_calls,
        "tool_call_ids": call_ids,
        "tool_result_ids": result_ids,
        "orphan_tool_results": orphan_results,
        "unfinished_tool_calls": unfinished_calls,
        "last_user_chars": len(last_user),
        "last_user_sha16": _sha(last_user),
        "has_conversation_summary": "[CONVERSATION SUMMARY]" in system,
        "system_has_expected_marker": EXPECTED_SYSTEM_MARKER in system,
    }


def _request_meta(path: str, body: bytes) -> dict:
    meta = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request_id": uuid.uuid4().hex,
        "method": "POST",
        "path": path,
    }
    data = None
    if body:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            data = None
    if not isinstance(data, dict):
        meta["kind"] = "non-json"
        meta["body_chars"] = len(body or b"")
        return meta

    if path == "/tokenize":
        meta["kind"] = "tokenize"
        meta["model"] = data.get("model")
        meta["content_chars"] = len(_text(data.get("content")))
        meta["has_model"] = bool(data.get("model"))
        return meta

    meta["kind"] = "chat"
    meta["model"] = data.get("model")
    meta["stream"] = bool(data.get("stream"))
    meta["max_tokens"] = data.get("max_tokens")
    meta["message_meta"] = _message_meta(data.get("messages"))
    tools = data.get("tools") or []
    meta["tool_schema_count"] = len(tools) if isinstance(tools, list) else 0
    meta["tool_schema_chars"] = len(json.dumps(tools, ensure_ascii=False)) if tools else 0

    system_text = "\n".join(
        _text(m.get("content"))
        for m in (data.get("messages") or [])
        if isinstance(m, dict) and m.get("role") == "system"
    )
    message_meta = meta["message_meta"]
    roles = message_meta["roles"]
    has_builtin_summary = "[CONVERSATION SUMMARY]" in system_text
    has_expected_system = EXPECTED_SYSTEM_MARKER in system_text
    # Built-in Open WebUI summary: one user message containing the template
    # and the compacted/recent messages, with no system message.
    is_builtin_summary = (
        len(roles) == 1 and roles[0] == "user" and message_meta["content_chars_total"] > 1000
    )
    # Legacy filter summary: system instruction + joined old content.
    is_legacy_summary = (
        "Сожми предыдущую беседу" in system_text
        and not has_builtin_summary
        and not has_expected_system
    )
    if is_builtin_summary or is_legacy_summary:
        meta["kind"] = "summary"
        user_text = ""
        for message in data.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "user":
                user_text = _text(message.get("content"))
        meta["summary_input_chars"] = len(user_text)
        meta["summary_input_sha16"] = _sha(user_text)
        meta["summary_input_head"] = user_text[:40]
        meta["summary_input_tail"] = user_text[-40:]
    return meta


def _response_meta(status: int, headers: dict, raw: bytes, elapsed_ms: float, ttft_ms: float | None) -> dict:
    meta = {
        "status": status,
        "elapsed_ms": round(elapsed_ms, 1),
        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
        "content_type": headers.get("Content-Type") or headers.get("content-type"),
    }
    text = raw.decode("utf-8", "replace")
    payload = None
    if text.strip().startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
    if isinstance(payload, dict):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            meta["usage"] = usage
        timings = payload.get("timings")
        if isinstance(timings, dict):
            meta["timings"] = {
                key: timings.get(key)
                for key in ("prompt_n", "cache_n", "predicted_n", "prompt_per_second", "predicted_per_second")
                if key in timings
            }
        choices = payload.get("choices") or []
        if choices:
            meta["finish_reason"] = choices[0].get("finish_reason")
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            meta["response_content_chars"] = len(content)
            meta["response_content_sha16"] = _sha(content) if content else None
        meta["error"] = payload.get("error")
    elif text.startswith("data:"):
        # llama.cpp may include timings/usage in the last SSE data object.
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            if isinstance(obj.get("timings"), dict):
                meta["timings"] = obj["timings"]
            if isinstance(obj.get("usage"), dict):
                meta["usage"] = obj["usage"]
    return meta


def _record(meta: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    with LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def _forward(self, method: str) -> None:
        started = time.perf_counter()
        parsed = urlsplit(self.path)
        path = UP_BASE_PATH + parsed.path
        if parsed.query:
            path += "?" + parsed.query
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        meta = _request_meta(parsed.path, body) | {"method": method, "upstream_path": path}
        headers = {}
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in {"host", "connection", "transfer-encoding", "content-length", "accept-encoding"}:
                continue
            headers[key] = value
        if body:
            headers["Content-Length"] = str(len(body))
        headers["Host"] = UP_HOST
        headers["Connection"] = "close"
        conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=1800)
        try:
            conn.request(method, path, body=body if body else None, headers=headers)
            response = conn.getresponse()
            status = response.status
            response_headers = {k: v for k, v in response.getheaders()}
            content_type = (response_headers.get("Content-Type") or "").lower()
            streaming = "text/event-stream" in content_type
            self.send_response(status)
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in {"connection", "transfer-encoding", "content-length", "keep-alive"}:
                    continue
                self.send_header(key, value)
            if not streaming:
                raw = response.read()
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()
                elapsed = (time.perf_counter() - started) * 1000
                meta["response"] = _response_meta(status, response_headers, raw, elapsed, None)
            else:
                self.end_headers()
                chunks = []
                ttft = None
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    if ttft is None:
                        ttft = (time.perf_counter() - started) * 1000
                    chunks.append(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                raw = b"".join(chunks)
                elapsed = (time.perf_counter() - started) * 1000
                meta["response"] = _response_meta(status, response_headers, raw, elapsed, ttft)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            meta["response"] = {"status": 502, "elapsed_ms": round(elapsed, 1), "error": f"{type(exc).__name__}: {exc}"}
            try:
                self.send_response(502)
                payload = json.dumps({"error": {"message": str(exc)}}).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception:
                pass
        finally:
            conn.close()
            _record(meta)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/spike"):
            payload = SPIKE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_DELETE(self):
        self._forward("DELETE")


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"trace_proxy: listening on :{PORT}, upstream={UPSTREAM}, log={LOG_PATH}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
