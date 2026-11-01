"""Pure context-budget and history-integrity helpers for the short experiment.

No Open WebUI, backend, GPU or network access is required to use this module.
The functions are intentionally small so they can be unit-tested and later
reused by a supported Open WebUI configuration helper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any


class PolicyError(RuntimeError):
    """Base class for policy failures that must be visible to the operator."""


class UnknownWindowError(PolicyError):
    """The backend window is unknown; apply is forbidden."""


class ProfileMismatchError(PolicyError):
    """A profile window does not match the confirmed backend window."""


class CompactionConflictError(PolicyError):
    """The legacy filter and built-in compaction are enabled together."""


class OversizedMessageError(PolicyError):
    """A single message cannot fit the available input budget."""


@dataclass(frozen=True)
class Budget:
    window: int
    window_confirmed: bool
    output_reserve: int
    tool_reserve: int
    margin: int
    tools_schema: int
    rag: int
    input_budget: int
    history_trigger: int
    safety_ratio: float
    method: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackendProfile:
    name: str
    window: int
    output_reserve: int
    tool_reserve: int = 0
    tools_schema: int = 0
    rag: int = 0
    margin_ratio: float = 0.03
    min_margin: int = 512
    safety_ratio: float = 0.80


def estimate_tokens_chars4(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _require_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise PolicyError(f"{name} must be a positive integer, got bool")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyError(f"{name} must be a positive integer, got {value!r}") from exc
    if parsed <= 0:
        raise PolicyError(f"{name} must be a positive integer, got {parsed}")
    return parsed


def compute_budget(
    *,
    window: int,
    window_confirmed: bool,
    output_reserve: int,
    tool_reserve: int = 0,
    tools_schema: int = 0,
    rag: int = 0,
    margin_ratio: float = 0.03,
    min_margin: int = 512,
    safety_ratio: float = 0.80,
    allow_hypothesis: bool = False,
) -> Budget:
    """Compute the input/history budget.

    ``allow_hypothesis`` is for dry-run only.  An apply path must pass
    ``window_confirmed=True`` and must have obtained the window from a
    confirmed backend/profile source.
    """

    window = _require_positive_int(window, "window")
    if not window_confirmed and not allow_hypothesis:
        raise UnknownWindowError(
            "backend window is not confirmed; apply is forbidden "
            "(dry-run may set allow_hypothesis=True)"
        )
    output_reserve = _require_positive_int(output_reserve, "output_reserve")
    tool_reserve = max(0, int(tool_reserve))
    tools_schema = max(0, int(tools_schema))
    rag = max(0, int(rag))
    if not 0 < margin_ratio < 1:
        raise PolicyError("margin_ratio must be between 0 and 1")
    if not 0 < safety_ratio <= 1:
        raise PolicyError("safety_ratio must be in (0, 1]")

    margin = max(_require_positive_int(min_margin, "min_margin"), math.ceil(window * margin_ratio))
    input_budget = window - output_reserve - tool_reserve - margin
    if input_budget <= 0:
        raise PolicyError(
            "reserves exceed the backend window: "
            f"window={window} output={output_reserve} tool={tool_reserve} margin={margin}"
        )

    non_history = tools_schema + rag
    if non_history >= input_budget:
        raise PolicyError(
            "tools/RAG alone exceed the input budget: "
            f"tools_schema={tools_schema} rag={rag} input_budget={input_budget}"
        )

    history_trigger = math.floor((input_budget - non_history) * safety_ratio)
    if history_trigger <= 0:
        raise PolicyError("computed history trigger is not positive")

    return Budget(
        window=window,
        window_confirmed=bool(window_confirmed),
        output_reserve=output_reserve,
        tool_reserve=tool_reserve,
        margin=margin,
        tools_schema=tools_schema,
        rag=rag,
        input_budget=input_budget,
        history_trigger=history_trigger,
        safety_ratio=safety_ratio,
        method="confirmed" if window_confirmed else "hypothesis",
    )


def compute_profile_budget(
    profile: BackendProfile,
    *,
    confirmed_window: int,
    allow_hypothesis: bool = False,
) -> Budget:
    if int(profile.window) != int(confirmed_window):
        raise ProfileMismatchError(
            f"profile {profile.name!r} expects {profile.window}, "
            f"but backend reports {confirmed_window}"
        )
    return compute_budget(
        window=confirmed_window,
        window_confirmed=True,
        output_reserve=profile.output_reserve,
        tool_reserve=profile.tool_reserve,
        tools_schema=profile.tools_schema,
        rag=profile.rag,
        margin_ratio=profile.margin_ratio,
        min_margin=profile.min_margin,
        safety_ratio=profile.safety_ratio,
        allow_hypothesis=allow_hypothesis,
    )


def extract_window(props: dict[str, Any]) -> int:
    """Extract a confirmed window from llama.cpp /props?model=... JSON."""

    if not isinstance(props, dict):
        raise UnknownWindowError("props response is not an object")
    default = props.get("default_generation_settings")
    candidates = []
    if isinstance(default, dict):
        candidates.extend([default.get("n_ctx"), default.get("n_ctx_slot")])
    candidates.extend([props.get("n_ctx"), props.get("n_ctx_slot")])
    for value in candidates:
        if isinstance(value, int) and value > 0:
            return value
    raise UnknownWindowError(
        "could not confirm n_ctx from /props; router /props without model "
        "returns a dummy n_ctx=0"
    )


def validate_mechanisms(
    *,
    legacy_filter_active: bool,
    legacy_filter_global: bool,
    builtin_compaction_enabled: bool,
) -> None:
    if legacy_filter_active and legacy_filter_global and builtin_compaction_enabled:
        raise CompactionConflictError(
            "chumba_summary (active+global) and chat.context_compaction are both enabled"
        )


def _tool_ids(messages: list[dict]) -> tuple[set[str], dict[str, int]]:
    calls: set[str] = set()
    call_positions: dict[str, int] = {}
    for idx, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            call_id = call.get("id")
            if call_id:
                calls.add(call_id)
                call_positions[call_id] = idx
    return calls, call_positions


def validate_tool_groups(messages: list[dict]) -> list[str]:
    """Return structural issues without changing the messages.

    A complete group is: assistant(tool_calls=[ids]) followed by one tool
    message for every id.  Multiple calls per assistant message are allowed.
    """

    issues: list[str] = []
    calls, call_positions = _tool_ids(messages)
    seen_results: dict[str, int] = {}

    for idx, message in enumerate(messages):
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not call_id:
                issues.append(f"tool result at {idx} has no tool_call_id")
                continue
            if call_id not in calls:
                issues.append(f"orphan tool result {call_id!r} at {idx}")
            else:
                seen_results[call_id] = idx
                if idx < call_positions[call_id]:
                    issues.append(f"tool result {call_id!r} appears before its call")

    for call_id, pos in call_positions.items():
        if call_id not in seen_results:
            issues.append(f"unfinished tool call {call_id!r} from assistant at {pos}")

    return issues


def detect_oversized_message(messages: list[dict], available_input_tokens: int) -> list[str]:
    issues = []
    for idx, message in enumerate(messages):
        content = message.get("content")
        if isinstance(content, str):
            size = estimate_tokens_chars4(content)
        else:
            size = estimate_tokens_chars4(str(content)) if content else 0
        size += estimate_tokens_chars4(str(message.get("tool_calls") or ""))
        if size > available_input_tokens:
            issues.append(
                f"message {idx} role={message.get('role')} estimated_tokens={size} "
                f"> available_input_tokens={available_input_tokens}"
            )
    return issues


def find_user_boundary(messages: list[dict], retention_percentage: int = 40) -> int:
    """Reference boundary: never cut before a user message or inside a tool group."""

    retention_percentage = min(50, max(10, int(retention_percentage)))
    if not messages:
        return 0
    keep_count = max(2, len(messages) * retention_percentage // 100)
    target = max(1, len(messages) - keep_count)
    boundaries = [idx for idx, message in enumerate(messages) if message.get("role") == "user"][1:]
    return next((idx for idx in reversed(boundaries) if idx <= target), 0)


def ensure_summary_reduces(before_tokens: int, after_tokens: int, min_reduction: float = 0.05) -> None:
    if before_tokens <= 0:
        raise PolicyError("before_tokens must be positive")
    reduction = (before_tokens - after_tokens) / before_tokens
    if after_tokens >= before_tokens or reduction < min_reduction:
        raise PolicyError(
            f"summary did not reduce the input enough: before={before_tokens} "
            f"after={after_tokens} reduction={reduction:.1%}"
        )
