# ChatGPT → DSH 本地桥接 —— 架构与运维手册

[English](../en/architecture.md) | 中文

ChatGPT Web 是**唯一的推理 agent**。DeepSeek Harness(DSH）在本地作为
**执行权威**运行：工具、沙箱、会话、技能、工作区。本文档记录最终可行
的架构、每个文件的作用，以及全部运维注意事项。

```
ChatGPT Web  (推理 agent；开发者模式 MCP 连接器)
   │  MCP Streamable HTTP + OAuth (PKCE, 动态客户端注册)
   ▼
https://dsh-gw.<you>.workers.dev/mcp        ← 固定 URL，永远不变
   │  Cloudflare Worker 门面（KV 里存当前上游地址）
   ▼
https://<random>.trycloudflare.com          ← 轮换的 quick tunnel，无感知
   │  cloudflared --protocol http2  (到 CF 边缘的持久出站连接)
   ▼
dsh-mcp-gateway  :8766  (Python/FastAPI)
   OAuth 校验 → MCP 协议适配 → 转发 HTTP 到 harness bridge
   │  /api/chatgpt-bridge/{revision,tools,call}
   ▼
dsh --profile web  :3081  (Node; DSH_HOME=~/.dsh-mcp-gateway/home)
   dsh-bridge-plugin → 经 helper capability agent 调 ctx.tools.execute
   ▼
DSH ToolRuntime → bash/pwsh, read/write/edit, glob/grep, jobs, web,
                  workspace_switch/status, subagent_devin, shell_session…
```

## 术语

| 术语 | 含义 |
|---|---|
| **ToolRuntime tool** | DSH 内部能力单元，注册在 `ctx.tools` 上（`bash`、`read`、`subagent_devin`…）。 |
| **capability profile** | DSH 侧给外部 agent 的显式工具白名单，不是全量 ToolRuntime 转储。我们的：`chatgpt-external-v1`(`dsh-bridge-plugin/chatgpt-capability-profile.js`)。 |
| **`allowExtraTools`** | bridge 配置项，把审查过的工具加进 profile。保留的 AgentLoop 生命周期名（`subagent`、`todo_write`…）会被 profile 自身拒绝。 |
| **meta-tools** | ChatGPT 实际看到的唯一 MCP 工具：`dsh_tool_catalog`（发现）、`dsh_tool_call`（调用），外加 `dsh_skill_catalog`/`dsh_skill_load`。DSH 工具经它们*投影*，不是一等 MCP 工具。meta-only 是刻意的：ChatGPT 把 MCP 工具列表当快照缓存，运行时动态发现才是正确性路径。 |
| **helper capability agent** | bridge 为每个工作区创建的执行专用 agent（不跑 loop、不调 LLM)。它的会话 `cwd` 锚定 bash cwd、文件根、沙箱边界。会话持久化，按 `capabilitySessionId(cwd, preset, fingerprint)` 分键。 |
| **Worker 门面** | 一个很小的 Cloudflare Worker:`PUT /_upstream`(Bearer secret）把当前 tunnel URL 写进 KV，其余请求原样转发。隧道轮换时给 ChatGPT 一个永久 URL。 |

## 组件清单

| 组件 | 来源 | 文件 |
|---|---|---|
| DSH runtime | `npm i @deepseek-ai/dsh@0.1.2-rc.1` 装进 `deploy/dsh-runtime` | `dsh-mcp-gateway/deploy/dsh-runtime/package.json` |
| DSH profile | `home/profiles/web` = `DSH_HOME/profiles/web` —— 自己的 cordis.yml/patch/package.json，与 `~/.dsh` 完全独立 | bundles `dsh-base` + `dsh-web-app`（+ 可选 `@quonaro/dsh-plugin-devin`) |
| Bridge patch | 自己写的 | `dsh-mcp-gateway/deploy/dsh/chatgpt-bridge.{macos,windows}.cordis.yml`（启动器替换 `<DSH_GW_HOME>` → `*.resolved`) |
| Bridge 插件 | 自己改的上游版本 | `dsh-mcp-gateway/dsh-bridge-plugin/` |
| Task-state 插件 | 上游未改 | `dsh-mcp-gateway/dsh-task-state-plugin/` |
| Shell-session 插件 | 上游未改（仅 POSIX bash) | `dsh-mcp-gateway/dsh-shell-session-plugin/` |
| Remote-worker 插件 | 上游未改（无轮询者时空转） | `dsh-mcp-gateway/dsh-remote-worker-plugin/` |
| MCP 网关 | vendored 自 `siddhartha-yz/dsh-mcp-gateway`@`faf9606`(MIT),patch 已应用 | `dsh-mcp-gateway/pyproject.toml` + `src/` —— delta 记录在 `gateway-local-changes.patch` |
| Worker 门面 | 自己写的 | `dsh-mcp-gateway/worker/_worker.js`（控制台配置: [cloudflare.md](cloudflare.md)) |
| 安装器 | 自己写的 | `install.sh` / `install.ps1` —— 复制 `dsh-mcp-gateway/` 到部署目录，然后 venv + npm + 生成 env.conf；幂等 |
| 启动器 | 自己写的 | `dsh-mcp-gateway/start.sh` / `start.ps1`（自定位 —— 载荷目录 = 部署目录） |
| 配置 | 自己写的，含密钥 | `env.conf`（安装器由 `env.conf.example` 生成） |

### cordis patch 插入了什么

- `chatgpt-task-state` —— 给 ChatGPT 的被动任务记录（不含 loop)。
- `chatgpt-remote-workers` —— 远程 worker 边缘（win32 禁用，无轮询者时空转）。
- `chatgpt-persistent-shell` 组 —— 终端注册表 + `dsh-terminal-bash` 后端 + `shell_session` 工具（PTY 持久 bash；仅 POSIX)。
- `tool-subagent-devin` —— `spawn` provider 上的 `dsh-tool-subagent`,`agentOptions: {provider: devin, model: swe-2-high}`：一个模型被钉死的进程内 DSH 子 loop。`modelSelection` 默认 false → schema 只暴露 `{description, prompt}`，调用方无法选模型。**可选且 env 门控**(`disabled: !!js process.env.DSH_SUBAGENT_DEVIN !== '1'`)—— 经 `devin/` 启用。
- `chatgpt-harness-bridge` —— HTTP bridge 本体 + `allowExtraTools`。

### 网关源码改动（`gateway-local-changes.patch`)

1. `cli.py`：可重复 `--allowed-host HOST` —— 加进 DNS-rebinding 白名单。必须加，因为 Worker 转发时 Host 头是 *tunnel* 域名，与 `--public-base-url` 不一致。（上游可能已合并；用前先重新 diff。)
2. `__init__.py`：提示层加固 —— MCP 工具描述点名了确切的必填参数（read/write/edit 是 `file_path` 不是 `path`;bash 是 `command`+`description`;glob/grep 是 `pattern`),`dsh_tool_catalog` 返回 `callContract` 提示。"参数名猜错"这一类故障因此绝迹。
3. `oauth.py`:`/proc/self/fd/<fd>` 是 Linux 专属 —— 没有它的平台回退到 `file:<path>?mode=rw`(macOS **和** Windows 都需要）。

## 工作区模型

- `WORKSPACE_ALLOWED_ROOT`(env，默认 `~/data/workspace`)—— `workspace_switch`
  可指向的子树。启动器透传给 DSH host；启动器与插件对解析后的根
  （含默认路径）做存在性校验，缺失时拒绝启动并提示"创建目录或改配"
  （realpath 校验，拒绝符号链接逃逸）。允许切到根本身 → 整个子树
  都成为 bash 可写根。
- `WORKSPACE`(env，默认 `<WORKSPACE_ALLOWED_ROOT>/default`，开箱即
  `~/data/workspace/default`)是 DSH host 的启动 cwd —— 无会话绑定调用的回落
  工作区根。目录缺失时启动器自动创建；同名位置已有非目录文件则启动
  失败。它**不受**允许根约束（运营者自有入口）；但
  `workspace_switch` 一旦从界外的 `WORKSPACE` 切走，就无法通过该
  工具切回。
- `workspace_switch {path}` / `workspace_status` —— bridge 插件注册的 DSH 工具。切换只绑定发起调用的 conversation:gateway 把 `x-openai-session`(ChatGPT 每会话头；其他客户端以 `mcp-session-id` 兜底）转发为 `sessionId`,bridge 的会话键 Map 持久化到 `$DSH_HOME/storages/chatgpt-bridge/session-workspaces.json` —— 并发 conversation 在允许子树内各自持根，重启不丢；无身份的调用共享启动根(`WORKSPACE` env)。持久条目加载时按允许子树重校验 —— 越界或不存在的路径被丢弃；Map 上限 256 条。无身份的 `workspace_switch` 只改进程内的默认值，不落盘。
- 切换**不**修改会话（DSH `cwd` 是创建期元数据）；它退役旧 helper，并按新 cwd 惰性创建/复用 helper 会话。沙箱边界 = helper 会话 `cwd`。

## 安全模型

- 公网路径端到端认证：OAuth 动态注册 + PKCE,authorize 时要 owner PIN,`/mcp` 走 Bearer token。光泄露 tunnel URL 什么都拿不到。
- `bash`/`pwsh` **受沙箱约束**(`workspace-write`)：只允许写当前工作区根（+ 系统临时目录）。macOS 用 Seatbelt,Windows 用 `windows-acl`（部分强制）,Linux 用 bwrap。
- `read`/`write`/`edit` 文件工具走自己的访问模型 —— **不**套 shell 沙箱，能写工作区外。
- `sandbox_permissions` 提权参数存在于工具 schema 但**在这里不可用**:`approval.request()` 必须发生在 agent turn 内；bridge helper 没有 turn → 报 `approval.request() outside an open turn`。不是 bug，不用追。
- Worker 能看到明文（TLS 在 CF 边缘终止）；内层 OAuth+PIN 就是为此存在。

## 运维注意事项（每一条都是承重点）

1. **421 Invalid Host** —— Worker→tunnel 的请求 Host 是 tunnel 域名；网关 DNS-rebinding 防护只认 `workers.dev`。修：每次启动带 `--allowed-host <tunnel-host>`(start.sh 自动取）。
2. **530 / 隧道死亡** —— 这条网络掐 UDP;QUIC 隧道 ~10 分钟就断。修：`cloudflared --protocol http2`(TCP 7844)。
3. **`/tmp/cloudflared.log` 变二进制** —— 被杀的写入进程留了 NUL 字节，`grep` 报 "Binary file matches"。修：启动前清空日志 + `grep -ao`。
4. **`workers.dev` 直连不通** —— 本机网络封 `*.workers.dev`；出站调用（`PUT /_upstream`、健康检查）走 `PROXY`。只影响 start.sh 自己的几条 curl——ChatGPT 从美国直连 Worker 无阻碍。
5. **`--allowed-host` / OAuth SQLite** —— 网关需要源码补丁；已预先打进 vendored 的 `dsh-mcp-gateway/src/`(editable 安装 → 改动即刻生效）。`gateway-local-changes.patch` 保留为对上游的 delta 记录。
6. **参数名错误伪装成"没权限"** —— 已在提示层修掉（callContract)。
7. **`additionalWritableRoots` 被静默忽略** —— 部署的 runtime `0.1.2-rc.1` 早于该配置字段；schemastery 会剥掉未知键。改用 `workspace_switch`（包括切到允许根本身)。runtime 升级后再加回来。
8. **会话钉死 `sandboxMode`** —— helper 会话保留创建时记录的 mode；部署级 `mode` 改动不回溯生效。但工作区根始终从 `session.header.cwd` 实时解析。
9. **host 重启窗口 = "连续内部错误"** —— `:3081` 挂掉期间每个 `/call` 都 ECONNREFUSED。ChatGPT 报这个时先查 host 存活（`/api/chatgpt-bridge/revision`)。

## 部署

平台相关的安装与启动在各平台指南里：

- **macOS** → [macos.md](macos.md) —— `start.sh`、seatbelt、`bash`
- **Windows** → [windows.md](windows.md) —— `start.ps1`、`windows-acl`、`pwsh`

`dsh-mcp-gateway/` 载荷就是部署目录的内容；`install.sh` / `install.ps1`
把它复制到 `~/.dsh-mcp-gateway`(`--target`/`-Target` 可改），然后执行
标准安装：venv + editable 网关（vendored 上游 @faf9606,patch 已应用）→
npm runtime → 生成 `env.conf`。安装器幂等 —— 代码目录全量刷新（含删除），
而 `env.conf`、`state/`、`home/profiles/web/*` 等用户文件永不被覆盖。
之后：部署 `worker/_worker.js` —— 分步见 [cloudflare.md](cloudflare.md) ——
填 `env.conf`、跑启动器，把连接器指向 `$WORKER_PUBLIC_URL/mcp`。要与
上游重同步 vendored 网关：将上游 `src/` 与 `dsh-mcp-gateway/src/` diff,
重新应用 `gateway-local-changes.patch` 里的 delta，并更新
`pyproject.toml` 里记录的 commit。

**网络要求（仅启动器侧）**：运行启动器的机器必须能访问 Worker —— 它要
向 `/_upstream` PUT 每个新 tunnel URL 并做健康检查。在 `*.workers.dev`
不可达的网络里（如中国大陆 —— 该域名被 DNS 污染并被 SNI 过滤），跑一个
本地 HTTP(S) 代理并在 env.conf 设 `PROXY=`。ChatGPT 从你的网络之外访问
Worker，产品链路两侧都不受影响。给 Worker 挂自定义域名（你持有、托管在
Cloudflare 的域名）可以免掉代理，但属可选。

## 运维（平台无关）

- 启动器（`start.sh` / `start.ps1`）幂等：复用活着的
  `:3081` host，进程清理只匹配我们的端口，清空 tunnel 日志，分配新的
  quick tunnel，按新主机名推 `--allowed-host`，PUT 到 Worker，重启网关。
- ChatGPT 连接器 URL **永远不变**:
  `https://dsh-gw.<you>.workers.dev/mcp`。重新认证用 owner PIN。
- 健康探针：`GET /api/chatgpt-bridge/revision` → `{workspace,
  sandboxPolicy, toolRevision, …}`。
- 日志在启动器的临时目录（macOS 为 `/tmp`,Windows 为 `$env:TEMP`):
  `dsh-web-host.log`、`cloudflared.log`、`dsh-mcp-gateway.launch.log`
  （启动级，每次启动截断）;`dsh-mcp-gateway.log` 是执行日志，
  进程内 5 MiB x3 轮转。
- tunnel URL 随 cloudflared 进程存续 —— 进程死了，Worker 还指着死上游，
  需要重跑启动器发布新 URL。
- 运行态全重置：停 host，如 helper 会话行为异常可删
  `$DSH_HOME/storages/chatgpt-bridge/session-workspaces.json` 和
  `$DSH_HOME/storages/session_projcache`;helper 会话会惰性重建。

## 评估过的备选路线：专用 MCP server(CodexPro)

[CodexPro](https://github.com/rebel0789/codexpro) 是另一种设计最干净的
参照实现：单个 Node 进程**本身**就是 MCP server,**自己**拥有 tool
runtime。

```
ChatGPT ──MCP──> codexpro(Express + 官方 MCP SDK,/mcp)──> 进程内工具函数
```

| | CodexPro | 本桥接 |
|---|---|---|
| 工具面 | 一等 MCP 工具,zod schema 直接进工具列表 | 只有 meta-tools;DSH 工具经 `dsh_tool_catalog`/`dsh_tool_call` 投影 |
| Tool runtime | 全部自写(`bashOps`/`fsOps`/`searchOps`/`analysis`)—— 名字带 codex 但无任何 Codex 代码 | 经 helper capability agent 调 DSH ToolRuntime |
| 认证 | 静态 bearer / `?codexpro_token=` query(≥24 字节) | OAuth PKCE + 动态注册 + owner PIN |
| 隔离 | `PathGuard`(allowedRoots + blocked globs)+ safe-bash 白名单;无 OS 沙箱 | seatbelt / windows-acl / bwrap `workspace-write` |
| 工作区 | `open_workspace` 在 allowedRoots 内选择;可按调用传 `workspace_id`;仅当前 MCP session,不持久化 | `workspace_switch`;按 conversation 绑定(`x-openai-session` 键控并持久化);回落到启动 `WORKSPACE`;沙箱锚定 helper 会话 cwd |
| 会话/agent | 无 agent/执行会话 —— 每次调用无状态;`CODEXPRO_BASH_SESSION_ID` 是共享密钥守卫,不是持久 shell | 持久 helper 会话、jobs、skills、`shell_session`、`subagent_devin` |
| "codex" 功能 | 读 `~/.codex/{sessions,archived_sessions}/*.jsonl`;`handoff_to_codex` 写 plan 文件等外部 codex 来取 —— 文件级约定,不是内嵌 agent | `subagent_devin` spawn 真进程内子 loop |

一等工具确实更干净:没有 catalog→call 两跳,没有 `callContract`
提示层补丁,上面第 6–8 条注意事项(参数名不匹配、配置键被剥、sandboxMode
会话快照)以及不可用的 `sandbox_permissions` 提权,是"把通用
harness 适配进 MCP"才存在的一类问题。

**直接替换的可行性。** Worker 门面 + quick tunnel 这一层不挑后端:
`codexpro start --tunnel none --root ~/data --port 8787` 可以不动
`_worker.js` 直接换掉 gateway+host —— `?codexpro_token=` 和
`Authorization` 都能穿过原样代理,`/mcp` 路径相同,codexpro 也不
校验 Host 头(不存在 421 类问题)。本地进程从三个减到两个。随之
失去的是:OAuth 退化为 URL 内嵌静态 token,OS 沙箱换成路径校验,
以及全部 DSH 能力(skills、jobs、持久 shell、subagent 委派)。

保留桥接的原因恰恰是这些能力 —— ChatGPT 拿到的是和本地 DSH
agent 相同的、受沙箱约束、有会话语义的执行权威。如果部署只需要
allowed roots 内的 read/write/edit/bash,单进程路线严格更简单;
看实际使用落在哪一侧。
