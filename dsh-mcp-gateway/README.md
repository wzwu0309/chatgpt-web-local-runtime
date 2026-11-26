# dsh-mcp-gateway — deployment payload

This directory **is** the deployment-home content: the launchers, the vendored
Python gateway, DSH plugins/patches/profile, the Worker façade, and the config
template.

From the repository root, run `./install.sh` (macOS) or
`powershell -ExecutionPolicy Bypass -File .\install.ps1` (Windows). The
default target is `~/.dsh-mcp-gateway` (override with `--target` or
`-Target`). The installed copy is self-contained, with its own Python venv
and npm runtime.
Re-running the installer refreshes code only — `env.conf`, `state/`, your
profile edits under `home/profiles/web/`, `bin/cloudflared`, and the venv are
never overwritten. The launchers reuse a healthy DSH host. After updating
bridge or plugin code, restart the host to load the changes.

Project documentation lives one level up: `../README.md` and `../docs/`.

## MCP mounts

- `/chatgpt/mcp` — ChatGPT Web, with conversation workspace binding.
- `/mcp` — the canonical generic mount; optional `dsh_tool_call.workspace` per call.
- `/gemini/mcp` — the Gemini named mount; shares the generic exposure.
