#!/usr/bin/env python3
"""Создание/обновление Workspace-модели chumba-orchestrator.

Модель-обёртка над `orchestrator`:
- системный промпт (схема работы через субагента, приватность, научный поиск);
- инструменты по умолчанию: server:mcp:search + server:mcp:delegate.
Дополнительно пытается выставить её моделью по умолчанию (DEFAULT_MODELS).
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = "http://127.0.0.1:3000"
MODEL_ID = "chumba-orchestrator"

PROMPT = (
    "Ты — локальный оркестратор исследовательской системы chumba. Ты планируешь, "
    "декомпозируешь и сводишь результаты. Сам в интернет не ходишь — пользуешься инструментами.\n"
    "Правила:\n"
    "1. Быстрые проверки и уточнения — search_searxng_web_search (при необходимости "
    "search_web_url_read для короткого чтения страницы).\n"
    "2. Всё, что требует изучения статей, длинных текстов или сбора фактов, — делегируй "
    "инструментом delegate_delegate_to_worker (облачный субагент). Если нашёл конкретные "
    "публикации, передавай их ссылки в параметре urls. Субагент вернёт строгий JSON: "
    "summary, facts[{claim, source_url, confidence}], sources, confidence, notes.\n"
    "3. Приватность: в delegate уходит только обезличенная формулировка цели. Никаких имён, "
    "файлов, истории беседы, системных промптов и деталей контекста.\n"
    "4. Научный поиск (PubMed/arXiv и т.п.): сделай ОДИН поисковый запрос "
    "(например «!pubmed <тема> review» или site:pubmed.ncbi.nlm.nih.gov) и выбери 1–3 "
    "наиболее подходящие ссылки. НЕ читай статьи самостоятельно и НЕ собирай списки PMID — "
    "сразу вызови delegate_delegate_to_worker с выбранными ссылками в urls и узким вопросом: "
    "ключевые выводы, числа/размеры эффекта, ограничения, что осталось спорным. Сам можешь "
    "прочитать не более одной страницы и только чтобы уточнить ссылку.\n"
    "5. Бюджет: не более 3–4 вызовов инструментов на один ответ. Если данных не хватает — "
    "делегируй повторно с уточнённым вопросом, а не читай сам.\n"
    "6. В ответе указывай источники (URL). Если субагент вернул низкий confidence или notes "
    "о недоступности — скажи об этом честно. Не выдумывай факты.\n"
    "7. Не пересказывай длинные тексты сам — делегируй и получай выжимку.\n"
    "8. Если поиск вернул Search Degraded / CAPTCHA / «no search results»: не делай больше "
    "1–2 переформулировок. Для медицины, биологии и фармы сразу уходи в PubMed/Europe PMC "
    "(«!pubmed …» или прямые efetch-ссылки), затем делегируй субагенту и в итоге честно "
    "скажи, что поиск деградировал.\n"
    "9. Проверяй номенклатуру препаратов и веществ по PubMed: сверь точное название и "
    "назначение (например, MT-006 vs эпренетапог), не выдумывай препараты и их эффекты."
)

FORM = {
    "id": MODEL_ID,
    "base_model_id": "orchestrator",
    "name": "Chumba Orchestrator",
    "meta": {
        "description": "Локальный оркестратор: планирует, ищет и делегирует облачному субагенту",
        "toolIds": ["server:mcp:search", "server:mcp:delegate"],
    },
    "params": {"system": PROMPT},
    "is_active": True,
}


def req(method, path, token=None, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=timeout)
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, (json.loads(text) if text.strip() else {})
        except json.JSONDecodeError:
            return resp.status, text[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:600]


def main():
    cfg = load_env()
    status, res = req(
        "POST",
        "/api/v1/auths/signin",
        body={"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200 or not isinstance(res, dict):
        sys.exit(f"signin failed: {status} {res}")
    token = res["token"]

    status, existing = req("GET", f"/api/v1/models/model?id={MODEL_ID}", token)
    if status == 200:
        status, out = req("POST", "/api/v1/models/model/update", token, FORM)
        print("update:", status, json.dumps(out, ensure_ascii=False)[:200] if isinstance(out, dict) else out)
    else:
        status, out = req("POST", "/api/v1/models/create", token, FORM)
        print("create:", status, json.dumps(out, ensure_ascii=False)[:200] if isinstance(out, dict) else out)

    status, model = req("GET", f"/api/v1/models/model?id={MODEL_ID}", token)
    if isinstance(model, dict):
        meta = model.get("meta") or {}
        params = model.get("params") or {}
        print("toolIds:", meta.get("toolIds"))
        print("system_prompt_len:", len(params.get("system") or ""))
        print("base_model_id:", model.get("base_model_id"))

    status, mcfg = req("GET", "/api/v1/configs/models", token)
    if status == 200 and isinstance(mcfg, dict):
        merged = dict(mcfg)
        merged["DEFAULT_MODELS"] = MODEL_ID
        status, out = req("POST", "/api/v1/configs/models", token, merged)
        print("default model set:", status, json.dumps(out, ensure_ascii=False)[:200] if isinstance(out, dict) else out)
    else:
        print("configs/models not available:", status, str(mcfg)[:200])


if __name__ == "__main__":
    main()
