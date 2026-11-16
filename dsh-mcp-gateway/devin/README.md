# Optional: Devin provider + `subagent_devin`

English | [中文](README.zh-CN.md)

Everything Devin-related lives here so a deployment without a Devin account
installs **zero** Devin code and exposes **zero** Devin tools. Skip this
directory entirely if you don't use Devin. All paths below are relative to
the deployment home (`~/.dsh-mcp-gateway`).

What enabling gives you:

- `devin` LLM provider (via `devin acp`, account auth — no API key env needed)
- `devin` delegation tool + `/devin` command in interactive sessions
- `subagent_devin` in the ChatGPT catalog: an in-process DSH child agent loop
  pinned to `swe-2-high` (model is entry-level; callers cannot override it)

## Enable

1. Merge `profile-package.snippet.json` into `home/profiles/web/package.json`
   (dependencies + bundles entry), then inside that profile dir:
   `pnpm install` (or `npm install`).
2. Append `cordis.patch.devin.yml` to `home/profiles/web/cordis.patch.yml`
   and set `devinPath` to your Devin CLI binary.
3. Add `DSH_SUBAGENT_DEVIN=1` to `env.conf` — the bridge patch's `tool-subagent-devin`
   entry is gated on it (`disabled: !!js process.env.DSH_SUBAGENT_DEVIN !== '1'`).
4. Re-run `start.sh` / `start.ps1`. `dsh_tool_catalog` should now list
   `subagent_devin`.

Set `DSH_SUBAGENT_DEVIN=1` only **after** the plugin is installed — without the provider
the tool registers but every call fails at execution.

## Files

- `profile-package.snippet.json` — dep + bundle lines to merge
- `cordis.patch.devin.yml` — the three profile patch rows
- The gated bridge entry lives in `deploy/dsh/chatgpt-bridge.*.cordis.yml`
  (`tool-subagent-devin`), shared by both platforms.
