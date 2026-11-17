# chatgpt-web-local-runtime

[English](README.md) | 中文

以 ChatGPT Web 为唯一推理 agent、本地 DeepSeek Harness 作为受守护执行运行时，
通过一个固定不变的公网 MCP 端点对外提供服务。

```
ChatGPT ──MCP+OAuth──> workers.dev façade ──> 轮换的 quick tunnel
        ──> 本地 gateway :8766 ──> 本地 DSH host :3081 ──> DSH 工具/沙箱
```

ChatGPT 需要**一个固定不变的连接器 URL**，而 quick tunnel 每次启动都在
轮换。**Cloudflare Worker 门面**弥合这个缺口：它持有永久的
`https://dsh-gw.<you>.workers.dev` 源，把当前 tunnel URL 存在 KV 里，
`start.sh` 每次运行都重新发布（`PUT /_upstream`,Bearer 密钥保护）。
免费套餐够用 —— 控制台一次性配置：
**[docs/zh-CN/cloudflare.md](docs/zh-CN/cloudflare.md)**。

先读 **[docs/zh-CN/architecture.md](docs/zh-CN/architecture.md)**——里面有完整
的组件清单、安全模型、所有踩过的坑与修法。然后按你的平台指南部署：
**[macOS](docs/zh-CN/macos.md)** 或 **[Windows](docs/zh-CN/windows.md)**。

**为什么一个连接器后面挂着整个 harness:** 这座桥刻意投影完整的
DSH ToolRuntime(沙箱 bash、会话、skills、`subagent_devin`),而不是
像 [CodexPro](https://github.com/rebel0789/codexpro) 那样做一个专用
工具 server(单 Node 进程、一等 MCP 工具、静态 token、无沙箱无
会话)。权衡分析与直接替换的可行性见
[architecture.md](docs/zh-CN/architecture.md#评估过的备选路线专用-mcp-servercodexpro)。

## 本项目在上游之上新增的部分

vendored 网关只是协议适配层；让它作为**长期**连接器运转起来的部分
是本仓库自己写的：

- **固定公网 URL。** `worker/_worker.js` 是个极小的 Cloudflare Worker
  门面，持有连接器源；启动器每次运行把轮换的 quick-tunnel URL 重新
  发布给它(`PUT /_upstream`,Bearer 保护),ChatGPT 的连接器 URL
  因此永远不变。
- **一键启动器。** `start.sh` / `start.ps1` 幂等且自定位 —— 复用存活
  host、轮换 tunnel(`--allowed-host` + `http2` 的规避已内置)、发布
  上游、重启 gateway。日常使用就是一条命令，不用重新配置。
- **幂等安装器。** `install.sh` / `install.ps1` 复制载荷、建 venv、装
  npm runtime、生成 `env.conf`;重跑只刷新代码 —— 配置、state、
  profile 改动、`bin/cloudflared` 永不被覆盖。
- **验证过的组合。** cordis patch 精确挂载本桥所需插件（持久 shell、
  task state、remote workers、可选门控 `subagent_devin`)，背后是审查
  过的 `chatgpt-external-v1` 能力画像；网关还带三个预应用的源码
  patch(`--allowed-host`、callContract 提示、跨平台 OAuth SQLite)
  —— 见 [architecture.md](docs/zh-CN/architecture.md)。
- **平台手册。** macOS / Windows 指南 + architecture.md 里的完整踩坑
  目录。

## 目录结构

仓库把**项目文件**(文档、安装器)和**部署载荷**(`dsh-mcp-gateway/` ——
目录名就是安装目标）分开。一条命令完成安装：

```bash
./install.sh                  # -> ~/.dsh-mcp-gateway(--target DIR 可改)
# Windows: powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器复制载荷、建 venv、装网关和 npm runtime、生成 `env.conf`。重跑只
刷新代码 —— 你的 `env.conf`、`state/`、profile 改动和 `bin/cloudflared`
永不被覆盖。装完后运行期不再需要本仓库。

```
docs/
  zh-CN/
    architecture.md          参考文档 —— 组件、坑(先读这个)
    cloudflare.md            CF 控制台里 Worker + KV + 密钥的配置
    macos.md                 macOS 平台指南(install.sh、start.sh、seatbelt)
    windows.md               Windows 平台指南(install.ps1、start.ps1、acl)
  images/                    cloudflare.md 的截图(两个语言共用)
  en/
    architecture.md          zh-CN/ 的英文原文
    cloudflare.md            zh-CN/ 的英文原文
    macos.md                 zh-CN/ 的英文原文
    windows.md               zh-CN/ 的英文原文
install.sh / install.ps1     幂等安装器(复制 + venv + npm + 种子配置)
dsh-mcp-gateway/             部署载荷 -> 装成 ~/.dsh-mcp-gateway
  README.md                  载荷自述(也是 pyproject 的 readme)
  pyproject.toml             vendored 网关清单([上游](https://github.com/siddhartha-yz/dsh-mcp-gateway) @faf9606,MIT)
  src/dsh_mcp_gateway/       vendored 的 Python 网关源码(patch 已应用)
  LICENSE                    网关的 MIT license
  gateway-local-changes.patch   对上游的 delta 记录(已应用,不要重打)
  env.conf.example           配置模板(安装器由此生成 env.conf)
  start.sh / start.ps1       自定位幂等启动器(macOS / Windows)
  worker/_worker.js          Cloudflare Worker 门面(KV upstream + bearer PUT)
  deploy/
    dsh-runtime/package.json DSH npm runtime 清单(@deepseek-ai/dsh)
    dsh/chatgpt-bridge.{macos,windows}.cordis.yml
                             组合 patch —— <DSH_GW_HOME> 占位符,
                             启动器替换生成 *.resolved
  dsh-bridge-plugin/         bridge 插件(改过的版本)+ 能力画像
  dsh-task-state-plugin/     上游插件,未改
  dsh-shell-session-plugin/  上游插件(仅 POSIX bash)
  dsh-remote-worker-plugin/  上游插件(无轮询者时空转)
  home/profiles/web/         DSH profile —— bundles dsh-base + dsh-web-app,
                             与你日常的 ~/.dsh 完全独立
  devin/                     可选 Devin 包(provider + 门控 subagent_devin)
    README(.zh-CN).md        启用步骤
    profile-package.snippet.json   合并进 profile package.json
    cordis.patch.devin.yml   追加进 profile cordis.patch.yml
```

运行时文件落在**安装目标目录**,不在仓库:`env.conf`、`.venv/`、
`state/`、`home/` 会话数据、`bin/cloudflared`、`*.resolved.cordis.yml`。
密钥绝不入库。

## 日常操作速记

1. `./install.sh`（或 `install.ps1`)→ 然后编辑
   `~/.dsh-mcp-gateway/env.conf`:PIN / Worker URL / secret /
   `PROXY` —— 启动器本机必须能访问 `workers.dev`；直连不了时（如中国大陆）
   `PROXY` 指向一个本地 HTTP(S) 代理。
2. `~/.dsh-mcp-gateway/start.sh`（或 `start.ps1`)——拉起 host、轮换 tunnel、
   把上游发布到 Worker（一次性配置：
   [cloudflare.md](docs/zh-CN/cloudflare.md))、重启 gateway。
3. ChatGPT 连接器 URL 永久不变：`https://dsh-gw.<you>.workers.dev/mcp`。
   重新认证 = 用 `env.conf` 里的 owner PIN。
4. 在 ChatGPT 里：`workspace_switch` 移动项目根（限定允许子树，默认
   `~/data`），
   `subagent_devin` 委派给 Devin（锁死 `swe-2-high`），文件/沙箱工具在
   沙箱边界内工作。

## 工作区

ChatGPT 操作的是一个**可切换的工作区根**，不是你的整块磁盘。下文的
`~` 指系统 home 目录（Windows 上即 `C:\Users\<你>`）。

- **`WORKSPACE`(环境变量)—— 初始根。** 默认 `~/data/workspace`
  （启动器的工作目录）。用 `WORKSPACE` 环境变量或 env.conf 里
  `WORKSPACE=<绝对路径>` 修改；在首次运行或无有效持久选择时生效。
- **`WORKSPACE_ALLOWED_ROOT`(环境变量)—— 允许根。** 默认 `~/data`;
  在 env.conf 里改可将整个可切换边界移走。解析后的根必须存在
  —— 目录缺失（含默认 `~/data` 缺失）时启动失败并给出修复提示。
  `workspace_switch` 只允许切到该子树内(realpath 校验，拒绝符号
  链接逃逸)；切到根本身则把边界放宽到整棵子树。
- **持久化**：共享默认选择存在
  `home/storages/chatgpt-bridge/active-workspace.json`，重启不丢，
  且优先于 `WORKSPACE`（删该文件即回落）。`workspace_switch` 只绑定
  发起调用的 conversation —— 以 ChatGPT 每会话携带的
  `x-openai-session` 头为键（`mcp-session-id` 兜底）—— 并发会话在
  内存中各自持根，未切换前回落共享默认。`workspace_status` 报告
  调用方会话的根。
- **边界**：沙箱化的 `bash`/`pwsh` 只能写活动根以内；`read`/`write`/
  `edit` 文件工具走自己的访问模型。重启后根不对就再
  `workspace_switch` 一次，或删状态文件做全量重置（见
  architecture.md 运维节）。

密钥类内容（`env.conf`、`ADMIN_PIN.txt`、`state/`）由 `.gitignore` 挡在
版本控制外——它们就生成在本目录里，但绝不入库。

## 致谢

MCP/OAuth 网关 vendored 自
[siddhartha-yz/dsh-mcp-gateway](https://github.com/siddhartha-yz/dsh-mcp-gateway)
(MIT，上游 commit `faf9606`)。本地 delta 已预应用在
`dsh-mcp-gateway/src/`，记录在 `gateway-local-changes.patch`；上游 MIT
license 随载荷发布在 `dsh-mcp-gateway/LICENSE`。
