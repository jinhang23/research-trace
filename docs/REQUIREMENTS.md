# Research Trace 最终需求与实现基线

状态：已确认，作为实现基线。
日期：2026-08-19

实现注（`2.0.0-alpha.4`）：

- GitHub OAuth 网页登录、PKCE、HttpOnly 哈希会话、CSRF、`reader/member/admin`、管理员用户管理，
  以及由 GitHub 账号批准的逐设备登录、撤销和自动凭证读取。原始 GitHub access token 不作为设备凭证。
  设备凭证有有效期（`TRACE_DEVICE_CREDENTIAL_DAYS`，默认 90 天），可用未过期凭证自助续期。
- 写入身份只来自凭证，不来自请求体；只有浏览器会话算 `human`，机器凭证一律 `recorder`。
- **采集是按项目 opt-in 的**：只有放了 `.research-trace.json` marker 的目录会被记录（§6、§7、§13）。
- **原始投递由独立进程 `trace-deliver` 负责**，不经过 Recorder、不经过 hook 的网络调用（§6、§12）。
- 备份导出按年份/容量分卷（格式版本 3；版本 2 的旧树仍可 verify/restore）并带容量阈值告警（§13），
  告警显示在健康视图的备份卡片上；数据流派生视图（§8）在网页项目视图里作为第三种呈现方式出现；
  团队配置映射（§7.1）已实现，但它的**网页管理界面**仍【未实现】，目前靠 REST 或直接编辑
  数据目录里的 JSON。
- 文档中标注【未实现】的条目是待办，不是错误描述：它们仍然是验收目标。

## 1. 产品目标

Research Trace 是团队共用的研究与项目记忆。它服务于使用 Claude Code、Codex CLI 和
Codex Desktop 完成的真实工作：想法讨论、论文阅读、数据理解、实验、试错、代码实现、
评估和阶段性总结。

系统分成两层：

1. **原始历史层**永久保存宿主实际暴露的对话、工具调用、子 agent 和运行事件，用于搜索和核对。
2. **语义记录层**由独立 Recorder 只整理以后值得理解、复用或避免重犯的内容，用于日常阅读。

首要原则：容易启用、不打断主任务、不伪造归因、不把低价值操作堆进项目主视图。

## 2. 用户、宿主与部署

- 第一优先宿主是 Claude Code，随后适配 Codex CLI 和 Codex Desktop。
- 一台中央服务，多台工作站和 HPC 节点通过 HTTPS/MCP 写入。
- 多人团队使用；成员默认可看到团队内全部项目。
- 网页最终使用 GitHub OAuth；机器使用独立、可撤销的 token。
- 权限保留只读、成员、管理员三档。
- 中央服务是唯一长期真相源。SQLite 只由单个服务实例访问，附件放服务数据卷。

## 3. 核心数据模型

```text
Project
├── Overview
│   ├── 当前项目总结
│   ├── 当前假设、待确认问题、关键决定、经验与错误
│   ├── 内联 Comments / Corrections
│   └── revisions / milestones
├── Chapters（用户定义的并列研究线/实验组，彼此没有时间顺序）
│   └── Nodes（Chapter 内按时间排列，可选 parent）
│       ├── 内联 Comments / Corrections
│       ├── Code Evidence
│       └── Attachments / Artifact References
└── Raw History
    └── Sessions → Agents → Events / Transcript chunks
```

### 3.1 Overview

- Overview 表达项目**当前认识**，不是追加式流水账。
- 项目级假设、争议、决定、阶段性成果、insights 和 mistakes 都直接写在 Overview。
- 人可以选中 Overview 的内容评论、确认或纠正；人工纠正是后续 Recorder 的最高优先级上下文。
- AI 更新 Overview 时不得覆盖人工 pinned 内容或未处理的 correction。
- 当前文本保持简洁；旧版本通过 revision diff 和显式 milestone 查看，不画时间树。

### 3.2 Chapter

- Chapter 是用户定义的并列研究线或实验组，例如“主实验”“消融实验”“基线复现”“补充实验”。
- Chapter 不是“数据理解”“实现”“评估”等内容类型，也不是线性 pipeline 阶段；这些内容仍是其所服务研究线中的 Node。
- Chapter 之间没有时间、父子或 pipeline 顺序。
- Chapter 有一份当前摘要；摘要是其 Nodes 的可编辑、带版本视图。
- `Inbox` 是每个项目的内建 Chapter，用于 Recorder 暂时无法可靠分类的内容。
- Chapter 的创建、改名和范围由人管理；Recorder 只能选择已有 `chapter_id`，不得自行发明 Chapter。

### 3.3 Node

- Node 是唯一的通用语义记录实体。
- 论文搜索、想法讨论、数据理解、实验、失败方案、关键实现和结果都使用同一个 Node 模型。
- Node 在 Chapter 内按发生时间排列；只有确实是前一节点延续时才设置 `parent_id`。
- 一段对话可以产生零个、一个或多个 Node，也可以更新既有 Node。
- 不建立 Experiment、Entry、Insight、Mistake、Pipeline 等平行实体。
- 内容分类只用可选、可编辑的 labels；分类错误不能造成数据丢失。

### 3.4 Comments / Corrections

- Comments 不是独立导航模块，只附着于 Overview、Chapter 摘要、Node 或其中的文本锚点。
- 评论可以是普通 comment、confirmation 或 correction。
- 被纠正的当前文本可以更新，但旧文本、评论和来源必须保留在 revision history。
- 记录可以是 `unreviewed`、`confirmed` 或 `corrected`；默认 `unreviewed`，不强迫用户逐条审核。

## 4. Recorder

Recorder 是后台编辑者，不是文件监控审计员。每个 batch 可以：

- 不创建任何语义记录；
- 创建或更新一个或多个 Node；
- 更新 Chapter 当前摘要；
- 在内容确实影响项目全局认识时更新 Overview；
- 把无法可靠分类的内容放进 Inbox。

Recorder 创建的 Node 一律为 `unreviewed`。确认同时表示人已确认内容和 Chapter 归属；人类移动、
编辑、确认或纠正过 Node 后，Recorder 的旧幂等重试不得覆盖该版本。内容跨多个 Chapter 时可以
拆成多个 Node；不能可靠拆分时放 Inbox，不猜测归属。

Recorder 应记录：为什么做、关键方法、重要命令和参数、指标及口径、图片、产物位置、
结论、失败原因、可复用实现和下一步。它不为普通文件读取、格式化、临时调试或每次工具调用建 Node。

用户显式说“记录这个”“总结本阶段”或“更新项目 Overview”时必须执行；自动判断作为默认便利，
不是唯一入口。

## 5. 关键代码实现记录

Research Trace 不自动保存每轮 Git diff，不为每次实验建 branch，也不尝试从共享 working tree
推断逐 agent 作者。

Recorder 只在核心算法、流程、接口、配置、数据格式、重要 bug 或可复用实现发生变化时记录：

- 实现目的、方法和设计理由；
- repo、commit（若存在）、文件路径和 symbol；
- 精选 diff 或代码片段；
- 参数、依赖、环境、验证结果和限制；
- 来源 session/events 和可确认的贡献者。

保存优先级：

1. 已 commit：commit + path + symbol + 内嵌关键 diff/snippet。
2. 未 commit 且重要：snippet + 内容 hash；必要时附小型完整文件。
3. 独立小脚本或配置：可以作为完整附件。
4. 大文件、数据、checkpoint 和生成产物：只登记 URI/path、机器、大小和校验和。

Code Evidence 原文不可被 AI 注释覆盖。代码证据、Recorder 注释和人工评论分开保存。
共享 working tree 下归因不明确时标为 `ambiguous` 或“并行 agent 协作”，不得虚构作者。

## 6. 原始历史与 Claude Code 接入

### 6.0 采集范围：按项目 opt-in

装上插件不等于同意记录这台机器上的每个项目。Hook 每次运行的第一件事是从 `cwd` 向上寻找项目
marker `.research-trace.json`（§7）：

- 找不到 marker，或 marker 写着 `"capture": false`，hook 立即返回，**不建目录、不写字节、
  不读 transcript**；
- 找到 marker 才进入下面的采集流程；
- 插件配置里的 `capture=off` 是一个额外的全局暂停开关，与 marker 是「与」关系。

这同时是 §13 的项目排除默认态和 §7 的「不能静默创建重复项目」的前提。

### 6.1 Hook 只做一件事：把事件落进 pending

插件 hooks 同步写本地 durable outbox，覆盖可获得的：

- SessionStart / SessionEnd / Stop / StopFailure；
- UserPromptSubmit；
- PreToolUse / PostToolUse / PostToolUseFailure；
- SubagentStart / SubagentStop；
- PreCompact / PostCompact；
- 主 agent 与子 agent transcript 增量。

每条 event 有不可变 `event_id`；batch 有 `batch_id`。投递为 at-least-once，中央服务按二者去重。

Hook **不发任何网络请求，也不等待任何人**：写完 `pending/` 就返回。中央不可达、DNS 失败、
凭证过期都不会让 hook 变慢或失败；这类重试成本必须落在投递器身上，不能落在用户的每次工具调用上。
Hook 的任何失败都 fail-open：退出码恒为 0，不输出会阻断主任务的 decision。

**隐藏 chain-of-thought 不采集。** transcript 增量按行解析，`thinking` 与 `redacted_thinking`
块（含 `signature`）在**写进 outbox 之前**就被丢弃，因此隐藏推理不进本机 outbox、不上传中央、
也不进每日备份。单行解析失败不得让 hook 崩溃、也不得因此丢掉整个文件：无法解析又含该字样的行
替换成只含长度与哈希的占位记录，留下缺口证据而不留原文。只保存宿主实际暴露的可见内容。

### 6.2 投递权威：独立投递器，不是 Recorder，也不是模型

原始投递由独立进程 `trace-deliver` 负责：

- 它扫 outbox 根下**所有** workspace 目录和**所有** session 目录，不只是当前活着的那个；
  被杀掉或已正常退出的 session 留在磁盘上的 `pending/` 因此有确定的重放路径；
- 按大小/条数分批 POST 到中央 `/api/ingest`；**只有 2xx 才**把文件搬进 `sent/`
  （transcript chunk 进 `transcripts/sent/`），其余情况原样留在 `pending/`；
- 触发方式：手动 `trace-deliver`、常驻 `trace-deliver --watch`、外部定时任务，
  以及 hook 在 SessionStart / SessionEnd 分离启动的一次性投递（fire-and-forget，绝不等待）。

投递结果不得由模型自述决定。Recorder **不参与原始投递**，不得为 hook batch 调用 `trace_ingest`，
系统也不存在任何由模型输出文本承载的投递回执——那样等于让「模型说存上了」变成「存上了」，
并且任何子 agent 的收尾文本都可能被误认成 Recorder 身份。

### 6.3 Recorder fork

- Claude Code 推荐 `2.1.232+`。
- 每个主会话首次派发时创建一个 fork Recorder；它继承主会话当时的实际上下文和 prompt cache。
- 后续恢复同一 recorder agent id，只发送增量 batch。
- Recorder 身份只来自派发时记录的 agent id，不得从任何消息文本中推断。
- Recorder 不跨主会话常驻；中央服务保存长期状态。
- fork 虽继承主会话工具，但 Hook 按 recorder agent id 强制只允许只读检查和 Research Trace MCP，
  禁止 Bash、Edit、Write、Agent、外部搜索及无关 MCP。
- Hook、fork、MCP 或网络故障不得阻断主任务；batch 留在 outbox 由投递器重放。
- 语义整理的取材范围不受投递影响：投递器把文件从 `pending/` 搬进 `sent/` 不得让任何一段历史
  永远进不了语义 batch。
- 正确性不能依赖 prompt cache、模型是否记得调用工具或一次派发是否成功。

## 7. 项目识别

绝对 cwd 不能作为中央项目身份。中央 `project_id` 是稳定 UUID；客户端可以用下列 workspace keys
发现同一项目：显式 project marker、规范化 Git remote、团队配置映射。
不同机器路径和 Git worktree 可以映射到同一项目。映射不确定时进入待确认状态，不能静默创建多个
重复项目。

`POST /api/context` 按这个顺序解析，**创建被推迟到最后**：先查 `workspace_keys` 表（前两种发现
方式都落在这张表里），没命中再过团队映射，只有两者都没有结果时才考虑 `create_if_missing`。
顺序反过来的话，第二台机器会在团队映射有机会说话之前就新建一个重复项目。

路径形态的字符串不是 workspace key：`/abs`、`C:\…`、`~/…`、`./…`、`\\UNC`、`file://` 都会被
`deliver.workspace_key_problem()` 拒绝（它是唯一实现，server 从 deliver 单向 import）。
`/api/context` 与 `POST /api/projects` 把这类 key 丢掉并在响应里回报
`rejected_workspace_keys`，只有当**每一个** key 都是路径形态时才 400——本地 mirror clone 的
仓库仍有 marker 的 `rt-ws-…` key 可用，不能一刀切。

显式 marker 是项目根目录下的 `.research-trace.json`，同时承担三个职责：采集开关、workspace
身份、投递时的项目归属。

```json
{
  "schema": "research-trace.project.v1",
  "workspace_key": "rt-ws-…",
  "workspace_keys": ["https://github.com/team/repo"],
  "project_id": "…",
  "project_name": "Batch effect correction",
  "capture": true
}
```

- marker 跟着项目目录走，所以不同机器的绝对路径、不同 Git worktree 读到同一个 `workspace_key`，
  映射到同一个中央项目；绝对 `cwd` 只是元数据。
- `workspace_keys` 是附加键，绑定时默认把规范化的 Git remote 写进去。
- `project_id` 在 workspace key 完成中央映射后写入；写入之前原始历史以未归属状态上传，
  而不是静默新建一个重复项目。
- `"capture": false` 保留 marker 但把项目排除在采集之外（§13）。
- 绑定是人的动作，有两条入口：CLI `trace-project bind|status|disable`；以及 agent 侧用
  `trace_context` 解析或创建项目后，由用户执行 `trace-project bind --project-id <id>`。
  agent 不得擅自为用户没有要求绑定的目录写 marker。`trace_context` 另有一个可选的
  `bind_path` 参数，agent 只能在用户明确要求绑定时传它，解析成功后由客户端写 marker。

### 7.1 团队配置映射（第三种发现方式）

管理员在中央维护一张 glob 规则表，把 workspace key 映射到已有 `project_id`。它解决的是
「团队里第 N 台机器 clone 同一个仓库，但 marker 还没写 `project_id`」——没有它，每台机器
都只能靠人手工 `--project-id`。

- 存储在 `<data_dir>/team-project-map.json`（`research-trace.team-map.v1`，原子写）。
  它是运维配置而不是研究数据，因此不进 SQLite：可以直接 diff、直接当团队配置文件发下去，
  也可以在没有 OAuth 的部署里由管理员手工编辑播种。
- 规则形状 `{id, pattern, project_id, note, created_by, created_at}`。`pattern` 是 glob，
  与 workspace key 走同一套规范化，且必须含至少 4 个字面字符——一条 `*` 会把全世界映射到
  同一个项目，那是「静默落进错误项目」，同样违背本节的意图。
- `created_by` 只来自凭证，请求体里自称的值被忽略；增删都写进 `history`（含 actor 与时间，
  上限 500 条），所以映射本身可被审计。
- REST：`GET /api/team/mapping`（read；同时是「导出成团队配置」的出口）、
  `POST /api/team/mapping`（规则必须指向真实存在的项目）、`DELETE /api/team/mapping/{rule_id}`。
  两个写端点要求**管理员的网页会话 + CSRF**，机器凭证不行；没有配置 OAuth 时 `require_admin`
  一律 404，此时只能编辑上面那个 JSON 文件（它在服务启动时读入一次，手工编辑后要重启）。
- 命中唯一项目时，本次的 keys 会被 `add_workspace_keys` 登记到该项目上：下一个 clone
  直接走第一/第二种发现方式，不必再过映射。响应带 `resolved_by="team_mapping"` 与
  `matched_rules`。
- **命中多于一个项目时进入待确认状态**：HTTP 200，`{matched: false, pending_confirmation: true,
  reason: "team_mapping_ambiguous", candidates: [...]}`，**即使调用方传了 `create_if_missing`
  也绝不创建**。`candidates[i]` 带 `project_id / project_name / pattern / created_by /
  created_at / matched_key`，所以「是谁加的规则把我送到这里」在客户端直接可见。
  `trace-project bind` 会把候选打印出来、退出码 2、一个字都不写 marker。
- 规则指向的项目已被 purge 时不算命中，退回「没有映射」，而不是让 `/api/context` 整条 404。
- 网页上的管理员界面【未实现】：目前维护映射只有 REST 与直接编辑 JSON 两条路。

## 8. 数据流

数据流是可选派生视图，只来自 Node 上明确登记的 input/output artifact references。
不从自然语言自动猜生产者和消费者，不建立独立 Pipeline 模型。没有 artifact 关系的项目仍可完整使用。

实现是一条按键 join 的查询 `Store.dataflow(project_id, limit=2000)`：不建表、不存边、
不写任何东西，每次调用重新 join `attachments`。一条边的唯一来源是「A 的 `direction='output'`
附件与 B 的 `direction='input'` 附件共享同一个键」。`reference` 方向两边都不参与——登记它的人
没有声明任何流向。

可比对的键只有三种，判据是「按定义相等」而不是「大概是同一个」：

- `sha256`：恰好 64 位十六进制，小写化。截断的前缀、`sha256:` 前缀都不算键。
- `uri`：scheme 至少两个字母（一个字母的是 Windows 盘符，即一条没有机器的裸路径）。
  scheme 与 host 小写化，path 大小写保留，尾斜杠**不删**（S3 里 `k` 与 `k/` 是两个对象），
  不做百分号解码、不省默认端口；`file://localhost/x` 归一到 `file:///x`。
- `machine` + 绝对 `external_path`：**成对**才算键。没有机器的路径不是任何一块磁盘上的东西，
  两台机器上的 `/data/out.csv` 也不是同一份产物。整条路径不做 casefold——那要先假设文件系统
  大小写不敏感，是对行为的猜测。

相对路径、`~/…`、只给 `external_path` 不给 `machine`、以及截断的哈希都不产生键，因此永远
连不上边。边上带 `key_kind`（`sha256` / `uri` / `path`）：前者是「同一份字节」，后两者只是
「同一个位置」——一次重跑覆盖 `latest.ckpt` 会给出同一个 path 键但不同的内容，把两者合并成
匿名的「相同」等于把这件事藏起来。

其它取舍：同一个 Node 既 output 又 input 同一份产物不产生自环；不按时间过滤方向，因此环
（A→B→A）可能出现，而查询只做一次键 join、不做图遍历，环不会让它转不出来；join `nodes`
顺带挡掉指向已 purge Node 的孤儿附件；边的生成量有硬上限（`max(limit*4, 10000)`），超了
如实标 `stats.truncated`。返回值另带 `unkeyed` 与 `stats.unkeyed`，因为「空图」必须能分辨
是「这个项目没有 artifact 关系」（正常）还是「登记时忘了给键」（可修）。

可修的空图有**两种**，因此还有 `stats.unlabeled_direction`：登记的键完全正确、只是
`direction` 停在默认值 `reference`。reference 两边都不参与 join，所以这种项目的
`artifacts / keyed / unkeyed` 全是 0，和「一个产物都没登记」在返回值里一模一样。它比缺键
更容易发生——键要主动写错，方向只要不写就错。查询不因此改变行为（reference 仍然一条边都
不连，登记它的人确实没有声明流向），只是单独数一格让人看得见。

取数有两个入口：MCP `trace_context` 的可选 `include_dataflow`（默认关闭——context 是每个
batch 都要拉的热路径），以及 `GET /api/projects/{project_id}/dataflow?limit=`。网页在项目视图
里把它作为第三种呈现方式渲染，见 §10。

## 9. 最小 MCP 接口

1. `trace_context`：发现项目，返回 Overview、Chapters、最近 Nodes、人工 corrections 和同步游标；
   可选 `include_dataflow` 附带 §8 的派生数据流（默认关闭）。
2. `trace_ingest`：幂等写入原始 events、sessions、agents 和 transcript chunks。
   Claude Code 路径**不使用**它：hook batch 由 `trace-deliver` 投递（§6.2），
   这个工具只用于手动补录和没有投递器的非 Claude 宿主。
3. `trace_record`：在已有 Chapter 中幂等创建/重试未确认的通用 Node 及 Code Evidence；省略 Chapter 时进入 Inbox。
4. `trace_curate`：带版本更新 Overview 或 Chapter 当前摘要。
5. `trace_attach`：上传小附件或登记外部 artifact reference。
6. `trace_search`：跨项目搜索语义记录与原始历史。

另有 `trace_login` 身份工具，只负责发起/完成当前机器的 GitHub 账号设备绑定，不属于研究数据
模型接口。六个研究工具保持不变。

Comments 的人工操作走网页 REST；不为每个网页动作增加 MCP 工具。

## 10. 网页 MVP

- 项目列表和 Overview 当前视图/编辑器。
- Overview 内联评论、确认和纠正。
- 无序 Chapter 导航；每章显示当前摘要和按时间排列的 Node。
- 结构图与记录详情必须并存：桌面端使用 master-detail 分栏，选择节点只更新详情，不丢失图上的位置。
- 项目视图把各 Chapter 显示为互不相连的独立图；Chapter 视图只画本章 Node。不得把 Chapter 之间画成时间或父子关系。
- Node 可切换明确 parent 的树图与按发生时间排列的记录列表；没有 parent 的 Node 作为根保留，不根据时间或语义猜边。
- 详情区显示代码证据、图片、产物引用、评论和纠正；移动端允许图与详情上下排列。
- Node 内联评论和修订历史。
- 原始 session/agent timeline 默认折叠，可从语义记录跳转。
- 跨项目全文搜索。
- outbox、Recorder 和 GitHub backup 健康状态。三格都有数据源：投递器每轮结束时
  `POST /api/telemetry/outbox` 上报本机的 pending/sent 计数、最老一条 pending 的时间、
  最近一次错误和 Recorder 未处理的 batch 数；`GET /api/health` 把最近一次结果放在
  `outbox.machines[]` 与 `recorder` 里。没有任何机器上报过时界面显示「未上报」，不画假绿灯。
  同一份统计也写在本机 `outbox/delivery-status.json`，`trace-deliver --status` 不联网就能读。
  备份卡片额外显示 `unpushed_commits`，「本地 commit 成功但远端落后几周」因此看得见；
  同一张卡片显示 `backup.capacity`（导出体积、仓库体积、分卷数、最大文件与逐条告警，
  `critical` 把整张卡片提到 danger）与 `backup.missing_objects` 的条数（§13）。
- 数据流只在存在明确 artifact 关系时显示：项目视图的结构面板有第三个切换「数据流」，
  **只有真的连出边时才出现**，并且上一个项目选过它、下一个项目没有关系时自动退回结构图。
  边只来自 `GET /api/projects/{project_id}/dataflow`（agent 侧是 `trace_context` 的
  `include_dataflow`），除这个 payload 之外不画任何边。界面必须说清三件事，都已落实：
  每条边标出凭什么连的（`key_kind`：同一份字节 vs 同一个位置，后者可能已被后一次运行覆盖）；
  一条边都没有但 `stats.unkeyed > 0` 时在结构面板写明「N 个产物登记时没有可比对的键」，
  `stats.unlabeled_direction > 0` 时另写一句「N 个产物的 direction 仍是默认的 reference」，
  否则这两种可修的缺口都和「这个项目没有 artifact 关系」长得一模一样；
  `stats.truncated` 时说明只画了前 N 条。空图是正常状态，不是错误（§8 最后一句）。
  数据流的边可以跨 Chapter（消融吃主实验的产物），但这不给 Chapter 之间引入任何顺序：
  这个视图里没有 Chapter 容器，Chapter 只作为节点卡片上的一行标签。
- 团队配置映射（§7.1）的管理员界面与待确认状态的界面【未实现】：`/api/context` 现在可能返回
  `pending_confirmation`，网页目前会把它当成普通的 `matched: false`。

## 11. 并发与可靠性

- 原始 Event 和 transcript chunks 只追加。
- `event_id`、`batch_id` 和 Recorder idempotency key 必须去重。
- Overview、Chapter summary 和 Node 编辑使用版本号/optimistic concurrency，不能静默覆盖他人内容。
- 所有可编辑内容保留 revisions。
- 同一项目允许多台机器和多个 agent 并发追加。
- 语义记录与原始历史最终一致；UI 显示 Recorder 尚未处理的游标和可重试状态：未处理量就是
  本机 `outbox/<ws>/<session>/batches/` 里还开着的 manifest 数（处理完归档进 `batches/done`），
  由投递器随 outbox 遥测一并上报，中央自己看不到这个目录。
- 原始投递与语义整理相互独立：语义 batch 用单调游标选材，投递器把文件从 `pending/` 搬进
  `sent/` 不改变任何一段历史是否会被整理。

## 12. 本机 outbox

客户端无需配置 outbox 路径，使用 `${CLAUDE_PLUGIN_DATA}`（可用 `TRACE_DATA_DIR` 覆盖）。
每个被绑定的 workspace 一个哈希目录，其下每个 session 一个目录：

```text
outbox/
├── delivery-status.json          最近一轮投递结果（供健康视图使用）
└── <workspace-hash>/<session>/
    ├── pending/                  hook 写入，等待投递
    ├── sent/                     中央已确认存储
    ├── transcripts/pending|sent  transcript 增量（thinking 已在 hook 端剥离）
    ├── transcripts/meta/         增量游标
    └── batches/                  语义 batch manifest
```

- 只存在两个状态：`pending/` 和 `sent/`。**没有 `awaiting_upload/`**——它的前提是「hook 负责
  投递、失败要归档」，而投递已经不是 hook 的职责（§6.2）；磁盘上遗留的同名目录由投递器在下一轮
  搬回 `pending/` 后按正常流程重投。
- `pending/` 永不自动删除，也不因任何失败被丢弃。
- 只有中央服务返回 2xx 确认存储的内容才进入 `sent/`；模型自述不是确认依据。
- `sent/` 默认保留 30 天：投递器每轮结束时按 mtime 清理 `sent/` 与 `transcripts/sent/`
  （`--retain-sent-days`，`TRACE_RETAIN_SENT_DAYS`，`0` 表示永不回收）。保留期从**中央确认**
  那一刻起算而不是事件产生的时刻，否则积压两个月的内容一投出去就会在同一轮被删掉。
- 达到磁盘阈值时告警（剩余空间低于 5% 或 512 MiB），**绝不删除未确认内容腾空间**：
  `pending/` 不参与任何回收。
- 卸载前显示未同步数量：`trace-deliver --status` 不联网打印本机 pending/sent 与最近一次投递
  结果，有 pending 时退出码为 1。插件数据目录默认保留。
- outbox 里是完整对话和可能含令牌的命令原文，因此目录 `0700`、文件 `0600`（Windows 上 best-effort）。

## 13. 中央存储与 GitHub 备份

- 中央服务永久保存原始历史和语义记录；默认无 30/90 天 TTL。
- SQLite WAL 只由单个服务实例访问；小附件按 SHA-256 内容寻址保存。
- 每日向专用 private GitHub repository 导出确定性的 JSON/JSONL、压缩 transcript chunks、
  小附件、manifest 和校验和；不提交运行中的 SQLite/WAL。
- 大产物只备份引用元数据。
- 常规备份只追加/正常 push，不 force-push；必须支持 verify 和从空数据库 restore。
  上一轮 push 失败但 commit 已成功时，下一轮即使没有新数据也要补 push。
- Git 接近容量阈值时告警，并支持按年份/容量分卷。

导出树是**先按年、年内再按容量**的分卷结构（备份格式版本 3）：

```text
research-trace-backup/
├── index.json                     每卷的 manifest 校验和、字节数、最大文件与行数
├── .gitattributes
└── volumes/<年 | base>/
    ├── manifest.json              format=research-trace-backup-volume
    ├── tables/<table>.NNNN.jsonl  年内按字节切的分片
    ├── transcripts/<chunk_id>.zlib
    └── objects/<sha 前缀路径>
```

按年是主轴，因为一行的 `created_at` 永不改变：去年的卷一旦写定就再也不被重写，Git 不必
每天重新打包全部历史，某一年太大时可以整卷搬走。纯按容量切做不到这点——中间插一行会推移
其后所有分片边界，等于每天重写整棵树。年内再按字节切分片，是因为托管方的限制有两个量级：
单文件 50 MiB 警告 / 100 MiB 拒绝 push，仓库 1 GiB 建议 / 5 GiB 附近受限；按年只压得住
仓库增速，压不住「某年 events 表本身 300 MB」的单文件超限。分片预算默认 32 MiB
（`TRACE_BACKUP_PART_BYTES` / `--part-bytes`）。没有 `created_at` 的行（`schema_meta`）
进 `volumes/base`。

- verify 有三种粒度：整体（校验根文件、每卷 manifest 的 sha、逐卷内容，并核对 `volumes/`
  下的目录集合与索引完全一致、各表行数逐卷求和等于索引总数）、`--volume <年>` 只验一卷、
  或直接把 `--source` 指到卷目录。
- restore 与卷的顺序无关：先把所有卷的所有表读进来合并，再按固定顺序一次性写库。否则
  2027 年的 Node 指向 2026 年的 Chapter 会撞外键。
- **旧格式（版本 2 的全量单树）仍然可以 verify 和 restore**：写入端只写当前版本，读取端
  永不退役。备份的全部意义是「几年后还能读回来」，一次不兼容的升级就把之前所有备份变成废纸。
  对旧树原地重新导出会把它升级成分卷并删掉根 `manifest.json`。
- 容量告警只报不拦——容量到顶时最不该做的事就是停止备份。`export_backup` 与
  `sync_git_backup` 的返回值都带 `capacity = {level: ok|warn|critical, warnings[], limits,
  export_bytes, largest_file, largest_file_bytes, volumes}`，sync 额外带 `repository_bytes`
  （`git count-objects -v`，含历史）。看三样东西：单文件、仓库总量、以及没有仓库尺寸时用
  导出树总量兜底。四个阈值都可用 `TRACE_BACKUP_{FILE,REPO}_{WARN,CRITICAL}_BYTES` 覆盖，
  因为自建 Gitea / GHE 的数字不一样。`repository_bytes` 故意不写进 `index.json`，否则每次
  push 后仓库尺寸变化都会让索引变，每轮产生一个「内容没变」的 commit。
  统计口径包含每个卷的 `manifest.json`：它不在自己的 `files` 表里（没法给自己算校验和），
  但它是树里真实存在的一个文件，而且每个文件一条记录——一个有几十万附件对象的卷，manifest
  本身就能越过单文件硬拒线。只有 `index.json` 不计，因为它内含 `export_bytes`，自我引用。
- 服务把 `capacity` 与 `missing_objects` 写进 `/api/health` 的 `backup`，level 为
  warn/critical 时另打一行 stderr（无人值守部署没人开网页）；网页备份卡片渲染同一份数据，
  `critical` 把整张卡片提到 danger（§10）。
- 附件对象在导出时已不存在不再中止整次导出：缺口登记进卷 manifest 与索引的
  `missing_objects` 并继续；restore 同样跳过并报出来。否则从那天起所有新增历史都进不了备份。
- push 之后重新数一遍积压：`unpushed_commits` 是 push 之后的数字（补推成功后为 0），
  push 之前的那个数字叫 `retried_commits`。否则刚补推成功的那一轮会和真的落后长得一样。
- `git add` 之后用 `git ls-files --cached` 与 `backup_file_paths()` 对账，备份仓的
  `.gitignore` 吞掉文件时抛错而不是报成功——verify 只看工作树，这是唯一能回答
  「推上去的那份是不是完整的」的一步。

默认永久保存不等于无法清除敏感内容：管理员必须有紧急 purge 能力。紧急 purge 可以重写备份、
轮换仓库或加密密钥，并留下不含原文的审计记录。系统不自动脱敏，但提供三层「不采集」控制：

1. **默认不采集**：没有 marker 的项目从一开始就不被记录（§6.0）；
2. **项目排除**：`trace-project disable` 写 `"capture": false`，保留绑定但停止采集；
3. **全局暂停**：插件配置 `capture=off`，暂停期间不补采。

已实现的 purge 路径：`trace-backup purge`（真删除中央库内容并写只含 id/计数/操作者/理由的审计
记录）与 `trace-backup rewrite-history`（重建备份分支，§13 允许的唯一 force-push 场景）。
purge 只保证中央库、下一次导出和被重写后的备份分支里不再有原文；远端托管方的旧对象要等它自己
GC，别的机器已经克隆的备份副本管不到，涉及令牌时仍必须轮换密钥。purge 有两条入口：
CLI（`trace-backup purge` / `rewrite-history`）与管理员 REST（`POST /api/admin/purge`、
`GET /api/admin/purges`，都要求管理员的网页会话与 CSRF，理由必填，操作者取自凭证而不是
请求体）；网页上的操作界面【未实现】。提示里要写明命令与 transcript 可能含令牌和敏感路径。

## 14. 明确不做

- 不保留 L0–L4、复现 agent、审计 agent和双语副本。
- 不建立开发路径/定稿流程、Experiment 专用模型、Pipeline include/exclude。
- 不强制 Git branch、commit 或每轮 diff。
- 不自动判断共享工作区里每一行代码的 agent 作者。
- 不让 Overview 成为所有历史的追加式垃圾场。
- 不把 Markdown/Git 当在线数据库，也不直接提交 SQLite/WAL。

## 15. 验收标准

标注是当前实现状态，不改变要求本身。

- 【已实现】杀掉 Claude Code、断网或中央服务停机后，已触发 Hook 的原始内容仍在本机 outbox：
  hook 同步写 `pending/` 后返回，全程不发网络请求。
- 【已实现】恢复后可重放，重复投递不产生重复 Event 或 Node：投递器扫全部 workspace/session 的
  `pending/`（包括已经退出的 session），中央按 `event_id` / `batch_id` 去重。
- 【已实现】**只有中央返回 2xx，文件才进入 `sent/`**；投递失败原样留在 `pending/`。
  任何模型输出的文本都不能让一个文件被判定为已存储。
- 【已实现】未绑定的项目一个字节都不写：没有 marker（或 `"capture": false`）时 hook 在
  建目录、落盘、读 transcript 之前就返回。
- 【已实现】outbox 与中央都不含 `thinking` / `redacted_thinking` 块；单行解析失败不让 hook 崩溃，
  也不导致整份 transcript 被丢弃。
- 【已实现】主 agent 和所有子 agent 的可见历史可永久检索（限已绑定项目）。
- 【已实现】Recorder 可以对无价值 batch 选择不建 Node。
- 【已实现】想法、论文、数据理解、实验、关键实现和失败都能用同一 Node 表达。
- 【已实现】Recorder 不能创建 Chapter、不能自我确认，也不能用旧幂等重试覆盖人类移动或修改过的
  Node：写入身份来自凭证而非请求体，只有浏览器会话算 `human`，机器凭证发 confirmation/correction
  或改 `review_state` 一律 403。`update_node` 也补上了 `record_node` 早就有的那道闸：
  最新一版是人写的时候，机器凭证即使给出正确的 `expect_version` 也不能 PATCH 覆盖它。
- 【已实现】Chapter 之间无虚假的时间顺序；Chapter 内节点按时间显示。
- 【已实现】人工 correction 会进入后续 Recorder 上下文且不会被自动摘要覆盖。
- 【已实现】多人并发编辑不会静默丢内容（版本号 + revisions）。
- 【已实现】关键代码记录包含可独立理解的 snippet/diff，而不是依赖可能消失的 branch。
- 【已实现】GitHub 备份可以从空数据库恢复并通过 manifest/hash 校验；备份格式版本为 3，
  且版本 2 的旧全量树仍然可以 `verify` 和 `restore`（读取端永不退役）。
- 【已实现】搜索不被原始事件淹没：存储层给语义层保底名额并算出截断信息，`/api/search`
  返回 `SearchResult.as_dict()`（旧的 `hits` 键仍在，另带 `totals` / `returned` / `omitted` /
  `truncated`），搜索下拉在结果末尾写出「还有 N 条未显示」。
- 【已实现】管理员可以紧急 purge 并留下不含原文的审计记录（CLI 与 `POST /api/admin/purge`）。
- 【已实现】`sent/` 的 30 天保留、磁盘阈值告警、`trace-deliver --status` 的未同步计数（§12）。
- 【已实现】网页显示 outbox 与 Recorder 健康状态：投递器 `POST /api/telemetry/outbox`
  上报，`/api/health` 返回 `outbox` 与 `recorder`（§10、§11）。
- 【已实现】人工 correction 不会被机器悄悄了结：Recorder 在 `resolve_comment_ids` 里回填的
  id 只记为 acknowledgement（解开 curate 闸门，不再被同一条永久挡住），`resolved_at` 只有
  真人能写，纠正在界面与后续 `trace_context` 里保持未处理直到有人关掉它。
- 【已实现】超长 outbox 路径不静默吞事件：Windows 上 hook 与投递器都对 outbox 用扩展长度前缀
  长路径，否则 260 字符的 MAX_PATH 会让每一次落盘失败而退出码仍是 0。
- 【已实现】按年份/容量分卷（§13）：导出树是 `volumes/<年>/…` + 顶层 `index.json`，
  每卷自足、可单独 `verify --volume <年>`，restore 与卷顺序无关；去年的卷写定后不再被重写。
- 【已实现】备份容量告警：单文件 / 仓库总量 / 导出树三个口径，阈值可用环境变量覆盖，
  告警只报不拦，结果进 `/api/health`、服务日志与网页备份卡片。
- 【已实现】数据流（§8）：`Store.dataflow()` 按明确登记的 sha256 / uri / machine+path 键
  join，`reference` 不参与，不从自然语言猜生产者与消费者；没有 artifact 关系的项目是空图
  而不是错误，缺键的登记如实记在 `unkeyed` 里并在界面上说出来。入口是
  `GET /api/projects/{project_id}/dataflow`、`trace_context {include_dataflow: true}`，
  以及项目视图里只在有边时才出现的「数据流」切换。
- 【已实现】团队配置映射（§7.1）：新机器 clone 后 `trace-project bind` 无需手工指定
  `--project-id` 就能落到同一个中央项目（命中唯一规则时还会把本次的 keys 登记上去，
  下一个 clone 直接走前两种发现方式）；映射不确定时返回 `pending_confirmation`，
  即使传了 `create_if_missing` 也不创建任何项目，CLI 列出候选并以退出码 2 结束。
  规则的增删有 `created_by`（取自凭证）与 history 审计。【未实现】网页上的映射管理界面
  与待确认状态界面（§10）。
- 【未实现】Codex CLI / Codex Desktop 的自动采集适配（§2）。

## 16. 命名与历史包袱

旧的 v1 实现已从仓库删除，`_v2` 后缀也已去掉：现在只有一套系统，包名是 `research_trace`，
环境变量前缀是 `TRACE_*`，不再存在「v1 / v2」两套东西，也不提供 v1 importer。

最后两个 `v2` 字符串也已经改掉——**趁还没有任何凭证被发出去**：

- HTTP 路由前缀 `/api/v2/*` → `/api/*`；
- 设备凭证前缀 `rtv2d_` → `rtd_`。

这两处是跨文件契约（`storage.py` / `server.py` / `webapp.py` / `mcp.py` /
`device_login.py` / `deliver.py` / `scripts/trace_hook.py` 与全部测试共同引用），
改名的代价随部署规模上升：凭证前缀一动，所有工作站上已有的凭证立即失效，
之后要么留一个「同时接受新旧前缀」的过渡期，要么强制全员重新登录。
现在没有任何已发凭证，所以这是成本最低的时刻；再往后就不是了。

于是仓库里不再有任何 `v1` / `v2` 命名。
