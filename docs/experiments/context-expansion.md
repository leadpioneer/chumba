# Расширение контекста локального оркестратора

Статус: эксперимент завершён, рабочая конфигурация не изменена.
Дата: 2026-11-01.
Железо: NVIDIA RTX 5060 Ti 16 ГБ, AMD Ryzen 5 7600X, 32 ГБ RAM, Windows 11 + Docker Desktop/WSL2.
Backend: llama.cpp build `b11312-0c1e57098`, образ
`ghcr.io/ggml-org/llama.cpp:server-cuda13@sha256:8c27804e7dd79c758d8e56722c2e393bfa32599e83d79f663e9e5ff8ac3b494f`.

## 1. Краткий вывод

- Фактическая рабочая конфигурация уже была **32K**, а не 16K: `ORCHESTRATOR_CTX=32768` в `.env`.
- 32K стабилен: 3 повтора, retrieval 9/9 на заполнении 25/50/85%, tool calling 2/2, длинная история 28.4K токенов.
- 64K стабилен на этой машине: retrieval 3/3 на каждом из 25/50/85% (для 50/85% ещё по 2 warm-повтора,
  все успешны), история 56K токенов, tools 2/2.
- 64K не требует RoPE/YaRN: модель обучена на 262144 токена, использован штатный контекст.
- 64K стоит примерно +720 МиБ VRAM и снижает prefill/decode на длинном конце:
  prefill 812 → 725 т/с, decode 19.1 → 16.7 т/с при заполнении 85%.
- Практический потолок для текущей модели/квантования/16 ГБ — **64K**. Для 96K прогнозно нужно
  ещё ~720 МиБ VRAM при свободных ~654 МиБ; это не проверено измерением и считается гипотезой.
- Главное ограничение для обычных чатов — не backend, а rolling summary в Open WebUI:
  история сжимается при `threshold_tokens=12000`, поэтому 64K не используется автоматически.
- Basalt и Strata не интегрировались: они рассчитаны на Qwen3.8-Flash-Next MoE
  (`general.architecture = qwen4exp`), а текущая модель — dense/hybrid `qwen35`.
  Проверка в коде Strata явно отказывает dense `qwen35`
  (`tests/core/gguf_geometry_test.cpp:269–277`), Basalt требует `qwen4exp`
  (`include/basalt/artifact/gguf_reader.hpp:570–573`).
  По документации Strata минимальный Flash-Next pack требует 37.6 ГБ RAM+VRAM и 66–76 ГБ загрузки;
  WSL2 VM здесь имеет 15.2 ГиБ RAM + 4 ГиБ swap. Это была бы смена модели и весов, а не
  оптимизация текущего движка.

## 2. Что фактически запущено и где лежит

### 2.1 Аудит

| Параметр | Факт |
|---|---|
| GPU | RTX 5060 Ti, 16 311 МиБ, драйвер 617.14, CUDA 13.4 |
| CPU | Ryzen 5 7600X, 6 ядер / 12 потоков |
| RAM | 31.1 ГиБ физически; Docker/WSL2 VM: 15 544 МиБ, swap 4 096 МиБ |
| ОС | Windows 11 Pro 10.0.26200 |
| Docker | 29.8.0, WSL2, overlayfs |
| Диск C: | свободно ~161 ГиБ |
| Модель | `models/Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf`, 13.41 ГиБ |
| Эмбеддинги | `models/embeddings/bge-m3-Q8_0.gguf`, 0.59 ГиБ, CPU |
| llama.cpp | digest `8c27804e...`, build `b11312-0c1e57098` |
| Режим | router, `--models-preset llama/models.ini`, id `orchestrator` |
| Контекст | `ORCHESTRATOR_CTX=32768` |
| Слоты | `-np 1`, `n_ctx_slot=32768` |
| GPU layers | `-ngl 99` |
| KV | `-ctk q4_0 -ctv q4_0`, `LLAMA_ARG_KV_OFFLOAD=true` |
| Flash Attention | `-fa on` |
| Сон | `--sleep-idle-seconds 180` (фактическое `.env`; в шаблоне 600) |

Модель и KV размещены на GPU. При 32K `nvidia-smi` показывал ~14.9 ГиБ, при 64K — ~15.66 ГиБ.
Отдельных строк с размерами model/KV/compute buffers этот build в router-логах не печатает;
размер весов взят из `/v1/models` (`size=14 392 500 224`), VRAM — из `nvidia-smi`.

### 2.2 Модель

| Свойство | Значение |
|---|---|
| architecture | `qwen35` |
| parameters | 27 320 697 856 |
| quantization | IQ4_XS, 4.25 bpw |
| blocks | 65 (64 основных + MTP/nextn block) |
| full attention | 16 слоёв (ids 3,7,…,63), `full_attention_interval=4` |
| recurrent/SSM | 48 слоёв, `state_size=128`, `group_count=16`, `inner_size=6144`, `conv_kernel=4` |
| heads | 24, KV heads 4 |
| head dim | K/V 256 |
| trained context | 262 144 |
| RoPE | base 10 000 000, partial rotary dimension 64 |
| chat template | Jinja, tools поддерживаются |

Это hybrid-модель, поэтому обычная формула KV для dense transformer не описывает её целиком.
Для full-attention слоёв при q4_0:

```
16 слоёв × 2 (K+V) × 4 KV-head × 256 dim × 0.5625 B/value ≈ 18 432 B/token ≈ 18 КиБ/token
32K ≈ 576 МиБ, 64K ≈ 1.15 ГиБ
```

Измеренный прирост VRAM 32K → 64K составил ~720 МиБ, что согласуется с расчётом плюс
compute/metadata. Recurrent state SSM — фиксированный per-slot кеш, он не растёт как KV на каждый токен;
точный его размер в логах этого build не выводится.

### 2.3 Frontend и бюджеты

- Open WebUI ходит напрямую в `http://llama-server:8080/v1`; LiteLLM — отдельный фасад,
  delegate-санитайзер ходит напрямую в `llama-server`.
- Модель по умолчанию: `chumba-orchestrator` (system prompt ~583 токена); `chumba-tutor` — ~772 токена.
- `chumba_summary_filter.py`: активный фильтр, valves `threshold_tokens=12000`, `keep_last_messages=6`,
  `summary_max_tokens=400`, `max_input_chars=30000`, fail-open.
- Встроенное `chat.context_compaction` Open WebUI выключено; его порог 80000 не действует.
- Бюджеты delegate: `WORKER_MAX_SOURCE_TOKENS=3000`, `WORKER_MAX_RESULT_TOKENS=800`,
  `DELEGATE_TASK_MAX_CHARS=1000`, `URL_READ_MAX_CHARS=8000`, `DELEGATE_SANITIZE_MAX_CHARS=300`.
- Сервер не резервирует отдельный output-бюджет: доступное окно = `n_ctx - prompt`.
  При 85% заполнении 32K остаётся ~5.8K токенов; тесты использовали 256 (perf), 160 (retrieval),
  128 (tools final), 80 (multi-turn final).

Проверка границы: запрос 36 020 токенов при 32K вернул HTTP 400
`exceed_context_size_error`, без молчаливого обрезания.

## 3. Методика benchmark

Клиент: `scripts/bench_context.py`. Фикстуры: `testdata/context_bench/facts.json`,
`testdata/context_bench/tools.json`.

- Синтетический текст генерируется с фиксированным seed, без повторения одной строки.
- Уникальные факты размещаются на 10%, 50%, 90% документа; вопросы не содержат ответ.
- Тесты: `perf`, `retrieval`, `multi_turn`, `tools`, `integration`.
- Streaming: TTFT по первому content/reasoning-дельта; серверные `timings` — отдельно.
- Память: `nvidia-smi` и `/proc/meminfo` в контейнере; пики в таблицах — **run-wide**, не per-request.
- Cold: перезапуск backend, прогрев загрузки модели, пустой KV/prompt cache; warm: без перезапуска.
- Повторы: 32K — 3 на точку, 64K — 1 cold + 2 warm на 50/85%; retrieval 64K — 1 cold + 2 warm.
- Ошибки/OOM/перезапуски записываются в CSV/JSON; отсутствующая метрика — `null`.

Примеры запуска:

```powershell
python scripts/bench_context.py --tests perf,retrieval,multi_turn,tools `
  --fills 0.25,0.5,0.85 --repeats 3 --stream --ctx-size 32768 `
  --backend-label llama.cpp-11312-baseline32k --container chumba-llama-server
```

```powershell
python scripts/bench_context.py --base-url http://127.0.0.1:8090/v1 `
  --tests perf,retrieval,multi_turn,tools --fills 0.25,0.5,0.85 --repeats 1 `
  --stream --ctx-size 65536 --backend-label llama.cpp-11312-exp64k `
  --container chumba-llama-exp
```

## 4. Результаты

### 4.1 32K baseline

Медианы; в скобках — min–max там, где было 3 повтора.

| Заполнение | Prompt tokens (факт) | Prefill, т/с | Decode, т/с | TTFT, с | Retrieval |
|---|---:|---:|---:|---:|---:|
| 25% | 7 213 cold / 7 103 warm | 866.7 / 885.1 (879.8–885.2) | 19.4 / 21.4 (20.2–21.5) | 8.5 / 8.9 | 3/3 |
| 50% | 15 401 / 15 331 | 858.2 / 851.9 (848.1–855.7) | 19.8 / 19.7 (19.6–19.7) | 18.3 / 18.9 | 3/3 |
| 85% | 26 903 / 26 849 | 812.5 / 810.6 (810.4–814.2) | 19.1 / 18.4 (18.4–19.1) | 33.7 / 33.9 | 3/3 |

VRAM peak: **14 937 МиБ**; свободно ~1.37 ГиБ. Ошибок/OOM/перезапусков нет.
RAM/swap в run-wide сэмплах: available 6.0–14.2 ГиБ, swap 0.2–0.7 ГиБ; значения зависят
от page cache и balloon WSL2 и не являются жёстким резервом.

### 4.2 64K experimental

| Заполнение | Prompt tokens (факт) | Prefill, т/с | Decode, т/с | TTFT, с | Retrieval |
|---|---:|---:|---:|---:|---:|
| 25% | 15 327 | 862.4 | 19.4 | 17.8 | 3/3 |
| 50% | 31 741 cold / 31 774 warm | 801.1 / 789.3 (782.1–796.6) | 18.6 / 18.0 (17.8–18.3) | 40.1 / 41.8 | 3/3 cold + 2/2 warm |
| 85% | 54 440 cold / 54 526 warm | 724.6 / 721.3 (719.6–723.0) | 16.7 / 16.9 (16.9–17.0) | 76.0 / 76.8 | 3/3 cold + 2/2 warm |

VRAM peak: **15 657 МиБ**; свободно ~654 МиБ. Ошибок/OOM/перезапусков нет.
Загрузка модели ~80–85 с с диска/кеша; при 64K `n_ctx_slot=65536`.

### 4.3 Длинная история и cache reuse

| Профиль | История в финале | Cache hit | Recall |
|---|---:|---:|---|
| 32K, 8 ходов | 28 432 токена | `cache_n=28 361`, `prompt_n=71` | первый и последний маркер найдены |
| 64K, 8 ходов | 56 535 токенов | `cache_n=56 464`, `prompt_n=71` | первый и последний маркер найдены |

На каждом ходе 32K обрабатывалось ~3.5K новых токенов при `cache_n` 3.5K → 24.8K; на 64K —
~7K новых при `cache_n` 7K → 49.4K. Это подтверждает переиспользование префикса, но не
гарантирует его для произвольных правок истории: при изменении середины кеш инвалидируется.

### 4.4 Tool calling

| Профиль | Первый запрос | Второй запрос | Результат |
|---|---:|---:|---|
| 32K, 50% | 15 667 prompt, prefill 859.2, decode 23.6 | 70 новых, `cache_n=15 738` | 2 вызова, JSON args валидны, код 4183 и сумма 42 |
| 32K, 85% | 27 172 prompt, prefill 811.2, decode 22.2 | 70 новых, `cache_n=27 243` | 2 вызова, результаты не потеряны |
| 64K, 50% | 32 108 prompt, prefill 792.2, decode 21.6 | 70 новых, `cache_n=32 179` | 2 вызова, результаты не потеряны |

Проверялись имена `lookup_project_code`/`add_numbers`, разбор JSON-аргументов и ответ после
tool result. Инструменты синтетические, без внешних побочных действий.

### 4.5 Интеграция

- Open WebUI: signin OK, `/api/models` возвращает `orchestrator`, `chumba-orchestrator`, `chumba-tutor`;
  tool servers `search` и `delegate` подключены.
- Прямой чат через Open WebUI (`orchestrator` и `chumba-orchestrator`) работает. При `max_tokens=16`
  ответ был пустым (`finish=length`), потому что thinking расходует бюджет; при `max_tokens=512`
  ответ получен (`finish=stop`). Это не ограничение контекста, а бюджет вывода.
- delegate: `sanitize_check.py` — `SANITIZE_OK=True`, исходный маркер остался локально,
  в sanitized задаче и outbound payload его нет. Использовались только синтетические данные.

## 5. Классификация выводов

**Измерено локально**

- 32K стабилен на 25/50/85%; 64K стабилен на 25/50/85%.
- 64K: +720 МиБ VRAM, prefill 725–801 т/с, decode 16.7–18.6 т/с на 50/85%.
- Retrieval 3/3 на 26.9K (32K) и 3/3 на 54.7K (64K), включая факты в начале/середине/конце.
- Tool calling и tool results сохраняются на 27K/32K; cache reuse работает.
- Запрос 36 020 токенов при 32K даёт HTTP 400.
- Open WebUI и delegate-санитайзер работают на текущем стеке.

**Подтверждено документацией/метаданными**

- Модель обучена на 262144 токена; 64K не требует RoPE scaling.
- 16 full-attention слоёв и 48 SSM-слоёв; KV-формула dense transformer неприменима целиком.
- Frontend-фильтр сжимает историю при 12K; встроенное compaction Open WebUI выключено.
- Basalt/Strata поддерживают Qwen3.8-Flash-Next MoE, а не текущую 27B-модель.

**Гипотеза**

- 96K не влезет: нужен ещё ~720 МиБ VRAM при свободных ~654 МиБ; требуется проверка.
- 64K — практический потолок при текущем квантовании и `-ngl 99`; 80K может быть возможен,
  но с меньшим запасом и без практического выигрыша для текущего frontend.
- 64K не даст выигрыша обычным чатам, пока `threshold_tokens` остаётся 12K.
- KV в RAM (`LLAMA_ARG_KV_OFFLOAD=false`) на 64K будет медленнее; по прошлым замерам
  генерация падала примерно втрое, но в этом эксперименте режим не перепроверялся.

**Не проверено**

- 96K/128K, RoPE/YaRN, `--parallel > 1`, KV offload на 64K.
- Генерация длиннее 256 токенов на 85% заполнении.
- Реальные пользовательские чаты и реальные MCP tool results.
- Basalt/Strata runtime, качество и скорость на этой машине.
- Поведение при одновременной работе других GPU-приложений и при заполнении VRAM desktop-ом.

## 6. Выбранные профили

### 6.1 Основной повседневный — 32K (оставить как есть)

- `ORCHESTRATOR_CTX=32768`, `-ngl 99`, `-np 1`, `q4_0/q4_0`, KV на GPU, FA on.
- VRAM ~14.9 ГиБ, запас ~1.37 ГиБ, prefill ~810–885 т/с, decode ~18.4–21.5 т/с.
- Подходит для Open WebUI с rolling summary 12K, RAG top-k 4, delegate и обычных tool-циклов.
- Это текущая рабочая конфигурация; `.env` и `docker-compose.yml` не изменялись.

### 6.2 Длинный контекст — 64K, только по необходимости

- Изолированный профиль: `experiments/context-profiles/docker-compose.context-exp.yml`,
  порт `127.0.0.1:8090`, отдельные имена контейнеров и сетей, без egress.
- VRAM ~15.66 ГиБ, запас ~0.65 ГиБ; не запускать одновременно с другими GPU-backend.
- Prefill 725–801 т/с, decode 16.7–18.6 т/с на 50/85%; TTFT 40–77 с на длинном конце.
- Использовать для больших RAG-документов, длинных tool results и агентных цепочек,
  которым реально нужно >32K. Для обычных чатов сначала поднимать `threshold_tokens`
  фильтра (например, 24000) и проверять качество summary; это отдельный эксперимент.

### 6.3 Basalt/Strata — не интегрировать

См. раздел 7. Для текущей 27B-модели и 32 ГБ RAM (15.2 ГиБ в WSL2) это несовместимая
смена модели/формата, а не оптимизация backend.

## 7. Basalt и Strata: проверка и решение

### 7.1 Basalt

- Репозиторий: `jesdga95/basalt`, tag `v0.1.0` (2026-10-29), main
  `9175e7a3211bda355c02e804c7c107b15b8ea89f` (2026-10-31).
- Назначение: локальный inference engine для **Qwen3.8-Flash-Next MoE**; модель — один
  `.basalt` pack (weights, tokenizer, MTP drafter, expert profile, vision).
- Architecture guard: `include/basalt/artifact/gguf_reader.hpp:570–573` требует
  `general.architecture == "qwen4exp"`; текущий файл — `qwen35`, поэтому движок откажется
  его загружать. Это не вопрос настройки, а другой формат/семейство модели.
- Платформы: Linux x64 и Windows x64, NVIDIA Blackwell `sm_120`, CUDA 13; RTX 5060 Ti
  формально подходит по GPU, но не по модели/RAM.
- API: OpenAI- и Anthropic-совместимый; tool/JSON grammar, streaming, parallel slots.
- Контекст/KV: `max-context` (default 4096), `kv` fp16/int8/q4_0/k8v4, `kv-resident`
  (KV в pinned RAM, streaming), elastic KV выше 32768, `rope-scaling`.
- Память: experts в RAM/VRAM/файле, expert cache, `resident-experts`, `mmap-experts`,
  `vram-elastic`; speculative MTP и prompt lookup.
- Ограничения: Windows pinned memory cap/WDDM TDR; качество на Windows не измерялось;
  нужен большой объём RAM под experts и SSD под n-gram table.
- Лицензия движка MIT; веса Qwen3.8-Flash-Next — Qwen Community License 1.0 (не MIT).

Вывод: для текущей Qwen3.8-27B **неподдерживаемая архитектура**. По документации Strata
минимальный Flash-Next pack требует ~37.6 ГБ RAM+VRAM и 66–76 ГБ загрузки; точные размеры
Basalt pack зависят от формата, но модель и требование к RAM те же. На 32 ГБ RAM / 15.2 ГиБ
WSL2 это не помещается без low-RAM/SSD-режима и было бы медленнее; интеграция нецелесообразна.

### 7.2 Strata

- Репозиторий: `Niko1221/Strata`, tag `v0.2.9` (2026-10-31), main
  `c62d5e3a15b40ea51c96aae9e8d36928965f53ab` (2026-10-31).
- Это родительский проект Basalt; поддерживает Qwen3.8-Flash-Next (`qwen4exp`),
  Qwen3.6-35B-A3B (`qwen35moe`) и DeepSeek V4.1-Flash. Текущей Qwen3.8-27B в списке нет.
- Architecture guard: `include/strata/artifact/gguf_reader.hpp` принимает `qwen4exp` и
  `qwen35moe`; тест `tests/core/gguf_geometry_test.cpp:269–277` прямо проверяет отказ для
  dense `qwen35`: `architecture is 'qwen35', this engine requires 'qwen4exp'`.
- Qwen3.8-Flash-Next: RAM+VRAM 37.6–54.8 ГБ для ISTA-паков, download 66–76 ГБ;
  Unsloth UD-IQ4_XS — 94 ГБ download и ~72 ГБ RAM для всех experts.
- KV: в `HOW_IT_WORKS.md` описано, что с 64K только наиболее читаемая часть KV остаётся
  в VRAM, остальное стримится из RAM. `kv-resident`/elastic KV — это механизмы Basalt
  config; переносить их на Strata без отдельной проверки нельзя.
- Есть conversation cache и prompt lookup (`HOW_IT_WORKS.md`), MTP speculative decoding;
  expert cache и low-RAM режим. Это разные механизмы, их нельзя автоматически переносить
  между Basalt и Strata.
- Лицензия движка MIT; веса — по лицензии соответствующей модели.

Вывод: Strata также не принимает текущую dense `qwen35` модель. Переход означал бы
скачивание десятков гигабайт, смену модели/квантования и другой алгоритм генерации;
сравнение скоростей нельзя было бы называть преимуществом движка. Решение:
**не интегрировать** в этом этапе.

## 8. Артефакты, запуск и откат

Добавлено:

- `scripts/bench_context.py` — воспроизводимый клиент, CSV/JSON, streaming TTFT,
  серверные timings, память, cold/warm, tool/retrieval/history тесты.
- `testdata/context_bench/facts.json`, `testdata/context_bench/tools.json` — синтетические
  факты и безопасные инструменты.
- `experiments/context-profiles/docker-compose.context-exp.yml` — изолированный 64K backend,
  отдельные контейнеры/сети, host-порт только `127.0.0.1:8090`.
- `experiments/context-profiles/context-exp.env.example` — несекретный пример профиля.
- `docs/experiments/context-expansion.md` — этот отчёт.
- `.gitignore`: `benchmark-results/`, `experiments/context-profiles/context-exp.env`.

Сырые результаты лежат в `benchmark-results/context/` (игнорируется git):
`baseline-32k-cold`, `baseline-32k-warm`, `baseline-32k-long`, `exp-64k-cold`, `exp-64k-warm`.
Большие логи и ответы моделей автоматически не коммитятся.

### 8.1 Запуск основного профиля

```powershell
docker compose up -d llama-server llama-port
# или весь стек:
docker compose up -d
```

Проверка:

```powershell
docker compose config llama-server
Invoke-RestMethod http://127.0.0.1:8080/v1/models
```

### 8.2 Запуск 64K эксперимента

```powershell
docker compose stop llama-server llama-port
docker compose -f experiments/context-profiles/docker-compose.context-exp.yml `
  --env-file experiments/context-profiles/context-exp.env.example up -d
python scripts/bench_context.py --base-url http://127.0.0.1:8090/v1 `
  --tests perf,retrieval,multi_turn,tools --fills 0.25,0.5,0.85 --repeats 1 `
  --stream --ctx-size 65536 --backend-label llama.cpp-11312-exp64k `
  --container chumba-llama-exp
```

Остановка эксперимента:

```powershell
docker compose -f experiments/context-profiles/docker-compose.context-exp.yml `
  --env-file experiments/context-profiles/context-exp.env.example down
```

### 8.3 Возврат к исходному состоянию

Файлы `.env`, `docker-compose.yml`, `llama/models.ini` не изменялись; текущий `.env` уже
содержит `ORCHESTRATOR_CTX=32768`. Вернуть исходное состояние (стек был остановлен):

```powershell
docker compose -f experiments/context-profiles/docker-compose.context-exp.yml `
  --env-file experiments/context-profiles/context-exp.env.example down
docker compose stop
```

Проверить, что volumes и модели на месте:

```powershell
docker volume ls | Select-String chumba
Get-ChildItem models
```

Если нужно оставить рабочий стек запущенным, используйте `docker compose up -d` вместо
`docker compose stop`; это не меняет конфигурацию.

### 8.4 Если понадобится 64K по умолчанию

Не делать это автоматически. После повторной проверки на своей машине:

1. Зафиксировать текущий `.env` и образ (digest уже закреплён).
2. Изменить только `ORCHESTRATOR_CTX=65536`.
3. Перезапустить `llama-server` и проверить `n_ctx_slot=65536`, VRAM и 85% заполнение.
4. При нехватке VRAM вернуть 32768; не включать RoPE/YaRN без отдельного теста качества.

## 9. Ограничения отчёта

- Пики RAM/swap — run-wide и зависят от WSL2 balloon/page cache; VRAM измерен точнее.
- 64K warm имеет 2 повтора на 50/85%, 32K — 3; retrieval 64K — 3 cold + 2 warm.
- Не проверялись 96K/128K, `--parallel > 1`, KV offload на 64K, длинная генерация >256 токенов.
- Интеграционный smoke не использовал реальные приватные данные; проверены Open WebUI
  signin/models/direct chat и delegate-санитайзер, но не полный браузерный MCP-цикл.
- Basalt/Strata оценены по документации/коду; runtime-сравнение не проводилось, потому что
  требуется другая модель, десятки гигабайт весов и больше RAM.
