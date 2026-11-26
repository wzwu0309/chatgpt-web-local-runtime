# Web Chat Agent → DSH 本地桥接 —— 架构与运维手册

[English](../en/architecture.md) | 中文

**Web Chat Agent（对话代理）**负责理解用户意图、推理和决定何时调用工具。
ChatGPT Web 是本项目的首要场景，Gemini 是另一种使用场景。这个术语描述的是
对话端的角色，不限定界面是浏览器还是桌面应用；接入需要客户端支持远程 MCP。
DeepSeek Harness（DSH）是本地执行权威，负责工具、沙箱、技能和工作区。
日常运行不需要第二个模型接管推理。本文介绍架构、组件和部署运维。

```
Web Chat Agent（ChatGPT Web、Gemini 等）
   │  MCP Streamable HTTP + OAuth (PKCE, 动态客户端注册)
   ▼
https://dsh-gw.<you>.workers.dev            ← 固定 Worker origin / Cloudflare Worker 门面
   ├─ /chatgpt/mcp  ChatGPT 绑定 exposure
   ├─ /gemini/mcp   Gemini 使用的 generic mount
   └─ /mcp          canonical generic mount
   │  KV 里存当前上游地址，所有路径原样转发
   ▼
https://<random>.trycloudflare.com          ← 轮换的 quick tunnel，无感知
   │  cloudflared --protocol http2  (到 CF 边缘的持久出站连接)
   ▼
dsh-mcp-gateway  :8766  (Python/Starlette)
   OAuth 校验 → 按 mount 选择 exposure → MCP 协议适配 → harness bridge
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
| **Web Chat Agent（对话代理）** | 直接与用户对话、负责推理和决策的上层代理；通过 MCP 委派本地执行，不是 DSH 的执行辅助 agent。 |
| **ToolRuntime tool** | DSH 内部能力单元，注册在 `ctx.tools` 上（`bash`、`read`、`subagent_devin`…）。 |
| **capability profile** | DSH 侧给外部 agent 的显式工具白名单，不是全量 ToolRuntime 转储。我们的：`chatgpt-external-v1`(`dsh-bridge-plugin/chatgpt-capability-profile.js`)。 |
| **`allowExtraTools`** | bridge 配置项，把审查过的工具加进 profile。保留的 AgentLoop 生命周期名（`subagent`、`todo_write`…）会被 profile 自身拒绝。 |
| **meta-tools** | 客户端稳定看到的 MCP 工具：`dsh_tool_catalog`（发现）、`dsh_tool_call`（调用），外加 `dsh_skill_catalog`/`dsh_skill_load`。DSH 工具经它们*投影*，默认不做一等 MCP 工具。meta-only 是刻意的：客户端可能把 MCP 工具列表当快照缓存，运行时动态发现才是正确性路径。 |
| **mount / exposure** | gateway 按 URL mount 选择客户端暴露实现。`/chatgpt/mcp` 是唯一 ChatGPT 特化面；`/gemini/mcp` 与 `/mcp` 共享同一个 generic exposure。 |
| **helper capability agent** | bridge 为每个工作区创建的执行专用 agent（不跑 loop、不调 LLM)。它的会话 `cwd` 锚定 bash cwd、文件根、沙箱边界。会话持久化，按 `capabilitySessionId(cwd, preset, fingerprint)` 分键。 |
| **Worker 门面** | 一个很小的 Cloudflare Worker:`PUT /_upstream`(Bearer secret）把当前 tunnel URL 写进 KV，其余请求按路径原样转发。隧道轮换时给各客户端一个永久入口。 |

## 组件清单

| 组件 | 来源 | 文件 |
|---|---|---|
| DSH runtime | `npm i @deepseek-ai/dsh@0.2.2-rc.5` 装进 `deploy/dsh-runtime` | `dsh-mcp-gateway/deploy/dsh-runtime/package.json` |
| DSH profile | `home/profiles/web` = `DSH_HOME/profiles/web` —— 自己的 cordis.yml/patch/package.json，与 `~/.dsh` 完全独立 | bundles `dsh-base` + `dsh-web-app`（+ 可选 `@quonaro/dsh-plugin-devin`) |
| Bridge patch | 自己写的 | `dsh-mcp-gateway/deploy/dsh/chatgpt-bridge.{macos,windows}.cordis.yml`（启动器替换 `<DSH_GW_HOME>` → `*.resolved`) |
| Bridge 插件 | 自己改的上游版本 | `dsh-mcp-gateway/dsh-bridge-plugin/` |
| Task-state 插件 | 上游未改 | `dsh-mcp-gateway/dsh-task-state-plugin/` |
| Shell-session 插件 | 上游未改（仅 POSIX bash) | `dsh-mcp-gateway/dsh-shell-session-plugin/` |
| Remote-worker 插件 | 上游未改（无轮询者时空转） | `dsh-mcp-gateway/dsh-remote-worker-plugin/` |
| MCP 网关 | vendored 自 `siddhartha-yz/dsh-mcp-gateway`@`faf9606`(MIT),本地扩展已应用 | `dsh-mcp-gateway/pyproject.toml` + `src/` —— `gateway-local-changes.patch` 仅是早期历史记录 |
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

### 网关本地扩展

网关以 vendored upstream 为起点，但 `src/dsh_mcp_gateway/` 已包含实质本地演进：

1. **多 mount composition**:`/chatgpt/mcp`、`/gemini/mcp`、`/mcp` 共用一套 OAuth 与 harness bridge，但按 mount 选择 exposure 对象。差异集中在 catalog 形状、`workspace` envelope 和会话身份透传；执行、校验、能力策略都留在 DSH bridge。
2. **路径感知 OAuth**:authorization-server metadata 支持任意路径后缀，protected-resource metadata 与 `WWW-Authenticate` 指向实际 mount;DCR 注册分层回收（未授权注册短期回收，已授权注册冗余保留）,refresh token TTL 为 180 天。
3. **传输与部署兼容**：可重复 `--allowed-host HOST` 处理 Worker→tunnel 的 Host 头差异；OAuth SQLite 在非 Linux 平台回退到 `file:<path>?mode=rw`。
4. **提示层与 harness bridge**:`callContract` 明确目标工具的必填参数；skill catalog/load、可选 first-class projection、调用结果边界校验都在 gateway 的 harness bridge 层完成。

`gateway-local-changes.patch` 是早期 vendored delta 的历史记录，**不是**当前完整差异的权威描述；当前权威源是 `src/` 与测试。重新同步上游时必须重新 diff 手工评估。

## 工作区模型

- `WORKSPACE_ALLOWED_ROOT`(env，默认 `~/data/workspace`)—— 所有客户端可路由到的允许子树。启动器透传给 DSH host；启动器与插件对解析后的根（含默认路径）做存在性校验，缺失时拒绝启动并提示"创建目录或改配"(realpath 校验，拒绝符号链接逃逸）。允许指到根本身 → 整个子树都成为 bash 可写根。
- `WORKSPACE`(env，默认 `<WORKSPACE_ALLOWED_ROOT>/default`，开箱即 `~/data/workspace/default`)是 DSH host 的启动 cwd 与默认工作区。目录缺失时启动器自动创建；同名位置已有非目录文件则启动失败。它**不受**允许根约束（运营者自有入口）；但 `workspace_switch` 与 generic
`workspace` 的目标必须位于允许根内。若启动根本身位于允许根之外，调用仍可
因未绑定/省略 `workspace` 而落在那里，但不能通过这两种机制把界外
`WORKSPACE` 显式设为目标。
- bridge 内部的工作区优先级为：显式 envelope `workspace` >
  conversation 持久绑定 > 启动根 `WORKSPACE`。生产 mount 不让两种模式混用：
  ChatGPT exposure 拒绝 `workspace` 参数，generic exposure 不参与绑定。
  显式 `workspace` 是会话无关的当次调用路由键；它不会写入绑定表，也不会进入
  目标工具的 `arguments`。为空字符串或越界时调用显式失败，绝不静默回落。
- 切换/路由**不**修改既有会话（DSH `cwd` 是创建期元数据）；bridge 退役旧 helper，并按新 cwd 惰性创建/复用 helper 会话。沙箱边界 = helper 会话 `cwd`。

## Mount 与 exposure

| mount | exposure | 工作区语义 | 身份语义 |
|---|---|---|---|
| `/chatgpt/mcp` | `ChatgptExposure` | catalog 含会话套件；conversation 绑定持久化 | 只承认 `x-openai-session`;不使用 OAuth principal 或 transport id 兜底 |
| `/gemini/mcp` | `GenericExposure` | catalog 过滤会话套件；`dsh_tool_call.workspace` 按调用显式路由 | 不向 bridge 透传会话身份 |
| `/mcp` | 同一个 `GenericExposure` 实例 | 同 `/gemini/mcp` | 同 `/gemini/mcp` |

下文把 `workspace_switch`/`workspace_status`/`task_state` 合称**会话套件**：
前两者操作或报告工作区绑定；`task_state` 的跨轮续接语义同样以稳定会话为前提。

**ChatGPT 绑定面。** `workspace_switch {path}` 只绑定发起调用的
conversation:`x-openai-session` 头值作为 `sessionId`,Map 持久化到
`$DSH_HOME/storages/chatgpt-bridge/session-workspaces.json` —— 并发
conversation 在允许子树内各自持根，重启不丢；持久条目加载时按允许子树
重校验，越界或不存在的路径被丢弃；Map 上限 256 条。会话身份缺席时绑定
禁用：`workspace_switch` 响亮失败，不会改写共享默认根；连续缺失会记录
日志并经 gateway `/healthz` 暴露 degraded 信号。

**Gemini/通用面。** Gemini 自定义应用已实测可通过标准 DCR + PKCE 连接
`/mcp`，网关会签发 refresh token。`/mcp` 是标准通用入口；`/gemini/mcp`
是为 Gemini 命名的入口，两者共享同一个 `GenericExposure` 实例。这个执行面
不读写会话绑定。每次调用可通过 `dsh_tool_call.workspace` 选择项目；要连续处理
同一项目，后续调用传同一路径。省略 `workspace` 时使用 `WORKSPACE`。

**发现即执行。** catalog 由 bridge 经 capability agent 与 `tools.resolve()`
求值，和实际执行走同一 admitted toolSet；generic catalog 只是额外过滤会话
套件。gateway 不维护第二份能力白名单。

## 安全模型

- 公网路径端到端认证：OAuth 动态注册 + PKCE,authorize 时要 owner PIN,所有 MCP mount 都走 Bearer token。光泄露 tunnel URL 什么都拿不到。
- `bash`/`pwsh` **受沙箱约束**(`workspace-write`)：只允许写当前工作区根（+ 系统临时目录）。macOS 用 Seatbelt,Windows 用 `windows-acl`（部分强制）,Linux 用 bwrap。
- `read`/`write`/`edit` 文件工具走自己的访问模型 —— **不**套 shell 沙箱，能写工作区外。
- `sandbox_permissions` 提权参数存在于工具 schema 但**在这里不可用**:`approval.request()` 必须发生在 agent turn 内；bridge helper 没有 turn → 报 `approval.request() outside an open turn`。不是 bug，不用追。
- Worker 能看到明文（TLS 在 CF 边缘终止）；内层 OAuth+PIN 就是为此存在。

## 运维注意事项（每一条都是承重点）

1. **421 Invalid Host** —— Worker→tunnel 的请求 Host 是 tunnel 域名；网关 DNS-rebinding 防护只认 `workers.dev`。修：每次启动带 `--allowed-host <tunnel-host>`(start.sh 自动取）。
2. **530 / 隧道死亡** —— 这条网络掐 UDP;QUIC 隧道 ~10 分钟就断。修：`cloudflared --protocol http2`(TCP 7844)。
3. **`/tmp/cloudflared.log` 变二进制** —— 被杀的写入进程留了 NUL 字节，`grep` 报 "Binary file matches"。修：启动前清空日志 + `grep -ao`。
4. **`workers.dev` 直连不通** —— 本机网络封 `*.workers.dev`；出站调用（`PUT /_upstream`、健康检查）走 `PROXY`。只影响 start.sh 自己的几条 curl——外部 MCP 客户端从互联网直连 Worker 无阻碍。
5. **`--allowed-host` / OAuth SQLite** —— 网关需要本地源码改动；已预先打进 vendored 的 `dsh-mcp-gateway/src/`(editable 安装 → 改动即刻生效）。不要假设 `gateway-local-changes.patch` 覆盖这些及后续改动。
6. **参数名错误伪装成"没权限"** —— 已在提示层修掉（callContract)。
7. **重新安装不会重载 DSH host** —— 两个平台的启动器都会复用健康的 host。修改 bridge 或插件后，重新启动前先停掉已有的 host。只改 gateway 则不需要轮换 tunnel。
8. **会话钉死 `sandboxMode`** —— helper 会话保留创建时记录的 mode；部署级 `mode` 改动不回溯生效。但工作区根始终从 `session.header.cwd` 实时解析。
9. **host 重启窗口 = "连续内部错误"** —— `:3081` 挂掉期间每个 `/call` 都 ECONNREFUSED。客户端报这个时先查 host 存活（`/api/chatgpt-bridge/revision`)。
10. **Gemini 单会话 MCP 工具调用上限（实测）** —— 2026-11-26T04:53:26Z 在 Gemini 客户端 `1.147.6.1557 (1.147.6.1557)` 观察到：同一对话可访问 MCP 工具的次数会被客户端限制。具体阈值和报错原文未记录，官方文档也未说明；影响整个 MCP 工具面，不是本桥或某个工具的配额。使用中避免把任务拆成大量琐碎、重复调用；触发后先按客户端限制排查，不要误判为 gateway/DSH 故障。

## 部署

平台相关的安装与启动在各平台指南里：

- **macOS** → [macos.md](macos.md) —— `start.sh`、seatbelt、`bash`
- **Windows** → [windows.md](windows.md) —— `start.ps1`、`windows-acl`、`pwsh`

`dsh-mcp-gateway/` 载荷就是部署目录的内容；`install.sh` / `install.ps1`
把它复制到 `~/.dsh-mcp-gateway`(`--target`/`-Target` 可改），然后执行
标准安装：venv + editable 网关（vendored 上游 @faf9606 + 本地扩展）→
npm runtime → 生成 `env.conf`。安装器幂等 —— 代码目录全量刷新（含删除），
而 `env.conf`、`state/`、`home/profiles/web/*` 等用户文件永不被覆盖。
之后：部署 `worker/_worker.js` —— 分步见 [cloudflare.md](cloudflare.md) ——
填 `env.conf`、跑启动器，然后按客户端配置连接器：ChatGPT 用
`$WORKER_PUBLIC_URL/chatgpt/mcp`,generic 客户端用 `$WORKER_PUBLIC_URL/mcp`。
Gemini 可用通用 URL 或 `$WORKER_PUBLIC_URL/gemini/mcp`。要与
上游重同步 vendored 网关：将上游 `src/` 与 `dsh-mcp-gateway/src/` 重新 diff,
逐项评估本地扩展如何迁移，并更新 `pyproject.toml` 里记录的 commit;不要
直接重打 `gateway-local-changes.patch`。

**网络要求（仅启动器侧）**：运行启动器的机器必须能访问 Worker —— 它要
向 `/_upstream` PUT 每个新 tunnel URL 并做健康检查。在 `*.workers.dev`
不可达的网络里（如中国大陆 —— 该域名被 DNS 污染并被 SNI 过滤），跑一个
本地 HTTP(S) 代理并在 env.conf 设 `PROXY=`。外部 MCP 客户端从你的网络之外访问
Worker，产品链路两侧都不受影响。给 Worker 挂自定义域名（你持有、托管在
Cloudflare 的域名）可以免掉代理，但属可选。

## 运维（平台无关）

- 启动器（`start.sh` / `start.ps1`）幂等：复用活着的
  `:3081` host，进程清理只匹配我们的端口，清空 tunnel 日志，分配新的
  quick tunnel，按新主机名推 `--allowed-host`，PUT 到 Worker，重启网关。
- 连接器 URL 不随 tunnel 轮换改变:ChatGPT 用
  `https://dsh-gw.<you>.workers.dev/chatgpt/mcp`,generic 客户端用
  `https://dsh-gw.<you>.workers.dev/mcp`。Gemini 可用通用 URL 或
  `https://dsh-gw.<you>.workers.dev/gemini/mcp`。重新认证用 owner PIN。
- 健康探针：`GET /api/chatgpt-bridge/revision` → `{workspace,
  sandboxPolicy, toolRevision, …}`。
- 日志在启动器的临时目录（macOS 为 `/tmp`,Windows 为 `$env:TEMP`):
  `cloudflared.log`、`dsh-mcp-gateway.launch.log` 是启动级日志，每次启动
  截断；`dsh-web-host.log` 只在启动 host 时截断（复用 host 时持续追加）。
  `dsh-mcp-gateway.log` 是执行日志，进程内单文件 8 MiB 截头限界。
- tunnel URL 随 cloudflared 进程存续 —— 进程死了，Worker 还指着死上游，
  需要重跑启动器发布新 URL。
- 运行态全重置：停 host，如 helper 会话行为异常可删
  `$DSH_HOME/storages/chatgpt-bridge/session-workspaces.json` 和
  `$DSH_HOME/storages/session_projcache`;helper 会话会惰性重建。

## 验证

仓库自带两级 bridge golden-path，通常在仓库根目录对本机部署运行：

```bash
# ChatGPT 绑定面主路径:catalog、bash、write/read、workspace_switch、沙箱拒绝等
node validation/golden-path.mjs

# exposure 对照:ChatGPT 绑定回归 + generic 显式 workspace 路由/拒绝/零绑定写入
node validation/exposure-golden-path.mjs \
  --state-file ~/.dsh-mcp-gateway/home/storages/chatgpt-bridge/session-workspaces.json
```

两个脚本默认探测 `http://127.0.0.1:3081/api/chatgpt-bridge`;`--base` 可指向
其他 bridge。它们使用独立 probe session 和临时目录，结束后会清理 probe 目录；
exposure 脚本还会把 probe 会话切回启动根。升级 DSH runtime、bridge 插件或
gateway exposure 后应先跑这两项，再做客户端级 OAuth 冒烟。

## 评估过的备选路线：专用 MCP server(CodexPro)

[CodexPro](https://github.com/rebel0789/codexpro) 是另一种设计最干净的
参照实现：单个 Node 进程**本身**就是 MCP server,**自己**拥有 tool
runtime。

```
ChatGPT ──MCP──> codexpro(Express + 官方 MCP SDK,/mcp)──> 进程内工具函数
```

| | CodexPro | 本桥接 |
|---|---|---|
| 工具面 | 一等 MCP 工具,zod schema 直接进工具列表 | 默认 meta-only;DSH 工具经 `dsh_tool_catalog`/`dsh_tool_call` 投影,first-class projection 仅为显式 opt-in 的 UX 增强 |
| Tool runtime | 全部自写(`bashOps`/`fsOps`/`searchOps`/`analysis`)—— 名字带 codex 但无任何 Codex 代码 | 经 helper capability agent 调 DSH ToolRuntime |
| 认证 | 静态 bearer / `?codexpro_token=` query(≥24 字节) | OAuth PKCE + 动态注册 + owner PIN |
| 隔离 | `PathGuard`(allowedRoots + blocked globs)+ safe-bash 白名单;无 OS 沙箱 | seatbelt / windows-acl / bwrap `workspace-write` |
| 工作区 | `open_workspace` 在 allowedRoots 内选择;可按调用传 `workspace_id`;仅当前 MCP session,不持久化 | 双模式：ChatGPT 用 `workspace_switch` 按 conversation 持久绑定;generic 面用 `workspace` 按调用显式路由;共同回落到启动 `WORKSPACE`;沙箱锚定 helper 会话 cwd |
| 会话/agent | 无 agent/执行会话 —— 每次调用无状态;`CODEXPRO_BASH_SESSION_ID` 是共享密钥守卫,不是持久 shell | 持久 helper 会话、jobs、skills、`shell_session`、`subagent_devin` |
| "codex" 功能 | 读 `~/.codex/{sessions,archived_sessions}/*.jsonl`;`handoff_to_codex` 写 plan 文件等外部 codex 来取 —— 文件级约定,不是内嵌 agent | `subagent_devin` spawn 真进程内子 loop |

一等工具确实更干净:没有 catalog→call 两跳,没有 `callContract`
提示层补丁;上面第 6、8 条注意事项(参数名不匹配、sandboxMode
会话快照)以及不可用的 `sandbox_permissions` 提权,是"把通用
harness 适配进 MCP"才存在的一类问题。

**后端替换。** Worker 门面 + quick tunnel 这一层不挑后端，可以复用，
但指向 `/chatgpt/mcp` 的连接器必须改配到 CodexPro 的 `/mcp` 端点。
`codexpro start --tunnel none --root ~/data --port 8787` 可以不动
`_worker.js` 直接换掉 gateway+host —— `?codexpro_token=` 和
`Authorization` 都能穿过原样代理,codexpro 也不
校验 Host 头(不存在 421 类问题)。本地进程从三个减到两个。随之
失去的是:OAuth 退化为 URL 内嵌静态 token,OS 沙箱换成路径校验,
以及全部 DSH 能力(skills、jobs、持久 shell、subagent 委派)。

保留桥接的原因恰恰是这些能力 —— 外部客户端拿到的是和本地 DSH
agent 相同的、受沙箱约束、有会话语义的执行权威。如果部署只需要
allowed roots 内的 read/write/edit/bash,单进程路线严格更简单;
看实际使用落在哪一侧。
