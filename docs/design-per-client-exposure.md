# 设计草案:按客户端策略暴露工具格式与显式 workspace 信封

- 状态: **草案(grilling 第一轮闭环,未开工)**
- 日期: 2026-11-25
- 关联: 与 `upgrade-to-dsh-0.2.md` **正交**——两者独立分支、独立合并、互不为前置

> **修订(2026-11-25,grilling 第一轮定稿)**:workspace 参数是**无稳定会话
> 标识客户端的专属暴露格式**;稳定标识客户端(ChatGPT)继续用 session 绑定 +
> workspace_switch/status 工具,不暴露 workspace 参数——每个客户端只有一种惯用
> 法,避免"既能又能"的二义性。统一只存在于 bridge 内部优先级链的实现层。
> 策略链降级为**统一管线 + profile 注册表**(纯数据);绑定表参与是 profile
> 控制的功能开关。Gemini 认证**复用现有 OAuth 体系**(DCR+PKCE+PIN 批准),
> 不引入静态 token。workspace 可发现性不新增工具:规则写进 Gemini 面
> dsh_tool_call 的 description,实际目录由模型用 bash 自行探索。
> Gemini web 接入前提(Connected Apps)已经 owner 实测连通。
>
> **修订(grilling 第二轮定稿,运维与认证层)**:①DCR 注册分层回收——
> 从未完成授权的注册(攻击噪声/半途配置)激进回收,完成过授权的注册
> **冗余保留不删**(闲置与删除服务端不可区分,宁冗余勿误删);②refresh
> token TTL 30d → 180d(减少闲置后的 PIN 重批准);③切换两阶段:先部署
> 新路径(/mcp 暂不动),连接器迁移验证后再翻 /mcp 为通用面,零破损窗口;
> ④实施必查:well-known 发现端点对任意路径后缀服务(/chatgpt/mcp 是
> 两层路径,RFC 8414 路径插入式发现需接住)。实证:生产库已有真实 Google
> DCR 注册(sandbox/test redirect),Gemini 的 OAuth 全流程已实机跑通。

## 1. 背景与问题

本项目服务 web 客户端访问本地工作区。不同 web 客户端提供会话标识的能力不同:

| 客户端 | 稳定会话标识 | 后果 |
|---|---|---|
| ChatGPT Web | **有**(每会话 header,跨重启稳定) | sessionId → 工作区绑定表机制成立 |
| Gemini Web | **无** | 绑定表无所锚定,隐式工作区机制整体失效 |

因此需要按客户端接入点暴露不同的工具格式。约束:ChatGPT 面**一行不动**,
继续沿用 sessionId 隐式绑定与现有 schema。

## 2. 关键架构事实(本设计成立的赋能条件)

1. **能力代理的键不含 sessionId**:`hash(workspace, presetId, compositionStamp)`。
   sessionId 在现状中仅用于查绑定表得到 workspace;查到之后的代理路由、cwd
   钉扎、Seatbelt 沙箱边界全部只认 workspace。因此"workspace 改由参数显式
   给出"时,执行层零改动。
2. **MCP 原生面永远只有 4 个 meta-tools**(`dsh_tool_catalog`/`dsh_tool_call`/
   `dsh_skill_catalog`/`dsh_skill_load`)。`workspace_switch` 等不是 MCP 工具,
   而是 catalog 返回的 JSON 数据条目,经 `dsh_tool_call` 通用调用执行。
   所以"不同工具格式"的差异面极小,不涉及 per-tool schema 手术。
3. **catalog 内容本就由 bridge 的 capabilityProfile 投影产生**,按客户端类
   出不同视图是既有机制的扩展,不是新机制。
4. 显式参数模式在并发语义上**优于**隐式绑定:每次调用自描述 workspace,
   无"切换与飞行中调用"的时序竞态。
5. **gateway 的 OAuth 体系是客户端无门的**:已落地实现含 DCR(`/register`)、
   PKCE(code_challenge/verifier 全链路)、operator PIN 批准页、签发/刷新/
   吊销——ChatGPT 经此接入。Google 官方对 Connected Apps 的要求(OAuth 2.1 +
   PKCE + discovery metadata)与此完全同构,Gemini 消费版走 DCR public PKCE
   客户端,无需预注册密钥。因此 Gemini 认证是**复用**,不是新建。

## 3. 设计决策(已收敛)

### D1 信封式 workspace,拒绝 per-tool 参数注入

Gemini 面的 `dsh_tool_call` 增加可选参数 `workspace`;catalog 内各工具
schema **保持原样**。workspace 不是工具参数,是**路由键**——没有任何 DSH
工具接受它,它只做一件事:选择执行代理。映射层唯一动作:从信封提取
workspace → 解析验证 → 选代理。"按命令决定是否传递"在此抽象下自然收敛:
workspace 永不进入任何工具的 arguments。

### D2 客户端识别用独立挂载点,零启发式

Gemini 经独立 URL path 接入(**同 gateway 进程**,如 `/mcp/gemini`;
OAuth/secrets/healthz 全复用);gateway 按挂载点确定 profile。
不采用 user-agent/OAuth client 等启发式信号(脆弱,版本迭代即漂移)。
operator 文档说明"哪个客户端配哪个 path",配置责任在用户侧。

### D3 workspace 解析优先级(bridge 唯一新增逻辑,客户端无感)

```
信封显式 workspace  >  session 绑定(仅 profile 启用绑定时参与)  >  启动默认根
```

优先级链在 bridge 内部统一实现(纯函数 `activeWorkspaceFor` 加一个分支,
一行量级)。但**暴露层刻意按客户端能力分化**:workspace 参数只在无稳定
标识客户端的 profile 暴露;稳定标识客户端(ChatGPT)不暴露该参数,继续用
workspace_switch/status 工具 + 绑定——每个客户端只有一种惯用法。

显式值与绑定一样过 `resolveAllowedWorkspace` 验证(`WORKSPACE_ALLOWED_ROOT`
子树强制、realpath 防符号链接逃逸),错误类与 workspace_switch 一致。

### D4 workspace 参数:可选,缺省 = 启动默认根

非必填,避免首轮交互被迫先探测;且 fallback 是唯一可预测的全局默认,
不是"回落 session 绑定"的混合语义(Gemini profile 不启用绑定)。
**可发现性不新增工具**:`dsh_tool_call` 的 description 写明合法规则
("allowedRoot 及其任意子目录可经 workspace 传入"),实际有哪些目录由
模型用 bash/ls 自行探索。workspace_status 因此**不进** Gemini 面——它报告
的信息已全部进 description。

### D5 catalog 按 profile 投影

```
gemini profile   → 剔除:
    workspace_switch   语义不复存在(无绑定可设;"切换"即下次传不同参数)
    workspace_status   信息已并入 dsh_tool_call description(见 D4)
    task_state         设计前提是持久会话跨轮续接,Gemini 无稳定会话可锚
    web_search         本部署无搜索凭证,执行面本就不可用(0.2.x 条件注册)
    能力/凭证维度过滤: 无(owner 定稿:web_search/remote_*/read_image 全保留;
              是否具备使用条件由模型自判,执行失败是诚实反馈,catalog 不替
              模型做能力假设;web_search 无凭证是部署级事实,对所有客户端
              平等,不属于 per-client 过滤层)
    结论: gemini catalog = 全量 - 绑定三件套,差异被 workspaceBinding 轴
              完全吸收,per-client 能力过滤清单整体不存在
chatgpt profile  → 现状,不变
```

发现与执行同 profile(架构契约):Gemini 即使猜到被剔除工具的名字,
执行同样拒绝。skill 维度(dsh_skill_catalog/dsh_skill_load)在 Gemini 面
**保留**;实施注意:skill 内容常假设持久工作区上下文,加载后指引模型用
workspace 参数显式锚定即可。

### D6 挂载解析直接返回实现对象,无 profile 注册表(owner 定稿)

差异面塌缩后(能力过滤清单为空,catalog 差异被绑定轴完全吸收),注册表只剩
一个布尔的花瓶——当配置数据退化成一个开关,数据就该让位给多态。注册表
整体蒸发:挂载表把 path 直接映射到**实现对象**(共同 exposure 接口背后,
底层拿到的是接口对象而非配置 flag),差异封装在接口的少数多态方法内,
而不是散落成管线各处的 `if binding:` 判断。

**关键反转:gemini 不是一个"客户端实现",它是通用实现**——显式 workspace
机制不依赖任何客户端特异能力,任何无稳定会话标识的客户端(未来的或未知
的)复用同一个通用实现对象,零新代码零新配置(除一条挂载)。唯一 bespoke
的是 chatgpt 实现:承载绑定套件(sessionId 提取/绑定表读写/绑定三件套
catalog/workspaceParam 隐藏),是对 ChatGPT 冻结行为的特化。

```
mounts(operator 配置,仅 path → 实现名,本身不含行为):
  /chatgpt/mcp  → ChatgptExposure   # 通用核心 + 绑定套件
  /gemini/mcp   → GenericExposure   # 通用实现;未来新客户端挂载即复用
  /mcp          → GenericExposure   # owner 定稿:旧路径语义新生为通用面,
                                    #   非兼容保留。三重作用:逃生舱(故障时连接器
                                    #   指回此 canonical URL)/迁移强制(语义已变,
                                    #   ChatGPT 必须迁走)/发现友好(最易猜的路径
                                    #   服务最通用的实现)。与 /gemini/mcp 共享同一
                                    #   实现对象实例,成本为零

exposure 接口的多态面(差异全部在此,其余全共享):
  catalog 尾滤      chatgpt 全量 / generic 剔除绑定三件套
  tool_call schema  workspaceParam 隐藏 / 暴露 + 教义文案
  信封提取          sessionId 透传 / 丢弃;workspace 参数升信封
```

执行路径(bridge 优先级链、代理路由、catalog 基础投影、skill 维度、
OAuth)全客户端共享,不属于任何 exposure 对象。正确的配置不可能表达错误
状态:"绑定禁用但 catalog 残留 workspace_switch"这类矛盾组合没有表达路径。

### D7 认证:Gemini 复用现有 OAuth 体系,不引入静态 token

Gemini 消费版 Connected Apps 走标准 MCP OAuth(DCR + public PKCE),与
ChatGPT 同构。接入流程:Gemini 配置 MCP URL(`/mcp/gemini`)→ 跳转批准页
→ operator 输 PIN → 签发 token。`DSH_MCP_GATEWAY_ADMIN_PIN` 继续只管批准
动作,不作访问凭证。**禁止复用 PIN 当静态 token**——Gemini URL 泄露将同时
交出 OAuth 批准能力,攻击者可自行批准恶意客户端。

已知接受项:两侧签发的 token 在 provider 层不可区分,理论上可跨挂载点使用;
单 owner 系统无实际影响。若未来需要凭证级隔离,按 client_id 绑定 mount
是增量改动,不影响本设计。

## 4. 工具类别分析(决定 Gemini 子集,而非参数传递)

| 类别 | 工具 | workspace 语义 | Gemini 面取舍 |
|---|---|---|---|
| 1. 直接敏感 | bash/read/write/edit/glob/grep | 决定操作哪个文件系统视图 | 含 |
| 2. 间接敏感(agent 态) | shell_session、job_list/output/kill | 持久 PTY 与后台 job 属主于 per-workspace 能力代理;序列一致性靠"模型持续传同一 workspace"自动成立;传错则目标 id 在该代理不存在,自然报错,局部可诊断(**owner 确认:不做显式校验**) | 含 |
| 3. 真正无关 | web_fetch | 纯网络操作,路由无关 | 含(不带 workspace 也正确) |
| —— | read_image | 图像读取,模型自行判断使用条件(owner 决策) | 含 |
| —— | workspace_switch / workspace_status / task_state / web_search / remote_machine / remote_exec | 见 D5 | 剔除 |

## 5. 完整链路

```
Gemini web ──MCP──> /mcp/gemini(同 gateway 进程,path 即 profile 选择)
  │  OAuth: DCR + PKCE + PIN 批准(与 ChatGPT 同一 provider)
  │
  ├─ dsh_tool_catalog ──> bridge /tools?client=gemini ──> gemini profile 投影
  │
  └─ dsh_tool_call(name, arguments, workspace?)
        │  gateway 提取 workspace 升为信封字段
        ▼
     bridge /call {name, arguments, workspace, sessionId}
        │  D3 优先级解析(binding 环节按 profile 跳过)
        │  + resolveAllowedWorkspace 验证
        ▼
     hash(workspace, preset, composition) ──> 能力代理
        │  cwd 钉扎 + Seatbelt 边界(per-agent,DSH 内部)
        ▼
     执行 —— 与 ChatGPT 面完全同路径
```

## 6. 安全语义变化(记账)

隐式模式下 workspace 变更只经 workspace_switch 一个显式动作;显式模式下
**模型每次调用自由指定 workspace**,prompt injection 攻击面从"诱骗一次
切换"变为"每次调用可指向别处"。缓解不变:`resolveAllowedWorkspace` 的
ALLOWED_ROOT 强制照旧。ALLOWED_ROOT 收紧是本设计的安全前提。

## 6.5 语义区分:信封 workspace ≠ 工具内 workdir

bash 等工具 schema 自带 `workdir` 参数(单次命令的 cwd 覆盖,在代理工作区内
相对解析,不动沙箱边界);信封 `workspace` 是代理路由键(决定沙箱根与所有
fs 工具的默认视图)。两者正交但极易被模型混用——Gemini 面 dsh_tool_call 的
教授文案必须显式区分:切项目用 workspace,项目内换目录用 workdir。

## 6.6 故障模式:sessionId 丢失——不做降级机制,故障必须响(owner 定稿)

场景:ChatGPT 客户端升级后不再发送稳定会话标识。**决策:不设计任何降级
机制**——该场景是推测性的,且所谓"配置级降级"本质是人工动作,写进设计
是冒充能力的投机。出问题时由 operator 手动恢复(连接器改指通用挂载 URL,
或重刷连接器),恢复路径因通用实现始终热备而天然存在,无需设计。

但手动恢复的前提是**故障必须响**,否则用户不知需要行动。因此以下硬规则
不是降级机制,而是正确性/可观测性要求,无条件实施:

1. sessionId 缺席 → 该请求**绑定完全禁用**(不查不写),解析直落默认根
   —— 空键会把所有对话 collapse 进同一条绑定(跨会话串味),必须杜绝;
2. workspace_switch 在无 sessionId 时被调 → **显式报错**("会话标识缺失,
   绑定不可用")——模型会把错误呈现给用户,这本身就是报警通道;
3. 启用绑定的挂载持续收到无 sessionId 请求 → log + healthz 可观测告警。

## 7. 非目标(本设计明确不做)

- 不改 ChatGPT 面任何事物(不暴露 workspace 参数,绑定语义不变——owner Q1 决策);
- 不为 Gemini 提供 task_state 等会话续接工具(待真实需求再设计);
- 不做启发式客户端识别;
- 不把 workspace 注入任何底层工具的 arguments;
- 不引入静态 token 或任何第二认证体系;
- 不做 per-path 策略链(统一管线 + profile 数据已覆盖全部真实差异)。

## 8. 开放问题(开工前确认)

全部第一轮问题已闭环。剩余的实施期验证项(非设计决策):

- [x] Gemini OAuth 实操验证:已实证——生产 oauth.sqlite3 存在真实 Google
      DCR 注册(grant_types/auth_method 与 ChatGPT 完全同构,public PKCE)。
      注:该注册走 sandbox/test redirect,属 Developer Mode 设施;正式路径
      机制相同。剩余硬要求:well-known 端点对任意路径后缀服务(两层路径挂载);
- [ ] OAuth 运维两项实施:DCR 分层回收 + refresh TTL 180d(第二轮定稿);
- [ ] gemini profile 的 catalog 投影需与 0.2.x 执行面条件(toolSet/`when`)
      保持一致求值——catalog 过滤不能只是名称黑名单,要与执行面同一解析路径
      (升级适配 6 的直接教训)。

## 9. 实施注记

- 在 main 另开分支;升级已落地,无分支协调负担。
- 切换两阶段(owner 定稿):阶段一部署全部代码但 /mcp 维持 ChatGPT 行为,
  operator 迁移连接器至 /chatgpt/mcp 并验证;阶段二小部署翻 /mcp 为通用面。
  全程零破损窗口;"暂不动"是部署排序,非兼容代码。
- **实施进度(2026-11-25)**:✅ ①bridge 完成(commit a429939,信封
  workspace+优先级链+binding 轴过滤+sessionId 缺席加固);⬜ ②gateway
  exposure 对象/挂载路由/教义文案;⬜ ③OAuth 三件套(well-known 任意后缀/
  分层回收/refresh 180d);⬜ ④golden-path 双侧验证;⬜ ⑤两阶段部署。
  ②③④⑤ 待续,按本文档规格实施即可。
- 验证可复用 golden-path 思路:ChatGPT 面回归(现状不变)+ Gemini 面
  模拟请求(workspace 显式路由、越界拒绝、缺省回落、序列一致性、
  绑定表零写入)。
