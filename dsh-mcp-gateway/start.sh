#!/usr/bin/env bash
# Boot the whole ChatGPT -> DSH bridge stack:
#   DSH web host :3081  ->  cloudflared quick tunnel  ->  gateway :8766
# If a Cloudflare Worker facade is configured in env.conf, the fresh tunnel
# URL is published to it and the public origin stays fixed.
set -euo pipefail
GW_HOME="$(cd "$(dirname "$0")" && pwd)"
cd "$GW_HOME"
CONF="$GW_HOME/env.conf"
[ -f "$CONF" ] && source "$CONF"

DSH_HOME_DIR="${DSH_HOME_DIR:-$GW_HOME/home}"
DSH_HOST_PORT="${DSH_HOST_PORT:-3081}"
GATEWAY_PORT="${GATEWAY_PORT:-8766}"
WORKSPACE="${WORKSPACE:-$HOME/data/workspace}"
WORKSPACE_ALLOWED_ROOT="${WORKSPACE_ALLOWED_ROOT:-$HOME/data}"
CLOUDFLARED="${CLOUDFLARED:-$GW_HOME/bin/cloudflared}"
DSH_BIN="$GW_HOME/deploy/dsh-runtime/node_modules/.bin/dsh"
STATE_DIR="$GW_HOME/state"

# cordis `name:` fields must be literal paths — substitute <DSH_GW_HOME>
PATCH_YML="$GW_HOME/deploy/dsh/chatgpt-bridge.macos.resolved.cordis.yml"
sed "s|<DSH_GW_HOME>|$GW_HOME|g" \
  "$GW_HOME/deploy/dsh/chatgpt-bridge.macos.cordis.yml" > "$PATCH_YML"

if [ -z "${DSH_MCP_GATEWAY_ADMIN_PIN:-}" ]; then
  echo "env.conf must set DSH_MCP_GATEWAY_ADMIN_PIN" >&2; exit 1
fi
if [ ! -d "$WORKSPACE_ALLOWED_ROOT" ]; then
  echo "WORKSPACE_ALLOWED_ROOT '$WORKSPACE_ALLOWED_ROOT' does not exist." >&2
  echo "Create it (mkdir -p \"$WORKSPACE_ALLOWED_ROOT\") or set WORKSPACE_ALLOWED_ROOT in env.conf." >&2
  exit 1
fi

echo "[1/4] DSH web host :$DSH_HOST_PORT"
if ! curl -sf --max-time 2 "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" >/dev/null; then
  (cd "$WORKSPACE" && DSH_HOME="$DSH_HOME_DIR" DSH_TELEMETRY_DISABLED=1 \
    DSH_MCP_PUBLIC_BASE_URL="${WORKER_PUBLIC_URL:-https://placeholder.invalid}" \
    DSH_DEVIN="${DSH_DEVIN:-}" \
    WORKSPACE_ALLOWED_ROOT="$WORKSPACE_ALLOWED_ROOT" \
    PATH="$(dirname "$DSH_BIN"):$PATH" \
    nohup "$DSH_BIN" --profile web --patch "$PATCH_YML" \
      --host 127.0.0.1 --port "$DSH_HOST_PORT" --no-open \
      > /tmp/dsh-web-host.log 2>&1 &)
  for i in $(seq 1 30); do
    curl -sf --max-time 2 "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" >/dev/null && break
    sleep 2
  done
fi
curl -sf "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" >/dev/null \
  && echo "  ok" || { echo "  FAILED — see /tmp/dsh-web-host.log"; exit 1; }

echo "[2/4] cloudflared quick tunnel -> :$GATEWAY_PORT"
# Kill only tunnels pointed at our gateway port, regardless of binary path.
pkill -f "cloudflared.*--url http://127\.0\.0\.1:$GATEWAY_PORT" 2>/dev/null || true
sleep 1
: > /tmp/cloudflared.log
# http2 (TCP 7844) survives networks that throttle UDP/QUIC.
nohup "$CLOUDFLARED" tunnel --url "http://127.0.0.1:$GATEWAY_PORT" \
  --protocol http2 --no-autoupdate \
  > /tmp/cloudflared.log 2>&1 &
TUNNEL_URL=""
for i in $(seq 1 30); do
  # -a: log may contain NUL bytes from a killed writer mid-append
  TUNNEL_URL=$(grep -ao "https://[a-z0-9-]*\.trycloudflare\.com" /tmp/cloudflared.log | head -1 || true)
  [ -n "$TUNNEL_URL" ] && break
  sleep 2
done
[ "${TUNNEL_URL#https://}" != "$TUNNEL_URL" ] || TUNNEL_URL=""
[ -n "$TUNNEL_URL" ] || { echo "  FAILED — see /tmp/cloudflared.log"; exit 1; }
echo "  $TUNNEL_URL"

echo "[3/4] public origin"
# workers.dev may be unreachable direct; env.conf can set PROXY for outbound calls.
CURL_PROXY_ARGS=()
[ -n "${PROXY:-}" ] && CURL_PROXY_ARGS=(--proxy "$PROXY")
if [ -n "${WORKER_PUBLIC_URL:-}" ] && [ -n "${UPSTREAM_ADMIN_SECRET:-}" ]; then
  PUBLIC_URL="$WORKER_PUBLIC_URL"
  if curl -sf --max-time 15 "${CURL_PROXY_ARGS[@]}" -X PUT "$PUBLIC_URL/_upstream" \
      -H "Authorization: Bearer $UPSTREAM_ADMIN_SECRET" -d "$TUNNEL_URL" >/dev/null; then
    echo "  published to worker facade: $PUBLIC_URL"
  else
    echo "  FAILED: worker upstream registration (check WORKER_PUBLIC_URL/PROXY/ADMIN_SECRET)" >&2
    exit 1
  fi
else
  PUBLIC_URL="$TUNNEL_URL"
  echo "  no facade configured — using tunnel URL directly"
fi

echo "[4/4] gateway :$GATEWAY_PORT (public=$PUBLIC_URL)"
# Kill only gateways pointed at our DSH host port, regardless of how invoked.
pkill -f "dsh-mcp-gateway --dsh-harness-url http://127\.0\.0\.1:$DSH_HOST_PORT" 2>/dev/null || true
sleep 1
DSH_MCP_GATEWAY_ADMIN_PIN="$DSH_MCP_GATEWAY_ADMIN_PIN" \
  nohup "$GW_HOME/.venv/bin/dsh-mcp-gateway" \
    --dsh-harness-url "http://127.0.0.1:$DSH_HOST_PORT" \
    --public-base-url "$PUBLIC_URL" \
    --allowed-host "${TUNNEL_URL#https://}" \
    --state-dir "$STATE_DIR" \
    > /tmp/dsh-mcp-gateway.log 2>&1 &
sleep 4
curl -sf --max-time 15 "${CURL_PROXY_ARGS[@]}" \
  "$PUBLIC_URL/.well-known/oauth-authorization-server" >/dev/null \
  && echo "  ok" || echo "  warn: public metadata not reachable yet (propagation)"

echo
echo "MCP connector URL : $PUBLIC_URL/mcp"
echo "Owner PIN         : $DSH_MCP_GATEWAY_ADMIN_PIN"
