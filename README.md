# chatgpt-web-local-runtime

English | [中文](README.zh-CN.md)

ChatGPT Web as the reasoning agent + a local DeepSeek Harness as the guarded
execution runtime, exposed through a fixed public MCP endpoint.

```
ChatGPT ──MCP+OAuth──> workers.dev façade ──> rotating quick tunnel
        ──> local gateway :8766 ──> local DSH host :3081 ──> DSH tools/sandbox
```

ChatGPT needs **one fixed connector URL**, but the quick tunnel rotates on
every launch. The **Cloudflare Worker façade** bridges that gap: it owns the
permanent `https://dsh-gw.<you>.workers.dev` origin, keeps the current tunnel
URL in KV, and `start.sh` republishes it on each run (`PUT /_upstream`,
Bearer-secreted). Free tier suffices — one-time dashboard setup:
**[docs/en/cloudflare.md](docs/en/cloudflare.md)**.

Read **[docs/en/architecture.md](docs/en/architecture.md)** first — it contains the
full component inventory, the security model, and every operational caveat
and fixed. Then deploy via your platform guide:
**[macOS](docs/en/macos.md)** or **[Windows](docs/en/windows.md)**.

**Why a whole harness behind one connector:** the bridge deliberately
projects a full DSH ToolRuntime (sandboxed bash, sessions, skills,
`subagent_devin`) instead of shipping a purpose-built tool server like
[CodexPro](https://github.com/rebel0789/codexpro) — one Node process,
first-class MCP tools, static token, no sandbox or sessions. The
trade-off and drop-in compatibility are analyzed in
[architecture.md](docs/en/architecture.md#alternative-route-evaluated-a-purpose-built-mcp-server-codexpro).

## What this adds on top of upstream

The vendored gateway is only the protocol adapter; running it as a
**permanent** connector is the part this repo owns:

- **A fixed public URL.** `worker/_worker.js` is a tiny Cloudflare Worker
  façade holding the connector origin; the launcher republishes the
  rotating quick-tunnel URL to it on every run (`PUT /_upstream`,
  Bearer-secreted), so ChatGPT's connector URL never changes.
- **One-command launchers.** `start.sh` / `start.ps1` are idempotent and
  self-locating — reuse a live host, rotate the tunnel with the
  `--allowed-host` + `http2` workarounds baked in, publish upstream,
  restart the gateway. Daily use is one command, no re-setup.
- **An idempotent installer.** `install.sh` / `install.ps1` copy the
  payload, build the venv, install the npm runtime, and seed `env.conf`;
  re-runs refresh code only — config, state, profile edits, and
  `bin/cloudflared` are never touched.
- **A tested composition.** The cordis patch mounts exactly the plugins
  this bridge needs (persistent shell, task state, remote workers,
  optional gated `subagent_devin`) behind the reviewed
  `chatgpt-external-v1` capability profile, and the gateway carries three
  pre-applied source patches (`--allowed-host`, callContract prompts,
  cross-platform OAuth SQLite) — details in
  [architecture.md](docs/en/architecture.md#component-inventory).
- **Per-OS playbooks.** macOS and Windows guides plus the full operational caveats
  catalog in [architecture.md](docs/en/architecture.md).

## Layout

The repo separates **project** files (docs, installer) from the **deployment
payload** (`dsh-mcp-gateway/` — its name is its install target). One command
installs it:

```bash
./install.sh                  # -> ~/.dsh-mcp-gateway   (--target DIR to change)
# Windows: powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer copies the payload, creates the venv, installs the gateway and
the npm runtime, and seeds `env.conf`. Re-running refreshes code only — your
`env.conf`, `state/`, profile edits and `bin/cloudflared` are never touched.
After install the repo is no longer needed at runtime.

```
docs/
  en/
    architecture.md          the reference doc — components, caveats (read this)
    cloudflare.md            Worker + KV + secret setup in the CF dashboard
    macos.md                 macOS platform guide (install.sh, start.sh, seatbelt)
    windows.md               Windows platform guide (install.ps1, start.ps1, acl)
  images/                    screenshots for cloudflare.md (shared by both languages)
  zh-CN/
    architecture.md          mirror of en/ in Chinese
    cloudflare.md            mirror of en/ in Chinese
    macos.md                 mirror of en/ in Chinese
    windows.md               mirror of en/ in Chinese
install.sh / install.ps1     idempotent installers (copy + venv + npm + seed)
dsh-mcp-gateway/             deployment payload -> installed as ~/.dsh-mcp-gateway
  README.md                  payload readme (also the pyproject readme)
  pyproject.toml             vendored gateway manifest ([upstream](https://github.com/siddhartha-yz/dsh-mcp-gateway) @faf9606, MIT)
  src/dsh_mcp_gateway/       vendored Python gateway source (patch pre-applied)
  LICENSE                    the gateway's MIT license
  gateway-local-changes.patch   delta record vs upstream (pre-applied, don't re-apply)
  env.conf.example           config template (installer seeds env.conf from it)
  start.sh / start.ps1       self-locating idempotent launchers (macOS / Windows)
  worker/_worker.js          Cloudflare Worker façade (KV upstream + bearer PUT)
  deploy/
    dsh-runtime/package.json DSH npm runtime manifest (@deepseek-ai/dsh)
    dsh/chatgpt-bridge.{macos,windows}.cordis.yml
                             composition patches — <DSH_GW_HOME> placeholder
                             resolved by the launcher to *.resolved
  dsh-bridge-plugin/         bridge plugin (ours-modified) + capability profile
  dsh-task-state-plugin/     upstream plugin, unmodified
  dsh-shell-session-plugin/  upstream plugin (POSIX bash only)
  dsh-remote-worker-plugin/  upstream plugin (inert without pollers)
  home/profiles/web/         DSH profile — bundles dsh-base + dsh-web-app,
                             independent of your everyday ~/.dsh
  devin/                     OPTIONAL Devin bundle (provider + gated subagent_devin)
    README(.zh-CN).md        enable steps
    profile-package.snippet.json   merge into profile package.json
    cordis.patch.devin.yml   append to profile cordis.patch.yml
```

Runtime files land in the **install target**, not this repo: `env.conf`,
`.venv/`, `state/`, `home/` session data, `bin/cloudflared`,
`*.resolved.cordis.yml`. Never commit secrets.

## TL;DR operating loop

1. `./install.sh` (or `install.ps1`) → then edit `~/.dsh-mcp-gateway/env.conf`:
   PIN / Worker URL / secret / `PROXY` — the launcher itself must reach
   `workers.dev`; where it can't (e.g. mainland China), `PROXY` points at a
   local HTTP(S) proxy.
2. `~/.dsh-mcp-gateway/start.sh` (or `start.ps1`) — boots host, rotates tunnel,
   publishes upstream to the Worker (one-time setup:
   [cloudflare.md](docs/en/cloudflare.md)), restarts the gateway.
3. ChatGPT connector URL is permanent: `https://dsh-gw.<you>.workers.dev/mcp`.
   Re-auth = owner PIN from `env.conf`.
4. In ChatGPT: `workspace_switch` moves the project root (limited to the
   allowed subtree, default `~/data/workspace`),
   `subagent_devin` delegates to Devin (pinned `swe-2-high`), file/shell tools
   work inside the sandbox boundary.

## Workspace

ChatGPT operates on a **switchable workspace root**, not your whole disk.
`~` below means your OS home directory (`C:\Users\<you>` on Windows).

- **`WORKSPACE_ALLOWED_ROOT` (env) — the allowed root.** Default `~/data/workspace`;
  set it in `env.conf` to relocate the whole switchable boundary. The
  resolved root must exist — a missing directory (default included) fails
  the launch with a fix-it hint. `workspace_switch` may only retarget
  inside this subtree — realpath-checked, symlink escapes rejected;
  switching to the root itself widens the boundary to the whole subtree.
- **`WORKSPACE` (env var) — the fallback root.** Default:
  `<WORKSPACE_ALLOWED_ROOT>/default` (`~/data/workspace/default` out of the box —
  the launcher's working directory). Change via the `WORKSPACE` env var
  or a `WORKSPACE=<abs path>` line in `env.conf`. Conversations without a
  binding, and calls without an identity, run here; the directory is
  created automatically when missing, while an existing non-directory
  path fails the launch with a fix-it hint.
- **Persistence:** `workspace_switch` binds only the calling
  conversation — keyed by the `x-openai-session` header ChatGPT sends
  per conversation (`mcp-session-id` is the fallback) — and the
  bindings persist in `home/storages/chatgpt-bridge/session-workspaces.json`
  across restarts. Conversations that never switched, and calls without
  an identity, use the launch root (`WORKSPACE`). `workspace_status`
  reports the calling conversation's root.
- **Boundary:** sandboxed `bash`/`pwsh` writes stay inside the active
  root; the read/write/edit file tools follow their own access model.
  Wrong root after a restart? `workspace_switch` again — or delete
  `session-workspaces.json` for a full reset (see architecture.md,
  Operations).

## Credits

The MCP/OAuth gateway is vendored from
[siddhartha-yz/dsh-mcp-gateway](https://github.com/siddhartha-yz/dsh-mcp-gateway)
(MIT, upstream commit `faf9606`). Local deltas are pre-applied in
`dsh-mcp-gateway/src/` and recorded in `gateway-local-changes.patch`; the
upstream MIT license ships as `dsh-mcp-gateway/LICENSE`.
