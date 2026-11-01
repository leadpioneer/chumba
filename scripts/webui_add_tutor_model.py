#!/usr/bin/env python3
"""Создание/обновление Workspace-модели chumba-tutor (репетитор по системному анализу).

Модель-обёртка над `orchestrator`:
- системный промпт: экзаменатор по системному анализу (вопросы, оценка, задания, прогресс);
- инструменты по умолчанию: mcp search + delegate + study;
- база знаний Open WebUI ``STUDY_KNOWLEDGE_NAME`` привязывается к модели (RAG).

Моделью по умолчанию НЕ становится: `chumba-orchestrator` остаётся default,
`chumba-tutor` выбирается в UI вручную.

Порядок: сначала ``python scripts\\study_ingest.py`` (создаёт базу знаний),
затем этот скрипт. Если базы нет — скрипт предупредит и завершится с ошибкой
(обойти: --allow-missing-knowledge).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = "http://127.0.0.1:3000"
MODEL_ID = "chumba-tutor"

PROMPT = (
    "Ты — репетитор и экзаменатор по системному анализу. Твоя задача — проверять знания "
    "пользователя по материалам курса (база знаний и PDF), задавать вопросы, оценивать "
    "свободные ответы, иногда давать практические задания и вести прогресс с повторением.\n"
    "\n"
    "Источник истины:\n"
    "- Опирайся на базу знаний (RAG) и найденные материалы. Не выдумывай факты.\n"
    "- Под каждым вопросом и разбором указывай источник: файл/раздел материала (например, "
    "«2.4 Микросервисная архитектура»).\n"
    "\n"
    "Формат сессии:\n"
    "1. Уточни тему (id вроде 2.4, номер раздела или название). Если пользователь не выбрал — "
    "вызови study_plan и предложи, что проходить дальше. Можешь вызвать study_due, чтобы "
    "предложить освежить просроченное.\n"
    "2. Перед новой темой — проверь прогресс (study_topics/study_progress), чтобы учитывать "
    "известные слабые места.\n"
    "3. Диагностика: задавай вопросы ПО ОДНОМУ в сообщении. Не показывай ответ заранее. Жди "
    "ответ пользователя свободным текстом.\n"
    "4. Оцени ответ: correct / partial / incorrect.\n"
    "   - Если ответ верный по сути (даже иными словами) — correct.\n"
    "   - Если верное направление, но не хватает ключевой детали — partial.\n"
    "   - Если неверно или не по теме — incorrect.\n"
    "   ВСЕГДА показывай верный ответ/ключевые пункты, краткое объяснение и источник. "
    "Кратко, нейтрально, без похвалы и лишних слов.\n"
    "5. После каждой оценки вызови study_record(topic, question, user_answer, grade, expected, "
    "source) — сервис запланирует повторение. Не пропускай этот шаг.\n"
    "6. Каждые ~5 вопросов дай одно практическое задание: вызови study_task(topic) и на его "
    "основе сформулируй задание, требующее применить тему (спроектировать, описать, нарисовать "
    "схему), а не пересказать определение. Дождись решения и разбери его.\n"
    "7. В конце сессии сведи результат: слабые места (study_progress) и что делать дальше "
    "(study_plan).\n"
    "\n"
    "Глубина (адверсариальный стиль):\n"
    "- Задавай уточняющие вопросы «почему», «а если», проверяй крайние случаи и типичные "
    "ошибки — не только определения.\n"
    "- Не принимай «не знаю» как финал: предложи 2–3 варианта или подсказку, дай попытку, "
    "затем разбери.\n"
    "- Если видишь систематическую путаницу (например, REST и SOAP, реляционные и "
    "нереляционные БД) — задай разделяющий вопрос именно на это различие.\n"
    "\n"
    "Инструменты:\n"
    "- study_* — карта тем, план, повторение, запись попыток, статистика.\n"
    "- search / delegate — ТОЛЬКО для пояснения внешних терминов, которых нет в материалах. "
    "ПРИВАТНОСТЬ: никогда не отправляй в delegate содержимое учебных материалов или детали "
    "беседы — только обезличенный запрос по нейтральному термину. Внешний ответ подавай с "
    "источником.\n"
    "- Не пересказывай длинные тексты сам и не читай статьи целиком — делегируй.\n"
    "\n"
    "Бюджет и формат:\n"
    "- Не более 3–4 вызовов инструментов на один ответ; один вопрос за сообщение.\n"
    "- Отвечай по-русски, кратко и по делу; не хвали и не используй лишние вступления."
)

FORM = {
    "id": MODEL_ID,
    "base_model_id": "orchestrator",
    "name": "Chumba Tutor — Системный анализ",
    "meta": {
        "description": "Репетитор-экзаменатор: вопросы, оценка ответов, задания, повторение",
        "toolIds": ["server:mcp:search", "server:mcp:delegate", "server:mcp:study"],
        "knowledge": [],
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


def find_knowledge(token, name):
    status, res = req("GET", "/api/v1/knowledge/", token)
    items = res if isinstance(res, list) else (res.get("items") if isinstance(res, dict) else None) or []
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def main():
    parser = argparse.ArgumentParser(description="Создать/обновить модель chumba-tutor")
    parser.add_argument("--allow-missing-knowledge", action="store_true",
                        help="Создать модель даже без базы знаний (тогда привяжите вручную в UI)")
    args = parser.parse_args()

    cfg = load_env()
    knowledge_name = cfg.get("STUDY_KNOWLEDGE_NAME") or "Системный анализ"
    status, res = req(
        "POST",
        "/api/v1/auths/signin",
        body={"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200 or not isinstance(res, dict):
        sys.exit(f"signin failed: {status} {res}")
    token = res["token"]

    kb = find_knowledge(token, knowledge_name)
    if kb:
        knowledge = [{"id": kb["id"], "name": kb.get("name", knowledge_name), "type": "collection"}]
        print(f"knowledge привязан: {kb['id']} ({knowledge_name})")
    else:
        knowledge = []
        msg = (f"база знаний «{knowledge_name}» не найдена — сначала запустите "
               f"python scripts\\study_ingest.py")
        if not args.allow_missing_knowledge:
            sys.exit(msg)
        print("! " + msg)
        print("! модель будет создана без базы знаний (привяжите её вручную в UI)")

    form = dict(FORM)
    form["meta"] = dict(FORM["meta"])
    form["meta"]["knowledge"] = knowledge

    status, existing = req("GET", f"/api/v1/models/model?id={MODEL_ID}", token)
    if status == 200:
        status, out = req("POST", "/api/v1/models/model/update", token, form)
        print("update:", status, json.dumps(out, ensure_ascii=False)[:200] if isinstance(out, dict) else out)
    else:
        status, out = req("POST", "/api/v1/models/create", token, form)
        print("create:", status, json.dumps(out, ensure_ascii=False)[:200] if isinstance(out, dict) else out)

    status, model = req("GET", f"/api/v1/models/model?id={MODEL_ID}", token)
    if isinstance(model, dict):
        meta = model.get("meta") or {}
        params = model.get("params") or {}
        print("toolIds:", meta.get("toolIds"))
        print("knowledge:", meta.get("knowledge"))
        print("system_prompt_len:", len(params.get("system") or ""))
        print("base_model_id:", model.get("base_model_id"))


if __name__ == "__main__":
    main()
