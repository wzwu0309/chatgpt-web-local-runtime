# Repository working rules

## Design rules

- **No backward-compatibility code for historical implementations.** The AI
  consumers of these tools adapt to schema/behavior changes themselves; compat
  shims and dual-shape handling only cost maintainability. When a dependency
  contract changes, adapt to its new shape directly and delete the old path.
  Comments explaining design rationale are welcome — the rule is about code,
  not documentation.

## Verification

- `node validation/golden-path.mjs --base <bridge-url> --out <file>` — behavior
  probe suite; compare against `validation/baselines/` snapshots field-by-field
  (path prefixes and random UUIDs are environmental, everything else must match).
- Node-side changes: `node --check <file>` for syntax; behavior must be proven
  through the golden path or targeted probes, never assumed from mount logs
  (cordis shelves invalid plugin config silently).

## Key facts

- Production deploys are file copies into `~/.dsh-mcp-gateway/` (not symlinks);
  restart only the DSH host for harness changes — gateway/cloudflared/Worker
  are request-forwarding and recover on their own.
- npm installs need `--registry=https://registry.npmmirror.com` on this machine
  (registry.npmjs.org is unreachable).
- DSH upgrade history and rollback procedure: `docs/upgrade-to-dsh-0.2.md`.
- The architecture contract (product invariant, responsibility split) lives in
  the deployment at `~/.dsh-mcp-gateway/AGENTS.md`.
