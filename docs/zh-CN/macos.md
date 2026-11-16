# macOS 部署指南

[English](../en/macos.md) | 中文

[architecture.md](architecture.md) 中架构的平台层。`./install.sh` 把
`dsh-mcp-gateway/` 载荷装成 `~/.dsh-mcp-gateway` 部署目录（`--target`
可改目标；`start.sh` 自定位）。

## 前置条件

- **Node.js ≥ 24.9** + npm(npm runtime 的下限是 22.15 —— `zlib.zstd`
  要求，DSH 会话持久化要用）
- **Python 3.12**(`venv`、`pip`)用于网关
- **cloudflared** —— `brew install cloudflared`（原生 arm64/x86_64)，或从
  [cloudflared releases](https://github.com/cloudflare/cloudflared/releases)
  下载 `cloudflared-darwin-*` 二进制。复制到 `bin/cloudflared`，或把
  env.conf 的 `CLOUDFLARED` 直接指到安装路径（如
  `$(brew --prefix)/bin/cloudflared`)
- **能出网访问 Worker** —— 启动器要 PUT tunnel URL 并做健康检查。如果你的
  网络直连不了 `*.workers.dev`（如中国大陆），跑一个本地 HTTP(S) 代理并在
  env.conf 设 `PROXY=`。
- 可选 Devin:`devin` CLI —— 见 `devin/`

`@deepseek-ai/dsh` 在 npmjs.org 公开 —— 不需要 `.npmrc` 或私有源。

## 安装

```bash
cd <repo>                      # 项目目录

# 1) 安装器 —— 把 dsh-mcp-gateway/ 复制到目标目录、建 venv、装网关
#    (editable;上游 @faf9606 + 我们的 patch,已应用)和 npm runtime、
#    生成 env.conf。
#    原生模块: node-pty/koffi 走自带预编译产物 —— 标准架构无需工具链
#    (allowScripts 表必需)。
#    任何时候重跑都只刷新代码 —— env.conf、state/、profile 改动、
#    bin/cloudflared 永不被覆盖。
./install.sh                   # -> ~/.dsh-mcp-gateway(--target DIR 可改)

#    版本注意: 0.1.2 的 pin 早于 sandbox-policy 的
#    additionalWritableRoots —— 0.2.2-rc.3 已带；升 pin 后把 bridge
#    patch 注释里说的 sandbox-policy 块加回来。

# 2) Worker 门面 —— 在 Cloudflare 控制台部署 worker/_worker.js;
#    分步说明: docs/zh-CN/cloudflare.md
#    (KV 命名空间绑定为 UPSTREAM,密钥 ADMIN_SECRET
#    = env.conf 里的 UPSTREAM_ADMIN_SECRET)

# 3) 配置 + 启动 —— 之后都在部署目录里操作
cd ~/.dsh-mcp-gateway
$EDITOR env.conf               # 填密钥;CLOUDFLARED 指向你的二进制,
                               # 或把二进制放到 bin/cloudflared
./start.sh
```

可选 Devin：部署目录里的 `devin/README.md` —— 把 package 片段合并进
`home/profiles/web/package.json`、追加 patch 行、设 `DSH_DEVIN=1`、
重跑 `./start.sh`。

把 ChatGPT 开发者模式连接器指向 `$WORKER_PUBLIC_URL/mcp`，用 owner PIN
完成 OAuth。

## 启动与运维

- `./start.sh` 幂等，可安全重跑。每次运行：只杀匹配我们端口的进程
  (`pkill -f` 模式含 `--url ...:8766` / `--dsh-harness-url ...:3081`),
  复用活着的 host，清空易产生二进制的 tunnel 日志，以
  `cloudflared --protocol http2` 启动，提取新的 `*.trycloudflare.com`
  URL,PUT 到 Worker(`/_upstream`)，并按新主机名带
  `--allowed-host <new-host>` 重启网关。
- 如果网络封了 `*.workers.dev`，在 env.conf 设 `PROXY=` —— 启动器自己的
  外发 curl（发布 upstream、健康检查）会走它。ChatGPT 从外部直连 Worker,
  不受影响。
- `env.conf` 是 `source` 而非 export —— DSH host 需要的值必须在启动行
  显式转发（这就是 `start.sh` 传 `DSH_DEVIN="${DSH_DEVIN:-}"` 的原因）。
- 日志：`/tmp/dsh-web-host.log`、`/tmp/cloudflared.log`、
  `/tmp/dsh-mcp-gateway.log`。健康检查：`GET /api/chatgpt-bridge/revision`。

## 平台说明

- **Shell 工具：`bash`** —— `dsh-bash-sandbox` + seatbelt，完整约束。
  `shell_session`（持久 bash PTY）在 POSIX 上可用。
- **插件路径**:cordis patch 的 `name:` 必须是字面量 —— patch 模板用
  `<DSH_GW_HOME>` 占位符，`start.sh` 启动前替换生成
  `deploy/dsh/chatgpt-bridge.macos.resolved.cordis.yml`。
- **工作区根**:`~/data`(bridge 经 `os.homedir()` 解析）。
- **可选 Devin**:`devin/` + `DSH_DEVIN=1`;`devinPath` 写绝对路径
  （如 `~/.local/bin/devin`),不依赖 PATH。
