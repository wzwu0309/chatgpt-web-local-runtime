# ChatGPT → DSH local bridge — architecture & playbook

English | [中文](../zh-CN/architecture.md)

ChatGPT Web is the **only reasoning agent**. DeepSeek Harness (DSH) runs locally
as the **execution authority**: tools, sandbox, sessions, skills, workspace.
This document captures the final working architecture, what every file does,
and every pitfall that was hit and fixed.

```
ChatGPT Web  (reasoning agent; dev-mode MCP connector)
   │  MCP Streamable HTTP + OAuth (PKCE, dynamic client registration)
   ▼
https://dsh-gw.<you>.workers.dev/mcp        ← FIXED URL, never changes
   │  Cloudflare Worker façade (KV stores current upstream)
   ▼
https://<random>.trycloudflare.com          ← rotating quick tunnel, invisible
   │  cloudflared --protocol http2  (persistent outbound conn to CF edge)
   ▼
dsh-mcp-gateway  :8766  (Python/FastAPI)
   OAuth verify → MCP adapt → forward HTTP to harness bridge
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
| **ToolRuntime tool** | DSH-internal capability unit registered on `ctx.tools` (`bash`, `read`, `subagent_devin`…). |
| **capability profile** | Explicit DSH-side allowlist of ToolRuntime tools exposed to the external agent. Ours: `chatgpt-external-v1` (`dsh-bridge-plugin/chatgpt-capability-profile.js`). |
| **`allowExtraTools`** | Bridge config list that adds reviewed tools into the profile. Reserved AgentLoop-lifecycle names (`subagent`, `todo_write`…) are rejected by the profile itself. |
| **meta-tools** | The only real MCP tools ChatGPT sees: `dsh_tool_catalog` (discover), `dsh_tool_call` (invoke), plus `dsh_skill_catalog`/`dsh_skill_load`. DSH tools are *projected* through them, never first-class MCP tools. Meta-only is deliberate: ChatGPT caches MCP tool lists as snapshots, so runtime discovery is the correctness path. |
| **helper capability agent** | Execution-only DSH agent (no loop, no LLM) the bridge spawns per workspace. Its session `cwd` anchors bash cwd, fs roots, and the sandbox boundary. Sessions persist and are keyed by `capabilitySessionId(cwd, preset, fingerprint)`. |
| **Worker façade** | Tiny Cloudflare Worker: `PUT /_upstream` (Bearer secret) stores the current tunnel URL in KV; everything else is proxied verbatim. Gives ChatGPT a permanent URL while the tunnel rotates. |

## Component inventory

| Piece | Source | File(s) |
|---|---|---|
| DSH runtime | `npm i @deepseek-ai/dsh@0.1.2-rc.1` into `deploy/dsh-runtime` | `dsh-mcp-gateway/deploy/dsh-runtime/package.json` |
| DSH profile | `home/profiles/web` = `DSH_HOME/profiles/web` — own cordis.yml/patch/package.json, independent of `~/.dsh` | bundles `dsh-base` + `dsh-web-app` (+ optional `@quonaro/dsh-plugin-devin`) |
| Bridge patch | ours | `dsh-mcp-gateway/deploy/dsh/chatgpt-bridge.{macos,windows}.cordis.yml` (launcher substitutes `<DSH_GW_HOME>` → `*.resolved`) |
| Bridge plugin | ours (modified upstream) | `dsh-mcp-gateway/dsh-bridge-plugin/` |
| Task-state plugin | upstream, unmodified | `dsh-mcp-gateway/dsh-task-state-plugin/` |
| Shell-session plugin | upstream, unmodified (POSIX bash only) | `dsh-mcp-gateway/dsh-shell-session-plugin/` |
| Remote-worker plugin | upstream, unmodified (inert without pollers) | `dsh-mcp-gateway/dsh-remote-worker-plugin/` |
| MCP gateway | vendored from `siddhartha-yz/dsh-mcp-gateway`@`faf9606` (MIT), our patch applied | `dsh-mcp-gateway/pyproject.toml` + `src/` — deltas recorded in `gateway-local-changes.patch` |
| Worker façade | ours | `dsh-mcp-gateway/worker/_worker.js` (dashboard setup: [cloudflare.md](cloudflare.md)) |
| Installer | ours | `install.sh` / `install.ps1` — copies `dsh-mcp-gateway/` to the deployment home, then venv + npm + env.conf seed; idempotent |
| Launcher | ours | `dsh-mcp-gateway/start.sh` / `start.ps1` (self-locating — payload dir = deployment home) |
| Config | ours, secrets | `env.conf` (from `env.conf.example`, seeded by the installer) |

### What the cordis patch inserts

- `chatgpt-task-state` — passive task records for ChatGPT (no loops).
- `chatgpt-remote-workers` — remote worker edge (disabled on win32, inert w/o pollers).
- `chatgpt-persistent-shell` group — terminal registry + `dsh-terminal-bash` backend + `shell_session` tool (persistent bash via PTY; POSIX only).
- `tool-subagent-devin` — `dsh-tool-subagent` on `spawn` provider with `agentOptions: {provider: devin, model: swe-2-high}`: an in-process DSH child loop whose model is pinned. `modelSelection` defaults false → schema exposes only `{description, prompt}`; callers cannot choose a model. **Optional & env-gated** (`disabled: !!js process.env.DSH_DEVIN !== '1'`) — enable via `devin/`.
- `chatgpt-harness-bridge` — the HTTP bridge itself + `allowExtraTools`.

### Gateway source changes (`gateway-local-changes.patch`)

1. `cli.py`: repeatable `--allowed-host HOST` — added to the DNS-rebinding allowlist. Required because the Worker fronts the gateway with the *tunnel* hostname, which differs from `--public-base-url`. (Upstream may merge this; re-diff before assuming.)
2. `__init__.py`: prompt-layer hardening — the MCP tool descriptions now name exact required params (`file_path` not `path` for read/write/edit; `command`+`description` for bash; `pattern` for glob/grep) and `dsh_tool_catalog` returns a `callContract` hint. This killed the "guessed wrong argument name" class of failures.
3. `oauth.py`: `/proc/self/fd/<fd>` is Linux-only — falls back to `file:<path>?mode=rw` on platforms without it (needed on macOS **and** Windows).

## Workspace model

- `WORKSPACE` (env, default `~/data/workspace`) is the DSH host's launch
  cwd — the initial workspace root when no persisted selection exists. It
  is **not** bound by the allowed root (operator's own entry point); but
  once `workspace_switch` moves away from an out-of-bounds `WORKSPACE`,
  there is no way back through the tool.
- `workspace_switch {path}` / `workspace_status` — DSH tools registered by the bridge plugin, persisted to `$DSH_HOME/storages/chatgpt-bridge/active-workspace.json` (survives restarts; takes precedence over `WORKSPACE` on later runs). Persisted selections are re-validated against the allowed subtree on load — an out-of-bounds or missing stored path is discarded and the launch cwd is used instead.
- `WORKSPACE_ALLOWED_ROOT` (env, default `~/data`) — the subtree `workspace_switch` may target. The launcher forwards it to the DSH host; both the launcher and the plugin refuse to start if the resolved root (default included) is missing, telling the operator to create it or reconfigure (realpath-checked, symlink-escape rejected). Switching to the root itself is allowed → whole subtree becomes the bash-writable root.
- Switching does **not** mutate a session (DSH `cwd` is creation metadata); it retires the old helper and lazily creates/reuses a helper session keyed by the new cwd. Sandbox boundary = helper session `cwd`.

## Security model

- Public path is authenticated end-to-end: OAuth dynamic registration + PKCE, owner PIN at authorize time, Bearer token on `/mcp`. Tunnel URL leakage alone grants nothing.
- `bash`/`pwsh` runs **confined** (`workspace-write`): writes allowed under the active workspace root (+ OS temp). Seatbelt on macOS, `windows-acl` (partial) on Windows, bwrap on Linux.
- `read`/`write`/`edit` file tools follow their own access model — they are **not** wrapped in the shell sandbox and can write outside the workspace.
- `sandbox_permissions` escalation exists in the tool schema but is **unusable here**: `approval.request()` must run inside an agent turn; bridge helpers never run turns → `approval.request() outside an open turn` error. Not a bug to chase.
- Worker sees plaintext (TLS terminates at CF edge); OAuth+PIN is the inner layer for that reason.

## Pitfalls hit (each is a load-bearing detail)

1. **421 Invalid Host** — Worker→tunnel requests carry the tunnel hostname; gateway DNS-rebinding protection allowed only `workers.dev`. Fix: `--allowed-host <tunnel-host>` per launch (start.sh derives it).
2. **530 / tunnel death** — network throttles UDP; QUIC tunnels die in ~10 min. Fix: `cloudflared --protocol http2` (TCP 7844).
3. **Binary `/tmp/cloudflared.log`** — killed writer left NULs; `grep` reported "Binary file matches". Fix: truncate log before launch + `grep -ao`.
4. **`workers.dev` unreachable direct** — this network blocks `*.workers.dev`; outbound calls (`PUT /_upstream`, health check) go via `PROXY`. Only affects start.sh's own curls — ChatGPT reaches the Worker from the US directly.
5. **`--allowed-host` / OAuth SQLite** — gateway needed source patches; they ship pre-applied in the vendored `dsh-mcp-gateway/src/` (editable install → edits take effect). `gateway-local-changes.patch` is retained as the delta record vs upstream.
6. **Wrong-argument-name failures** masquerade as "no permission" — fixed in the prompt layer (callContract).
7. **`additionalWritableRoots` silently ignored** — deployed runtime `0.1.2-rc.1` predates the config field; schemastery strips unknown keys. Use `workspace_switch` (incl. to `~/data`) instead. Re-add when runtime upgrades.
8. **Session-pinned `sandboxMode`** — helper sessions keep the mode recorded at creation; deployment-level `mode` changes don't retro-apply. Workspace root still resolves live from `session.header.cwd`.
9. **Host restart window = "continuous internal errors"** — while `:3081` is down every `/call` ECONNREFUSEDs. If ChatGPT reports this, check host liveness first (`/api/chatgpt-bridge/revision`).

## Deployment

Platform-specific install + launch lives in the per-OS guides:

- **macOS** → [macos.md](macos.md) — `start.sh`, seatbelt, `bash`
- **Windows** → [windows.md](windows.md) — `start.ps1`, `windows-acl`, `pwsh`

The `dsh-mcp-gateway/` payload is the deployment-home content;
`install.sh` / `install.ps1` copies it to `~/.dsh-mcp-gateway`
(`--target`/`-Target` to override), then performs the standard install: venv
+ editable gateway (vendored upstream @faf9606, patch pre-applied) → npm
runtime → `env.conf` seed. The installer is idempotent — code dirs refresh
(with deletions), while `env.conf`, `state/`, `home/profiles/web/*` and other
user-owned files are never overwritten. Then: deploy `worker/_worker.js` —
step-by-step in [cloudflare.md](cloudflare.md) — fill `env.conf`, run the launcher, and
point the connector at `$WORKER_PUBLIC_URL/mcp`. To re-sync the vendored
gateway with upstream: diff upstream `src/` against `dsh-mcp-gateway/src/`,
re-apply the deltas in `gateway-local-changes.patch`, and bump the commit
noted in `pyproject.toml`.

**Network requirement (launcher side only):** the machine running the
launcher must reach the Worker — it PUTs each fresh tunnel URL to
`/_upstream` and health-checks it. Where `*.workers.dev` is unreachable
(e.g. mainland China — the domain is DNS-poisoned and SNI-filtered), run a
local HTTP(S) proxy and set `PROXY=` in env.conf. ChatGPT reaches the Worker
from outside your network, so the product path is unaffected either way.
Attaching a custom domain to the Worker (domain you own, hosted on
Cloudflare) removes the proxy need but is optional.

## Operations (platform-neutral)

- The launcher (`start.sh` / `start.ps1`) is idempotent: it
  reuses a live `:3081` host, kills only processes matching our ports,
  truncates the tunnel log, allocates a fresh quick tunnel, derives
  `--allowed-host` from the new hostname, PUTs it to the Worker, and restarts
  the gateway.
- ChatGPT connector URL **never changes**:
  `https://dsh-gw.<you>.workers.dev/mcp`. Re-auth uses the owner PIN.
- Health probe: `GET /api/chatgpt-bridge/revision` → `{workspace,
  sandboxPolicy, toolRevision, …}`.
- Logs live in the launcher temp dir (`/tmp` on macOS, `$env:TEMP` on Windows):
  `dsh-web-host.log`, `cloudflared.log`, `dsh-mcp-gateway.log`.
- The tunnel URL lives as long as the cloudflared process does — if it dies,
  the Worker keeps pointing at a dead upstream until the launcher publishes a
  fresh one.
- Full reset of runtime state: stop the host, delete
  `$DSH_HOME/storages/chatgpt-bridge/active-workspace.json` and
  `$DSH_HOME/storages/session_projcache` if helper sessions misbehave; they
  rebuild lazily.

## Alternative route evaluated: a purpose-built MCP server (CodexPro)

[CodexPro](https://github.com/rebel0789/codexpro) is the cleanest reference
implementation of the other design: a single Node process that **is** the MCP
server and **owns** the tool runtime.

```
ChatGPT ──MCP──> codexpro (Express + official MCP SDK, /mcp) ──> in-process tool fns
```

| | CodexPro | this bridge |
|---|---|---|
| Tool surface | first-class MCP tools, zod schemas in the tool list | meta-tools only; DSH tools projected through `dsh_tool_catalog`/`dsh_tool_call` |
| Tool runtime | own code (`bashOps`/`fsOps`/`searchOps`/`analysis`) — no Codex code despite the name | DSH ToolRuntime via helper capability agents |
| Auth | static bearer / `?codexpro_token=` query (≥24 bytes) | OAuth PKCE + dynamic registration + owner PIN |
| Confinement | `PathGuard` (allowedRoots + blocked globs) + safe-bash allowlist; no OS sandbox | seatbelt / windows-acl / bwrap `workspace-write` |
| Workspace | `open_workspace` selects inside allowedRoots; per-call `workspace_id`; per-MCP-session, not persisted | `workspace_switch`; persisted; sandbox anchored to helper-session cwd |
| Sessions/agents | no agent or exec sessions — each call is stateless; `CODEXPRO_BASH_SESSION_ID` is a shared-secret guard, not a shell | durable helper sessions, jobs, skills, `shell_session`, `subagent_devin` |
| "Codex" features | reads `~/.codex/{sessions,archived_sessions}/*.jsonl`; `handoff_to_codex` drops a plan file for an external codex — file conventions, not an embedded agent | `subagent_devin` spawns a real in-process child loop |

First-class tools are genuinely cleaner: no catalog→call hop, no
`callContract` prompt patch, and pitfalls 6–8 above (arg-name
mismatches, stripped config keys, session-pinned `sandboxMode`) — plus
the unusable `sandbox_permissions` escalation — are a class that only
exists when a general harness is adapted to MCP.

**Drop-in compatibility.** The Worker façade + quick-tunnel layer is
backend-agnostic: `codexpro start --tunnel none --root ~/data --port 8787`
could replace gateway+host without touching `_worker.js` —
`?codexpro_token=` and `Authorization` survive the verbatim proxy, the
`/mcp` path is identical, and codexpro has no Host-header check (no
421-class pitfall). Local processes drop from three to two. What goes
with them: OAuth degrades to a URL-borne static token, the OS sandbox
is replaced by path checks, and the entire DSH capability set (skills,
jobs, persistent shells, subagent delegation) disappears.

The bridge exists because those capabilities are the point — ChatGPT
gets the same sandboxed, session-aware execution authority a local DSH
agent gets. If a deployment only needs read/write/edit/bash inside
allowed roots, the single-process route is strictly simpler; evaluate
which side of that line the actual usage sits on.
