from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GUARD_PATH = Path(__file__).resolve().parent / "budget_guard_filter.py"

pydantic_stub = types.ModuleType("pydantic")


class _BaseModel:
    def __init__(self, **kwargs):
        for name in getattr(type(self), "__annotations__", {}):
            if hasattr(type(self), name):
                setattr(self, name, getattr(type(self), name))
        for name, value in kwargs.items():
            setattr(self, name, value)


def _Field(default=None, **kwargs):
    return default


pydantic_stub.BaseModel = _BaseModel
pydantic_stub.Field = _Field
sys.modules.setdefault("pydantic", pydantic_stub)


class _Tokenizer(BaseHTTPRequestHandler):
    seen: list[dict] = []
    window: int = 32768

    def log_message(self, *args):
        pass

    def do_GET(self):
        payload = json.dumps({"default_generation_settings": {"n_ctx": type(self).window}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        type(self).seen.append(data)
        content = data.get("content") or ""
        payload = json.dumps({"tokens": ["t"] * len(content)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class BudgetGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Tokenizer)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        spec = importlib.util.spec_from_file_location("budget_guard_filter", GUARD_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Tokenizer.seen.clear()
        _Tokenizer.window = 32768
        self.guard = self.module.Filter()
        self.guard.valves.window = 32768
        self.guard.valves.output_reserve = 1024
        self.guard.valves.tool_reserve = 1024
        self.guard.valves.margin = 1024
        self.guard.valves.tokenizer_url = f"http://127.0.0.1:{self.port}"
        self.guard.valves.props_url = f"http://127.0.0.1:{self.port}"
        self.guard.valves.template_overhead_per_message = 8
        self.guard.valves.template_overhead_fixed = 256

    def run_request(self, body):
        return asyncio.run(self.guard.request(body))

    def test_normal_request_passes_without_mutation(self):
        body = {"messages": [{"role": "user", "content": "hello"}], "tools": []}
        result = self.run_request(body)
        self.assertIs(result, body)
        self.assertEqual(_Tokenizer.seen[0]["model"], "orchestrator")

    def test_oversized_message_is_refused_without_content(self):
        secret = "SECRET_MARKER_DO_NOT_ECHO"
        body = {"messages": [{"role": "user", "content": secret + "x" * 40000}], "tools": []}
        with self.assertRaises(self.module.BudgetGuardError) as context:
            self.run_request(body)
        message = str(context.exception)
        self.assertIn("CONTEXT_BUDGET_EXCEEDED", message)
        self.assertIn("window=32768", message)
        self.assertIn("not an exact serialized chat-template", message)
        self.assertIn("not rolled back", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("x" * 100, message)

    def test_tool_schema_and_tool_result_are_counted(self):
        body = {
            "messages": [
                {"role": "user", "content": "run"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "r" * 30000},
            ],
            "tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
        }
        with self.assertRaises(self.module.BudgetGuardError):
            self.run_request(body)
        self.assertGreater(len(_Tokenizer.seen), 3)

    def test_backend_window_mismatch_fails_closed(self):
        _Tokenizer.window = 65536
        with self.assertRaises(self.module.BudgetGuardError) as context:
            self.run_request({"messages": [{"role": "user", "content": "hello"}]})
        self.assertIn("does not match the backend", str(context.exception))

    def test_unknown_window_fails_closed(self):
        self.guard.valves.window = 0
        with self.assertRaises(self.module.BudgetGuardError) as context:
            self.run_request({"messages": [{"role": "user", "content": "hello"}]})
        self.assertIn("explicit backend window is required", str(context.exception))
        self.assertEqual(_Tokenizer.seen, [])

    def test_tokenizer_unavailable_fails_closed(self):
        self.guard.valves.fallback_chars_per_token = 0.0
        self.guard.valves.tokenizer_url = "http://127.0.0.1:1"
        with self.assertRaises(self.module.BudgetGuardError) as context:
            self.run_request({"messages": [{"role": "user", "content": "hello"}]})
        self.assertIn("cannot tokenize", str(context.exception))

    def test_explicit_fallback_is_conservative(self):
        self.guard.valves.tokenizer_url = "http://127.0.0.1:1"
        self.guard.valves.fallback_chars_per_token = 4.0
        body = {"messages": [{"role": "user", "content": "hello"}], "tools": []}
        self.assertIs(self.run_request(body), body)

    def test_near_boundary_request_still_passes(self):
        self.guard.valves.template_overhead_fixed = 0
        self.guard.valves.template_overhead_per_message = 0
        body = {"messages": [{"role": "user", "content": "x" * 29696}], "tools": []}
        self.assertIs(self.run_request(body), body)

    def test_over_boundary_request_fails(self):
        self.guard.valves.template_overhead_fixed = 0
        self.guard.valves.template_overhead_per_message = 0
        body = {"messages": [{"role": "user", "content": "x" * 29697}], "tools": []}
        with self.assertRaises(self.module.BudgetGuardError):
            self.run_request(body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
