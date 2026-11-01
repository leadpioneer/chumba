from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import apply_context_policy as app  # noqa: E402
from summary_policy import (  # noqa: E402
    SUMMARY_TEMPLATE,
    missing_required_markers,
    render_reference_prompt,
    template_contract_errors,
)


class FakeClient:
    base_url = "http://fake-owui"

    def __init__(self, *, props_status=200, window=32768, function=None, config=None, task=None):
        self.props_status = props_status
        self.window = window
        self.function_value = function or {}
        self.config = dict(
            config
            or {
                "ENABLE_CONTEXT_COMPACTION": False,
                "CONTEXT_COMPACTION_MODEL": "",
                "CONTEXT_COMPACTION_TOKEN_THRESHOLD": 80000,
                "CONTEXT_COMPACTION_TOKEN_CAP": None,
                "CONTEXT_COMPACTION_RETENTION_PERCENTAGE": 40,
                "CONTEXT_COMPACTION_PROMPT_TEMPLATE": "",
            }
        )
        self.task = dict(task or {})
        self.set_config_calls = 0
        self.set_task_calls = 0
        self.corrupt_next_config = False

    def props(self, model):
        if self.props_status != 200:
            return self.props_status, "unavailable"
        return 200, {"default_generation_settings": {"n_ctx": self.window}}

    def function_optional(self):
        return dict(self.function_value)

    def chats_config(self):
        return dict(self.config)

    def task_params(self):
        return dict(self.task)

    def set_chats_config(self, config):
        self.set_config_calls += 1
        self.config = dict(config)
        if self.corrupt_next_config:
            self.corrupt_next_config = False
            self.config["CONTEXT_COMPACTION_TOKEN_THRESHOLD"] = 1

    def set_task_params(self, params):
        self.set_task_calls += 1
        self.task = dict(params)


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chumba-policy-test-")
        self.rollback = Path(self.tmp.name) / "rollback.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_window_apply_is_forbidden(self):
        client = FakeClient(props_status=0)
        with self.assertRaises(app.UnknownWindowError):
            app.build_plan(client, profile_name="32k", allow_hypothesis=False)
        plan = app.build_plan(client, profile_name="32k", allow_hypothesis=True)
        self.assertFalse(plan.window_confirmed)
        self.assertFalse(plan.apply_allowed)
        with self.assertRaises(app.ApplyError):
            app.apply_plan(client, plan, self.rollback)

    def test_profile_mismatch_is_an_error(self):
        client = FakeClient(window=32768)
        with self.assertRaises(app.ProfileMismatchError):
            app.build_plan(client, profile_name="64k", allow_hypothesis=True)

    def test_conflict_is_forbidden_even_if_only_active(self):
        client = FakeClient(
            function={"is_active": True, "is_global": False},
        )
        plan = app.build_plan(client, profile_name="32k")
        self.assertTrue(plan.conflict)
        self.assertFalse(plan.apply_allowed)
        with self.assertRaises(app.ApplyError):
            app.apply_plan(client, plan, self.rollback)
        self.assertEqual(client.set_config_calls, 0)

    def test_dry_run_does_not_write(self):
        client = FakeClient(task={"max_tokens": 1000, "custom": "keep"})
        plan = app.build_plan(client, profile_name="32k")
        self.assertTrue(plan.apply_allowed)
        self.assertEqual(client.set_config_calls, 0)
        self.assertEqual(client.set_task_calls, 0)
        self.assertEqual(plan.desired_compaction["CONTEXT_COMPACTION_TOKEN_THRESHOLD"], 21504)
        self.assertEqual(plan.desired_task_params["max_tokens"], 500)
        self.assertEqual(plan.desired_task_params["custom"], "keep")

    def test_apply_verify_and_rollback(self):
        before_config = {
            "ENABLE_CONTEXT_COMPACTION": False,
            "CONTEXT_COMPACTION_MODEL": "",
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD": 80000,
            "CONTEXT_COMPACTION_TOKEN_CAP": None,
            "CONTEXT_COMPACTION_RETENTION_PERCENTAGE": 40,
            "CONTEXT_COMPACTION_PROMPT_TEMPLATE": "",
        }
        before_task = {"max_tokens": 1000, "temperature": 0.3, "custom": "keep"}
        client = FakeClient(config=before_config, task=before_task)
        plan = app.build_plan(client, profile_name="32k")
        app.apply_plan(client, plan, self.rollback)
        self.assertTrue(client.config["ENABLE_CONTEXT_COMPACTION"])
        self.assertEqual(client.config["CONTEXT_COMPACTION_TOKEN_THRESHOLD"], 21504)
        self.assertEqual(client.task["max_tokens"], 500)
        self.assertEqual(client.task["chat_template_kwargs"], {"enable_thinking": False})
        payload = json.loads(self.rollback.read_text(encoding="utf-8"))
        dumped = json.dumps(payload).lower()
        for secret_name in ("api_key", "password", "authorization", "access_token", "refresh_token"):
            self.assertNotIn(secret_name, dumped)
        self.assertEqual(payload["compaction_before"], before_config)
        self.assertEqual(payload["task_model_params_before"], before_task)

        app.rollback(client, self.rollback)
        self.assertEqual(client.config, before_config)
        self.assertEqual(client.task, before_task)

    def test_verification_failure_restores_before_state(self):
        before_config = {
            "ENABLE_CONTEXT_COMPACTION": False,
            "CONTEXT_COMPACTION_MODEL": "",
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD": 80000,
            "CONTEXT_COMPACTION_TOKEN_CAP": None,
            "CONTEXT_COMPACTION_RETENTION_PERCENTAGE": 40,
            "CONTEXT_COMPACTION_PROMPT_TEMPLATE": "",
        }
        before_task = {"max_tokens": 1000}
        client = FakeClient(config=before_config, task=before_task)
        client.corrupt_next_config = True
        plan = app.build_plan(client, profile_name="32k")
        with self.assertRaises(app.ApplyError):
            app.apply_plan(client, plan, self.rollback)
        self.assertEqual(client.config, before_config)
        self.assertEqual(client.task, before_task)

    def test_rollback_rejects_secret_like_keys(self):
        payload = {
            "compaction_before": {},
            "task_model_params_before": {"api_key": "not-allowed"},
        }
        self.rollback.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(app.ApplyError):
            app.rollback(FakeClient(), self.rollback)

    def test_template_has_previous_summary_and_no_recent_question(self):
        self.assertEqual(template_contract_errors(), [])
        rendered = render_reference_prompt(
            previous_summary="PREVIOUS_FACT",
            compacted_messages=[{"role": "user", "content": "HISTORY_FACT"}],
        )
        self.assertIn("PREVIOUS_FACT", rendered)
        self.assertIn("HISTORY_FACT", rendered)
        self.assertNotIn("CURRENT_QUESTION", rendered)
        self.assertNotIn("{{", rendered)

    def test_required_facts_are_reported_not_silently_dropped(self):
        missing = missing_required_markers("ID SN-7742 only")
        self.assertIn("/srv/chumba/state.json", missing)
        self.assertIn("8093", missing)
        self.assertEqual(missing_required_markers("SN-7742 /srv/chumba/state.json 8093 1.27.4 9999"), [])

    def test_template_contract_catches_recent_and_missing_previous(self):
        errors = template_contract_errors("{{COMPACTED_MESSAGES}} {{RECENT_MESSAGES}}")
        self.assertTrue(any("PREVIOUS_SUMMARY" in error for error in errors))
        self.assertTrue(any("RECENT_MESSAGES" in error for error in errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
