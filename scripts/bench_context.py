#!/usr/bin/env python3
"""Reproducible context benchmark for an OpenAI-compatible local backend.

The client records server timings, end-to-end latency, memory samples and the
actual prompt/output token counts in CSV and JSON. Synthetic text is generated
from a fixed seed; no user data or secrets are written to the artifacts.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "testdata" / "context_bench"
VERSION = "1.0"

WORDS = [
    "архив", "журнал", "сектор", "контур", "стенд", "модуль", "канал", "узел",
    "поток", "кварц", "сигнал", "контур", "протокол", "слой", "срез", "маршрут",
    "буфер", "регистр", "контейнер", "профиль", "сеанс", "запрос", "ответ", "код",
    "схема", "проверка", "сверка", "источник", "приёмник", "индекс", "сегмент",
    "каталог", "фрагмент", "проход", "отчёт", "сводка", "метка", "граница", "окно",
    "полоса", "шаг", "цикл", "уровень", "порог", "фон", "шум", "фильтр", "маска",
    "вектор", "матрица", "строка", "столбец", "ячейка", "блок", "пакет", "слот",
    "ключ", "замок", "мост", "шлюз", "канал", "порт", "адрес", "путь", "след",
    "точка", "ось", "план", "карта", "слой", "ряд", "набор", "список", "реестр",
]


class ApiError(RuntimeError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class MemorySampler(threading.Thread):
    def __init__(self, container: str | None, interval: float = 1.0):
        super().__init__(daemon=True)
        self.container = container
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[dict] = []
        self.base = self.sample_once()

    def sample_once(self) -> dict:
        out = {
            "vram_used_mib": None,
            "vram_total_mib": None,
            "ram_available_mib": None,
            "ram_total_mib": None,
            "swap_used_mib": None,
            "swap_total_mib": None,
            "container_mem_mib": None,
            "error": None,
        }
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                first = res.stdout.strip().splitlines()[0]
                used, total = [int(x.strip()) for x in first.split(",")[:2]]
                out["vram_used_mib"] = used
                out["vram_total_mib"] = total
        except Exception as exc:
            out["error"] = f"nvidia-smi: {exc}"
        try:
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                out["ram_total_mib"] = int(status.ullTotalPhys / (1 << 20))
                out["ram_available_mib"] = int(status.ullAvailPhys / (1 << 20))
        except Exception:
            pass
        if self.container:
            try:
                res = subprocess.run(
                    ["docker", "exec", self.container, "cat", "/proc/meminfo"],
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode == 0:
                    info = {}
                    for line in res.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            info[parts[0].rstrip(":")] = int(parts[1])
                    total = info.get("MemTotal", 0)
                    avail = info.get("MemAvailable", 0)
                    swap_total = info.get("SwapTotal", 0)
                    swap_free = info.get("SwapFree", 0)
                    out["ram_total_mib"] = int(total / 1024) or out["ram_total_mib"]
                    out["ram_available_mib"] = int(avail / 1024) or out["ram_available_mib"]
                    out["swap_total_mib"] = int(swap_total / 1024)
                    out["swap_used_mib"] = int((swap_total - swap_free) / 1024)
            except Exception:
                pass
            try:
                res = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", self.container],
                    capture_output=True, text=True, timeout=10,
                )
                if res.returncode == 0 and res.stdout.strip():
                    out["container_mem_mib"] = parse_mem_mib(res.stdout.strip().split("/")[0].strip())
            except Exception:
                pass
        return out

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.samples.append(self.sample_once())
            self.stop_event.wait(self.interval)

    def stop(self) -> dict:
        self.stop_event.set()
        self.join(timeout=5)
        peaks = {}
        for key in ("vram_used_mib", "ram_available_mib", "swap_used_mib", "container_mem_mib"):
            vals = [s.get(key) for s in self.samples if s.get(key) is not None]
            peaks[key] = max(vals) if vals else None
        peaks["vram_min_available_mib"] = None
        if peaks["vram_used_mib"] is not None and self.base.get("vram_total_mib"):
            peaks["vram_min_available_mib"] = self.base["vram_total_mib"] - peaks["vram_used_mib"]
        peaks["base"] = self.base
        peaks["samples"] = len(self.samples)
        return peaks


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def parse_mem_mib(text: str) -> float | None:
    m = re.match(r"([0-9.]+)\s*([KMGTP]?i?B)", text.strip(), re.I)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    factor = {
        "b": 1 / (1 << 20), "kb": 1 / 1024, "kib": 1 / 1024,
        "mb": 1, "mib": 1, "gb": 1024, "gib": 1024,
        "tb": 1024 * 1024, "tib": 1024 * 1024,
    }.get(unit)
    return round(value * factor, 1) if factor else None


class Client:
    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.root_url = self.base_url[:-3].rstrip("/") if self.base_url.endswith("/v1") else self.base_url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.tokenizer_available = True
        self.tokenizer_error = None

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, url: str, payload: dict | None, timeout: float | None = None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="GET" if payload is None else "POST")
        for key, value in self._headers().items():
            req.add_header(key, value)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise ApiError(f"HTTP {exc.code}: {body[:1200]}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"connection failed: {exc.reason}") from exc

    def get_json(self, path: str) -> dict:
        with self._request(self.root_url + path, None, 30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def token_count(self, text: str) -> int | None:
        if not self.tokenizer_available:
            return None
        try:
            with self._request(self.root_url + "/tokenize", {"content": text, "model": self.model}, 120) as resp:
                out = json.loads(resp.read().decode("utf-8", "replace"))
            tokens = out.get("tokens")
            if isinstance(tokens, list):
                return len(tokens)
        except Exception as exc:
            self.tokenizer_available = False
            self.tokenizer_error = str(exc)[:300]
        return None

    def chat(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float = 0.0,
        stream: bool = False,
        tools: list[dict] | None = None,
        seed: int | None = 42,
        top_p: float | None = None,
        extra: dict | None = None,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if seed is not None:
            payload["seed"] = seed
        if top_p is not None:
            payload["top_p"] = top_p
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if extra:
            payload.update(extra)
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
            return self._stream_chat(payload)
        started = time.perf_counter()
        with self._request(self.base_url + "/chat/completions", payload) as resp:
            raw = resp.read().decode("utf-8", "replace")
        elapsed = time.perf_counter() - started
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"invalid JSON response: {raw[:800]}") from exc
        return {"data": data, "elapsed_s": elapsed, "ttft_s": None, "raw": raw}

    def _stream_chat(self, payload: dict) -> dict:
        started = time.perf_counter()
        ttft = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict] = []
        final = None
        usage = None
        with self._request(self.base_url + "/chat/completions", payload) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                if chunk.get("timings"):
                    final = chunk
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        if ttft is None:
                            ttft = time.perf_counter() - started
                        content_parts.append(piece)
                    reason = delta.get("reasoning_content") or delta.get("reasoning")
                    if reason:
                        if ttft is None:
                            ttft = time.perf_counter() - started
                        reasoning_parts.append(reason)
                    for tc in delta.get("tool_calls") or []:
                        tool_calls.append(tc)
        elapsed = time.perf_counter() - started
        data = final or {}
        if not data:
            data = {
                "choices": [{"message": {"content": "".join(content_parts), "tool_calls": tool_calls}}],
                "usage": usage,
            }
        else:
            choices = data.get("choices") or []
            if not choices:
                data["choices"] = [{"message": {"content": "".join(content_parts), "tool_calls": tool_calls}}]
            else:
                message = choices[0].setdefault("message", {})
                if not message.get("content"):
                    message["content"] = "".join(content_parts)
                if tool_calls and not message.get("tool_calls"):
                    message["tool_calls"] = tool_calls
            if usage and not data.get("usage"):
                data["usage"] = usage
        return {"data": data, "elapsed_s": elapsed, "ttft_s": ttft, "raw": None}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def make_sentence(rng: random.Random, counter: int) -> str:
    words = [rng.choice(WORDS) for _ in range(rng.randint(9, 15))]
    return f"Запись {counter}: " + " ".join(words) + "."


def build_filler(
    rng: random.Random,
    target_tokens: int,
    client: Client,
    counter_start: int,
    chars_per_token: float = 3.2,
) -> tuple[str, int]:
    target_chars = max(200, int(target_tokens * chars_per_token))
    text = ""
    counter = counter_start
    for _ in range(3):
        parts = []
        size = 0
        local_counter = counter
        while size < target_chars:
            sentence = make_sentence(rng, local_counter)
            parts.append(sentence)
            size += len(sentence) + 1
            local_counter += 1
        text = " ".join(parts)
        if client.tokenizer_available:
            actual = client.token_count(text)
            if actual and actual > 0:
                if abs(actual - target_tokens) / max(1, target_tokens) <= 0.015:
                    counter = local_counter
                    break
                factor = target_tokens / actual
                target_chars = max(200, int(len(text) * factor))
                counter = local_counter
            else:
                break
        else:
            counter = local_counter
            break
    cut = text.rfind(".")
    if cut > len(text) * 0.9:
        text = text[: cut + 1]
    return text, counter


def build_retrieval_doc(
    rng: random.Random,
    target_tokens: int,
    client: Client,
    fixtures: dict,
    counter_start: int,
) -> tuple[str, list[dict]]:
    facts = fixtures["facts"]
    distractors = fixtures["distractors"]
    filler, counter = build_filler(rng, max(512, target_tokens - 300), client, counter_start)
    sentences = split_sentences(filler)
    buckets: list[list[str]] = [[] for _ in range(10)]
    total_chars = sum(len(s) for s in sentences) or 1
    running = 0
    for sentence in sentences:
        idx = min(9, int(running * 10 / total_chars))
        buckets[idx].append(sentence)
        running += len(sentence) + 1
    placements = [(1, facts[0]), (5, facts[1]), (9, facts[2])]
    used_distractors = 0
    placements_log = []
    for bucket_idx, fact in placements:
        buckets[bucket_idx].insert(0, fact["sentence"])
        placements_log.append({"id": fact["id"], "bucket": bucket_idx, "fraction": bucket_idx / 10.0})
    for bucket_idx in (0, 2, 3, 4, 6, 7, 8):
        if used_distractors < len(distractors):
            buckets[bucket_idx].append(distractors[used_distractors])
            used_distractors += 1
    doc = " ".join(" ".join(bucket) for bucket in buckets)
    prefix_chars = 0
    for entry in placements_log:
        target = entry["bucket"] / 10.0
        prefix = doc[: int(len(doc) * target)]
        count = client.token_count(prefix)
        entry["approx_token_position"] = count
    return doc, placements_log


def extract_text(data: dict) -> tuple[str, str, list[dict]]:
    choices = data.get("choices") or [{}]
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    tool_calls = message.get("tool_calls") or []
    return content, reasoning, tool_calls


def timings_from(data: dict) -> dict:
    timings = data.get("timings") or {}
    usage = data.get("usage") or {}
    prompt_n = timings.get("prompt_n")
    output_n = timings.get("predicted_n")
    if prompt_n is None:
        prompt_n = usage.get("prompt_tokens")
    if output_n is None:
        output_n = usage.get("completion_tokens")
    return {
        "prompt_n": prompt_n,
        "output_n": output_n,
        "cache_n": timings.get("cache_n"),
        "prompt_ms": timings.get("prompt_ms"),
        "output_ms": timings.get("predicted_ms"),
        "prefill_tps": timings.get("prompt_per_second"),
        "decode_tps": timings.get("predicted_per_second"),
        "truncated": timings.get("truncated"),
        "raw": timings,
    }


def make_record(
    test: str,
    case: str,
    repeat: int,
    target_tokens: int | None,
    response: dict | None,
    error: str | None,
    cold: bool,
    details: dict | None = None,
) -> dict:
    rec = {
        "run_id": f"{test}-{case}-r{repeat}-{int(time.time() * 1000)}",
        "timestamp": now_iso(),
        "test": test,
        "case": case,
        "repeat": repeat,
        "cold": cold,
        "target_tokens": target_tokens,
        "prompt_n": None,
        "prompt_total": None,
        "output_n": None,
        "cache_n": None,
        "cache_hit_ratio": None,
        "prompt_ms": None,
        "output_ms": None,
        "prefill_tps": None,
        "decode_tps": None,
        "ttft_s": None,
        "total_s": None,
        "e2e_tps": None,
        "truncated": None,
        "answer_excerpt": None,
        "answer_ok": None,
        "error": error,
        "details": details or {},
        "timings_raw": None,
    }
    if response:
        data = response["data"]
        content, reasoning, _ = extract_text(data)
        timing = timings_from(data)
        rec.update({k: timing[k] for k in ("prompt_n", "output_n", "cache_n", "prompt_ms", "output_ms", "prefill_tps", "decode_tps", "truncated")})
        rec["timings_raw"] = timing["raw"]
        if rec["prompt_n"] is not None and rec["cache_n"] is not None:
            rec["prompt_total"] = rec["prompt_n"] + rec["cache_n"]
            if rec["prompt_total"] > 0:
                rec["cache_hit_ratio"] = rec["cache_n"] / rec["prompt_total"]
        rec["ttft_s"] = response.get("ttft_s")
        rec["total_s"] = response.get("elapsed_s")
        if rec["prompt_n"] is not None and rec["output_n"] is not None and rec["total_s"]:
            rec["e2e_tps"] = (rec["prompt_n"] + rec["output_n"]) / rec["total_s"]
        rec["answer_excerpt"] = (content or reasoning or "")[:500]
        rec["details"]["reasoning_excerpt"] = (reasoning or "")[:200]
    return rec


def score_contains(text: str, expected: str) -> bool:
    return re.search(r"(?<!\d)" + re.escape(expected) + r"(?!\d)", text or "") is not None


def run_perf(client: Client, ctx: int, fills: list[float], repeats: int, args, sampler: MemorySampler) -> list[dict]:
    records = []
    counter = 1000
    for frac in fills:
        target = int(ctx * frac) - 1100
        if target < 256:
            continue
        for rep in range(repeats):
            rng = random.Random(args.seed + int(frac * 10000) + rep * 7919)
            filler, counter = build_filler(rng, target, client, counter)
            messages = [
                {"role": "system", "content": "Отвечай предельно кратко, одним словом."},
                {"role": "user", "content": filler + "\n\nКонец документа. Ответь одним словом: ОК."},
            ]
            details = {"fill_fraction": frac, "filler_chars": len(filler)}
            try:
                response = client.chat(
                    messages, max_tokens=args.max_tokens, stream=args.stream,
                    temperature=0.0, seed=args.seed,
                )
                rec = make_record("perf", f"fill_{frac:.2f}", rep, target, response, None, args.cold, details)
                content, _, _ = extract_text(response["data"])
                rec["answer_ok"] = "ок" in (content or "").lower()
            except Exception as exc:
                rec = make_record("perf", f"fill_{frac:.2f}", rep, target, None, str(exc)[:800], args.cold, details)
            records.append(rec)
            print(f"[perf] fill={frac:.2f} rep={rep} prompt_n={rec['prompt_n']} "
                  f"prefill={rec['prefill_tps']} decode={rec['decode_tps']} total={rec['total_s']}", flush=True)
    return records


def run_retrieval(client: Client, ctx: int, fills: list[float], repeats: int, args, fixtures: dict, sampler: MemorySampler) -> list[dict]:
    records = []
    counter = 5000
    for frac in fills:
        target = int(ctx * frac) - 900
        if target < 512:
            continue
        for rep in range(repeats):
            rng = random.Random(args.seed + 101 + int(frac * 10000) + rep * 104729)
            doc, placements = build_retrieval_doc(rng, target, client, fixtures, counter)
            counter += 10000
            questions = "\n".join(f"{i + 1}) {f['question']}" for i, f in enumerate(fixtures["facts"]))
            messages = [
                {"role": "system", "content": "Ты извлекаешь факты из документа. Не догадывайся. Отвечай строго по документу."},
                {"role": "user", "content": doc + "\n\nВопросы:\n" + questions + "\n\nДай три ответа, каждый с новой строки."},
            ]
            details = {"fill_fraction": frac, "placements": placements, "doc_chars": len(doc)}
            try:
                response = client.chat(messages, max_tokens=args.retrieval_max_tokens, stream=args.stream, temperature=0.0, seed=args.seed)
                content, _, _ = extract_text(response["data"])
                found = {}
                for fact in fixtures["facts"]:
                    found[fact["id"]] = score_contains(content, fact["answer"])
                details["found"] = found
                details["score"] = sum(1 for v in found.values() if v)
                rec = make_record("retrieval", f"fill_{frac:.2f}", rep, target, response, None, args.cold, details)
                rec["answer_ok"] = details["score"] == len(fixtures["facts"])
            except Exception as exc:
                rec = make_record("retrieval", f"fill_{frac:.2f}", rep, target, None, str(exc)[:800], args.cold, details)
                rec["answer_ok"] = False
            records.append(rec)
            print(f"[retrieval] fill={frac:.2f} rep={rep} score={rec['answer_ok']} prompt_n={rec['prompt_n']}", flush=True)
    return records


def run_multi_turn(client: Client, ctx: int, args, sampler: MemorySampler) -> list[dict]:
    records = []
    target_per_turn = max(512, int(ctx * args.turn_fill) // max(1, args.turns))
    rng = random.Random(args.seed + 202)
    markers = [f"MARKER-{i + 1}-{rng.randrange(1000, 9999)}" for i in range(args.turns)]
    messages = [{"role": "system", "content": "Ты ведёшь длинный журнал. Подтверждай новые записи одной короткой фразой."}]
    counter = 20000
    for turn in range(args.turns):
        filler, counter = build_filler(rng, target_per_turn, client, counter)
        messages.append({
            "role": "user",
            "content": filler + f"\n\nЗапомни маркер {turn + 1}: {markers[turn]}. Подтверди одной короткой фразой.",
        })
        try:
            response = client.chat(messages, max_tokens=args.turn_max_tokens, stream=False, temperature=0.0, seed=args.seed)
            content, _, _ = extract_text(response["data"])
            rec = make_record("multi_turn", f"turn_{turn + 1}", turn, target_per_turn * (turn + 1), response, None, args.cold, {"marker": markers[turn]})
        except Exception as exc:
            content = ""
            rec = make_record("multi_turn", f"turn_{turn + 1}", turn, target_per_turn * (turn + 1), None, str(exc)[:800], args.cold, {"marker": markers[turn]})
        records.append(rec)
        messages.append({"role": "assistant", "content": content.strip() or "Принято."})
        print(f"[multi_turn] turn={turn + 1} prompt_n={rec['prompt_n']} cache_n={rec['cache_n']} total={rec['total_s']}", flush=True)
    final_question = (
        f"Не используя внешние источники, назови два маркера из журнала: первый ({markers[0]}) "
        f"и последний ({markers[-1]}). Если маркер неизвестен, напиши НЕТ. Ответь двумя строками."
    )
    messages.append({"role": "user", "content": final_question})
    try:
        response = client.chat(messages, max_tokens=args.turn_final_max_tokens, stream=False, temperature=0.0, seed=args.seed)
        content, _, _ = extract_text(response["data"])
        details = {
            "first_marker": markers[0],
            "last_marker": markers[-1],
            "first_found": markers[0].lower() in (content or "").lower(),
            "last_found": markers[-1].lower() in (content or "").lower(),
        }
        rec = make_record("multi_turn", "final_recall", args.turns, None, response, None, args.cold, details)
        rec["answer_ok"] = details["first_found"] and details["last_found"]
    except Exception as exc:
        rec = make_record("multi_turn", "final_recall", args.turns, None, None, str(exc)[:800], args.cold, {})
        rec["answer_ok"] = False
    records.append(rec)
    print(f"[multi_turn] final_recall ok={rec['answer_ok']} prompt_n={rec['prompt_n']}", flush=True)
    return records


def run_tools(client: Client, ctx: int, args, fixtures: dict, sampler: MemorySampler) -> list[dict]:
    records = []
    rng = random.Random(args.seed + 303)
    filler, _ = build_filler(rng, max(512, int(ctx * args.tool_fill) - 1200), client, 30000)
    tools = fixtures["tools"]
    messages = [
        {"role": "system", "content": "Используй инструменты, когда они нужны. Не выдумывай результаты."},
        {"role": "user", "content": filler + "\n\n" + fixtures["prompt"]},
    ]
    details = {"fill_fraction": args.tool_fill, "tool_calls": [], "tool_names_ok": False, "tool_args_ok": False}
    try:
        first = client.chat(messages, max_tokens=args.tool_max_tokens, stream=False, temperature=0.0, seed=args.seed, tools=tools)
        first_timing = timings_from(first["data"])
        details["first_request"] = {
            "prompt_n": first_timing.get("prompt_n"),
            "output_n": first_timing.get("output_n"),
            "total_s": first.get("elapsed_s"),
            "prompt_ms": first_timing.get("prompt_ms"),
            "output_ms": first_timing.get("output_ms"),
            "prefill_tps": first_timing.get("prefill_tps"),
            "decode_tps": first_timing.get("decode_tps"),
        }
        content, _, tool_calls = extract_text(first["data"])
        details["tool_calls"] = tool_calls
        names = []
        args_ok = True
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name")
            names.append(name)
            try:
                parsed = json.loads(fn.get("arguments") or "{}")
            except Exception:
                parsed = None
            details.setdefault("parsed_arguments", []).append(parsed)
            if parsed is None:
                args_ok = False
            if name == "lookup_project_code":
                if not isinstance(parsed, dict) or not isinstance(parsed.get("project"), str):
                    args_ok = False
            elif name == "add_numbers":
                if not isinstance(parsed, dict) or not all(isinstance(parsed.get(k), int) for k in ("a", "b")):
                    args_ok = False
        details["tool_names_ok"] = bool(tool_calls) and all(n in ("lookup_project_code", "add_numbers") for n in names)
        details["tool_args_ok"] = args_ok and bool(tool_calls)
        assistant = {"role": "assistant", "content": content or ""}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name")
            try:
                parsed = json.loads(fn.get("arguments") or "{}")
            except Exception:
                parsed = {}
            if name == "lookup_project_code":
                result = {"project": parsed.get("project"), "code": fixtures["project_codes"].get(parsed.get("project"), "UNKNOWN")}
            elif name == "add_numbers":
                result = {"sum": parsed.get("a", 0) + parsed.get("b", 0)}
            else:
                result = {"error": "unknown_tool"}
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": json.dumps(result, ensure_ascii=False)})
        messages.append({"role": "user", "content": "Теперь одной строкой назови код проекта и сумму."})
        second = client.chat(messages, max_tokens=args.tool_final_max_tokens, stream=False, temperature=0.0, seed=args.seed, tools=tools)
        final_content, _, _ = extract_text(second["data"])
        code_ok = score_contains(final_content, fixtures["expected_code"])
        sum_ok = score_contains(final_content, str(fixtures["expected_sum"]))
        details.update({"final_code_ok": code_ok, "final_sum_ok": sum_ok})
        rec = make_record("tools", f"fill_{args.tool_fill:.2f}", 0, int(ctx * args.tool_fill), second, None, args.cold, details)
        rec["answer_ok"] = bool(details["tool_names_ok"] and details["tool_args_ok"] and code_ok and sum_ok)
    except Exception as exc:
        rec = make_record("tools", f"fill_{args.tool_fill:.2f}", 0, int(ctx * args.tool_fill), None, str(exc)[:800], args.cold, details)
        rec["answer_ok"] = False
    records.append(rec)
    print(f"[tools] ok={rec['answer_ok']} calls={len(details['tool_calls'])} names_ok={details['tool_names_ok']} args_ok={details['tool_args_ok']}", flush=True)
    return records


def probe_backend(client: Client, args) -> dict:
    out = {"base_url": client.base_url, "model": client.model, "time": now_iso(), "health": None, "models": None, "props": None, "container": None, "gpu": None}
    try:
        out["health"] = client.get_json("/health")
    except Exception as exc:
        out["health"] = {"error": str(exc)[:500]}
    try:
        out["models"] = client.get_json("/v1/models")
    except Exception as exc:
        out["models"] = {"error": str(exc)[:500]}
    try:
        out["props"] = client.get_json("/props")
    except Exception as exc:
        out["props"] = {"error": str(exc)[:500]}
    if args.container:
        try:
            res = subprocess.run(
                ["docker", "inspect", args.container],
                capture_output=True, text=True, timeout=20,
            )
            if res.returncode == 0:
                info = json.loads(res.stdout)[0]
                out["container"] = {
                    "Name": info.get("Name"),
                    "Id": info.get("Id"),
                    "Image": info.get("Image"),
                    "ImageDigest": (info.get("Config") or {}).get("Image"),
                    "Created": info.get("Created"),
                    "Running": (info.get("State") or {}).get("Running"),
                }
        except Exception as exc:
            out["container"] = {"error": str(exc)[:500]}
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        out["gpu"] = res.stdout.strip() if res.returncode == 0 else res.stderr.strip()
    except Exception as exc:
        out["gpu"] = f"error: {exc}"
    return out


def write_outputs(records: list[dict], metadata: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "runs.json").write_text(json.dumps({"metadata": metadata, "runs": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "run_id", "timestamp", "test", "case", "repeat", "cold", "target_tokens",
        "prompt_n", "prompt_total", "output_n", "cache_n", "cache_hit_ratio", "prompt_ms", "output_ms", "prefill_tps",
        "decode_tps", "ttft_s", "total_s", "e2e_tps", "truncated", "answer_ok",
        "vram_base_mib", "vram_peak_mib", "vram_min_available_mib", "ram_available_mib",
        "swap_used_mib", "container_mem_mib", "error", "answer_excerpt", "details_json",
    ]
    with (out_dir / "runs.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rec in records:
            mem = rec.get("memory") or {}
            row = {k: rec.get(k) for k in fields}
            row.update({
                "vram_base_mib": (mem.get("base") or {}).get("vram_used_mib"),
                "vram_peak_mib": mem.get("vram_used_mib"),
                "vram_min_available_mib": mem.get("vram_min_available_mib"),
                "ram_available_mib": mem.get("ram_available_mib"),
                "swap_used_mib": mem.get("swap_used_mib"),
                "container_mem_mib": mem.get("container_mem_mib"),
                "details_json": json.dumps(rec.get("details") or {}, ensure_ascii=False),
            })
            writer.writerow(row)
    summary = []
    for test in sorted({r["test"] for r in records}):
        subset = [r for r in records if r["test"] == test and not r.get("error")]
        if not subset:
            continue
        summary.append({
            "test": test,
            "runs": len(subset),
            "prompt_n_median": statistics.median([r["prompt_n"] for r in subset if r["prompt_n"] is not None]) if any(r["prompt_n"] is not None for r in subset) else None,
            "prefill_tps_median": statistics.median([r["prefill_tps"] for r in subset if r["prefill_tps"] is not None]) if any(r["prefill_tps"] is not None for r in subset) else None,
            "decode_tps_median": statistics.median([r["decode_tps"] for r in subset if r["decode_tps"] is not None]) if any(r["decode_tps"] is not None for r in subset) else None,
            "ttft_s_median": statistics.median([r["ttft_s"] for r in subset if r["ttft_s"] is not None]) if any(r["ttft_s"] is not None for r in subset) else None,
            "total_s_median": statistics.median([r["total_s"] for r in subset if r["total_s"] is not None]) if any(r["total_s"] is not None for r in subset) else None,
            "answer_ok_rate": round(sum(1 for r in subset if r.get("answer_ok") is True) / len([r for r in subset if r.get("answer_ok") is not None]), 3) if any(r.get("answer_ok") is not None for r in subset) else None,
        })
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def restart_backend(container: str, client: Client) -> dict:
    started = time.perf_counter()
    subprocess.run(["docker", "restart", container], check=True, capture_output=True, text=True, timeout=180)
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            health = client.get_json("/health")
            if health.get("status") == "ok":
                break
        except Exception:
            pass
        time.sleep(2)
    warm = client.chat([{"role": "user", "content": "Ответь одним словом: ОК"}], max_tokens=1, stream=False, temperature=0.0)
    elapsed = time.perf_counter() - started
    return {"restart_and_load_s": round(elapsed, 2), "warmup_prompt_n": timings_from(warm["data"]).get("prompt_n")}


def parse_fills(text: str) -> list[float]:
    values = []
    for part in text.split(","):
        part = part.strip()
        if part:
            value = float(part)
            if not 0 < value < 1:
                raise argparse.ArgumentTypeError("fill values must be between 0 and 1")
            values.append(value)
    return values


def parse_tests(text: str) -> list[str]:
    allowed = {"perf", "retrieval", "multi_turn", "tools", "integration"}
    values = [x.strip() for x in text.split(",") if x.strip()]
    bad = [x for x in values if x not in allowed]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown tests: {bad}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="orchestrator")
    parser.add_argument("--api-key-env", default=None, help="Environment variable that holds the API key")
    parser.add_argument("--ctx-size", type=int, default=32768)
    parser.add_argument("--tests", type=parse_tests, default=parse_tests("perf,retrieval,multi_turn,tools"))
    parser.add_argument("--fills", type=parse_fills, default=parse_fills("0.25,0.5,0.85"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--retrieval-max-tokens", type=int, default=160)
    parser.add_argument("--turns", type=int, default=6)
    parser.add_argument("--turn-fill", type=float, default=0.55)
    parser.add_argument("--turn-max-tokens", type=int, default=24)
    parser.add_argument("--turn-final-max-tokens", type=int, default=80)
    parser.add_argument("--tool-fill", type=float, default=0.5)
    parser.add_argument("--tool-max-tokens", type=int, default=256)
    parser.add_argument("--tool-final-max-tokens", type=int, default=128)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--stream", action="store_true", help="Use SSE streaming for perf and retrieval")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--backend-label", default="llama.cpp")
    parser.add_argument("--container", default="chumba-llama-server")
    parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--restart-container", action="store_true", help="Restart the backend for a cold run; explicit and disruptive")
    parser.add_argument("--cold", action="store_true", help="Label this run as cold (set automatically with --restart-container)")
    parser.add_argument("--dry-run", action="store_true", help="Print generated sizes without sending completions")
    parser.add_argument("--smoke", action="store_true", help="Small quick matrix")
    args = parser.parse_args()

    if args.smoke:
        args.fills = [0.05, 0.2]
        args.repeats = 1
        args.tests = ["perf", "tools"]
        args.tool_fill = 0.2
        args.turns = 2
        args.turn_fill = 0.2
    if args.restart_container:
        args.cold = True
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir = ROOT / "benchmark-results" / "context" / f"{args.backend_label}-{stamp}"
    fixtures_dir = Path(args.fixtures)
    facts = json.loads((fixtures_dir / "facts.json").read_text(encoding="utf-8"))
    tools = json.loads((fixtures_dir / "tools.json").read_text(encoding="utf-8"))
    fixtures_hash = sha256_text(json.dumps({"facts": facts, "tools": tools}, ensure_ascii=False, sort_keys=True))

    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    client = Client(args.base_url, args.model, api_key, args.timeout)
    if args.dry_run:
        rng = random.Random(args.seed)
        counter = 1000
        for frac in args.fills:
            target = max(256, int(args.ctx_size * frac) - 1100)
            filler, counter = build_filler(rng, target, client, counter)
            tokens = client.token_count(filler)
            print(f"dry-run perf fill={frac:.2f} target={target} chars={len(filler)} tokens={tokens}")
        if "retrieval" in args.tests:
            doc, placements = build_retrieval_doc(rng, max(512, int(args.ctx_size * max(args.fills)) - 900), client, facts, counter)
            print(f"dry-run retrieval chars={len(doc)} tokens={client.token_count(doc)} placements={placements}")
        print(f"dry-run tokenizer_available={client.tokenizer_available} error={client.tokenizer_error}")
        return 0
    metadata = {
        "script_version": VERSION,
        "script_sha256": file_sha256(Path(__file__)),
        "created": now_iso(),
        "args": {k: v for k, v in vars(args).items() if k != "api_key_env"},
        "fixtures_hash": fixtures_hash,
        "config_id": sha256_text(json.dumps({
            "base_url": args.base_url, "model": args.model, "ctx": args.ctx_size,
            "tests": args.tests, "fills": args.fills, "repeats": args.repeats,
            "max_tokens": args.max_tokens, "stream": args.stream, "fixtures": fixtures_hash,
            "backend": args.backend_label, "seed": args.seed,
        }, sort_keys=True))[:16],
        "backend": probe_backend(client, args),
        "cold": args.cold,
    }
    if args.restart_container:
        metadata["restart"] = restart_backend(args.container, client)
    sampler = MemorySampler(args.container if args.container else None)
    records: list[dict] = []
    sampler.start()
    try:
        if "perf" in args.tests:
            records.extend(run_perf(client, args.ctx_size, args.fills, args.repeats, args, sampler))
        if "retrieval" in args.tests:
            records.extend(run_retrieval(client, args.ctx_size, args.fills, args.repeats, args, facts, sampler))
        if "multi_turn" in args.tests:
            records.extend(run_multi_turn(client, args.ctx_size, args, sampler))
        if "tools" in args.tests:
            records.extend(run_tools(client, args.ctx_size, args, tools, sampler))
        if "integration" in args.tests:
            try:
                response = client.chat([{"role": "user", "content": "Ответь одним словом: ОК"}], max_tokens=8, temperature=0.0)
                content, _, _ = extract_text(response["data"])
                rec = make_record("integration", "smoke", 0, None, response, None, args.cold, {"answer": content[:100]})
                rec["answer_ok"] = bool(content)
                records.append(rec)
            except Exception as exc:
                rec = make_record("integration", "smoke", 0, None, None, str(exc)[:800], args.cold, {})
                rec["answer_ok"] = False
                records.append(rec)
    finally:
        memory = sampler.stop()
        for rec in records:
            rec["memory"] = memory
    metadata["finished"] = now_iso()
    metadata["tokenizer_available"] = client.tokenizer_available
    metadata["tokenizer_error"] = client.tokenizer_error
    write_outputs(records, metadata, out_dir)
    return 0 if not any(r.get("error") for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
