# Windows 部署指南

[English](../en/windows.md) | 中文

[architecture.md](architecture.md) 中架构的平台层。`install.ps1` 把
`dsh-mcp-gateway\` 载荷装成 `%USERPROFILE%\.dsh-mcp-gateway` 部署目录
(`-Target` 可改目标；`start.ps1` 自定位)。

## 前置条件

- **Node.js ≥ 24.9** + npm
- **Python 3.12**(`py -3.12 -m venv`）用于网关
- **cloudflared** —— `winget install --id Cloudflare.cloudflared`，或从
  [cloudflared releases](https://github.com/cloudflare/cloudflared/releases)
  下载 `cloudflared-windows-amd64.exe` 放为 `bin\cloudflared.exe`。两种方式
  都把 env.conf 的 `CLOUDFLARED` 指向该 exe。单一静态二进制，无需安装器
  —— 参数与 macOS 相同（`tunnel --url … --protocol http2 --no-autoupdate`)
- **能出网访问 Worker** —— 启动器要 PUT tunnel URL 并做健康检查。如果你的
  网络直连不了 `*.workers.dev`（如中国大陆），跑一个本地 HTTP(S) 代理并在
  env.conf 设 `PROXY=`(Windows 10+ 自带 `curl.exe`，支持 `--proxy`)。
- 可选 Devin：适用于 Windows 的 `devin` CLI —— 见 `devin/`

`@deepseek-ai/dsh` 在 npmjs.org 公开 —— 不需要 `.npmrc` 或私有源。

## 安装

```powershell
cd <repo>                      # 项目目录

# 1) 安装器 —— 把 dsh-mcp-gateway\ 复制到目标目录、建 venv、装网关
#    (editable;上游 @faf9606 + 我们的 patch,已应用)和 npm runtime、
#    生成 env.conf。
#    原生模块: node-pty/koffi 走自带预编译产物 —— 仅在没有匹配
#    prebuild 的平台上才需要工具链。
#    任何时候重跑都只刷新代码 —— env.conf、state\、profile 改动、
#    bin\cloudflared.exe 永不被覆盖。
powershell -ExecutionPolicy Bypass -File .\install.ps1
#                                -> %USERPROFILE%\.dsh-mcp-gateway(-Target 可改)

#    版本注意: 0.1.2 的 pin 早于 sandbox-policy 的
#    additionalWritableRoots —— 0.2.2-rc.3 已带；需要时升 pin。

# 2) Worker 门面 —— 在 Cloudflare 控制台部署 worker/_worker.js;
#    分步说明: docs/zh-CN/cloudflare.md
#    (KV 命名空间绑定为 UPSTREAM,密钥 ADMIN_SECRET
#    = env.conf 里的 UPSTREAM_ADMIN_SECRET)

# 3) 配置 + 启动 —— 之后都在部署目录里操作
cd "$env:USERPROFILE\.dsh-mcp-gateway"
notepad env.conf               # 填密钥;CLOUDFLARED 指向你的 exe,
                               # 或把 exe 放到 bin\cloudflared.exe
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

可选 Devin：部署目录里的 `devin\README.md` —— 把 package 片段合并进
`home\profiles\web\package.json`、追加 patch 行、设 `DSH_SUBAGENT_DEVIN=1`、
重跑 `start.ps1`。

把 ChatGPT 开发者模式连接器指向 `$WORKER_PUBLIC_URL/mcp`，用 owner PIN
完成 OAuth。

## 启动与运维

- `start.ps1` 与 start.sh 一样幂等：经 `Win32_Process` CommandLine 做
  端口级进程匹配，`Start-Process -WindowStyle Hidden` 加日志重定向，
  清 tunnel 日志，按新主机名推 `--allowed-host`,PUT `/_upstream`，重启
  网关。
- `start.ps1` 会替换 cordis patch 模板里的 `<DSH_GW_HOME>` 并生成
  `deploy/dsh/chatgpt-bridge.windows.resolved.cordis.yml` 再启动 —— cordis 的
  `name:` 必须是字面量，所以模板放占位符（正斜杠在 Windows 的 Node 里
  有效）。
- 子进程继承启动器环境，env.conf 里的 `DSH_SUBAGENT_DEVIN=1` 自然到达 DSH
  host —— 没有 macOS 那种 source/export 的坑。
- 如果网络封了 `*.workers.dev`，在 env.conf 设 `PROXY=` —— Windows 10+
  自带 `curl.exe`，支持 `--proxy`。
- 日志：`$env:TEMP\dsh-web-host.log`、`cloudflared.log`、
  `dsh-mcp-gateway.launch.log` —— 启动级，每次启动截断（另有 `*.err.log` —— `Start-Process` 分流
  stdout/stderr)。健康检查：`GET /api/chatgpt-bridge/revision`。

## 平台说明

- **Shell 工具：`pwsh`** —— `standard` preset 在 win32 挂 `tool-pwsh`
  （其他平台挂 `tool-bash`）自动完成；Windows patch 已把 `pwsh` 加进
  `allowExtraTools`。契约与 bash 同形：`command` + `description`。
- **沙箱：`windows-acl`，部分强制** —— 经工作区 SID 的 ACL ACE 做写
  授权；约束强度低于 seatbelt/bwrap。把 Windows 上的 `workspace-write`
  当尽力而为看。
- **`shell_session`、`remote_machine`、`remote_exec`** 留在
  `allowExtraTools` 但 win32 上投影为空（provider 已禁用）—— 无害。
  shell 需求由 `pwsh` 工具覆盖。
- **二进制**:`node_modules/.bin/dsh.cmd`、
  `.venv\Scripts\dsh-mcp-gateway.exe`。
- **工作区根**:`%USERPROFILE%\data\workspace` —— bridge 经 `os.homedir()` 解析，
  `workspace_switch` 的允许根自动跟随。
- **网关**:Python venv 与 `oauth.py` 的 `/proc/self/fd` 回退本来就覆盖
  Windows —— 除 `gateway-local-changes.patch` 外无需额外源码改动。
