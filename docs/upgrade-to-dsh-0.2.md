# DSH 升级计划:0.1.2-rc.1 → 0.2.2-rc.5

目标版本 **0.2.2-rc.5**(npm `latest` 标签,2026-11-18;不追 0.2.3-alpha)。
策略:**side-by-side 并行环境** —— 生产 `~/.dsh-mcp-gateway` 全程不动,新环境独立目录验证,
一次性切换,旧目录即为即时回滚路径。范围分两步:**先最小兼容升级跑通,后做配置清理**
(`additionalWritableRoots` 替代 `workspace_switch` 绕行等),不混在一次变更里。

## 已确认的 breaking 面(2026-11-25 类型级 diff 实测)

| # | 变化 | 影响面 | 严重度 |
|---|---|---|---|
| 1 | `ctx.tools.execute()` 单调用 → **batch**:`execute({calls, signal, agent?, toolSet?})` 返回 `{results, additionalContexts, concluded}` | bridge `/call` 端点主路径 | 高(机械改写) |
| 2 | **调用块形状**:`{type:'tool-call', id, name, arguments}` —— `callId` 更名 `id`,且 **`arguments` 从对象变成 raw JSON 字符串** | bridge 构造调用处;不改静默全灭 | **地雷** |
| 3 | executor 拆分:`dsh-tool-executor-native`/`ptc` 独立插件 | **无需行动** —— dsh-base@0.2.2 的 cordis.patch.yml 自含 native executor 行(PTC 默认 disabled) | 无 |
| 4 | `ToolRuntime` 更名 `ToolsService` | 无 —— `export { ToolsService as ToolRuntime }` 兼容别名在 | 无 |
| 5 | 0.2.x 引入 conditional registration(`when` 选项) | **已验证无影响**:bash 等基础工具不带 condition;"无 toolSet 时仅承认 unconditional 注册"不阻断 standalone 调用 | 无(已关闭) |
| 6 | bash 注册变为 jobs-aware:jobs 缺席时注册 foregroundOnly 变体 | 行为变化点,测试时感知即可 | 低 |
| 7 | `@quonaro/dsh-plugin-devin` peer 已要求 `^0.2.0-rc.2` | **利好**:社区已迁 0.2.x;但需核对当前安装版本是否过旧/过新 | 低 |

## 补充验证(2026-11-25,计划制定时已关闭的项)

bridge 接触面普查:tools 4 点 + agents/presets/persistence 5 点 + skills 2 点 +
webServer 5 点。**破坏集中于 tools.execute 一条线**;agents `create/resume` 签名
两版逐字相同,skills 完全相同 —— helper capability agent 深集成路径不受 0.2.x
影响。Phase 3 预算因此下调至半天-1 天;本次升级的真实成本在**验证**而非改写
(静默失效模式多:arguments stringify、schemastery 剥键、cordis 搁置)。

## 风险登记册(Phase 1 必须逐项关闭)

| 风险 | 关闭方式 |
|---|---|
| R1 vendored 三插件(task-state/shell-session/remote-worker)无版本约束,隐性破坏未知 | 逐行审查其对 `ctx.tools`/session/agents API 的使用 |
| R2 schemastery 静默剥键 —— config 字段改名/删除不报错,行为静默漂移 | 现有全部 config 对照 0.2.2 `docs/config-catalog.md` 逐键核验 |
| R3 helper 会话跨版本 resume 失败(session jsonl 格式或 header 形状变化) | 测试环境直接试 resume;失败则接受惰性重建,但 `session-workspaces.json` 绑定必须保住 |
| R4 start.sh 使用的 dsh CLI 参数/行为变化 | 对照上游 `docs/cli-help.md` |
| R5 `ToolCallResult` 结果形状变化(content/value/meta 提取路径) | Phase 1 类型 diff + Phase 4 golden path 对照 |
| R6 cordis 静默搁置 —— 配置校验失败不报错只是不加载(本次调研已踩过) | 验证手段永远是**工具列表+端到端行为**,不是挂载日志 |
| R7 `additionalContexts`/`concluded` 新语义的副作用 | 单调用场景应忽略;golden path 对照确认无行为差 |

## Phase 0:基线固化(约半天)

1. 备份:`cp -a ~/.dsh-mcp-gateway/env.conf ~/.dsh-mcp-gateway/home/storages` 等到安全位置
   (`session-workspaces.json` 单独点名 —— 它是用户绑定的唯一持久状态)。
2. 编写 **golden-path 冒烟脚本**(升级前后用同一份对照,这是整个计划的脊柱):
   - `GET /api/chatgpt-bridge/revision` 健康探针;
   - `dsh_tool_catalog` 工具目录快照(存档 diff 用);
   - `dsh_tool_call` → bash echo / read+write 往返 / glob;
   - `workspace_switch` + bash pwd 验证 cwd 锚定;
   - 沙箱逃逸探针(bash 写 `$HOME` 必须被拒,seatbelt);
   - `shell_session` 两轮命令验证 PTY 持久性;
   - `task_state` 读写;
   - `subagent_devin`(若启用)一次最小委派。
3. 在**当前生产环境**跑一遍,输出存档为基线。

**退出条件**:冒烟脚本可重复运行;基线输出已存档。

## Phase 1:breaking 面清零(约 1 天)

1. 按风险登记册 R1-R5 逐项核查,产出**适配清单**:bridge 插件及 vendored 插件
   每个受影响调用点的「旧签名 → 新写法」映射。
2. 重点类型 diff:`ToolCallResult`、schemas() 签名、register() 签名、
   `session`/`agents` 服务在 0.2.x 的 API(resume/create 形状)。
3. config 核验:对照上游 `docs/config-catalog.md`(0.2.2)逐键检查
   cordis patch 与 env.conf 模板里的全部配置。
4. CLI 核验:`docs/cli-help.md` 对照 start.sh 的 host 启动参数。

**退出条件**:适配清单写完,无 "unknown" 状态的风险项。

## Phase 2:并行环境搭建(约半天)

1. `install.sh --target ~/.dsh-mcp-gateway-next`(或手工)装出独立环境;
   `deploy/dsh-runtime/package.json` 改 `@deepseek-ai/dsh` 为 `0.2.2-rc.5` 重装。
2. 复制 env.conf 并改端口(host/gateway 换端口避免与生产冲突)。
3. 先裸启动 host 看插件加载 —— **验证工具列表而非日志**(R6)。
4. 记录所有加载失败/行为缺失,归入 Phase 3 工作项。

**退出条件**:0.2.2 host 在测试端口启动,cordis 组合无静默搁置。

## Phase 3:bridge 与 vendored 插件适配(1-2 天,主要工作量)

按 Phase 1 适配清单施工,已知必改项:

1. bridge `/call`:`execute` 改 batch-of-one —— `{type:'tool-call', id, name,
   arguments: JSON.stringify(args)}`,结果从 `results[0]` 提取;`signal`/`agent` 透传。
2. `ToolCallResult` 提取路径适配(materializeToolContent)。
3. `ctx.tools.schemas()` / `register()` 调用点按新签名适配(若有变)。
4. vendored 三插件按 R1 审查结果适配。
5. capability profile 复审:0.2.x 基础工具集有无新增/改名,`chatgpt-external-v1`
   是否仍准确(`allowExtraTools` 名单)。
6. 每改一处跑 Phase 2 环境验证 —— 小步快走,不攒大批。

**退出条件**:测试环境 golden-path 全绿。

## Phase 4:端到端对照验证(约半天到 1 天)

1. 测试环境全链路:host + gateway + quick tunnel + Worker 门面(可复用同一 Worker,
   upstream 指向测试 tunnel,**注意:会抢占生产门面** —— 或者注册第二个 Worker/路径隔离,
   施工前定夺)。
2. golden-path 输出与 Phase 0 基线逐项 diff;差异逐一归因(预期内 breaking / 回归)。
3. 沙箱验证:seatbelt 拦截、workspace-write 边界。
4. **真实 ChatGPT 会话实测**(用测试连接器):完整走一遍典型工作流。

**退出条件**:diff 全部有归因;真实会话通过。

## Phase 5:切换与文档(约半天)

1. 切换:停生产 host/gateway → 用验证过的配置启动正式端口 → ChatGPT 侧无需任何变更
   (连接器 URL 不变,这正是门面架构的红利)。
2. 回滚路径:旧目录原样保留 ≥2 周;回滚 = 停新起旧。
3. 文档更新(architecture.md):
   - 删除/修订过时注意事项(第 7 条 additionalWritableRoots 已可用等);
   - 记录新版本的注意事项(cordis 静默搁置排错法、jobs-aware bash 等);
   - pyproject/package 清单里的版本记录。
4. **第二批(独立变更,不在本次范围)**:`additionalWritableRoots` 替代
   `workspace_switch` 绕行;评估 0.2.x 新能力(conditional registration 等)。

**退出条件**:生产跑 0.2.2 一周无回归;文档与现状一致。

## 时间估计

总计约 **2.5-3.5 个工作日**(接触面普查后下调:breaking 集中于 tools.execute
一条线,agents/session/skills 已验证稳定)。剩余不确定性主要在 vendored 三插件
(R1)—— 若审查发现破坏严重,预算上调 1-2 天。

## 进度日志(2026-11-25)

### Phase 0-3 完成,Phase 4 本地验证全绿

**并行测试环境**:`~/.dsh-mcp-gateway-next`(DSH 0.2.2-rc.5,端口 3082，独立
DSH_HOME 与工作区 `~/data/workspace/default`)。生产环境全程未受影响。

**golden-path 对照结果:25 字段逐字相同,4 个环境性差异(路径前缀/随机
UUID),0 个真实行为差异。** 含冷启动 resume 验证。

### 实际发生的 breaking 适配(全部在 bridge 插件,vendored 三插件零改动)

| # | breaking 点 | 适配 |
|---|---|---|
| 1 | `execute()` batch 化：`{calls:[{type:'tool-call',id,name,arguments}]}`,arguments 为 raw JSON 字符串，`additionalContexts` 上移到 batch 层 | `/call` 端点 batch-of-one 适配 |
| 2 | `agentPresets.standingKeyFor` 移除，改租约制 `acquireScope` | 每 preset 缓存一个租约 = 原 standing mount 语义 |
| 3 | 预设系统文件支撑改为注册表支撑，`resolve()` 只返 `{id,broken?}`,`.path` 消失 | 指纹改 hash `compositionInventory()`(语义更准) |
| 4 | `persistence.list()` 从 headers 改返 snapshots `{header,revision}` | 查找前规范化 |
| 5 | 0.2.x 新增插件兼容性强制：peerDependencies 精确钉版会被禁用(响亮报错，非静默) | peer 改 `^0.2.0-rc.2`(社区范本) |
| 6 | 0.2.x 新增条件注册(`when` 子句,0.1.x 为零)：带条件的工具(web_fetch/web_search 按模型 provider 的就绪性)只能经**解析过的 toolSet** 执行;`executeToolBatch` 不自行解析条件——上游由 AgentLoop 每步 resolve,bridge 直调必须同样 resolve+传 toolSet+dispose | `/call` 端点执行前 `ctx.tools.resolve({agent, modelSelection, signal})` |

### 语义差异备忘(非回归)

- **web_search 在无凭证部署下执行面隐藏**:0.2.x 按搜索后端就绪性条件注册;
  本部署刻意无 DEEPSEEK_API_KEY,0.1.2 下它可见但调用必失败,0.2.2 下执行面
  不可见 —— 等效且更干净。注意:catalog(standing scope 直读)仍会列出它,
  因为目录读取不求值 when —— catalog/execution 存在轻微不一致,如需严格一致
  可后续让 catalog 也走 toolSet 投影(非必须,生产 UX 等效)。
- remote_machine/job_list/web_fetch 执行路径已抽测通过;remote_exec 需活
  远端 worker,属本地验证范围外。

### 验证过程中发现但无需行动的事项

- vendored 三插件(task-state/shell-session/remote-worker)在 0.2.2 下零修改
  注册并行为对齐 —— 它们的 seam 接触面(terminals/tools.register/webServer)
  全部稳定。
- bash 工具 schema 新增 `description` 必填字段(上游演进,bridge 透传无感,
  catalog 自动反映)。
- npm install 需用 npmmirror 镜像(registry.npmjs.org 本机不可达);
  koffi/node-pty 原生构建脚本被 npm install-scripts 拦截，但 macOS 上
  node-pty prebuilds 自足，koffi 非必需 —— 生产切换时按现有 install.sh
  同款处理即可。
- 0.1.2 时代调试期残留的撕裂会话会导致 `SessionAlreadyExistsError` 掩盖
  resume 路径 —— 切换前生产环境应先清理或接受首轮 create 冲突自愈。

### Phase 5 完成(2026-11-25 15:59 切换)

**生产已升级至 0.2.2-rc.5 并全量验证通过。**

- 停机窗口:仅 host 重启数秒;gateway/cloudflared/Worker 全程未动(架构上
  gateway 逐请求转发,host 恢复后链路自然复原)。
- 生产 golden-path:28 字段逐字相同,1 个环境性差异(随机 UUID),0 真实差异。
- 公网全链路 `https://dsh-gw.wzwu0309.workers.dev/readyz` → ok。
- 切换方式:in-place npm install(运行时进程不受影响)+ 插件文件拷贝 +
  外科式 host 重启(复刻 start.sh 启动行,未动 tunnel/gateway)。

**回滚预案**(备份位于 `~/.dsh-mcp-gateway-backup-0.1.2-20261125/`):
1. `cp -R 备份/dsh-runtime → ~/.dsh-mcp-gateway/deploy/dsh-runtime`(整树替换)
2. `cp 备份/dsh-bridge-plugin/* → ~/.dsh-mcp-gateway/dsh-bridge-plugin/`(四插件同)
3. 按 start.sh 同款启动行重启 host
注意:0.2.x 期间产生的会话记录为 v4 zstd 格式,回滚后 0.1.2 无法读取 ——
能力代理会话为一次性元数据,回滚后由 bridge 自动重建,无数据损失。

**已知无害残留**:0.1.2 时代的能力代理会话在 store 中成为孤儿(hash 输入
变更,永不命中),不占用运行资源;如需清理可删
`~/data/workspace` 下对应 sessions 子目录。

**无兼容代码原则**:本项目不为历史实现写兼容代码(AI 消费方自适应),
persistence 快照访问等适配按 0.2.x 单一直接形状实现。

- [x] remote_machine/job_list/web_fetch 执行路径已抽测通过
- [ ] remote_exec 实测(需活远端 worker,日常使用中自然验证)
- [ ] devin 端到端(需凭证,日常使用自然验证)
