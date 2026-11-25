# chatgpt-web-local-runtime

[English](README.md) | 中文

本项目以 ChatGPT Web 为主客户端、本地 DeepSeek Harness 作为受守护执行运行时，
通过一个固定不变的公网 Worker origin 下的稳定 MCP mount 对外提供服务；
Gemini 及其他客户端可共用 generic 执行面，该面不依赖持久会话标识。

```
ChatGPT / Gemini / 其他 MCP 客户端 ──MCP+OAuth──> workers.dev façade
        ──> 轮换的 quick tunnel ──> 本地 gateway :8766
        ──> 本地 DSH host :3081 ──> DSH 工具/沙箱
```

MCP 客户端需要**固定不变的连接器 URL**，而 quick tunnel 每次启动都在
轮换。**Cloudflare Worker 门面**弥合这个缺口：它持有永久的
`https://dsh-gw.<you>.workers.dev` origin，把当前 tunnel URL 存在 KV 里，
`start.sh` 每次运行都重新发布（`PUT /_upstream`,Bearer 密钥保护）。
连接器路径也固定：ChatGPT 用 `/chatgpt/mcp`,Gemini 用 `/gemini/mcp`,
通用客户端用 `/mcp`。
免费套餐够用 —— 控制台一次性配置：
**[docs/zh-CN/cloudflare.md](docs/zh-CN/cloudflare.md)**。

先读 **[docs/zh-CN/architecture.md](docs/zh-CN/architecture.md)**——里面有完整
的组件清单、安全模型、全部运维注意事项。然后按你的平台指南部署：
**[macOS](docs/zh-CN/macos.md)** 或 **[Windows](docs/zh-CN/windows.md)**。

**为什么连接器后面挂着整个 harness:** 这座桥刻意投影经过审查的
DSH harness 能力面(沙箱 bash、会话、skills、`subagent_devin`),而不是
像 [CodexPro](https://github.com/rebel0789/codexpro) 那样做一个专用
工具 server(单 Node 进程、一等 MCP 工具、静态 token、无沙箱无
会话)。权衡分析与直接替换的可行性见
[architecture.md](docs/zh-CN/architecture.md#评估过的备选路线专用-mcp-servercodexpro)。

## 本项目在上游之上新增的部分

vendored 网关只是协议适配层；让它作为**长期**连接器运转起来的部分
是本仓库自己写的：

- **固定公网入口。** `worker/_worker.js` 是个极小的 Cloudflare Worker
  门面，持有连接器 origin；启动器每次运行把轮换的 quick-tunnel URL 重新
  发布给它(`PUT /_upstream`,Bearer 保护),各客户端的连接器 URL
  因此永远不变。
- **一键启动器。** `start.sh` / `start.ps1` 幂等且自定位 —— 复用存活
  host、轮换 tunnel(`--allowed-host` + `http2` 的规避已内置)、发布
  上游、重启 gateway。日常使用就是一条命令，不用重新配置。
- **幂等安装器。** `install.sh` / `install.ps1` 复制载荷、建 venv、装
  npm runtime、生成 `env.conf`;重跑只刷新代码 —— 配置、state、
  profile 改动、`bin/cloudflared` 永不被覆盖。
- **验证过的组合。** cordis patch 精确挂载本桥所需插件（持久 shell、
  task state、remote workers、可选门控 `subagent_devin`)，背后是审查
  过的 `chatgpt-external-v1` 能力画像；网关本地扩展覆盖多 mount
  exposure、路径感知 OAuth、callContract 提示与跨平台 SQLite —— 见
  [architecture.md](docs/zh-CN/architecture.md)。
- **平台手册。** macOS / Windows 指南 + architecture.md 里的完整运维注意事项
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
    architecture.md          参考文档 —— 组件、运维注意事项(先读这个)
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
  src/dsh_mcp_gateway/       vendored 网关源码 + 本地扩展
  LICENSE                    网关的 MIT license
  gateway-local-changes.patch   早期上游 delta 的历史记录(已应用；不再完整)
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
3. 连接器 URL 永久不变：
   - ChatGPT:`https://dsh-gw.<you>.workers.dev/chatgpt/mcp`
   - Gemini:`https://dsh-gw.<you>.workers.dev/gemini/mcp`
   - 通用客户端：`https://dsh-gw.<you>.workers.dev/mcp`

   重新认证 = 用 `env.conf` 里的 owner PIN。
4. ChatGPT 会话用 `workspace_switch` 绑定项目根；Gemini/通用客户端在
   `dsh_tool_call.workspace` 中按调用显式传项目根。`subagent_devin`
   委派给 Devin（锁死 `swe-2-high`），文件/沙箱工具在沙箱边界内工作。

## 工作区

默认情况下，客户端操作的是一个**受允许根约束的工作区**，不是你的整块磁盘。
下文的 `~` 指系统 home 目录（Windows 上即 `C:\Users\<你>`）。

- **`WORKSPACE_ALLOWED_ROOT`(环境变量)—— 允许根。** 默认 `~/data/workspace`;
  在 env.conf 里改可将整个可路由边界移走。解析后的根必须存在
  —— 目录缺失（含默认 `~/data/workspace` 缺失）时启动失败并给出修复提示。
  ChatGPT 的 `workspace_switch` 和通用面的 `workspace` 参数都只能指向该
  子树内(realpath 校验，拒绝符号链接逃逸)；指到根本身则把边界放宽到
  整棵子树。
- **`WORKSPACE`(环境变量)—— 启动/回落根。** 默认
  `<WORKSPACE_ALLOWED_ROOT>/default`（开箱即 `~/data/workspace/default`）。启动器
  自动创建缺失目录；同名位置已有非目录文件则启动失败。它是两类调用共同的
  默认根：ChatGPT 会话尚未绑定前落在这里，通用调用省略 `workspace` 时也落在这里。
  它是运营者拥有的入口，本身不强制位于允许根内；但若位于界外，客户端不能用
  `workspace_switch` 或 `workspace` 把它显式设为目标。
- **ChatGPT 面：会话绑定。** `/chatgpt/mcp` 暴露
  `workspace_switch`/`workspace_status`。绑定以 ChatGPT 每会话携带的
  `x-openai-session` 头为键，持久化在
  `home/storages/chatgpt-bridge/session-workspaces.json`，重启不丢；并发
  conversation 可以各自持有项目根。会话身份缺席时绑定被禁用，
  `workspace_switch` 显式失败，不会静默改写共享默认根。
- **Gemini/通用面：显式路由。** `/gemini/mcp` 与 `/mcp` 不依赖客户端提供
  会话标识，也不暴露 `workspace_switch`/`workspace_status`/`task_state` 这组会话套件。`dsh_tool_call` 顶层有可选 `workspace`
  参数：每次调用显式选择项目根；省略则使用 `WORKSPACE`。要继续同一项目，
  后续调用重复传同一个 `workspace` 值。它是路由键，不会进入目标工具的
  `arguments`，也不同于工具级的 `workdir`（后者只改变已选项目内单条命令的目录）。
- **边界**：沙箱化的 `bash`/`pwsh` 只能写活动根以内；`read`/`write`/
  `edit` 文件工具走自己的访问模型。切换/路由**不**修改既有 DSH 会话；
  bridge 退役旧 helper，并按新 cwd 惰性创建/复用 helper 会话，沙箱边界
  等于 helper 会话 `cwd`。

密钥类内容（`env.conf`、`ADMIN_PIN.txt`、`state/`）由 `.gitignore` 挡在
版本控制外——它们就生成在本目录里，但绝不入库。

## 致谢

MCP/OAuth 网关 vendored 自
[siddhartha-yz/dsh-mcp-gateway](https://github.com/siddhartha-yz/dsh-mcp-gateway)
(MIT，上游 commit `faf9606`)。本地扩展已直接应用在
`dsh-mcp-gateway/src/`;`gateway-local-changes.patch` 只保留早期 delta 的
历史记录，不再是完整差异。上游 MIT license 随载荷发布在
`dsh-mcp-gateway/LICENSE`。
