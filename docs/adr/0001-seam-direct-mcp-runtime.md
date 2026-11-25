# seam 直连架构:面向 ChatGPT 的纯 MCP 执行服务

**Status**: rejected(2026-11-25,owner 决策)—— 保留现有 harness 桥接架构。
本文件保留为完整的评估记录:实证证据依然有效,seam 直连的可行性结论不变,
但"可行"不等于"应行"。拒绝理由见文末"决策记录"。

仓库现状是"ChatGPT → Python OAuth 网关 → DSH host → ToolRuntime → 工具插件 → seam 服务"的五层链路。
经实证调研,我们提出改为**单 Node 进程:手写 MCP 工具直接消费 DSH 的 seam 执行服务**(`ctx.shell`/`ctx.fs`),
完全移除 ToolRuntime、工具插件层与 Python 网关 —— CodexPro 的表皮(一等 MCP 工具、自写 schema),
DSH 的骨骼(OS 级沙箱执行底座)。

## 实证依据(2026-11-25,本机 macOS 实测)

以下事实均在裸 `cordis` 组合中实测验证,非推测:

| 验证项 | 结果 |
|---|---|
| seam-only 最小组合 | **7 行插件**即可挂载成功:`session-projection` `subprocess-local` `sandbox-local` `sandbox-policy` `bash-sandbox` `shell-env` `fs-sandbox` —— 进程内无 dsh-tools、无 systemPrompt、无任何 `dsh-tool-*` |
| seam 接口的 agent 耦合 | **零耦合** —— `ShellExecRequest` 是纯数据(command/workdir/timeoutMs/signal);agent 耦合只存在于工具插件层(从 `session.header.cwd` 解析 workdir) |
| 直接 `ctx.shell.run()` 沙箱强制 | Seatbelt 内核级禁闭生效:工作区内可写,写 `$HOME` 被内核拒绝(`enforcement: "full"`) |
| npm 依赖闭包 | 含工具层时 58 个包;全量 runtime 为 223 个 @deepseek-ai 包 |
| 内存 | RSS ~79–85 MB |
| ToolRuntime 的 systemPrompt 硬依赖 | 仅用于 prompt 装配桥接(构造函数内 `systemPrompt.tools()` 注册),与执行无关;纯 MCP 服务不需要,桩替代 ~25 行即可 —— 但 seam 直连路线下此问题整个不存在 |

辅助事实:`dsh-tools` 编译产物 3589 行中约 42% 是纯 MCP 服务用不到的 PTC/SDK 渲染死重;
真正的运行时(注册表+管线)约 1194 行;`defineTool` 把参数校验嵌入工具定义本身,
上游工具插件以 `import { defineTool } from '@deepseek-ai/dsh-tools'` 方式耦合该包。

## 评估过的备选路线

### 保留现状(全量 harness 桥接)— 否决于复杂度

五层链路、三个进程、meta-tools 两跳投影、vendored Python 网关 3845 行。
真实痛点(tunnel/网络层)与架构无关,但概念复杂度与故障面是持续税。
保留理由原本只有一个:社区扩展验收测试 —— 见"后果"一节。

### 路线 A:完全自写(CodexPro 克隆)— 否决于安全成本

工具本体好写,但 Seatbelt/Windows-ACL/bwrap 是安全关键、平台特定的硬骨头,
CodexPro 自己都放弃了(仅路径校验)。做到与 DSH 对等需数月且永远做不对等。

### 路线 B:全量 ToolRuntime 透传 — 否决于收益/成本错配

裸 cordis + ToolRuntime 实证可行(agentless 执行、沙箱完整),原型数天。
但:(1) 每会话多工作区语义在 agentless 下退化为单固定根,需重新设计;
(2) ChatGPT 工具快照缓存问题与实现无关,meta-tools 在社区扩展场景下仍不可省略,
"纯"一等工具服务不存在;(3) 保留 ToolRuntime 就要承担其行为合同(exec 形状/管线语义/取消分类学)
与上游版本演进(0.1.2→0.2.x 已发生 batch API 与 executor 拆分两次 breaking)。

### 路线 B':重写/vendor ToolRuntime — 否决于永久合同所有权

初稿便宜(~500-700 行,库函数可复用),但重写只是替换 Service 类 ——
上游工具插件仍 import 真实包,你永久持有与上游插件生态的行为合同。
为 systemPrompt 这种已用 25 行桩归零的问题重写核心调度器,用力过度。

### 选定:seam 直连

手写 MCP 工具层(schema/格式化/访问策略归我们,~800-1200 行),
执行层委托 DSH seam 服务。上游耦合面收缩为几个**小而稳定的数据接口**
(`ShellExecRequest`/`ShellRunResult`/`FsTarget`)—— seam 抽象层是 upstream
设计来稳定的缝隙,演进远慢于工具层。

## 后果

### 获得

- 单进程(加 cloudflared 两个),删 Python 网关全量与 meta-tools 两跳;
- 一等 MCP 工具:无 catalog→call 间接、无 callContract 提示层补丁、无参数名失配类故障;
- 保留 DSH 最难自写的能力:Seatbelt/ACL 内核级沙箱、进程组管理、有界 spill 输出、
  超时/取消的 first-cause 分类;
- 上游耦合最小化:seam 数据接口稳定,工具层演进与我们无关。

### 失去(接受前必须确认)

- **社区 DSH 扩展即插即用彻底死亡**。无 ToolRuntime 则社区插件无处注册,
  AGENTS.md 的验收测试("安装代表性社区扩展并在 ChatGPT 中使用")不可通过。
  这是采用本 ADR 的**前提条件**:owner 须明确修订 AGENTS.md 的产品方向。

  生态规模实证(2026-11-25 npm 数据):官方目录 `dsh-plugin-catalog` 自称
  **13,136 条目**且当日仍在更新;存在多家插件市场(`dshmarket` 周下载 273k,
  含市场同步放大效应)、评审目录(awesome-dsh-plugin.com)与质量工具链
  (guide/health/upgrader)。能力型代表:`dsh-agent-compare` 180k/周、
  `dsh-windows-mcp` 120k/周、`dsh-remote` 114k/周、`dsh-free-search` 51k/周、
  `@xmanrui/dsh-im`(微信/飞书/钉钉/TG/Slack 等 IM 接入)20k/周。
  生态真实、庞大、增长中 —— 但其重心(IM、桌面自动化、UI 挂件)大半与
  "ChatGPT 远程执行"场景无关;且本部署**实际**用上的社区能力目前仅
  `@quonaro/dsh-plugin-devin` 一个(45/周,小众但承重:`subagent_devin` 的
  provider)。失去的是"可能性",不是"当前在用的批量能力"。

  接入机制实证(2026-11-25 拆包分析):能力型插件**几乎全部**经
  `ctx.tools.register(defineTool)` 接入(devin lib/index.js:87、dsh-remote
  的 rw_* 族、dsh-free-search)—— 这是 DSH 里暴露模型可调用能力的唯一通道,
  连外部 MCP server 桥(dsh-windows-mcp)最终也落在 ToolRuntime。但多数热门
  能力插件是**薄包装**:devin 本体是 subprocess 拉 CLI,windows-mcp 的能力
  本体是独立的 CursorTouch MCP server(可让 ChatGPT 直连),free-search 是
  HTTP 调引擎。真正死亡的只是 **harness 深度集成型**插件与"零修改即插即用"
  体验;薄包装型价值在外部能力,移植/替代成本低。非能力型插件(市场/UI)
  本就与桥接场景无关;skills 是数据产物,可移植。
- `subagent_devin`(它是 ToolRuntime 工具)、skills、jobs 编排消失;
  持久 PTY 可经 `dsh-terminal` seam 选择性自接。
- fs seam 不套 OS 沙箱(与现生产语义一致,非退化),但文件访问策略归我们自写
  (CodexPro 式 PathGuard,~100 行)。
- glob/grep 无现成 seam,直接调 `@vscode/ripgrep`(闭包内已有)并自写格式化。

### 不变

- Worker 门面 + quick tunnel 层与后端无关,原样保留;
- ChatGPT 工具快照约束是客户端属性 —— 但因不再追求动态扩展,静态一等工具集即为正确性路径,
  该约束从"架构限制"变为"无影响";
- 每会话多工作区:静态配置根 + 自写 workspace 绑定逻辑(参考现有 bridge 的
  `session-workspaces.json` 机制,语义重设计)。

## 工作量估计

实证原型已完成(验证脚本会话内跑通)。产品化:MCP server(官方 SDK)+ 4-6 个手写工具
+ PathGuard + OAuth + 双平台运维矩阵重测,约 1-2 周硬化。

## 决策记录(2026-11-25,rejected)

owner 在评估后决定**保留现有 harness 桥接架构**。除正文各路线的成本分析外,
决定性理由是发现了现状 meta-tools 设计的正当性 —— 它不是妥协,而是规模下的正确:

1. **工具描述常驻上下文的经济学**。一等 MCP 工具的完整 schema 在每个会话、
   每一轮永久占用上下文(20 个工具 ≈ 4-10k token 固定税);meta-tools 仅常驻
   4 个小 schema,详细 schema 经 catalog 按需进入上下文,未用工具零成本。
   工具面小时差异不大,但接入插件生态后工具面以几十上百计 —— 渐进披露
   (progressive disclosure)是唯一可持续设计。外部同此趋势:Anthropic 的
   tool search 方向、Cloudflare Code Mode、DSH 自己的 PTC 模式,均为同一思想。
2. **ChatGPT 快照缓存使一等工具的主要卖点无法交付** —— 客户端不采纳运行时
   工具列表刷新,"更好的 UX"在 ChatGPT 上本就不存在,比较无从谈起。
3. **投影层是策展面**。callContract 提示层修复参数名失配、能力画像白名单审查,
   都依赖"我们控制模型看到什么措辞的 schema"这个控制点;一等直连放弃该控制点。

代价记账(接受):catalog 两跳在每会话首次使用时多一次往返;catalog 结果本身
占上下文。在"工具面大、单会话使用工具种类少"的典型分布下几乎总是划算。

**本 ADR 的遗留价值**:若未来出现以下情况,应重新打开本评估 ——
(a) 实际使用证实工具面长期很小(<10)且无扩展需求;
(b) ChatGPT 客户端改为支持运行时工具列表刷新;
(c) 运维痛感集中于 Python 网关层且无法局部修复。
seam 直连的实证结论(7 行组合、沙箱完整、~80MB)届时可直接复用,无需重新调研。
