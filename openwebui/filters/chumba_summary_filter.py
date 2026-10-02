"""
title: Chumba Rolling Summary
author: chumba
version: 0.1.0
required_open_webui_version: 0.6.0
description: Сжимает старые сообщения чата в конспект локальной моделью, когда история превышает порог. Конспекты кэшируются (инкрементально по chat_id).
"""

import hashlib
import json
import threading
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        threshold_tokens: int = Field(default=12000, description="Порог сжатия истории (примерно токенов)")
        keep_last_messages: int = Field(default=6, description="Сколько последних сообщений оставить без сжатия")
        summary_max_tokens: int = Field(default=400, description="Максимум токенов конспекта")
        max_input_chars: int = Field(default=30000, description="Сколько символов старой истории подавать на сжатие")
        llama_url: str = Field(default="http://llama-server:8080/v1", description="OpenAI-совместимый URL локальной модели")
        llama_model: str = Field(default="orchestrator", description="Имя модели на локальном сервере")
        cache_file: str = Field(default="/app/backend/data/chumba_summary_cache.json", description="Файл кэша конспектов")
        fail_open: bool = Field(default=True, description="При ошибке не сжимать (True) или прерывать запрос (False)")

    def __init__(self):
        self.valves = self.Valves()
        self._lock = threading.Lock()

    # ---------- вспомогательное ----------

    def _tokenize(self, text: str) -> int:
        try:
            base = self.valves.llama_url.rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            req = urllib.request.Request(
                base + "/tokenize",
                data=json.dumps({"content": text}).encode("utf-8"),
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            resp = urllib.request.urlopen(req, timeout=60)
            tokens = json.loads(resp.read().decode("utf-8", "replace")).get("tokens")
            if isinstance(tokens, list):
                return len(tokens)
        except Exception:
            pass
        return len(text) // 4

    def _cache(self) -> dict:
        path = Path(self.valves.cache_file)
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print("chumba_summary: cache read failed:", exc, flush=True)
        return {}

    def _save_cache(self, cache: dict) -> None:
        try:
            path = Path(self.valves.cache_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            if len(cache) > 100:
                for key in list(cache)[:-100]:
                    cache.pop(key, None)
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            print("chumba_summary: cache save failed:", exc, flush=True)

    def _summarize(self, text: str) -> str:
        instruction = (
            "Сожми предыдущую беседу в конспект для продолжения диалога. "
            "Сохрани: факты, принятые решения, договорённости, важные детали и открытые вопросы. "
            "Не добавляй домыслов. Пиши тезисами, не длиннее ~%d токенов." % self.valves.summary_max_tokens
        )
        payload = {
            "model": self.valves.llama_model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_tokens": self.valves.summary_max_tokens + 100,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            self.valves.llama_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        resp = urllib.request.urlopen(req, timeout=300)
        out = json.loads(resp.read().decode("utf-8", "replace"))
        return (out["choices"][0]["message"].get("content") or "").strip()

    def _get_summary(self, key: str, text: str) -> str:
        with self._lock:
            cache = self._cache()
            if key in cache:
                print(f"chumba_summary: cache hit ({key[:20]})", flush=True)
                return cache[key]
        print(f"chumba_summary: summarizing {len(text)} chars (key={key[:20]})", flush=True)
        summary = self._summarize(text)
        if summary:
            with self._lock:
                cache = self._cache()
                cache[key] = summary
                self._save_cache(cache)
        return summary

    # ---------- хук ----------

    def inlet(self, body: dict, __user__=None, __metadata__=None) -> dict:
        try:
            messages = body.get("messages") or []
            keep = max(0, int(self.valves.keep_last_messages))
            if len(messages) <= keep + 1:
                return body
            old = [m for m in messages[:-keep] if isinstance(m, dict)]
            tail = messages[-keep:] if keep else []
            joined = "\n".join(m.get("content") or "" for m in old if isinstance(m.get("content"), str))
            if not joined.strip():
                return body
            if self._tokenize(joined) < self.valves.threshold_tokens:
                return body

            joined = joined[-int(self.valves.max_input_chars):]
            chat_id = (__metadata__ or {}).get("chat_id")
            if chat_id:
                key = "c:" + str(chat_id) + ":" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
            else:
                key = "h:" + hashlib.sha1(joined.encode("utf-8")).hexdigest()
            summary = self._get_summary(key, joined)
            if summary:
                body = dict(body)
                body["messages"] = [
                    {
                        "role": "system",
                        "content": "Конспект предыдущей беседы (сжат автоматически; считай его достоверной историей):\n"
                        + summary,
                    }
                ] + tail
        except Exception as exc:
            print("chumba_summary: failed:", exc, flush=True)
            if not self.valves.fail_open:
                raise
        return body
