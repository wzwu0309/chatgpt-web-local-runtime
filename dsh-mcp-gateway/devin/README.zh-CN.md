# 可选：Devin provider + `subagent_devin`

[English](README.md) | 中文

所有 Devin 相关内容集中在这里 —— 没有 Devin 账户的部署**完全不装**任何
Devin 代码、catalog 里也**不会出现**任何 Devin 工具。不用 Devin 就直接
无视本目录。下文路径均相对于部署目录（`~/.dsh-mcp-gateway`)。

启用后得到：

- `devin` LLM provider（经 `devin acp`，账户认证 —— 不需要 API key 环境变量）
- `devin` 委派工具 + 交互会话里的 `/devin` 命令
- 外部 MCP catalog 里的 `subagent_devin`：模型钉死在 `swe-2-high` 的进程内
  DSH 子 agent loop（模型在入口级固定，调用方无法覆盖；ChatGPT 与 generic
  exposure 共用同一能力画像）

## 启用步骤

1. 把 `profile-package.snippet.json` 合并进 `home/profiles/web/package.json`
   (dependencies + bundles 条目），然后在该 profile 目录里跑
   `pnpm install`（或 `npm install`)。
2. 把 `cordis.patch.devin.yml` 追加到 `home/profiles/web/cordis.patch.yml`,
   并把 `devinPath` 指向你的 Devin CLI 二进制。
3. 在 `env.conf` 里加 `DSH_SUBAGENT_DEVIN=1` —— bridge patch 的 `tool-subagent-devin`
   条目由它门控（`disabled: !!js process.env.DSH_SUBAGENT_DEVIN !== '1'`)。
4. 重跑 `start.sh` / `start.ps1`。`dsh_tool_catalog` 应能看到 `subagent_devin`。

只有在插件**装好之后**才设 `DSH_SUBAGENT_DEVIN=1` —— 没有 provider 时工具会注册但
每次调用都在执行期失败。

## 文件

- `profile-package.snippet.json` —— 要合并的依赖 + bundle 行
- `cordis.patch.devin.yml` —— 三条 profile patch 行
- 门控的 bridge 条目在 `deploy/dsh/chatgpt-bridge.*.cordis.yml`
  (`tool-subagent-devin`)，两个平台共用。
