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
WORKSPACE_ALLOWED_ROOT="${WORKSPACE_ALLOWED_ROOT:-$HOME/data/workspace}"
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
WORKSPACE="${WORKSPACE:-$WORKSPACE_ALLOWED_ROOT/default}"
if [ -e "$WORKSPACE" ] && [ ! -d "$WORKSPACE" ]; then
  echo "WORKSPACE '$WORKSPACE' exists but is not a directory." >&2
  echo "Remove it or set WORKSPACE in env.conf to another path." >&2
  exit 1
fi
if ! mkdir -p "$WORKSPACE" 2>/dev/null; then
  echo "WORKSPACE '$WORKSPACE' could not be created." >&2
  echo "Check permissions or set WORKSPACE in env.conf." >&2
  exit 1
fi
if ! DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS="${DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS:-}" \
  node "$GW_HOME/deploy/dsh/validate-additional-roots.cjs"; then
  echo "Fix DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS in env.conf." >&2
  exit 1
fi

# Startup-level logs are truncated on each launch; the gateway's
# execution log rotates by size inside the process instead.
echo "[1/4] DSH web host :$DSH_HOST_PORT"
if ! curl -sf --max-time 2 "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" >/dev/null; then
  : > /tmp/dsh-web-host.log
  # Daemons must never be direct children of this script: bash waits on them
  # at exit otherwise. The bash -c shim backgrounds (exec daemon &) inside a
  # short-lived shell, so each daemon reparents to init immediately.
  (cd "$WORKSPACE" && DSH_HOME="$DSH_HOME_DIR" DSH_TELEMETRY_DISABLED=1 \
    DSH_MCP_PUBLIC_BASE_URL="${WORKER_PUBLIC_URL:-https://placeholder.invalid}" \
    DSH_SUBAGENT_DEVIN="${DSH_SUBAGENT_DEVIN:-}" \
    WORKSPACE_ALLOWED_ROOT="$WORKSPACE_ALLOWED_ROOT" \
    DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS="${DSH_SANDBOX_ADDITIONAL_WRITABLE_ROOTS:-}" \
    PATH="$(dirname "$DSH_BIN"):$PATH" \
    bash -c 'log="$1"; shift; (exec nohup "$@" </dev/null >>"$log" 2>&1 &)' \
      _ /tmp/dsh-web-host.log "$DSH_BIN" --profile web --patch "$PATCH_YML" \
      --host 127.0.0.1 --port "$DSH_HOST_PORT" --no-open)
  for i in $(seq 1 30); do
    curl -sf --max-time 2 "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" >/dev/null && break
    sleep 2
  done
fi
curl -sf "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" >/dev/null \
  && echo "  ok" || { echo "  FAILED — see /tmp/dsh-web-host.log"; exit 1; }

echo "[2/4] cloudflared quick tunnel -> :$GATEWAY_PORT"
TUNNEL_URL=""
# Quick-tunnel provisioning is rate-limited per egress IP (429 Tunnelstore)
# and cloudflared exits immediately on it — retry with backoff instead of
# failing the whole launch. Each attempt starts clean so strays can't pile up.
for attempt in 1 2 3; do
  # Kill only tunnels pointed at our gateway port, regardless of binary path.
  pkill -f "cloudflared.*--url http://127\.0\.0\.1:$GATEWAY_PORT" 2>/dev/null || true
  sleep 1
  : > /tmp/cloudflared.log
  # http2 (TCP 7844) survives networks that throttle UDP/QUIC.
  bash -c 'log="$1"; shift; (exec nohup "$@" </dev/null >>"$log" 2>&1 &)' \
    _ /tmp/cloudflared.log "$CLOUDFLARED" tunnel \
    --url "http://127.0.0.1:$GATEWAY_PORT" --protocol http2 --no-autoupdate
  for i in $(seq 1 30); do
    sleep 2
    # -a: log may contain NUL bytes from a killed writer mid-append
    TUNNEL_URL=$(grep -ao "https://[a-z0-9-]*\.trycloudflare\.com" /tmp/cloudflared.log | head -1 || true)
    [ -n "$TUNNEL_URL" ] && break
    # Provisioning failure kills cloudflared — no point waiting out the minute.
    pgrep -f "cloudflared.*--url http://127\.0\.0\.1:$GATEWAY_PORT" >/dev/null || break
  done
  [ "${TUNNEL_URL#https://}" != "$TUNNEL_URL" ] || TUNNEL_URL=""
  [ -n "$TUNNEL_URL" ] && break
  [ "$attempt" -lt 3 ] && echo "  provisioning attempt $attempt failed (see /tmp/cloudflared.log); retrying in 15s"
  [ "$attempt" -lt 3 ] && sleep 15
done
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
: > /tmp/dsh-mcp-gateway.launch.log
DSH_MCP_GATEWAY_ADMIN_PIN="$DSH_MCP_GATEWAY_ADMIN_PIN" \
  bash -c 'log="$1"; shift; (exec nohup "$@" </dev/null >>"$log" 2>&1 &)' \
    _ /tmp/dsh-mcp-gateway.launch.log "$GW_HOME/.venv/bin/dsh-mcp-gateway" \
    --dsh-harness-url "http://127.0.0.1:$DSH_HOST_PORT" \
    --public-base-url "$PUBLIC_URL" \
    --allowed-host "${TUNNEL_URL#https://}" \
    --state-dir "$STATE_DIR"
# Fresh tunnel edge registration and worker upstream propagation routinely
# outlast a few seconds — poll briefly instead of probing once.
ready=
for _ in $(seq 1 8); do
  sleep 3
  if curl -sf --max-time 5 "${CURL_PROXY_ARGS[@]}" \
    "$PUBLIC_URL/.well-known/oauth-authorization-server" >/dev/null; then
    ready=1
    break
  fi
done
[ -n "$ready" ] \
  && echo "  ok" \
  || echo "  warn: public metadata not reachable after ~30s (check PROXY/worker upstream)"

echo
echo "ChatGPT connector URL : $PUBLIC_URL/chatgpt/mcp"
echo "Gemini connector URL  : $PUBLIC_URL/gemini/mcp"
echo "Root MCP URL          : $PUBLIC_URL/mcp"
echo "Owner PIN             : $DSH_MCP_GATEWAY_ADMIN_PIN"
