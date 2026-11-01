from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import validate_tool_groups  # noqa: E402

FILTER_PATH = Path(__file__).resolve().parents[2] / "openwebui" / "filters" / "chumba_summary_filter.py"


# The host Python does not have pydantic; the filter only needs BaseModel/Field.
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


class _MockBackend(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict]] = []

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"<raw>": raw[:200]}
        type(self).seen.append((self.path, data))
        if self.path == "/tokenize":
            self._send(400, {"error": {"message": "model name is missing from the request"}})
        elif self.path.endswith("/chat/completions"):
            self._send(200, {"choices": [{"message": {"content": "SYNTHETIC SUMMARY"}}]})
        else:
            self._send(404, {"error": "not found"})


class CurrentFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockBackend)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        spec = importlib.util.spec_from_file_location("chumba_summary_filter", FILTER_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _MockBackend.seen.clear()
        self.tmp = tempfile.TemporaryDirectory(prefix="chumba-filter-test-")
        self.filter = self.module.Filter()
        self.filter.valves.llama_url = f"http://127.0.0.1:{self.port}/v1"
        self.filter.valves.cache_file = str(Path(self.tmp.name) / "cache.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _summaries(self):
        return [data for path, data in _MockBackend.seen if path.endswith("/chat/completions")]

    def _tokenize_calls(self):
        return [data for path, data in _MockBackend.seen if path == "/tokenize"]

    def test_tokenize_is_called_without_model_and_falls_back_to_chars4(self):
        # old content is 47992 chars; //4 == 11998, below 12000
        messages = [{"role": "user", "content": "x" * 47990}] + [
            {"role": "user", "content": "t"} for _ in range(7)
        ]
        self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "threshold-low"})
        tokenize = self._tokenize_calls()
        self.assertEqual(len(tokenize), 1)
        self.assertEqual(sorted(tokenize[0].keys()), ["content"])
        self.assertEqual(self._summaries(), [])

        # old content is 48012 chars; //4 == 12003, above 12000
        messages = [{"role": "user", "content": "x" * 48010}] + [
            {"role": "user", "content": "t"} for _ in range(7)
        ]
        self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "threshold-high"})
        self.assertEqual(len(self._summaries()), 1)
        summary_input = self._summaries()[0]["messages"][1]["content"]
        self.assertEqual(len(summary_input), 30000)

    def test_system_prompt_is_replaced_by_summary(self):
        messages = [
            {"role": "system", "content": "SYSTEM_MARKER permanent constraints"},
            {"role": "user", "content": "x" * 48000},
        ] + [{"role": "user", "content": "t"} for _ in range(7)]
        out = self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "system-loss"})
        self.assertEqual(out["messages"][0]["role"], "system")
        self.assertIn("Конспект предыдущей беседы", out["messages"][0]["content"])
        self.assertFalse(
            any("SYSTEM_MARKER" in (message.get("content") or "") for message in out["messages"])
        )
        summary_input = self._summaries()[0]["messages"][1]["content"]
        self.assertNotIn("SYSTEM_MARKER", summary_input)
        self.assertLessEqual(len(summary_input), 30000)

    def test_last_user_message_can_be_summarized_away_by_tool_tail(self):
        messages = []
        for i in range(20):
            messages += [
                {"role": "user", "content": "u" * 2000},
                {"role": "assistant", "content": "a" * 2000},
            ]
        messages.append({"role": "user", "content": "CURRENT_REQUEST_MARKER"})
        for i in range(3):
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        }
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r"})

        out = self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "lost-current"})
        self.assertFalse(
            any("CURRENT_REQUEST_MARKER" in (message.get("content") or "") for message in out["messages"])
        )
        self.assertTrue(
            any("CURRENT_REQUEST_MARKER" in data["messages"][1]["content"] for data in self._summaries())
        )

    def test_tail_cut_can_create_orphan_tool_results(self):
        messages = []
        for i in range(20):
            messages += [
                {"role": "user", "content": "u" * 2000},
                {"role": "assistant", "content": "a" * 2000},
            ]
        messages.append({"role": "user", "content": "TOOL_TASK"})
        for i in range(3):
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{i}a",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        },
                        {
                            "id": f"c{i}b",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        },
                    ],
                }
            )
            messages += [
                {"role": "tool", "tool_call_id": f"c{i}a", "content": f"r{i}a"},
                {"role": "tool", "tool_call_id": f"c{i}b", "content": f"r{i}b"},
            ]
        messages.append({"role": "assistant", "content": "partial answer"})

        out = self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "orphan"})
        issues = validate_tool_groups(out["messages"])
        self.assertTrue(any("orphan" in issue for issue in issues), issues)

    def test_non_string_content_is_not_counted(self):
        messages = [{"role": "system", "content": "S"}]
        for i in range(10):
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "z" * 10000}],
                }
            )
        messages += [{"role": "user", "content": "t"} for _ in range(7)]
        out = self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "list-content"})
        self.assertEqual(self._summaries(), [])
        self.assertEqual(len(out["messages"]), len(messages))

    def test_identical_retry_uses_cache(self):
        messages = [{"role": "user", "content": "x" * 48010}] + [
            {"role": "user", "content": "t"} for _ in range(7)
        ]
        body = {"messages": messages}
        self.filter.inlet(body, __metadata__={"chat_id": "cache"})
        self.assertEqual(len(self._summaries()), 1)
        self.filter.inlet(body, __metadata__={"chat_id": "cache"})
        self.assertEqual(len(self._summaries()), 1)

    def test_zero_keep_disables_compression(self):
        self.filter.valves.keep_last_messages = 0
        messages = [{"role": "user", "content": "x" * 60000}] + [
            {"role": "user", "content": "t"} for _ in range(7)
        ]
        out = self.filter.inlet({"messages": messages}, __metadata__={"chat_id": "keep-zero"})
        self.assertEqual(self._summaries(), [])
        self.assertEqual(out["messages"], messages)


if __name__ == "__main__":
    unittest.main(verbosity=2)
