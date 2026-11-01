---
id: seed-operations
title: "Запуск, остановка, диагностика, откат"
type: reference
status: active
sources: ["README.md", "docker-compose.yml", "docs/experiments/mvp-results.md", "experiments/notes-mvp/docker-compose.yml"]
---

# Запуск, остановка, диагностика, откат

**Рабочий стек:**

```powershell
docker compose up -d
docker compose ps
docker compose logs -f open-webui
docker compose stop
```

Проверки: `GET /props?model=orchestrator`, `GET /health`, `GET /v1/models`; OWUI `http://127.0.0.1:3000`; MCP check `python scripts\check_mcp.py`.

**Экспериментальный notes MVP:**

```powershell
docker build -t chumba-notes:exp notes
# apply experiments/notes-mvp/owui-memory.patch к pinned OWUI source
docker build -t chumba-open-webui-notes:exp <patched-source>
docker compose -f experiments/notes-mvp/docker-compose.yml up -d
python experiments/notes-mvp/configure_temp.py
```

Rollback: `docker compose -f experiments/notes-mvp/docker-compose.yml down`, remove temp OWUI volume, keep `chumba-notes-data`; stop `llama-server`. Не менять рабочие `.env`/compose/volumes.

Диагностика notes: direct 8089 требует internal token; UI — same-origin proxy; MCP — agent token. См. [[seed-storage-volumes|storage]], [[seed-limitations|ограничения]].
