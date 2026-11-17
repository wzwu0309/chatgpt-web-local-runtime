# Windows deployment guide

English | [中文](../zh-CN/windows.md)

Platform layer for the architecture in [architecture.md](architecture.md).
`install.ps1` installs the `dsh-mcp-gateway\` payload as the deployment home
at `%USERPROFILE%\.dsh-mcp-gateway` (any target via `-Target`; `start.ps1`
self-locates).

## Prerequisites

- **Node.js ≥ 24.9** + npm
- **Python 3.12** (`py -3.12 -m venv`) for the gateway
- **cloudflared** — `winget install --id Cloudflare.cloudflared`, or download
  `cloudflared-windows-amd64.exe` from
  [cloudflared releases](https://github.com/cloudflare/cloudflared/releases)
  and place it as `bin\cloudflared.exe`. Either way point `CLOUDFLARED` in
  env.conf at the exe. Single static binary, no installer needed — same args
  as macOS (`tunnel --url … --protocol http2 --no-autoupdate`)
- **Outbound reachability to the Worker** — the launcher PUTs the tunnel URL
  and health-checks it. If your network can't reach `*.workers.dev` (e.g.
  mainland China), run a local HTTP(S) proxy and set `PROXY=` in env.conf
  (`curl.exe` ships with Windows 10+ and supports `--proxy`).
- Optional Devin: `devin` CLI for Windows — see `devin/`

`@deepseek-ai/dsh` is public on npmjs.org — no `.npmrc` or private registry.

## Install

```powershell
cd <repo>                      # wherever you keep the project

# 1) installer — copies dsh-mcp-gateway\ to the target, creates the venv,
#    installs the gateway (editable; upstream @faf9606 + our patch,
#    pre-applied) and the npm runtime, seeds env.conf.
#    Native modules: node-pty/koffi resolve from shipped prebuilds — a
#    toolchain is only needed on platforms without a matching prebuild.
#    Re-run any time to refresh code — it never overwrites env.conf,
#    state\, profile edits, or bin\cloudflared.exe.
powershell -ExecutionPolicy Bypass -File .\install.ps1
#                                -> %USERPROFILE%\.dsh-mcp-gateway  (-Target DIR to change)

#    version note: the 0.1.2 pin predates sandbox-policy's
#    additionalWritableRoots — 0.2.2-rc.3 ships it; bump the pin when needed.

# 2) Worker façade — deploy worker/_worker.js in the Cloudflare dashboard;
#    step-by-step: docs/en/cloudflare.md
#    (KV namespace bound as UPSTREAM, secret ADMIN_SECRET
#    = UPSTREAM_ADMIN_SECRET in env.conf)

# 3) config + launch — from now on work inside the deployment home
cd "$env:USERPROFILE\.dsh-mcp-gateway"
notepad env.conf               # fill secrets; point CLOUDFLARED at your
                               # exe or drop it at bin\cloudflared.exe
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Optional Devin: `devin\README.md` inside the deployment home — merge the
package snippet into `home\profiles\web\package.json`, append the patch
rows, set `DSH_SUBAGENT_DEVIN=1`, re-run `start.ps1`.

Point the ChatGPT dev-mode connector at `$WORKER_PUBLIC_URL/mcp` and complete
OAuth with the owner PIN.

## Launch & operate

- `start.ps1` is idempotent like start.sh: port-scoped process matching via
  `Win32_Process` CommandLine, `Start-Process -WindowStyle Hidden` with log
  redirection, tunnel-log truncate, `--allowed-host` per fresh hostname,
  `/_upstream` PUT, gateway restart.
- `start.ps1` substitutes `<DSH_GW_HOME>` in the cordis patch template and
  emits `deploy/dsh/chatgpt-bridge.windows.resolved.cordis.yml` before
  launch — cordis `name:` fields must be literal, so the template ships a
  placeholder (forward slashes are fine for Node on Windows).
- Child processes inherit the launcher environment, so `DSH_SUBAGENT_DEVIN=1` in
  env.conf reaches the DSH host naturally — no source/export quirk like macOS.
- If your network blocks `*.workers.dev`, set `PROXY=` in env.conf —
  `curl.exe` ships with Windows 10+ and supports `--proxy`.
- Logs: `$env:TEMP\dsh-web-host.log`, `cloudflared.log`,
  `dsh-mcp-gateway.log` (plus `*.err.log` siblings — `Start-Process` splits
  stdout/stderr). Health: `GET /api/chatgpt-bridge/revision`.

## Platform notes

- **Shell tool: `pwsh`** — the `standard` preset mounts `tool-pwsh` on win32
  (and `tool-bash` elsewhere) automatically; the windows patch adds `pwsh` to
  `allowExtraTools`. Same contract as bash: `command` + `description`.
- **Sandbox: `windows-acl`, partial enforcement** — write grants via ACL ACEs
  on the workspace SID; weaker confinement than seatbelt/bwrap. Treat
  `workspace-write` on Windows as best-effort.
- **`shell_session`, `remote_machine`, `remote_exec`** stay in
  `allowExtraTools` but project nothing on win32 (their providers are
  disabled) — harmless. The `pwsh` tool covers shell needs.
- **Binaries**: `node_modules/.bin/dsh.cmd`,
  `.venv\Scripts\dsh-mcp-gateway.exe`.
- **Workspace root**: `%USERPROFILE%\data\workspace` — the bridge resolves via
  `os.homedir()`, so `workspace_switch`'s allowed root follows automatically.
- **Gateway**: the Python venv and our `oauth.py` `/proc/self/fd` fallback
  already cover Windows — no extra source changes beyond
  `gateway-local-changes.patch`.
