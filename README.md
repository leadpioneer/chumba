# chumba — локальный исследовательский LLM-стек

> **Локальный мозг. Облачные руки. Ноль лишних утечек.**
> `llama.cpp`-оркестратор на вашей GPU планирует и сводит результаты, облачный субагент
> (OpenAI-совместимый роутер, заменяемый на локальную модель) ищет и читает, а между ними —
> принудительный санитайзер, сетевой периметр и полный журнал всего исходящего.
> Есть [маркетинговое описание](docs/marketing.md) и [журнал инженерных решений](docs/decisions.md).

---

## Содержание

1. [Что это](#1-что-это)
2. [Архитектура](#2-архитектура)
3. [Состав стека и порты](#3-состав-стека-и-порты)
4. [Быстрый старт](#4-быстрый-старт)
5. [Использование](#5-использование)
6. [Стратегия контекста](#6-стратегия-контекста)
7. [Приватность, санитайзинг и аудит](#7-приватность-санитайзинг-и-аудит)
8. [Проверки и тесты](#8-проверки-и-тесты)
9. [Обслуживание](#9-обслуживание)
10. [Troubleshooting](#10-troubleshooting)
11. [Компоненты и лицензии](#11-компоненты-и-лицензии)
12. [Структура репозитория](#12-структура-репозитория)
13. [Ограничения и планы](#13-ограничения-и-планы)
14. [Память: linked notes внутри Chumba](#14-память-linked-notes-внутри-chumba)

---

## 1. Что это

Разворачиваемый на одной машине с GPU стек из трёх ролей:

- **Оркестратор** — локальная модель (`Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS`, IQ4_XS, 13.4 ГиБ)
  на `llama.cpp`: планирует, декомпозирует и сводит результаты. В интернет не ходит.
- **Субагент (worker)** — облачная модель через OpenAI-совместимый роутер (например, RouterAI):
  поиск, чтение страниц, подготовка данных. Получает только обезличенную задачу и публичные
  материалы. Подменяется на локальную модель одной строкой в `.env`.
- **Инструменты** — локальный SearXNG (JSON API), MCP-серверы поиска/чтения (с anti-bot браузером
  Byparr) и тонкий MCP-сервис делегирования `delegate_to_worker`.

Плюс интерфейс Open WebUI с моделью-обёрткой `Chumba Orchestrator` (промпт + инструменты по
умолчанию), стратегия контекста «32k + rolling summary + RAG на bge-m3» и сон на простое,
освобождающий VRAM/RAM, когда система не используется.

**Кому:** тем, кто хочет пользоваться облаком для «рутинной» работы с интернетом, но не отдавать
туда переписку, системные промпты, файлы и контекст; и при этом не требует 128k контекста и
цензурных компромиссов от одной модели.

---

## 2. Архитектура

```
Браузер ──► 127.0.0.1:3000 Open WebUI ──► llama-server orchestrator (только llm_internal)
                │  нативные MCP-инструменты (Streamable HTTP)
                ├──► search-mcp ──► SearXNG ──► интернет (движки)            [egress]
                │         └── web_url_read ──► Byparr (stealth-браузер) ──► интернет [egress]
                └──► delegate ──► search-mcp (поиск+чтение, локально)
                          └──► LiteLLM alias `worker` ──► облачный роутер       [egress]
```

Поток данных одной задачи:

1. Оркестратор решает, что нужен внешний материал, и вызывает `delegate_to_worker(task[, urls])`.
2. `delegate` **локально** ищет (SearXNG) и читает 1–3 страницы (Byparr/`web_url_read`).
3. Локальная модель **перефразирует** задачу в обезличенный запрос (санитайзер).
4. В облако уходит один запрос: фиксированный системный промпт воркера + обезличенный запрос +
   публичные материалы. Возвращается строгий JSON (`summary`, `facts`, `sources`, `confidence`, `notes`).
5. JSON (≤800 токенов) попадает в контекст оркестратора; всё исходящее пишется в локальный журнал.

Сетевые контуры:

| Сеть | Кто в ней | Интернет |
|---|---|---|
| `llm_internal` (internal: true) | llama-server, open-webui, delegate, litellm, searxng, search-mcp, embeddings | **нет** |
| `edge` | только socat-форвардеры портов | нет (маршрут есть, но не используется) |
| `egress` | litellm (воркер), searxng/search-mcp (поиск/чтение), byparr, tor (профиль) | да |

`llama-server`, `open-webui` и `delegate` физически не имеют маршрута в интернет.
Ключ облачного роутера видит только контейнер `litellm`.

---

## 3. Состав стека и порты

Все host-порты публикуются **только на 127.0.0.1**.

| Сервис | Host-порт | Назначение |
|---|---|---|
| open-webui | 3000 | Интерфейс; модель `chumba-orchestrator`, MCP-инструменты, RAG |
| llama-server | 8080 | OpenAI-совместимый API оркестратора (router-режим), `/health`, `/props` |
| searxng | 8888 | Поиск: `/search?q=...&format=json` |
| litellm | 4000 | Алиасы `worker` / `worker-local` / `orchestrator` |
| search-mcp | 8085 | MCP: `searxng_web_search`, `web_url_read` (Bearer) |
| delegate | 8086 | MCP: `delegate_to_worker(task, urls, max_sources)` (Bearer) |
| embeddings | 8087 | Локальные эмбеддинги bge-m3 (CPU) для RAG |
| study | 8088 | MCP: `study_topics/plan/due/record/progress/task` (учебный прогресс, локально; Bearer) |
| byparr | — | Stealth-браузер (Camoufox) для чтения страниц; только `egress` |
| tor | 9050 | SOCKS5 для SearXNG; профиль `tor`, по умолчанию выключен |

**Зачем socat-форвардеры (`*-port`)**: Docker Desktop (WSL2) не публикует порты контейнеров,
подключённых только к `internal`-сети. Поэтому host-порт отдаёт тонкий TCP-форвардер `socat`,
а целевой сервис остаётся без маршрута в интернет. Не удаляйте эти сервисы без замены механизма.

---

## 4. Быстрый старт

### 4.1 Требования

- Windows + Docker Desktop (WSL2) с GPU-пробросом:
  `docker run --rm --gpus all nvidia/cuda:12.9.0-base-ubuntu24.04 nvidia-smi`
- NVIDIA-драйвер с CUDA ≥ 13.4 для образа `server-cuda13` (проверено на 617.14).
  Для более старого драйвера в `.env` укажите `ghcr.io/ggml-org/llama.cpp:server-cuda` (CUDA 12).
- ~21 ГБ диска на образы + GGUF-модели.
- GPU 16 ГБ VRAM: модель 13.4 ГиБ умещается вместе с KV-кэшем q4_0 и контекстом 32k
  (замер: 15.6 / 16.3 ГБ).

### 4.2 Секреты `.env`

```powershell
Copy-Item .env.example .env
```

Обязательно заполнить: `SEARXNG_SECRET`, `LITELLM_MASTER_KEY`, `SEARCH_MCP_TOKEN`,
`DELEGATE_MCP_TOKEN`, `STUDY_MCP_TOKEN`, `NOTES_INTERNAL_TOKEN`, `NOTES_AGENT_TOKEN`,
`WEBUI_SECRET_KEY`, `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD` и блок воркера
(`WORKER_MODEL=openai/<provider>/<model-id>`, `WORKER_BASE_URL`, `WORKER_API_KEY`).
Секреты генерируются как случайная hex-строка 32–64 символов.

### 4.3 Модели

```powershell
# Оркестратор: положите GGUF в models/
#   Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf
# Эмбеддинги для RAG (bge-m3, ~605 МиБ):
powershell -File scripts\download-embedding-model.ps1
```

Если меняете имя файла оркестратора — поправьте путь в `llama/models.ini`
(секция `[orchestrator]`).

### 4.4 Запуск и первый вход

```powershell
docker compose up -d
docker compose ps
```

Или в один клик: `start.bat` (запуск всего стека, при необходимости сам запускает
Docker Desktop) и `stop.bat` (остановка: модель выгружается из VRAM/RAM, данные
в томах сохраняются).

- Первый запуск скачивает образы (несколько ГБ) и загружает модель (~90 с).
- Интерфейс: http://127.0.0.1:3000. Админ создаётся автоматически из `.env`
  (`WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD`) — смените пароль после входа.
- Модель `Chumba Orchestrator` уже содержит системный промпт и оба инструмента; она же
  выбрана моделью по умолчанию.

### 4.5 Если том Open WebUI пересоздавали

Все настройки Open WebUI (MCP, RAG, фильтр, модель) хранятся в томе `open-webui-data`.
Восстановление одной командой на каждый слой:

```powershell
python scripts\webui_add_mcp.py        # MCP-подключения search + delegate + study
python scripts\webui_rag_config.py     # движок эмбеддингов bge-m3 + TOP_K
python scripts\webui_add_filter.py     # фильтр rolling summary (создание + активация)
python scripts\webui_add_model.py      # модель chumba-orchestrator + DEFAULT_MODELS
python scripts\study_ingest.py         # (репетитор) PDF по системному анализу -> Knowledge
python scripts\webui_add_tutor_model.py  # (репетитор) модель chumba-tutor + Knowledge
```

---

## 5. Использование

### 5.1 Модель по умолчанию и субагенты

Workspace-модель **`chumba-orchestrator`** (база `orchestrator`) задаёт поведение:

- быстрые проверки — инструментом поиска; любые исследования и чтение длинных текстов —
  делегированием облачному субагенту (не локальной моделью);
- бюджет 3–4 вызова инструментов на ответ;
- приватность: в `task` — только обезличенная формулировка цели;
- в ответе — источники; низкий `confidence` и недоступность источников сообщаются честно.

Обновить/переустановить: `python scripts\webui_add_model.py` (идемпотентно).

### 5.2 Научный поиск (PubMed, Europe PMC, arXiv)

1. Оркестратор ищет публикации: `!pubmed <тема>`, `!arxiv <тема>`, `site:pubmed.ncbi.nlm.nih.gov`.
   Движки PubMed / Europe PMC / Semantic Scholar / Google Scholar включены в SearXNG по умолчанию.
2. Найденные ссылки передаются субагенту: `delegate_to_worker(task, urls=[...])`.
3. PubMed-ссылки читаются через официальный NCBI E-utilities (`efetch`, без cookies),
   с фоллбэком на Europe PMC и оригинал. Страницы PubMed, требующие cookies, не ломают чтение.
4. Ответ — JSON с фактами и источниками; оркестратор сводит его в резюме со ссылками.

Полный сценарий можно прогнать из CLI (клиент исполняет `tool_calls`, как это делает UI):

```powershell
python scripts\e2e_loop.py chumba-orchestrator "Найди на PubMed обзорную статью про связь сна и метаболизма и делегируй субагенту изучить её ключевые выводы; итог дай с источниками."
```

### 5.3 Инструменты MCP

| Инструмент | Что делает | Примечания |
|---|---|---|
| `search_searxng_web_search(query)` | Поиск через SearXNG | Лидер: `!bing …`, `!pubmed …` (bang-синтаксис) |
| `search_web_url_read(url)` | Чтение страницы (markdown/PDF) | Через Byparr; лимит `URL_READ_MAX_CHARS=8000` |
| `delegate_delegate_to_worker(task, urls, max_sources)` | Облачный субагент: поиск/чтение/резюме в JSON | `urls` — до 3 явных ссылок (без поиска) |

### 5.4 Подмена облачного воркера на локальный

Одна строка в `.env`, без правок кода:

```
DELEGATE_WORKER_MODEL=worker-local   # вместо worker
```

```powershell
docker compose up -d delegate
```

`worker-local` ходит в тот же `llama-server` через LiteLLM.

### 5.5 Управление thinking

- Глобально: `ENABLE_THINKING=true/false` + `docker compose up -d llama-server`.
- На запрос: `chat_template_kwargs: {"enable_thinking": false}` (пример `scripts/chat-no-think.json`).
- Примечание: llama.cpp считает `--chat-template-kwargs` устаревающим в пользу `--reasoning on/off`;
  текущий вариант работает и менять его не обязательно.

### 5.6 Сон на простое и router-режим

`llama-server` сам выгружает модель после простоя: `SLEEP_IDLE_SECONDS` (по умолчанию 600 с;
`-1` — выключить). Замеры (TTL 60 с для теста):

| Состояние | VRAM | RAM (MemFree / MemAvailable) |
|---|---|---|
| Активен | ~15.2 ГБ | 1.8 ГБ / 5.0 ГБ |
| Спит | **1.7 ГБ** | **12.4 ГБ / 13.2 ГБ** |
| Пробуждение | 15.2 ГБ | — |

- Статус: `GET /props` → `is_sleeping` (в router-режиме — `/props?model=orchestrator`).
- `/health`, `/props`, `/models` не будят модель — мониторинг можно не отключать.
- Первый запрос после сна ждёт перезагрузку (замеры 87–149 с) и отвечает нормально;
  таймаут Open WebUI по умолчанию (300 с) это выдерживает.
- **Router-режим**: llama-server запускается с `--models-preset llama/models.ini` (секция
  `[orchestrator]` задаёт id модели). Поэтому `/v1/models` отвечает даже когда модель выгружена —
  Open WebUI не теряет `orchestrator`/`chumba-orchestrator` из списка (иначе после простоя
  UI показывал «Модель не найдена»).

### 5.7 Репетитор по системному анализу (`chumba-tutor`)

Локальный репетитор-экзаменатор: после того как вы прочитали тему, он проверяет вас — задаёт
вопросы по одному, принимает свободный ответ, оценивает (`correct`/`partial`/`incorrect`),
показывает верный ответ и источник, иногда даёт практическое задание и ведёт прогресс с
интервальным повторением.

Подготовка (один раз; база знаний по умолчанию `Системный анализ`):

```powershell
python scripts\study_ingest.py           # PDF из STUDY_DOCS_DIR -> Knowledge (RAG)
python scripts\webui_add_tutor_model.py  # модель chumba-tutor + привязка Knowledge
```

Использование: в UI выберите модель **Chumba Tutor — Системный анализ** и напишите, например,
«проверь меня по микросервисам» или «что мне проходить дальше». Модель по умолчанию
(`chumba-orchestrator`) не меняется.

- Прогресс и повторение хранит сервис `study` (SQLite в томе `study-data`):
  `study_topics`, `study_plan`, `study_due`, `study_record`, `study_progress`, `study_task`.
  Интервалы — как в memento: incorrect → +1 день, partial → +3 дня, correct → +7 дней;
  `STUDY_RETIRE_STREAK` «correct» подряд убирают карточку из ротации.
- Карта тем — `study/syllabus.json` (8 разделов; файлы-задания «Задание 1/2/3» используются как
  банк практических заданий).
- Приватность: материалы и чат не уходят наружу; `search`/`delegate` — только для пояснения
  нейтральных внешних терминов (в облако идёт обезличенный запрос, не текст PDF).
- Проверка сервиса: `python scripts\study_check.py`.

---

## 6. Стратегия контекста

Четыре слоя, чтобы работать с объёмом больше «физического» окна одной модели.

### 6.1 Ёмкость: 32k, KV на GPU

`ORCHESTRATOR_CTX=32768`, `LLAMA_ARG_KV_OFFLOAD=true`. Замеры:

| Метрика | 16k (было) | 32k (стало) |
|---|---|---|
| Промпт 18 464 токена | HTTP 400 `exceed_context_size_error` | **принят** (17 796 токенов, 21 с) |
| VRAM | ~15.2 ГБ | ~15.6 / 16.3 ГБ |
| Prefill / генерация | ~700 / ~19–21 т/с | ~860 / ~19.7 т/с |

Фоллбэки: `LLAMA_ARG_KV_OFFLOAD=false` (KV в RAM: VRAM −1.6 ГБ, но генерация падает ~втрое —
6.7–8.1 т/с — у этой модели hybrid attention и KV маленький, поэтому вариант невыгоден),
затем `ORCHESTRATOR_CTX=24576` → `16384`.

### 6.2 Сжатие истории: rolling summary

Фильтр Open WebUI `openwebui/filters/chumba_summary_filter.py` (установка:
`python scripts\webui_add_filter.py`). Когда история превышает `threshold_tokens` (12 000),
старые сообщения сворачиваются локальной моделью в конспект (≤400 токенов), последние
6 сообщений остаются как есть. Конспекты кэшируются в data-томе; при любой ошибке — fail-open
(чат не ломается). Аудит: `docker compose logs open-webui` (`chumba_summary: summarizing …`,
`cache hit`). Проверка: `python scripts\summary_check.py`.

Настройки — Valves фильтра (Admin → Functions → Chumba Rolling Summary): порог, число
сохраняемых сообщений, длина конспекта, URL/имя локальной модели, файл кэша, fail-open.

### 6.3 RAG на локальных эмбеддингах bge-m3

- Сервис `embeddings`: llama.cpp (CPU) + `models/embeddings/bge-m3-Q8_0.gguf`
  (MIT, 0.6B, 1024-dim, 100+ языков), OpenAI-совместимый `/v1/embeddings` на 127.0.0.1:8087.
- Open WebUI подключён к нему как RAG-движок: `RAG_EMBEDDING_ENGINE=openai`, `RAG_TOP_K=4`.
  Настройки применяются `python scripts\webui_rag_config.py` (persistent ConfigVar).
- Векторная БД — встроенная Chroma в томе Open WebUI.
- Проверки: `scripts\embed_check.py` (RU↔EN близость 0.93 против 0.42 у несвязанных) и
  `scripts\kb_check.py` (создание базы → загрузка → эмбеддинг → retrieval: находит и по смыслу,
  и по маркеру).

### 6.4 Изоляция тяжёлых материалов

Сырые страницы никогда не попадают в контекст оркестратора: их читает `delegate` и возвращает
сжатый JSON ≤800 токенов. Поисковая выдача обрезается до 4 результатов, каждый сниппет — до
600 символов, страницы — до `URL_READ_MAX_CHARS`. Страницы читаются по порядку выдачи, пока
не наберётся нужное число читаемых: закрытая антиботом ссылка не выбрасывает следующие годные.

---

## 7. Приватность, санитайзинг и аудит

### 7.1 Что уходит наружу

Один вызов воркера = один HTTP-запрос `delegate → litellm → роутер`, в котором:

```
system: фиксированный текст воркера (из delegate/app.py, без ваших данных)
user:   "Задача: <обезличенный запрос>\n\nМатериалы:\n[1] title/URL/сниппет/текст …"
```

История чата, системный промпт оркестратора, файлы и идентификаторы в запрос физически
не попадают. Отдельно наружу ходят: поисковые запросы (SearXNG → движки) и чтение
публичных страниц (search-mcp/Byparr) — с домашнего IP, если не задан прокси.

### 7.2 Санитайзер задач (принудительный)

Перед каждым облачным вызовом локальная модель переписывает задачу в нейтральный запрос:

- `DELEGATE_SANITIZE=llm` (по умолчанию) — в облако уходит только обезличенная версия;
  исходник остаётся в журнале (`task_original` vs `task_sanitized`).
- `DELEGATE_SANITIZE_FAIL=reject` — при сбое рерайта задача в облако **не** отправляется.
- `DELEGATE_SANITIZE_MAX_CHARS=300` — предел длины обезличенного запроса.
- Отключение: `DELEGATE_SANITIZE=off`.
- Любое другое значение — ошибка при старте сервиса: опечатка не должна молча
  превращаться в отключение обезличивания (fail closed).

Описание инструмента `delegate_to_worker` дополнительно требует от оркестратора обезличенных
формулировок (видно модели в схеме инструмента). Проверка: `python scripts\sanitize_check.py`
(кладёт в задачу имя и маркер — в payload к облаку их нет, `SANITIZE_OK=True`).

### 7.3 Журнал исходящего (без «слепых зон»)

`logs/outbound/worker-YYYYMMDD.jsonl` — каждый вызов целиком: исходная и обезличенная задача,
URL, полный промпт и ответ, `status`, `search_path`, `explicit_urls`, тайминги. Пишутся все
исходы: `ok`, `sanitize_failed`, `no_results`, `search_failed`. Файлы старше 90 дней удаляются
автоматически. Дублирование запросов — в логах `litellm`.

### 7.4 Проверка утечек

```powershell
python scripts\leak_check.py
Select-String -Path logs\outbound\*.jsonl -Pattern 'МОЙ_МАРКЕР'
docker compose logs litellm | Select-String 'МОЙ_МАРКЕР'
```

Маркерный тест проверен: приватный маркер из чата в outbound не попадает; в логах
litellm/delegate/searxng его тоже нет.

### 7.5 Egress: DNS, прокси, Tor, Byparr

- **DNS**: провайдерские заглушки (`198.18.0.0/15`) для заблокированных доменов обходятся
  публичными резолверами `dns: [1.1.1.1, 8.8.8.8]` у `searxng`, `search-mcp`, `byparr`.
- **Прокси** (три независимые переменные): `SEARXNG_PROXY` (движки), `URL_READER_PROXY`
  (прямой путь чтения), `BYPARR_PROXY` (браузер; сюда же — резидентный прокси против капч).
- **Tor**: `docker compose --profile tor up -d tor` + `SEARXNG_PROXY=socks5h://tor:9150`.
  В проверенной сети Tor-релеи блокируются DPI (bootstrap застревает на 10%) — нужны
  obfs4/webtunnel-мосты, которых текущий образ не умеет.
- **Byparr**: stealth-браузер (Camoufox) для `web_url_read`; `BYPARR_TIMEOUT_SECONDS=120`,
  `BYPARR_MAX_CONCURRENT_REQUESTS=2`; аудит URL — `docker compose logs byparr`.

Эффект Byparr (символов текста, до/после):

| URL | До | После |
|---|---|---|
| github.com/ggml-org/llama.cpp | 156 (DNS-заглушка) | 8000 |
| en.wikipedia.org/wiki/Llama.cpp | 158 | 8000 |
| nowsecure.nl (Cloudflare-тест) | 63 (challenge) | настоящая страница (челлендж пройден) |
| reddit.com/r/LocalLLaMA | 155 | полная страница (~92 с на solve) |

### 7.6 Отказоустойчивость поиска

Основные движки (brave, DuckDuckGo, google cse) периодически упираются в лимиты/CAPTCHA.
Поэтому:

- включены резервные general-движки: bing, mwmbl, yandex, yep (в `searxng/settings.yml.template`);
- `delegate` использует цепочку **SearXNG → science-категория → Europe PMC REST**
  (последний — официальный API без капч);
- фактический путь виден в журнале: `search_path` = `searxng` / `searxng-science` /
  `europepmc` / `explicit`.

---

## 8. Проверки и тесты

### 8.1 Быстрые проверки

```powershell
# llama-server: health + ответ + tool calling
curl.exe -s http://127.0.0.1:8080/health
curl.exe -s -H "Content-Type: application/json" -d "@scripts/chat-test.json" http://127.0.0.1:8080/v1/chat/completions
curl.exe -s -H "Content-Type: application/json" -d "@scripts/test-toolcall.json" http://127.0.0.1:8080/v1/chat/completions

# SearXNG JSON
curl.exe "http://127.0.0.1:8888/search?q=test&format=json"

# MCP
$tok = ((Get-Content .env | Where-Object { $_ -like 'SEARCH_MCP_TOKEN=*' }) -split '=',2)[1].Trim()
$env:MCP_TOKEN = $tok
python scripts\check_mcp.py http://127.0.0.1:8085/mcp list
$tok = ((Get-Content .env | Where-Object { $_ -like 'DELEGATE_MCP_TOKEN=*' }) -split '=',2)[1].Trim()
$env:MCP_TOKEN = $tok
python scripts\check_mcp.py http://127.0.0.1:8086/mcp call delegate_to_worker '@scripts/delegate-args.json'
$tok = ((Get-Content .env | Where-Object { $_ -like 'STUDY_MCP_TOKEN=*' }) -split '=',2)[1].Trim()
$env:MCP_TOKEN = $tok
python scripts\check_mcp.py http://127.0.0.1:8088/mcp list
Remove-Item Env:\MCP_TOKEN

# Эмбеддинги
python scripts\embed_check.py
```

### 8.2 Скрипты (scripts/)

| Скрипт | Назначение |
|---|---|
| `mcp_client.py` | общий MCP-клиент (Streamable HTTP) для остальных скриптов |
| `check_mcp.py` | список/вызов инструментов любого MCP (raw Streamable HTTP) |
| `e2e_loop.py [model] [prompt]` | сквозной цикл «оркестратор → tool_calls → ответ» |
| `leak_check.py` | маркерный тест утечек через UI-путь |
| `sanitize_check.py` | проверка принудительного санитайзера |
| `read_batch.py` | батч-чтение URL (эффект Byparr/DNS) |
| `embed_check.py` | размерность и качество эмбеддингов |
| `kb_check.py` | RAG end-to-end: база → файл → retrieval |
| `summary_check.py` | тест rolling summary и кэша |
| `ctx_bench.py [N]` | замер контекста/латентности (prompt_n, t/s, truncated) |
| `epmc_check.py [query]` | проверка запасного поиска Europe PMC REST |
| `webui_smoke.py` | вход админом, список моделей, конфиг tool servers |
| `webui_chat_test.py` | чат через API Open WebUI с MCP-инструментами |
| `webui_add_mcp.py` | MCP-подключения (search + delegate + study) |
| `webui_rag_config.py` | RAG-настройки (движок, TOP_K) |
| `webui_add_filter.py` | установка/активация фильтра summary (+Valves) |
| `webui_add_model.py` | создание/обновление `chumba-orchestrator` + DEFAULT_MODELS |
| `study_ingest.py` | загрузка PDF по системному анализу в Knowledge Open WebUI (репетитор) |
| `webui_add_tutor_model.py` | модель `chumba-tutor` + привязка базы знаний (репетитор) |
| `study_check.py` | проверка MCP-сервиса `study` (запись → прогресс → повторение) |
| `download-embedding-model.ps1` | скачать `bge-m3-Q8_0.gguf` |

JSON-фикстуры (`scripts/*.json`) — тела запросов для `curl` и аргументы инструментов.

Юнит-тесты (без сети и GPU): `python delegate\test_app.py` (санитайзер fail-closed,
выбор читаемых источников, парсинг JSON) и `python notes\test_storage.py` (заметки:
revision-конфликты, идемпотентность, восстановление).

---

## 9. Обслуживание

### 9.1 Обновление образов

Все образы закреплены digest-ами в `.env`. Обновление:

```powershell
docker buildx imagetools inspect ghcr.io/ggml-org/llama.cpp:server-cuda13   # взять digest amd64
# вписать новый digest в .env (LLAMA_IMAGE=...@sha256:...)
docker compose pull
docker compose up -d
```

Аналогично для `SEARXNG_IMAGE`, `LITELLM_IMAGE`, `SEARCHMCP_IMAGE`, `OPENWEBUI_IMAGE`,
`ALPINE_IMAGE`, `SOCAT_IMAGE`, `TOR_IMAGE`, `BYPARR_IMAGE`, `EMBEDDINGS_IMAGE`.

### 9.2 Остановка, бэкап

```powershell
docker compose stop            # пауза
docker compose up -d           # продолжить
docker compose down            # удалить контейнеры (тома сохраняются)
docker compose down -v         # ВНИМАНИЕ: удалит и тома (чаты Open WebUI, кэш SearXNG)
```

Бэкап данных Open WebUI (чаты, настройки MCP/RAG/фильтр/модель): скопируйте том
`chumba_open-webui-data`. Секреты — в `.env` (в `.gitignore`, включает `.env.bak*`).

### 9.3 Типовой апдейт стека

1. `docker compose pull` → `docker compose up -d`.
2. Проверьте: `docker compose ps`, health-эндпоинты (см. §8.1), `python scripts\e2e_loop.py`.
3. При обновлении Open WebUI-образа настройки в томе сохраняются; если том пересоздан —
   прогоните `webui_add_*.py` из §4.5.

---

## 10. Troubleshooting

- **`nvidia-container-cli: unsatisfied condition: cuda>=13.4`** — драйвер старее образа:
  обновите драйвер или переключите `LLAMA_IMAGE` на `server-cuda` (CUDA 12).
- **GPU-хук падает после обновления драйвера** — `docker desktop restart` (при необходимости
  `wsl --shutdown`), чтобы WSL подхватил новые GPU-библиотеки.
- **«Модель не найдена» после простоя** — убедитесь, что llama-server в router-режиме
  (`--models-preset`, файл `llama/models.ini`), а не запущен как single-model. Обновите страницу UI.
- **Порт контейнера не публикуется** — Docker Desktop не публикует порты `internal`-контейнеров;
  в стеке это обойдено socat-форвардерами, не убирайте их.
- **`403 Invalid Host header` от search-mcp** — hardened-режим проверяет Host; добавьте нужный
  `host:port` в `MCP_HTTP_ALLOWED_HOSTS`.
- **`401 unauthorized` от MCP-сервиса (delegate/study)** — токен не задан или не совпадает:
  проверьте `DELEGATE_MCP_TOKEN`/`STUDY_MCP_TOKEN` в `.env` и перезапустите сервис;
  для UI-подключений — прогоните `python scripts\webui_add_mcp.py`.
- **Воркер отвечает `401/404`** — проверьте `WORKER_BASE_URL`, `WORKER_API_KEY`, `WORKER_MODEL`
  (префикс `openai/` обязателен; id — как ждёт роутер).
- **Модель не влезает в VRAM** — уменьшите `ORCHESTRATOR_CTX` (32768 → 24576/16384) или
  переведите KV в RAM (`LLAMA_ARG_KV_OFFLOAD=false`, медленнее).
- **Ответы модели «мусор»** — обновите digest образа llama.cpp; при необходимости соберите образ
  под свою арку (`CUDA_DOCKER_ARCH`), см. docs llama.cpp.
- **`URL blocked by security policy` в web_url_read** — DNS вернул заглушку/приватный IP
  (в РФ-сетях блокировки отдают `198.18.0.0/15`). Проверьте `dns:` у сервисов и `nslookup <host> 1.1.1.1`.
- **`Search Degraded` / CAPTCHA / `too many requests`** — лимиты основных движков; включены
  резервные (bing, mwmbl, yandex, yep), делегат переключается на science-категорию и Europe PMC
  (`search_path` в журнале).
- **Tor не поднимается (`Stuck at 10% (conn_done)`)** — DPI-блокировка релеев: нужны
  obfs4/webtunnel-мосты либо внешний прокси/VPN; сервис `tor` — профиль.
- **Byparr медленный/таймаут** — тяжёлые CF-сайты решаются до 1–2 минут; проверьте RAM
  (`wsl -d docker-desktop free -m`), при нехватке уменьшите `BYPARR_MAX_CONCURRENT_REQUESTS` до 1.

---

## 11. Компоненты и лицензии

| Слой | Выбор | Лицензия | Почему |
|---|---|---|---|
| Inference | ggml-org/llama.cpp, образ `server-cuda13`/`server-cuda` | MIT | контроль флагов (KV q4_0, `-fa on`, `--chat-template-kwargs`), tool calling, router-режим, сон |
| Поиск/чтение | `ihor-sokoliuk/mcp-searxng` | MIT | 1.3k★, stdio+HTTP, поиск + `web_url_read` (PDF, лимиты, прокси, anti-bot) |
| Metasearch | `searxng/searxng` (официальный контейнер) | AGPL-3.0 | JSON API, движки (в т.ч. научные), прокси |
| Anti-bot | `ThePhaseless/Byparr` | GPL-3.0 | Camoufox, интеграция в mcp-searxng (`BYPARR_URL`), проверенный пин |
| UI | Open WebUI | Open WebUI License (на базе BSD-3) | нативный MCP (Streamable HTTP), фильтры, RAG, headless-админ |
| Прокси/алиасы | LiteLLM | MIT | алиасы `worker`/`worker-local`, ключ роутера в одном месте |
| Эмбеддинги | `gpustack/bge-m3-GGUF` | MIT | мультиязычная (RU), 1024-dim, CPU, без внешних сервисов |
| Glue | официальный MCP Python SDK (FastMCP) + httpx/pydantic | MIT | один файл `delegate`, вместо самописного агентного цикла |
| Tor (профиль) | `PeterDaveHello/tor-socks-proxy` | GPL-3.0 | минимальный SOCKS5-контейнер без relay/exit |

---

## 12. Структура репозитория

```
docker-compose.yml          # весь стек (сервисы, тома, socat-форвардеры портов)
start.bat / stop.bat        # запуск/остановка всего стека в один клик
.env.example                # контракт переменных; копируется в .env (секреты)
models/                     # GGUF оркестратора
models/embeddings/          # bge-m3-Q8_0.gguf (RAG)
llama/models.ini            # router-режим: секция [orchestrator] = id модели
searxng/
  settings.yml.template     # formats json, резервные движки, __OUTGOING_PLACEHOLDER__
  render.sh                 # рендер settings.yml (+ outgoing.proxies, Tor-режим)
  core-config/              # сгенерированный settings.yml (gitignore)
litellm/config.yaml         # алиасы worker / worker-local / orchestrator
delegate/                   # FastMCP-сервис delegate_to_worker (поиск, чтение, санитайзер, JSONL)
study/                      # FastMCP-сервис учебного прогресса + study/syllabus.json (репетитор)
notes/                      # FastMCP-сервис linked notes (Markdown + SQLite FTS5) + seed
openwebui/filters/          # chumba_summary_filter.py (rolling summary)
testdata/                   # тестовый документ для RAG
scripts/                    # тесты и провижининг (см. §8.2)
logs/outbound/              # журнал всего исходящего к воркеру (аудит, ротация 90 дней)
docs/marketing.md           # маркетинговое описание
docs/decisions.md           # журнал инженерных решений и замеров
```

---

## 13. Ограничения и планы

Ограничения v1:

- Сервисы доступны только на `127.0.0.1` (без TLS и внешней публикации).
- Первый запрос после простоя ≥ `SLEEP_IDLE_SECONDS` ждёт перезагрузку модели (~1.5–2.5 мин).
- Tor в проверенной сети блокируется DPI (нужны мосты); Byparr читает с домашнего IP,
  гарантий обхода не даёт; тяжёлые CF-сайты читаются 1–2 минуты.
- API Open WebUI (`/api/chat/completions`) — OpenAI-совместимый pass-through: `tool_calls`
  исполняет клиент (браузерный UI делает это сам; пример — `scripts/e2e_loop.py`).
- `worker-local` — тот же оркестратор (27B); отдельная локальная модель-воркер не разворачивалась.
- RAG работает по документам/Knowledge; RAG по истории чатов не включён (историю сжимает summary).
- Не включены: gluetun, llama-swap, reranker `bge-reranker-v2-m3`, onion-сервис для удалённого доступа.

Идеи развития: RAG по истории чатов; reranker (`--rerank --pooling rank`); платный SERP API как
дополнительный поисковый путь; obfs4/webtunnel-образ для Tor; onion-доступ к UI.


## 14. Память: linked notes внутри Chumba

Основной запуск включает notes-сервис и patched Open WebUI. Сборка
воспроизводимая: `openwebui/Dockerfile` скачивает pinned upstream source,
применяет `openwebui/owui-memory.patch`, собирает frontend и накладывает
backend patch на pinned base image. Ручной `git apply` больше не нужен.

```powershell
docker compose up -d --build
```

Что поднимется дополнительно:

- `notes` — Markdown + SQLite FTS5, volume `chumba-notes-data`, MCP и internal HTTP;
- `open-webui` — локальный образ `chumba-open-webui-memory:local` с `/memory`;
- `notes-config` — одноразовая идемпотентная настройка notes MCP и импорт
  seed-документации; compaction/guard policy не включает.

URL:

- `http://127.0.0.1:3000/memory`
- `http://127.0.0.1:3000/memory/seed-chumba-map`
- `http://127.0.0.1:3000/memory/graph`

Настройки в `.env`: `NOTES_INTERNAL_TOKEN`, `NOTES_AGENT_TOKEN`,
`OPENWEBUI_MEMORY_IMAGE`, `OPENWEBUI_BASE_IMAGE`, `OPENWEBUI_REV`. Секреты не
коммитятся. Notes MCP доступен агенту, но память не загружается автоматически;
агент использует явные tool calls.

Откат образа: остановить стек, задать
`OPENWEBUI_MEMORY_IMAGE=ghcr.io/open-webui/open-webui@sha256:8b432fe0...f3cae9`
и запустить без `--build`; notes-сервис можно оставить остановленным. Рабочий
volume Open WebUI и активная compaction policy при этом не меняются.

Seed-заметки: `notes/seed/chumba/`; импорт вручную:
`python notes/seed/chumba/import_seed.py --url http://127.0.0.1:3000 --apply`.
Backup notes: сохранить volume `chumba-notes-data` (Markdown, revisions, FTS).
