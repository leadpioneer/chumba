"""
title: Experiment Context Budget Guard
author: chumba experiment
version: 0.1.0
required_open_webui_version: 0.11.4
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request

try:
    from pydantic import BaseModel
except Exception:
    BaseModel = object

try:
    from fastapi import HTTPException
except Exception:
    HTTPException = None


class BudgetGuardError(RuntimeError):
    pass


class Filter:
    class Valves(BaseModel):
        enabled: bool = True
        window: int = 0
        output_reserve: int = 1024
        tool_reserve: int = 1024
        margin: int = 1024
        tokenizer_url: str = "http://llama-server:8080"
        props_url: str = "http://llama-server:8080"
        verify_backend_window: bool = True
        model: str = "orchestrator"
        tokenizer_timeout: int = 30
        template_overhead_per_message: int = 8
        template_overhead_fixed: int = 256
        fallback_chars_per_token: float = 2.5

    def __init__(self):
        self.valves = self.Valves()
        self._window_cache: int | None = None
        self._window_checked_at = 0.0

    def _tokenize(self, text: str) -> int:
        if not text:
            return 0
        payload = json.dumps({"model": self.valves.model, "content": text}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.valves.tokenizer_url.rstrip("/") + "/tokenize",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.valves.tokenizer_timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        tokens = data.get("tokens")
        if not isinstance(tokens, list):
            raise BudgetGuardError("tokenizer response has no tokens array")
        return len(tokens)

    def _confirmed_window(self, model: str) -> int:
        import time
        if self._window_cache is not None and time.monotonic() - self._window_checked_at < 300:
            return self._window_cache
        request = urllib.request.Request(
            self.valves.props_url.rstrip("/") + "/props?model=" + urllib.parse.quote(model, safe=""),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.valves.tokenizer_timeout) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            raise BudgetGuardError(
                "CONTEXT_BUDGET_CONFIG: cannot confirm backend window from /props; "
                "refusing the request"
            ) from exc
        candidates = []
        default = data.get("default_generation_settings") if isinstance(data, dict) else None
        if isinstance(default, dict):
            candidates.extend([default.get("n_ctx"), default.get("n_ctx_slot")])
        if isinstance(data, dict):
            candidates.extend([data.get("n_ctx"), data.get("n_ctx_slot")])
        for value in candidates:
            if isinstance(value, int) and value > 0:
                self._window_cache = value
                self._window_checked_at = time.monotonic()
                return value
        raise BudgetGuardError(
            "CONTEXT_BUDGET_CONFIG: /props did not expose a confirmed n_ctx "
            "(router /props without model returns a dummy n_ctx=0)"
        )

    def _text(self, value) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _estimate(self, body: dict) -> tuple[int, str]:
        messages = body.get("messages") or []
        tools = body.get("tools") or []
        parts = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            text = self._text(message.get("content"))
            if message.get("tool_calls"):
                text += "\n" + self._text(message["tool_calls"])
            if message.get("tool_call_id"):
                text += "\n" + str(message["tool_call_id"])
            parts.append(text)
        if tools:
            parts.append(self._text(tools))

        try:
            counted = sum(self._tokenize(part) for part in parts)
            method = "text_parts_tokenizer_plus_estimated_overhead"
        except Exception as exc:
            ratio = float(self.valves.fallback_chars_per_token or 0)
            if ratio <= 0:
                raise BudgetGuardError(
                    "budget guard cannot tokenize the request and no conservative "
                    f"fallback is configured: {type(exc).__name__}"
                ) from exc
            counted = sum(math.ceil(len(part) / ratio) for part in parts)
            method = "conservative_chars_per_token_fallback_plus_estimated_overhead"

        overhead = (
            len(messages) * max(0, int(self.valves.template_overhead_per_message))
            + max(0, int(self.valves.template_overhead_fixed))
        )
        return counted + overhead, method

    async def request(self, body, __metadata__=None):
        try:
            return await self._request(body)
        except BudgetGuardError as exc:
            if HTTPException is not None:
                code = 503 if "CONTEXT_BUDGET_CONFIG" in str(exc) else 413
                raise HTTPException(status_code=code, detail=str(exc)) from exc
            raise

    async def _request(self, body, __metadata__=None):
        if not self.valves.enabled:
            return body
        if not isinstance(body, dict):
            return body
        window = int(self.valves.window or 0)
        if window <= 0:
            raise BudgetGuardError(
                "CONTEXT_BUDGET_CONFIG: explicit backend window is required; "
                "the guard will not assume 32768 or inherit a 64K profile"
            )
        if self.valves.verify_backend_window:
            model = self.valves.model
            confirmed = self._confirmed_window(model)
            if confirmed != window:
                raise BudgetGuardError(
                    "CONTEXT_BUDGET_CONFIG: configured window does not match the backend. "
                    f"configured={window}; backend={confirmed}; model={model}. "
                    "Refusing the request; a 32K profile must not inherit a 64K budget."
                )
        output_reserve = max(0, int(self.valves.output_reserve))
        tool_reserve = max(0, int(self.valves.tool_reserve))
        margin = max(0, int(self.valves.margin))
        budget = window - output_reserve - tool_reserve - margin
        if budget <= 0:
            raise BudgetGuardError(
                "CONTEXT_BUDGET_CONFIG: reserves exceed window "
                f"(window={window}, output={output_reserve}, tool={tool_reserve}, margin={margin})"
            )

        estimated, method = self._estimate(body)
        print(
            f"budget_guard: messages={len(body.get('messages') or [])} "
            f"estimated={estimated} budget={budget} window={window} method={method}",
            flush=True,
        )
        if estimated > budget:
            over_by = estimated - budget
            raise BudgetGuardError(
                "CONTEXT_BUDGET_EXCEEDED: the request was not sent to the backend. "
                f"window={window}; estimated_prompt_tokens={estimated}; "
                f"budget={budget}; output_reserve={output_reserve}; "
                f"tool_reserve={tool_reserve}; margin={margin}; over_by={over_by}; "
                f"method={method}. "
                "This is a conservative budget check of text parts plus estimated "
                "template overhead, not an exact serialized chat-template token count. "
                "Shorten the message or attachments, start a separate task, or choose "
                "a smaller profile. Tool calls already executed are not rolled back."
            )
        return body
