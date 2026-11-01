ARG BASE_IMAGE=ghcr.io/open-webui/open-webui@sha256:8b432fe0a65b91116afc7961365c6cca5379cc923171386a96691f3471f3cae9
ARG UPSTREAM_REV=8bd8b4fac5e059578ac0c74b3c18d11139f88b7d

FROM node:22-bookworm AS frontend
ARG UPSTREAM_REV
ARG SOURCE_URL=https://codeload.github.com/open-webui/open-webui/tar.gz/${UPSTREAM_REV}
ENV NODE_OPTIONS=--max-old-space-size=8192
ENV USE_SLIM=true
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar patch \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL "$SOURCE_URL" -o /tmp/open-webui.tar.gz \
    && tar -xzf /tmp/open-webui.tar.gz --strip-components=1 -C /src
COPY owui-memory.patch /tmp/owui-memory.patch
RUN patch -p1 < /tmp/owui-memory.patch
RUN npm ci --force
RUN npm run build

FROM ${BASE_IMAGE}
COPY --from=frontend /src/build /app/build
COPY --from=frontend /src/backend/open_webui/main.py /app/backend/open_webui/main.py
COPY --from=frontend /src/backend/open_webui/routers/memory.py /app/backend/open_webui/routers/memory.py
