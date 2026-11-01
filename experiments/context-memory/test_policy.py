from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from policy import (  # noqa: E402
    BackendProfile,
    CompactionConflictError,
    OversizedMessageError,
    PolicyError,
    ProfileMismatchError,
    UnknownWindowError,
    compute_budget,
    compute_profile_budget,
    detect_oversized_message,
    ensure_summary_reduces,
    estimate_tokens_chars4,
    extract_window,
    find_user_boundary,
    validate_mechanisms,
    validate_tool_groups,
)


def user(content="u"):
    return {"role": "user", "content": content}


def assistant(content="a", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def tool(call_id, content="r"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def call(call_id):
    return {"id": call_id, "type": "function", "function": {"name": "f", "arguments": "{}"}}


class BudgetTests(unittest.TestCase):
    def test_unknown_window_apply_is_forbidden(self):
        with self.assertRaises(UnknownWindowError):
            compute_budget(window=32768, window_confirmed=False, output_reserve=1024)

    def test_unknown_window_dry_run_is_explicit_hypothesis(self):
        budget = compute_budget(
            window=32768,
            window_confirmed=False,
            output_reserve=1024,
            allow_hypothesis=True,
        )
        self.assertEqual(budget.method, "hypothesis")
        self.assertFalse(budget.window_confirmed)

    def test_profile_mismatch_64k_profile_32k_backend(self):
        profile = BackendProfile(name="64k", window=65536, output_reserve=1024)
        with self.assertRaises(ProfileMismatchError):
            compute_profile_budget(profile, confirmed_window=32768)

    def test_confirmed_32k_budget(self):
        budget = compute_budget(window=32768, window_confirmed=True, output_reserve=1024)
        self.assertEqual(budget.margin, 984)
        self.assertEqual(budget.input_budget, 30760)
        self.assertEqual(budget.history_trigger, 24608)
        self.assertEqual(budget.method, "confirmed")

    def test_tools_schema_larger_than_input_budget(self):
        with self.assertRaises(PolicyError):
            compute_budget(
                window=32768,
                window_confirmed=True,
                output_reserve=1024,
                tools_schema=40000,
            )

    def test_extract_window_rejects_router_dummy(self):
        with self.assertRaises(UnknownWindowError):
            extract_window({"n_ctx": 0, "default_generation_settings": {"n_ctx": 0}})

    def test_extract_window_nested(self):
        self.assertEqual(
            extract_window({"default_generation_settings": {"n_ctx": 32768}}),
            32768,
        )


class InvariantTests(unittest.TestCase):
    def test_multiple_tool_calls_and_results_are_valid(self):
        messages = [
            user("q"),
            assistant("", [call("a"), call("b")]),
            tool("a"),
            tool("b"),
            assistant("done"),
        ]
        self.assertEqual(validate_tool_groups(messages), [])

    def test_orphan_tool_result_is_reported(self):
        messages = [user("q"), tool("missing"), assistant("done")]
        issues = validate_tool_groups(messages)
        self.assertTrue(any("orphan" in issue for issue in issues), issues)

    def test_unfinished_tool_call_is_reported(self):
        messages = [user("q"), assistant("", [call("a")]), assistant("partial")]
        issues = validate_tool_groups(messages)
        self.assertTrue(any("unfinished" in issue for issue in issues), issues)

    def test_tool_result_before_call_is_reported(self):
        messages = [user("q"), tool("a"), assistant("", [call("a")])]
        issues = validate_tool_groups(messages)
        self.assertTrue(any("appears before" in issue for issue in issues), issues)

    def test_oversized_single_message(self):
        issues = detect_oversized_message([user("x" * 4000)], available_input_tokens=500)
        self.assertEqual(len(issues), 1)
        self.assertGreater(estimate_tokens_chars4("x" * 4000), 500)

    def test_user_boundary_does_not_split_tool_group(self):
        messages = [
            user("start"),
            assistant("", [call("a")]),
            tool("a"),
            assistant("", [call("b")]),
            tool("b"),
            user("next"),
            assistant("", [call("c")]),
            tool("c"),
            user("last"),
        ]
        boundary = find_user_boundary(messages, retention_percentage=40)
        self.assertEqual(messages[boundary]["role"], "user")
        self.assertNotEqual(boundary, 0)
        # The retained suffix must not begin with a tool result.
        self.assertNotEqual(messages[boundary]["role"], "tool")

    def test_summary_must_reduce_input(self):
        with self.assertRaises(PolicyError):
            ensure_summary_reduces(1000, 1000)
        with self.assertRaises(PolicyError):
            ensure_summary_reduces(1000, 980, min_reduction=0.05)
        ensure_summary_reduces(1000, 700, min_reduction=0.05)

    def test_two_compaction_mechanisms_conflict(self):
        with self.assertRaises(CompactionConflictError):
            validate_mechanisms(
                legacy_filter_active=True,
                legacy_filter_global=True,
                builtin_compaction_enabled=True,
            )
        validate_mechanisms(
            legacy_filter_active=True,
            legacy_filter_global=False,
            builtin_compaction_enabled=True,
        )

    def test_oversized_message_error_type_is_available(self):
        self.assertTrue(issubclass(OversizedMessageError, PolicyError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
