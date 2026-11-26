# chatgpt-web-local-runtime

English | [中文](README.zh-CN.md)

This project connects a **Web Chat Agent** to a local DeepSeek Harness
(DSH). The agent reasons and decides what to do; DSH executes locally.
ChatGPT Web is the primary use case. Gemini and other chat agents can use
the same bridge through a client that supports remote MCP.

```
Web Chat Agent (ChatGPT Web, Gemini, ...) ──MCP+OAuth──> workers.dev façade ──> rotating quick tunnel
        ──> local gateway :8766 ──> local DSH host :3081 ──> DSH tools/sandbox
```

MCP connectors need **stable URLs**, but the quick tunnel rotates on each
launch. The Cloudflare Worker holds a permanent
`https://dsh-gw.<you>.workers.dev` origin and stores the active tunnel URL
in KV. The launcher publishes it on every run (`PUT /_upstream`, protected
by a Bearer secret). ChatGPT uses `/chatgpt/mcp`; Gemini can use
`/gemini/mcp` or the canonical generic mount, `/mcp`. The free tier is
sufficient; see the one-time [Cloudflare setup](docs/en/cloudflare.md).

Read **[docs/en/architecture.md](docs/en/architecture.md)** first — it contains the
full component inventory, the security model, and every operational caveat.
Then deploy via your platform guide:
**[macOS](docs/en/macos.md)** or **[Windows](docs/en/windows.md)**.

**Why a whole harness behind one connector:** the bridge deliberately
projects a reviewed set of DSH capabilities (sandboxed bash, sessions, skills,
`subagent_devin`) instead of shipping a purpose-built tool server like
[CodexPro](https://github.com/rebel0789/codexpro) — one Node process,
first-class MCP tools, static token, no sandbox or sessions. The
trade-offs and backend substitution are analyzed in
[architecture.md](docs/en/architecture.md#alternative-route-evaluated-a-purpose-built-mcp-server-codexpro).

## What this adds on top of upstream

The vendored gateway is only the protocol adapter; running it as a
**permanent** connector is the part this repo owns:

- **A fixed public URL.** `worker/_worker.js` is a tiny Cloudflare Worker
  façade holding the connector origin; the launcher republishes the
  rotating quick-tunnel URL to it on every run (`PUT /_upstream`,
  protected by a Bearer secret), so connector URLs stay stable as tunnels rotate.
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
  `chatgpt-external-v1` capability profile. The gateway's local extensions
  include mount-specific exposures, path-aware OAuth, `callContract`
  hints, and cross-platform OAuth SQLite support — details in
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
  src/dsh_mcp_gateway/       vendored Python gateway source + applied local extensions
  LICENSE                    the gateway's MIT license
  gateway-local-changes.patch   historical record of early changes (current state lives in src/)
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
3. Use the connector URL for your client:
   - ChatGPT: `https://dsh-gw.<you>.workers.dev/chatgpt/mcp`
   - Gemini: `https://dsh-gw.<you>.workers.dev/gemini/mcp`, or the generic URL below
   - Generic MCP clients: `https://dsh-gw.<you>.workers.dev/mcp`

   Complete OAuth with the owner PIN from `env.conf`.
4. In ChatGPT, use `workspace_switch` to bind a project to the conversation.
   On the generic mounts, select a project per call with
   `dsh_tool_call.workspace`. If enabled, `subagent_devin` delegates to
   Devin (pinned to `swe-2-high`).

**Gemini client limit (observed).** On 2026-11-26T04:53:26Z, Gemini client
`1.147.6.1557 (1.147.6.1557)` limited MCP tool accesses within one
conversation. This is not a bridge or per-tool quota; it is not documented
officially, and the exact threshold/error text was not captured. Avoid
repetitive, chatty tool use. See the operational caveats in
[architecture.md](docs/en/architecture.md).

## Workspace

Clients work in a selected **workspace**, not the whole disk by default.
`~` means your OS home directory (`C:\Users\<you>` on Windows).

- **`WORKSPACE_ALLOWED_ROOT` (env):** the allowed root, defaulting to
  `~/data/workspace`. It must exist at startup. Explicit workspace targets
  on either exposure must resolve within this subtree (including the root
  itself); symlink escapes are rejected.
- **`WORKSPACE` (env):** the launch and fallback root, defaulting to
  `<WORKSPACE_ALLOWED_ROOT>/default`. The launcher creates it if missing
  and fails if the path is an existing non-directory. It can be outside
  the allowed root because it is set by the operator, but clients cannot
  explicitly route to it in that case. Unbound ChatGPT calls and generic
  calls without `workspace` use this root.
- **ChatGPT exposure (`/chatgpt/mcp`):** `workspace_switch` binds only the
  calling conversation, and `workspace_status` reports its workspace.
  Bindings use `x-openai-session` and persist in
  `home/storages/chatgpt-bridge/session-workspaces.json`. If the header
  is missing, `workspace_switch` fails instead of changing the shared default.
- **Generic exposure (`/mcp`, `/gemini/mcp`):** pass the optional top-level
  `workspace` to `dsh_tool_call`; repeat it on later calls to use the same
  project. It is a routing field, not part of `arguments` or the tool-level
  `workdir`. The generic exposure does not offer `workspace_switch`,
  `workspace_status`, or `task_state`.
- **Sandbox boundary:** sandboxed `bash`/`pwsh` writes are confined to the
  active root plus OS temporary directories. The `read`/`write`/`edit`
  tools follow their own access model. Routing selects a helper session
  keyed by the new cwd; it does not change existing DSH sessions.

## Credits

The MCP/OAuth gateway is vendored from
[siddhartha-yz/dsh-mcp-gateway](https://github.com/siddhartha-yz/dsh-mcp-gateway)
(MIT, upstream commit `faf9606`). Current modifications ship in
`dsh-mcp-gateway/src/`; `gateway-local-changes.patch` is a historical
record of the early changes, not a current delta. The upstream MIT
license ships as `dsh-mcp-gateway/LICENSE`.
