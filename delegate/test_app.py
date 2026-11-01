"""Юнит-тесты приватности и выбора источников delegate (без сети и GPU).

Запуск: python delegate/test_app.py
"""

import json
import os
import unittest

os.environ.setdefault("DELEGATE_MCP_TOKEN", "test-token")
os.environ.setdefault("DELEGATE_SANITIZE", "llm")

import app as delegate_app  # noqa: E402


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        parsed = delegate_app.extract_json('{"summary": "ok", "facts": []}')
        self.assertEqual(parsed["summary"], "ok")

    def test_fenced_json(self):
        parsed = delegate_app.extract_json('```json\n{"summary": "ok"}\n```')
        self.assertEqual(parsed["summary"], "ok")

    def test_json_in_prose(self):
        parsed = delegate_app.extract_json(
            'Вот результат: {"summary": "ok"} надеюсь, всё так'
        )
        self.assertEqual(parsed["summary"], "ok")

    def test_invalid_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            delegate_app.extract_json("no json here")


class SanitizeFailClosedTests(unittest.IsolatedAsyncioTestCase):
    """Санитайзер упал + FAIL=reject: в облако ничего не уходит."""

    async def asyncSetUp(self):
        self.logs = []
        self.orig = (
            delegate_app.SANITIZE_MODE,
            delegate_app.SANITIZE_FAIL,
            delegate_app.sanitize_task,
            delegate_app.call_worker,
            delegate_app.log_outbound,
        )
        delegate_app.SANITIZE_MODE = "llm"
        delegate_app.SANITIZE_FAIL = "reject"
        delegate_app.log_outbound = self.logs.append

        async def sanitize_down(client, task):
            raise RuntimeError("llama-server down")

        async def worker_forbidden(*args, **kwargs):
            raise AssertionError("worker must not be called when sanitize fails")

        delegate_app.sanitize_task = sanitize_down
        delegate_app.call_worker = worker_forbidden

    async def asyncTearDown(self):
        (
            delegate_app.SANITIZE_MODE,
            delegate_app.SANITIZE_FAIL,
            delegate_app.sanitize_task,
            delegate_app.call_worker,
            delegate_app.log_outbound,
        ) = self.orig

    async def test_sanitize_failure_rejects(self):
        out = await delegate_app.delegate_to_worker(task="имя Иван + приватный токен")
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertEqual(self.logs[0]["status"], "sanitize_failed")
        self.assertIn("Иван", self.logs[0]["task_original"])


class ReadSelectionTests(unittest.IsolatedAsyncioTestCase):
    """Нечитаемые топ-результаты не выбрасывают годные следующие."""

    async def asyncSetUp(self):
        self.read_calls = []
        self.sent = []
        self.orig = (
            delegate_app.SANITIZE_MODE,
            delegate_app.READ_TOP,
            delegate_app.searxng_search,
            delegate_app.mcp_read_url,
            delegate_app.call_worker,
            delegate_app.log_outbound,
        )
        delegate_app.SANITIZE_MODE = "off"  # в тесте санитайзер не трогаем
        delegate_app.READ_TOP = 2
        delegate_app.log_outbound = lambda record: None

        async def fake_search(client, query, categories=None):
            return [
                {"url": "r1", "title": "one", "snippet": "s1"},
                {"url": "r2", "title": "two", "snippet": "s2"},
                {"url": "r3", "title": "three", "snippet": "s3"},
                {"url": "r4", "title": "four", "snippet": "s4"},
            ]

        delegate_app.searxng_search = fake_search

        async def fake_read(client, url):
            self.read_calls.append(url)
            if url == "r1":
                raise RuntimeError("cloudflare wall")
            if url == "r3":
                return "Website Error: security policy blocked this page"
            return f"real text of {url}"

        delegate_app.mcp_read_url = fake_read

        async def fake_worker(client, messages):
            self.sent.append(messages)
            answer = json.dumps(
                {
                    "summary": "ok",
                    "facts": [],
                    "sources": ["r2", "r4"],
                    "confidence": 0.5,
                    "notes": None,
                }
            )
            return {"choices": [{"message": {"content": answer}}]}, messages

        delegate_app.call_worker = fake_worker

    async def asyncTearDown(self):
        (
            delegate_app.SANITIZE_MODE,
            delegate_app.READ_TOP,
            delegate_app.searxng_search,
            delegate_app.mcp_read_url,
            delegate_app.call_worker,
            delegate_app.log_outbound,
        ) = self.orig

    async def test_reads_until_n_read_usable_pages(self):
        out = await delegate_app.delegate_to_worker(task="обзор llama.cpp")
        data = json.loads(out)
        self.assertEqual(data["summary"], "ok")
        # r1 упал, r3 закрыт — но бюджет (2 читаемые) заполнен r2 и r4.
        self.assertEqual(self.read_calls, ["r1", "r2", "r3", "r4"])
        prompt = self.sent[0][1]["content"]
        self.assertIn("real text of r2", prompt)
        self.assertIn("real text of r4", prompt)
        self.assertNotIn("real text of r1", prompt)
        self.assertNotIn("Website Error", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
