#!/bin/sh
# Рендер settings.yml для SearXNG.
# Секция outgoing собирается из OUTBOUND_PROXY (если задан в .env).
# Причина: SearXNG не поддерживает env-подстановку для outgoing.proxies.
set -eu

TPL="${TPL:-/template/settings.yml.template}"
OUT="${OUT:-/out/settings.yml}"

mkdir -p "$(dirname "$OUT")"

{
  awk '
    /__OUTGOING_PLACEHOLDER__/ { next }
    { print }
  ' "$TPL"
  if [ -n "${OUTBOUND_PROXY:-}" ]; then
    # Официальный паттерн SearXNG для Tor: socks5h + using_tor_proxy
    # + extra_proxy_timeout; таймауты подняты под Tor-латентность.
    printf 'outgoing:\n'
    printf '  request_timeout: 10.0\n'
    printf '  max_request_timeout: 10.0\n'
    printf '  using_tor_proxy: true\n'
    printf '  extra_proxy_timeout: 10.0\n'
    printf '  retries: 2\n'
    printf '  proxies:\n'
    printf '    all://:\n'
    printf '      - %s\n' "$OUTBOUND_PROXY"
  else
    printf 'outgoing: {}\n'
  fi
} > "$OUT"

echo "searxng settings rendered: $OUT (proxy: ${OUTBOUND_PROXY:-none})"
