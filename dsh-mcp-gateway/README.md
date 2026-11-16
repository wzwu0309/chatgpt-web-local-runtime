# dsh-mcp-gateway — deployment payload

This directory **is** the deployment-home content: the launchers, the vendored
Python gateway, DSH plugins/patches/profile, the Worker façade, and the config
template.

Install it with `../install.sh` (macOS) or `../install.ps1` (Windows) from the
repository root — it lands at `~/.dsh-mcp-gateway` (`--target DIR` to override)
with a Python venv and the npm runtime installed for you. Once installed, this
source tree can be moved or deleted; the deployed copy is self-contained.
Re-running the installer refreshes code only — `env.conf`, `state/`, your
profile edits under `home/profiles/web/`, `bin/cloudflared`, and the venv are
never overwritten.

Project documentation lives one level up: `../README.md` and `../docs/`.
