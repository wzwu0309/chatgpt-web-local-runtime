# macOS deployment guide

English | [中文](../zh-CN/macos.md)

Platform layer for the architecture in [architecture.md](architecture.md).
`./install.sh` installs the `dsh-mcp-gateway/` payload as the deployment home
at `~/.dsh-mcp-gateway` (any target via `--target`; `start.sh` self-locates).

## Prerequisites

- **Node.js ≥ 24.9** + npm (the npm runtime's floor is 22.15 — required for
  `zlib.zstd`, which DSH session persistence uses)
- **Python 3.12** (`venv`, `pip`) for the gateway
- **cloudflared** — `brew install cloudflared` (native arm64/x86_64), or
  download the `cloudflared-darwin-*` binary from
  [cloudflared releases](https://github.com/cloudflare/cloudflared/releases).
  Either copy it to `bin/cloudflared` or point `CLOUDFLARED` in env.conf
  straight at the installed path (e.g. `$(brew --prefix)/bin/cloudflared`)
- **Outbound reachability to the Worker** — the launcher PUTs the tunnel URL
  and health-checks it. If your network can't reach `*.workers.dev` (e.g.
  mainland China), run a local HTTP(S) proxy and set `PROXY=` in env.conf.
- Optional Devin: `devin` CLI — see `devin/`

`@deepseek-ai/dsh` is public on npmjs.org — no `.npmrc` or private registry.

## Install

```bash
cd <repo>                      # wherever you keep the project

# 1) installer — copies dsh-mcp-gateway/ to the target, creates the venv,
#    installs the gateway (editable; upstream @faf9606 + our patch,
#    pre-applied) and the npm runtime, seeds env.conf.
#    Native modules: node-pty/koffi resolve from shipped prebuilds — no
#    toolchain needed on standard arches (allowScripts table is required).
#    Re-run any time to refresh code — it never overwrites env.conf,
#    state/, profile edits, or bin/cloudflared.
./install.sh                   # -> ~/.dsh-mcp-gateway   (--target DIR to change)

#    version note: the 0.1.2 pin predates sandbox-policy's
#    additionalWritableRoots — 0.2.2-rc.3 ships it; bump the pin and re-add
#    the sandbox-policy block the bridge patch comments about.

# 2) Worker façade — deploy worker/_worker.js in the Cloudflare dashboard;
#    step-by-step: docs/en/cloudflare.md
#    (KV namespace bound as UPSTREAM, secret ADMIN_SECRET
#    = UPSTREAM_ADMIN_SECRET in env.conf)

# 3) config + launch — from now on work inside the deployment home
cd ~/.dsh-mcp-gateway
$EDITOR env.conf               # fill secrets; point CLOUDFLARED at your
                               # binary or drop it at bin/cloudflared
./start.sh
```

Optional Devin: `devin/README.md` inside the deployment home — merge the
package snippet into `home/profiles/web/package.json`, append the patch
rows, set `DSH_SUBAGENT_DEVIN=1`, re-run `./start.sh`.

Point the ChatGPT dev-mode connector at `$WORKER_PUBLIC_URL/mcp` and complete
OAuth with the owner PIN.

## Launch & operate

- `./start.sh` is idempotent — safe to re-run. Per run it: kills only processes
  matching our ports (`pkill -f` patterns include `--url ...:8766` /
  `--dsh-harness-url ...:3081`), reuses a live host, truncates the binary-prone
  tunnel log, launches `cloudflared --protocol http2`, extracts the new
  `*.trycloudflare.com` URL, PUTs it to the Worker (`/_upstream`), and restarts
  the gateway with `--allowed-host <new-host>`.
- If your network blocks `*.workers.dev`, set `PROXY=` in env.conf — the
  launcher's own outbound curls (upstream PUT, health check) go through it.
  ChatGPT reaches the Worker from outside; unaffected.
- `env.conf` is `source`d, not exported — values the DSH host needs must be
  forwarded explicitly on the launch line (this is why `start.sh` passes
  `DSH_SUBAGENT_DEVIN="${DSH_SUBAGENT_DEVIN:-}"`).
- Logs: `/tmp/dsh-web-host.log`, `/tmp/cloudflared.log`,
  `/tmp/dsh-mcp-gateway.log`. Health: `GET /api/chatgpt-bridge/revision`.

## Platform notes

- **Shell tool: `bash`** — `dsh-bash-sandbox` + seatbelt, full confinement.
  `shell_session` (persistent bash over PTY) is available on POSIX.
- **Plugin paths**: cordis patch `name:` fields must be literal — the patch
  template uses `<DSH_GW_HOME>` placeholders; `start.sh` substitutes them
  into `deploy/dsh/chatgpt-bridge.macos.resolved.cordis.yml` before launch.
- **Workspace root**: `~/data` (the bridge resolves via `os.homedir()`).
- **Optional Devin**: `devin/` + `DSH_SUBAGENT_DEVIN=1`; set `devinPath`
  absolutely (e.g. `~/.local/bin/devin`) so PATH doesn't matter.
