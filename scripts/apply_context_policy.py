#!/usr/bin/env python3
"""Apply/rollback the experimental built-in context-compaction policy.

Dry-run is the default.  The script uses only supported Open WebUI HTTP APIs;
it never writes the database directly, never reads or writes access tokens, and
never applies a profile whose window does not exactly match the backend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "experiments" / "context-memory"
sys.path.insert(0, str(POLICY_DIR))

from policy import (  # noqa: E402
    BackendProfile,
    CompactionConflictError,
    PolicyError,
    ProfileMismatchError,
    UnknownWindowError,
    compute_budget,
    compute_profile_budget,
    extract_window,
)
from summary_policy import SUMMARY_MODEL, SUMMARY_TEMPLATE, merge_task_params, template_contract_errors  # noqa: E402

FILTER_ID = "chumba_summary"
COMPACTION_FIELDS = (
    "CONTEXT_COMPACTION_MODEL",
    "ENABLE_CONTEXT_COMPACTION",
    "CONTEXT_COMPACTION_TOKEN_THRESHOLD",
    "CONTEXT_COMPACTION_TOKEN_CAP",
    "CONTEXT_COMPACTION_RETENTION_PERCENTAGE",
    "CONTEXT_COMPACTION_PROMPT_TEMPLATE",
)
TASK_PARAMS_KEY = "task.model.params"

PROFILES = {
    "32k": BackendProfile(
        name="32k",
        window=32768,
        output_reserve=1024,
        tool_reserve=2048,
        tools_schema=0,
        rag=0,
        margin_ratio=0.03,
        min_margin=1024,
        safety_ratio=0.75,
    ),
    "64k": BackendProfile(
        name="64k",
        window=65536,
        output_reserve=1024,
        tool_reserve=2048,
        tools_schema=0,
        rag=0,
        margin_ratio=0.03,
        min_margin=1024,
        safety_ratio=0.75,
    ),
}


class ApplyError(PolicyError):
    pass


class OwuiClient:
    def __init__(self, base_url: str, backend_url: str | None, token: str | None = None, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.backend_url = backend_url.rstrip("/") if backend_url else None
        self.token = token or os.environ.get("OWUI_TOKEN", "")
        self.timeout = timeout

    def _request(self, base: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(base + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token and base == self.base_url:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                try:
                    return response.status, json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    return response.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return exc.code, raw
        except Exception as exc:
            return 0, f"{type(exc).__name__}: {exc}"

    def get(self, path: str) -> tuple[int, Any]:
        return self._request(self.base_url, "GET", path)

    def post(self, path: str, body: Any) -> tuple[int, Any]:
        return self._request(self.base_url, "POST", path, body)

    def props(self, model: str) -> tuple[int, Any]:
        if not self.backend_url:
            return 0, "backend URL is not configured"
        return self._request(
            self.backend_url,
            "GET",
            "/props?" + urllib.parse.urlencode({"model": model}),
        )

    def function_optional(self) -> dict[str, Any]:
        status, data = self.get(f"/api/v1/functions/id/{FILTER_ID}")
        if status == 404:
            return {}
        # Open WebUI returns 401 "could not find" for a missing function; do
        # not confuse that with an authentication failure.
        if status == 401 and isinstance(data, str) and "not find" in data.lower():
            return {}
        if status != 200 or not isinstance(data, dict):
            raise ApplyError(f"cannot read {FILTER_ID}: HTTP {status} {data}")
        return data

    def chats_config(self) -> dict[str, Any]:
        status, data = self.get("/api/v1/chats/config")
        if status != 200 or not isinstance(data, dict):
            raise ApplyError(f"cannot read chat config: HTTP {status} {data}")
        return data

    def task_params(self) -> dict[str, Any]:
        status, data = self.get("/api/v1/configs/namespace/task")
        if status != 200 or not isinstance(data, dict):
            raise ApplyError(
                "cannot read task namespace; refusing to use a full config export "
                f"(HTTP {status} {data})"
            )
        for key in (TASK_PARAMS_KEY, "model.params"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        model = data.get("model")
        if isinstance(model, dict) and isinstance(model.get("params"), dict):
            return model["params"]
        return {}

    def set_chats_config(self, config: dict[str, Any]) -> None:
        status, data = self.post("/api/v1/chats/config", config)
        if status != 200:
            raise ApplyError(f"chat config update failed: HTTP {status} {data}")

    def set_task_params(self, params: dict[str, Any]) -> None:
        status, data = self.post("/api/v1/configs/import", {"config": {TASK_PARAMS_KEY: params}})
        if status != 200:
            raise ApplyError(f"task params update failed: HTTP {status} {data}")


@dataclass
class Plan:
    profile: str
    model: str
    backend_window: int
    window_confirmed: bool
    budget: dict[str, Any]
    legacy_filter_active: bool
    legacy_filter_global: bool
    builtin_currently_enabled: bool
    conflict: bool
    apply_allowed: bool
    reasons: list[str]
    current_compaction: dict[str, Any]
    desired_compaction: dict[str, Any]
    current_task_params: dict[str, Any]
    desired_task_params: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _desired_compaction(threshold: int) -> dict[str, Any]:
    return {
        "CONTEXT_COMPACTION_MODEL": SUMMARY_MODEL,
        "ENABLE_CONTEXT_COMPACTION": True,
        "CONTEXT_COMPACTION_TOKEN_THRESHOLD": threshold,
        "CONTEXT_COMPACTION_TOKEN_CAP": threshold,
        "CONTEXT_COMPACTION_RETENTION_PERCENTAGE": 40,
        "CONTEXT_COMPACTION_PROMPT_TEMPLATE": SUMMARY_TEMPLATE,
    }


def build_plan(
    client: OwuiClient,
    *,
    profile_name: str,
    model: str = "orchestrator",
    allow_hypothesis: bool = False,
) -> Plan:
    if profile_name not in PROFILES:
        raise ApplyError(f"unknown profile {profile_name!r}; choose one of {sorted(PROFILES)}")
    profile = PROFILES[profile_name]
    contract_errors = template_contract_errors()
    if contract_errors:
        raise ApplyError("summary template contract failed: " + ", ".join(contract_errors))

    status, props = client.props(model)
    reasons: list[str] = []
    confirmed = False
    if status == 200:
        try:
            backend_window = extract_window(props)
            confirmed = True
        except UnknownWindowError:
            backend_window = profile.window
            reasons.append("backend /props did not expose a confirmed window")
    else:
        backend_window = profile.window
        reasons.append(f"backend /props probe failed: HTTP {status}")

    if confirmed and backend_window != profile.window:
        raise ProfileMismatchError(
            f"profile {profile_name} expects {profile.window}, backend reports {backend_window}"
        )

    if confirmed:
        budget = compute_profile_budget(profile, confirmed_window=backend_window)
    else:
        if not allow_hypothesis:
            raise UnknownWindowError(
                "backend window is unknown; apply is forbidden (dry-run may use --allow-hypothesis)"
            )
        budget = compute_budget(
            window=backend_window,
            window_confirmed=False,
            output_reserve=profile.output_reserve,
            tool_reserve=profile.tool_reserve,
            tools_schema=profile.tools_schema,
            rag=profile.rag,
            margin_ratio=profile.margin_ratio,
            min_margin=profile.min_margin,
            safety_ratio=profile.safety_ratio,
            allow_hypothesis=True,
        )

    function = client.function_optional()
    current_config = client.chats_config()
    current_task = client.task_params()
    legacy_active = bool(function.get("is_active"))
    legacy_global = bool(function.get("is_global"))
    builtin_enabled = bool(current_config.get("ENABLE_CONTEXT_COMPACTION"))
    conflict = legacy_active
    if conflict:
        reasons.append("legacy chumba_summary is active; built-in compaction would conflict")
    if not confirmed:
        reasons.append("window is a hypothesis; apply is forbidden")
    apply_allowed = confirmed and not conflict

    desired_compaction = _desired_compaction(budget.history_trigger)
    desired_task = merge_task_params(current_task)
    return Plan(
        profile=profile_name,
        model=model,
        backend_window=backend_window,
        window_confirmed=confirmed,
        budget=budget.as_dict(),
        legacy_filter_active=legacy_active,
        legacy_filter_global=legacy_global,
        builtin_currently_enabled=builtin_enabled,
        conflict=conflict,
        apply_allowed=apply_allowed,
        reasons=reasons,
        current_compaction={key: current_config.get(key) for key in COMPACTION_FIELDS},
        desired_compaction=desired_compaction,
        current_task_params=current_task,
        desired_task_params=desired_task,
    )


def _rollback_payload(plan: Plan, owui_url: str) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owui_url": owui_url,
        "profile": plan.profile,
        "backend_window": plan.backend_window,
        "window_confirmed": plan.window_confirmed,
        "compaction_before": plan.current_compaction,
        "task_model_params_before": plan.current_task_params,
        "compaction_after": plan.desired_compaction,
        "task_model_params_after": plan.desired_task_params,
    }


def _assert_no_secret_keys(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            secret_names = {
                "token",
                "access_token",
                "refresh_token",
                "secret",
                "password",
                "api_key",
                "authorization",
            }
            if lowered in secret_names or lowered.endswith(("_secret", "_password", "_api_key")):
                raise ApplyError(f"refusing rollback file with secret-like key at {path}/{key}")
            _assert_no_secret_keys(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secret_keys(child, f"{path}[{index}]")


def write_rollback(path: Path, plan: Plan, owui_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _rollback_payload(plan, owui_url)
    _assert_no_secret_keys(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _restore(
    client: OwuiClient,
    *,
    compaction_before: dict[str, Any],
    task_before: dict[str, Any],
) -> None:
    current = client.chats_config()
    current.update({key: compaction_before.get(key) for key in COMPACTION_FIELDS})
    client.set_chats_config(current)
    client.set_task_params(task_before)


def _verify(
    client: OwuiClient,
    *,
    desired_compaction: dict[str, Any],
    desired_task: dict[str, Any],
) -> None:
    config = client.chats_config()
    for key, expected in desired_compaction.items():
        if config.get(key) != expected:
            raise ApplyError(f"verification failed for {key}: {config.get(key)!r} != {expected!r}")
    params = client.task_params()
    for key, expected in desired_task.items():
        if params.get(key) != expected:
            raise ApplyError(f"verification failed for task param {key}: {params.get(key)!r} != {expected!r}")


def apply_plan(client: OwuiClient, plan: Plan, rollback_path: Path) -> dict[str, Any]:
    if not plan.apply_allowed:
        raise ApplyError("apply refused: " + "; ".join(plan.reasons or ["not allowed"]))
    write_rollback(rollback_path, plan, client.base_url)
    current = client.chats_config()
    current.update(plan.desired_compaction)
    try:
        client.set_chats_config(current)
        client.set_task_params(plan.desired_task_params)
        _verify(client, desired_compaction=plan.desired_compaction, desired_task=plan.desired_task_params)
    except Exception:
        try:
            _restore(
                client,
                compaction_before=plan.current_compaction,
                task_before=plan.current_task_params,
            )
        except Exception:
            pass
        raise
    return {"applied": True, "rollback_file": str(rollback_path), "plan": plan.public()}


def rollback(client: OwuiClient, rollback_path: Path) -> dict[str, Any]:
    payload = json.loads(rollback_path.read_text(encoding="utf-8"))
    _assert_no_secret_keys(payload)
    compaction_before = payload.get("compaction_before") or {}
    task_before = payload.get("task_model_params_before") or {}
    _restore(client, compaction_before=compaction_before, task_before=task_before)
    _verify(
        client,
        desired_compaction={key: compaction_before.get(key) for key in COMPACTION_FIELDS},
        desired_task=task_before,
    )
    return {"rolled_back": True, "rollback_file": str(rollback_path)}


def default_rollback_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "benchmark-results" / "context-memory" / f"policy-rollback-{stamp}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--owui-url", default="http://127.0.0.1:3001")
    parser.add_argument("--backend-url", default=None)
    parser.add_argument("--model", default="orchestrator")
    parser.add_argument("--token", default=None)
    parser.add_argument("--rollback-file", default=None)
    parser.add_argument("--allow-hypothesis", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", metavar="FILE")
    args = parser.parse_args(argv)

    client = OwuiClient(args.owui_url, args.backend_url, args.token)
    try:
        if args.rollback:
            result = rollback(client, Path(args.rollback))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        plan = build_plan(
            client,
            profile_name=args.profile,
            model=args.model,
            allow_hypothesis=args.allow_hypothesis,
        )
        if not args.apply:
            print(json.dumps(plan.public(), ensure_ascii=False, indent=2))
            return 0
        rollback_path = Path(args.rollback_file) if args.rollback_file else default_rollback_path()
        result = apply_plan(client, plan, rollback_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PolicyError, ApplyError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
