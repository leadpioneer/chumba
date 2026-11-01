# Patched Open WebUI for Chumba memory

`Dockerfile` builds the experimental memory UI reproducibly from the pinned
upstream source revision and applies `owui-memory.patch` automatically.

```powershell
docker compose build open-webui
```

Inputs:

- `BASE_IMAGE` / `OPENWEBUI_BASE_IMAGE`: pinned upstream Open WebUI digest;
- `UPSTREAM_REV` / `OPENWEBUI_REV`: pinned source revision;
- `owui-memory.patch`: canonical UI/backend patch (also kept under
  `experiments/notes-mvp/` for experiment history).

The final image starts from the pinned upstream image and overlays only the
built frontend plus `backend/open_webui/main.py` and `routers/memory.py`. The
working image is not replaced unless the compose service is pointed at this
build. Use `docker compose up -d --build` for the normal launch.

Build requires network access for the pinned source tarball and npm
dependencies. The first build is slow; subsequent builds use Docker cache.
