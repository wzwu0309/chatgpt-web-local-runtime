#!/usr/bin/env bash
# Install (or refresh) the chatgpt-web-local-runtime deployment home.
#
#   ./install.sh [--target DIR]     default: ~/.dsh-mcp-gateway
#
# Copies dsh-mcp-gateway/ to the target, then performs the standard install:
# Python venv + editable gateway, npm DSH runtime, env.conf seed.
#
# Idempotent — re-running refreshes code only. It never overwrites:
#   env.conf, state/, home/profiles/web/* (your profile edits),
#   bin/cloudflared, .venv, node_modules, *.resolved.cordis.yml.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/dsh-mcp-gateway" && pwd)"
TARGET="$HOME/.dsh-mcp-gateway"
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    -h|--help) echo "usage: install.sh [--target DIR]"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"

echo "[1/4] sync $SRC -> $TARGET"
# Pure-code dirs refresh with --delete so removed files disappear. Artifacts
# (egg-info, __pycache__, node_modules, *.resolved) are excluded and rebuilt
# by the steps below.
for d in src deploy worker devin \
         dsh-bridge-plugin dsh-remote-worker-plugin \
         dsh-shell-session-plugin dsh-task-state-plugin; do
  rsync -a --delete \
    --exclude='__pycache__/' --exclude='*.egg-info/' \
    --exclude='node_modules/' --exclude='*.resolved.cordis.yml' \
    "$SRC/$d/" "$TARGET/$d/"
done
# Top-level files are all ours — always refresh.
for f in pyproject.toml LICENSE README.md env.conf.example \
         gateway-local-changes.patch start.sh start.ps1; do
  cp "$SRC/$f" "$TARGET/$f"
done
# Profile files may carry user edits (devin merge) — seed only, never clobber.
rsync -a --ignore-existing "$SRC/home/" "$TARGET/home/"
mkdir -p "$TARGET/bin" "$TARGET/state"
chmod +x "$TARGET/start.sh"

echo "[2/4] Python gateway (.venv + editable install)"
PYTHON="${PYTHON:-python3.12}"
command -v "$PYTHON" >/dev/null || { echo "  FAILED: $PYTHON not on PATH" >&2; exit 1; }
[ -x "$TARGET/.venv/bin/pip" ] || "$PYTHON" -m venv "$TARGET/.venv"
(cd "$TARGET" && .venv/bin/pip install -q -e ".[server]")

echo "[3/4] DSH runtime (npm)"
command -v npm >/dev/null || { echo "  FAILED: npm not on PATH" >&2; exit 1; }
(cd "$TARGET/deploy/dsh-runtime" && npm install --no-fund --no-audit)

echo "[4/4] env.conf"
if [ -f "$TARGET/env.conf" ]; then
  echo "  exists — kept"
else
  cp "$TARGET/env.conf.example" "$TARGET/env.conf"
  echo "  seeded from env.conf.example — fill in the secrets"
fi

cat <<EOF

Installed to $TARGET

Next:
  1. edit $TARGET/env.conf            (PIN, WORKER_PUBLIC_URL, UPSTREAM_ADMIN_SECRET, PROXY?)
  2. deploy worker/_worker.js on Cloudflare   (KV UPSTREAM + secret ADMIN_SECRET)
  3. put cloudflared at $TARGET/bin/cloudflared  (or set CLOUDFLARED in env.conf)
  4. $TARGET/start.sh
EOF
