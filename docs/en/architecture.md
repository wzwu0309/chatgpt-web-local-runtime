# Web Chat Agent → DSH local bridge — architecture and operations

English | [中文](../zh-CN/architecture.md)

A **Web Chat Agent** interprets the user's request, reasons about it, and
chooses when to call tools. ChatGPT Web is the primary use case, and
Gemini is another example. The agent has the same role in a browser or
in a desktop chat app; access requires a client that supports remote MCP.
DeepSeek Harness (DSH) is the local execution authority for tools, sandboxing,
skills, and workspaces. Normal use does not require a second reasoning model.
This guide covers the architecture, components, deployment, and operations.

```
Web Chat Agent (ChatGPT Web, Gemini, ...)
   │  MCP Streamable HTTP + OAuth (PKCE, dynamic client registration)
   ▼
https://dsh-gw.<you>.workers.dev            ← fixed Worker origin / Cloudflare Worker façade
   ├─ /chatgpt/mcp  ChatGPT exposure
   ├─ /gemini/mcp   generic mount named for Gemini
   └─ /mcp          canonical generic mount
   │  paths proxied verbatim; KV stores the current upstream
   ▼
https://<random>.trycloudflare.com          ← rotating quick tunnel, invisible
   │  cloudflared --protocol http2  (persistent outbound conn to CF edge)
   ▼
dsh-mcp-gateway  :8766  (Python/Starlette)
   OAuth verification → exposure selection by mount → MCP adapter → harness bridge
   │  /api/chatgpt-bridge/{revision,tools,call}
   ▼
dsh --profile web  :3081  (Node; DSH_HOME=~/.dsh-mcp-gateway/home)
   dsh-bridge-plugin → ctx.tools.execute via helper capability agent
   ▼
DSH ToolRuntime → bash/pwsh, read/write/edit, glob/grep, jobs, web,
                  workspace_switch/status, subagent_devin, shell_session…
```

## Terminology

| Term | Meaning |
|---|---|
| **Web Chat Agent** | The user-facing reasoning role that decides when to delegate local work through MCP. Distinct from DSH's execution-only helper agent. |
| **ToolRuntime tool** | DSH-internal capability unit registered on `ctx.tools` (`bash`, `read`, `subagent_devin`…). |
| **capability profile** | Explicit DSH-side allowlist of ToolRuntime tools exposed to the external agent. Ours: `chatgpt-external-v1` (`dsh-bridge-plugin/chatgpt-capability-profile.js`). |
| **`allowExtraTools`** | Bridge config list that adds reviewed tools into the profile. Reserved AgentLoop-lifecycle names (`subagent`, `todo_write`…) are rejected by the profile itself. |
| **meta-tools** | The stable MCP tools clients see: `dsh_tool_catalog` (discover), `dsh_tool_call` (invoke), plus `dsh_skill_catalog`/`dsh_skill_load`. DSH tools are *projected* through them; first-class projections are an explicit opt-in. Meta-only is deliberate: clients may cache MCP tool lists as snapshots, so runtime discovery is the correctness path. |
| **mount / exposure** | A mount is the MCP URL path; its exposure defines the client-facing contract. The two generic mounts share one exposure. |
| **helper capability agent** | Execution-only DSH agent (no loop, no LLM) the bridge spawns per workspace. Its session `cwd` anchors bash cwd, fs roots, and the sandbox boundary. Sessions persist and are keyed by `capabilitySessionId(cwd, preset, fingerprint)`. |
| **Worker façade** | Tiny Cloudflare Worker: `PUT /_upstream` (Bearer secret) stores the current tunnel URL in KV; everything else is proxied verbatim. Gives clients a permanent URL while the tunnel rotates. |

## Component inventory

| Piece | Source | File(s) |
|---|---|---|
| DSH runtime | `npm i @deepseek-ai/dsh@0.2.2-rc.5` into `deploy/dsh-runtime` | `dsh-mcp-gateway/deploy/dsh-runtime/package.json` |
| DSH profile | `home/profiles/web` = `DSH_HOME/profiles/web` — own cordis.yml/patch/package.json, independent of `~/.dsh` | bundles `dsh-base` + `dsh-web-app` (+ optional `@quonaro/dsh-plugin-devin`) |
| Bridge patch | ours | `dsh-mcp-gateway/deploy/dsh/chatgpt-bridge.{macos,windows}.cordis.yml` (launcher substitutes `<DSH_GW_HOME>` → `*.resolved`) |
| Bridge plugin | ours (modified upstream) | `dsh-mcp-gateway/dsh-bridge-plugin/` |
| Task-state plugin | upstream, unmodified | `dsh-mcp-gateway/dsh-task-state-plugin/` |
| Shell-session plugin | upstream, unmodified (POSIX bash only) | `dsh-mcp-gateway/dsh-shell-session-plugin/` |
| Remote-worker plugin | upstream, unmodified (inert without pollers) | `dsh-mcp-gateway/dsh-remote-worker-plugin/` |
| MCP gateway | vendored from `siddhartha-yz/dsh-mcp-gateway`@`faf9606` (MIT), local extensions applied | `dsh-mcp-gateway/pyproject.toml` + `src/` — `gateway-local-changes.patch` is a historical record only |
| Worker façade | ours | `dsh-mcp-gateway/worker/_worker.js` (dashboard setup: [cloudflare.md](cloudflare.md)) |
| Installer | ours | `install.sh` / `install.ps1` — copies `dsh-mcp-gateway/` to the deployment home, then venv + npm + env.conf seed; idempotent |
| Launcher | ours | `dsh-mcp-gateway/start.sh` / `start.ps1` (self-locating — payload dir = deployment home) |
| Config | ours, secrets | `env.conf` (from `env.conf.example`, seeded by the installer) |

### What the cordis patch inserts

- `chatgpt-task-state` — passive task records for ChatGPT (no loops).
- `chatgpt-remote-workers` — remote worker edge (disabled on win32, inert w/o pollers).
- `chatgpt-persistent-shell` group — terminal registry + `dsh-terminal-bash` backend + `shell_session` tool (persistent bash via PTY; POSIX only).
- `tool-subagent-devin` — `dsh-tool-subagent` on `spawn` provider with `agentOptions: {provider: devin, model: swe-2-high}`: an in-process DSH child loop whose model is pinned. `modelSelection` defaults false → schema exposes only `{description, prompt}`; callers cannot choose a model. **Optional & env-gated** (`disabled: !!js process.env.DSH_SUBAGENT_DEVIN !== '1'`) — enable via `devin/`.
- `chatgpt-harness-bridge` — the HTTP bridge itself + `allowExtraTools`.

### Local gateway extensions

1. **Mount-specific exposures.** The three mounts share OAuth and the
   harness bridge. The exposure controls the catalog projection, the
   `workspace` envelope, and whether conversation identity is forwarded.
   Execution, workspace validation, and capability policy stay in the bridge.
2. **Path-aware OAuth.** Authorization-server metadata supports path
   suffixes; protected-resource metadata and `WWW-Authenticate` point to
   the actual mount. DCR registrations that were never authorized are
   pruned after seven days; authorized registrations are retained. Refresh
   tokens have a 180-day lifetime.
3. **Transport and platform compatibility.** Repeatable `--allowed-host`
   entries account for the Worker-to-tunnel hostname. OAuth SQLite falls
   back to `file:<path>?mode=rw` on platforms without `/proc/self/fd`.
4. **Harness integration.** `callContract` spells out required tool
   arguments. The gateway's harness adapter also handles skills, opt-in
   first-class projection, and call-result validation.

`gateway-local-changes.patch` is a historical record of the early vendored
changes. Use `src/` and the tests as the current reference, not the patch.

## Workspace model

- `WORKSPACE_ALLOWED_ROOT` defaults to `~/data/workspace`. The launcher
  and bridge require it to exist. Explicit workspace targets must resolve
  inside it (including the root itself); symlink escapes are rejected.
- `WORKSPACE` is the DSH host's launch directory and fallback workspace.
  It defaults to `<WORKSPACE_ALLOWED_ROOT>/default`. The launcher creates
  the directory if missing and fails if the path exists but is not a
  directory. This operator-controlled root may be outside the allowed
  subtree: calls can still use it by default, but clients cannot select
  it explicitly with `workspace_switch` or `workspace`.
- The bridge checks an explicit `workspace` envelope first, then a
  conversation binding if applicable, then `WORKSPACE`. Public mounts do
  not mix the two routing modes. An explicit workspace is a per-call
  routing field, not a target tool argument or a binding. An empty or
  out-of-bounds value fails rather than falling back.
- Selecting a different root does not change the `cwd` of an existing
  DSH session. The bridge retires the old helper and lazily creates or
  reuses one keyed by the new cwd, which also anchors the shell sandbox.

## Mounts and exposures

| Mount | Exposure | Workspace routing | Conversation identity |
|---|---|---|---|
| `/chatgpt/mcp` | `ChatgptExposure` | Persistent conversation binding; includes the session tools | Uses only `x-openai-session`; no OAuth or transport fallback |
| `/mcp` | `GenericExposure` (canonical generic mount) | Optional `dsh_tool_call.workspace` per call; excludes the session tools | No conversation identity forwarded |
| `/gemini/mcp` | Same `GenericExposure` instance | Same as `/mcp` | Same as `/mcp` |

The **session tools** are `workspace_switch`, `workspace_status`, and
`task_state`. The first two set or report the bound workspace; the last
relies on stable conversation identity for cross-turn task context.

**ChatGPT exposure.** `workspace_switch {path}` binds only the calling
conversation. The gateway forwards the `x-openai-session` header value as
the session key; the bridge persists up to 256 bindings in
`$DSH_HOME/storages/chatgpt-bridge/session-workspaces.json`. Bindings
survive restarts; missing or out-of-bounds paths are discarded on load.
Without the header, the call cannot use a binding and `workspace_switch`
fails explicitly. Repeated missing identity is logged and reported as
degraded in `/healthz`.

**Generic exposure.** A Gemini custom app has been tested against `/mcp`
using DCR and PKCE; the gateway issues refresh tokens. Gemini can also
use `/gemini/mcp`, the named mount for the same exposure. This exposure
never reads or writes conversation bindings. To work on the same project
across calls, repeat the same `dsh_tool_call.workspace` value. Omitting
it uses `WORKSPACE`; `workspace` chooses the project, while a tool-level
`workdir` changes the directory for one command within that project.

**Discovery and execution share one capability policy.** The bridge
builds the catalog through the helper capability agent and `tools.resolve()`,
using the same admitted tool set as execution. The generic exposure only
filters out the session tools and rejects them at execution. The gateway
does not maintain a second allowlist.

## Security model

- Every public MCP mount requires an OAuth Bearer token. Onboarding uses dynamic client registration, PKCE, and owner PIN approval. The tunnel URL alone grants no access.
- `bash`/`pwsh` runs **confined** (`workspace-write`): writes allowed under the active workspace root (+ OS temp). Seatbelt on macOS, `windows-acl` (partial) on Windows, bwrap on Linux.
- `read`/`write`/`edit` file tools follow their own access model — they are **not** wrapped in the shell sandbox and can write outside the workspace.
- `sandbox_permissions` escalation exists in the tool schema but is **unusable here**: `approval.request()` must run inside an agent turn; bridge helpers never run turns → `approval.request() outside an open turn` error. Not a bug to chase.
- TLS terminates at Cloudflare; the Worker can see plaintext. The gateway still enforces OAuth on each MCP mount.

## Operational caveats (each is a load-bearing detail)

1. **421 Invalid Host** — Worker→tunnel requests carry the tunnel hostname; gateway DNS-rebinding protection allowed only `workers.dev`. Fix: `--allowed-host <tunnel-host>` per launch (start.sh derives it).
2. **530 / tunnel death** — network throttles UDP; QUIC tunnels die in ~10 min. Fix: `cloudflared --protocol http2` (TCP 7844).
3. **Binary `/tmp/cloudflared.log`** — killed writer left NULs; `grep` reported "Binary file matches". Fix: truncate log before launch + `grep -ao`.
4. **`workers.dev` unreachable direct** — this network blocks `*.workers.dev`; outbound calls (`PUT /_upstream`, health check) go via `PROXY`. Only affects start.sh's own curls — external MCP clients reach the Worker from outside your network.
5. **`--allowed-host` / OAuth SQLite** — gateway needed source patches; they ship pre-applied in the vendored `dsh-mcp-gateway/src/` (editable install → edits take effect). Do not assume `gateway-local-changes.patch` covers these or later changes.
6. **Wrong-argument-name failures** masquerade as "no permission" — fixed in the prompt layer (callContract).
7. **Reinstalling does not reload the DSH host** — Both launchers reuse a healthy host. After changing the bridge or its plugins, stop the existing host before relaunching. Gateway-only changes do not require a new tunnel.
8. **Session-pinned `sandboxMode`** — helper sessions keep the mode recorded at creation; deployment-level `mode` changes don't retro-apply. Workspace root still resolves live from `session.header.cwd`.
9. **Host restart window = "continuous internal errors"** — while `:3081` is down every `/call` ECONNREFUSEDs. If ChatGPT reports this, check host liveness first (`/api/chatgpt-bridge/revision`).
10. **Gemini per-conversation MCP tool-call cap (observed)** — on 2026-11-26T04:53:26Z, Gemini client `1.147.6.1557 (1.147.6.1557)` limited the number of MCP tool accesses in one conversation. The exact threshold and error text were not captured, and no official quota is documented. This affects the whole MCP tool surface; it is not a bridge or per-tool quota. Avoid splitting work into many repetitive calls, and troubleshoot a hit as a client-side limit before blaming the gateway or DSH.

## Deployment

Platform-specific install + launch lives in the per-OS guides:

- **macOS** → [macos.md](macos.md) — `start.sh`, seatbelt, `bash`
- **Windows** → [windows.md](windows.md) — `start.ps1`, `windows-acl`, `pwsh`

The `dsh-mcp-gateway/` payload is the deployment-home content;
`install.sh` / `install.ps1` copies it to `~/.dsh-mcp-gateway`
(`--target`/`-Target` to override), then performs the standard install: venv
+ editable gateway (vendored upstream @faf9606 with local extensions) → npm
runtime → `env.conf` seed. The installer is idempotent — code dirs refresh
(with deletions), while `env.conf`, `state/`, `home/profiles/web/*` and other
user-owned files are never overwritten. Then: deploy `worker/_worker.js` —
step-by-step in [cloudflare.md](cloudflare.md) — fill `env.conf`, run the launcher, and
point the connector at the mount for your client. Use
`$WORKER_PUBLIC_URL/chatgpt/mcp` for ChatGPT and `$WORKER_PUBLIC_URL/mcp`
for generic clients. Gemini can use the generic URL or
`$WORKER_PUBLIC_URL/gemini/mcp`. To re-sync the vendored
gateway with upstream: diff upstream `src/` against `dsh-mcp-gateway/src/`,
review how each local extension migrates, and update the pinned commit in
`pyproject.toml`; do not re-apply the historical
`gateway-local-changes.patch`.

**Network requirement (launcher side only):** the machine running the
launcher must reach the Worker — it PUTs each fresh tunnel URL to
`/_upstream` and health-checks it. Where `*.workers.dev` is unreachable
(e.g. mainland China — the domain is DNS-poisoned and SNI-filtered), run a
local HTTP(S) proxy and set `PROXY=` in env.conf. External MCP clients reach
the Worker from outside your network, so the product path is unaffected either way.
Attaching a custom domain to the Worker (domain you own, hosted on
Cloudflare) removes the proxy need but is optional.

## Operations (platform-neutral)

- The launcher (`start.sh` / `start.ps1`) is idempotent: it
  reuses a live `:3081` host, kills only processes matching our ports,
  truncates the tunnel log, allocates a fresh quick tunnel, derives
  `--allowed-host` from the new hostname, PUTs it to the Worker, and restarts
  the gateway.
- Connector URLs stay fixed as the tunnel rotates: use
  `https://dsh-gw.<you>.workers.dev/chatgpt/mcp` for ChatGPT and
  `https://dsh-gw.<you>.workers.dev/mcp` for generic clients. Gemini can
  use the generic URL or `https://dsh-gw.<you>.workers.dev/gemini/mcp`.
  Re-authentication uses the owner PIN.
- Health probe: `GET /api/chatgpt-bridge/revision` → `{workspace,
  sandboxPolicy, toolRevision, …}`.
- Logs live in the launcher temp dir (`/tmp` on macOS, `$env:TEMP` on Windows):
  `cloudflared.log` and `dsh-mcp-gateway.launch.log` are launch-level and
  truncated each start; `dsh-web-host.log` is truncated only when the host
  actually starts (a reused host keeps appending). `dsh-mcp-gateway.log` is
  the gateway execution log, a single file bounded to 8 MiB inside the
  process.
- The tunnel URL lives as long as the cloudflared process does — if it dies,
  the Worker keeps pointing at a dead upstream until the launcher publishes a
  fresh one.
- Full reset of runtime state: stop the host, delete
  `$DSH_HOME/storages/chatgpt-bridge/session-workspaces.json` and
  `$DSH_HOME/storages/session_projcache` if helper sessions misbehave; they
  rebuild lazily.

## Validation

Run these from the repository root against the local DSH bridge:

```bash
node validation/golden-path.mjs
node validation/exposure-golden-path.mjs \
  --state-file ~/.dsh-mcp-gateway/home/storages/chatgpt-bridge/session-workspaces.json
```

Both default to `http://127.0.0.1:3081/api/chatgpt-bridge`; override it
with `--base`. The first exercises the core tool, workspace-binding, and
sandbox behavior. The second compares binding and generic routing,
including rejection of out-of-bounds workspaces; `--state-file` enables
the check that generic calls do not write bindings. These probes exercise
the bridge, not the public MCP/OAuth path. After a runtime, bridge, or
exposure update, follow them with a client-level OAuth and tool-call smoke test.

## Alternative route evaluated: a purpose-built MCP server (CodexPro)

[CodexPro](https://github.com/rebel0789/codexpro) is the cleanest reference
implementation of the other design: a single Node process that **is** the MCP
server and **owns** the tool runtime.

```
ChatGPT ──MCP──> codexpro (Express + official MCP SDK, /mcp) ──> in-process tool fns
```

| | CodexPro | this bridge |
|---|---|---|
| Tool surface | first-class MCP tools, zod schemas in the tool list | Meta-tools by default; first-class projection is opt-in |
| Tool runtime | own code (`bashOps`/`fsOps`/`searchOps`/`analysis`) — no Codex code despite the name | DSH ToolRuntime via helper capability agents |
| Auth | static bearer / `?codexpro_token=` query (≥24 bytes) | OAuth PKCE + dynamic registration + owner PIN |
| Confinement | `PathGuard` (allowedRoots + blocked globs) + safe-bash allowlist; no OS sandbox | seatbelt / windows-acl / bwrap `workspace-write` |
| Workspace | `open_workspace` selects inside allowedRoots; per-call `workspace_id`; per-MCP-session, not persisted | ChatGPT conversation binding or generic per-call `workspace`; both fall back to launch `WORKSPACE`. The sandbox follows the helper session's `cwd`. |
| Sessions/agents | no agent or exec sessions — each call is stateless; `CODEXPRO_BASH_SESSION_ID` is a shared-secret guard, not a shell | durable helper sessions, jobs, skills, `shell_session`, `subagent_devin` |
| "Codex" features | reads `~/.codex/{sessions,archived_sessions}/*.jsonl`; `handoff_to_codex` drops a plan file for an external codex — file conventions, not an embedded agent | `subagent_devin` spawns a real in-process child loop |

First-class tools are genuinely cleaner: no catalog→call hop.
Call-contract prompts, session-pinned sandbox modes, and unavailable
sandbox escalation are trade-offs of adapting a general harness to MCP.

**Backend substitution.** The Worker and quick tunnel can be reused, but
a connector on `/chatgpt/mcp` must be reconfigured for CodexPro's `/mcp`
endpoint. `codexpro start --tunnel none --root ~/data --port 8787`
could replace gateway+host without touching `_worker.js` —
`?codexpro_token=` and `Authorization` survive the verbatim proxy, and
codexpro has no Host-header check (no 421-class caveat). Local
processes drop from three to two. What goes
with them: OAuth degrades to a URL-borne static token, the OS sandbox
is replaced by path checks, and the entire DSH capability set (skills,
jobs, persistent shells, subagent delegation) disappears.

The bridge exists because those capabilities are the point — ChatGPT
gets the same sandboxed, session-aware execution authority a local DSH
agent gets. If a deployment only needs read/write/edit/bash inside
allowed roots, the single-process route is strictly simpler; evaluate
which side of that line the actual usage sits on.
