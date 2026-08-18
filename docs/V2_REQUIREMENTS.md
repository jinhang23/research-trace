# Research Trace v2 最终需求与实现基线

状态：已确认，可作为 v2 实现基线。
日期：2026-08-18

实现注：`2.0.0-alpha.3` 已提供 GitHub OAuth 网页登录、PKCE、HttpOnly 哈希会话、CSRF、
`reader/member/admin`、管理员用户管理，以及由 GitHub 账号批准的逐设备登录、撤销和自动凭证
读取。原始 GitHub access token 不作为设备凭证。

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
├── Chapters（语义主题，彼此没有时间顺序）
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

- Chapter 是长期主题或工作区域，例如“数据理解”“RNA QC”“实现”“评估”。
- Chapter 之间没有时间、父子或 pipeline 顺序。
- Chapter 有一份当前摘要；摘要是其 Nodes 的可编辑、带版本视图。
- `Inbox` 是每个项目的内建 Chapter，用于 Recorder 暂时无法可靠分类的内容。

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

插件 hooks 先同步写本地 durable outbox，覆盖可获得的：

- SessionStart / SessionEnd / Stop / StopFailure；
- UserPromptSubmit；
- PreToolUse / PostToolUse / PostToolUseFailure；
- SubagentStart / SubagentStop；
- PreCompact / PostCompact；
- 主 agent 与子 agent transcript 增量。

每条 event 有不可变 `event_id`；batch 有 `batch_id`。投递为 at-least-once，中央服务按二者去重。
隐藏 chain-of-thought 不采集，只保存宿主实际暴露的可见内容。

### 6.1 Recorder fork

- Claude Code 推荐 `2.1.232+`。
- 每个主会话首次派发时创建一个 fork Recorder；它继承主会话当时的实际上下文和 prompt cache。
- 后续恢复同一 recorder agent id，只发送增量 batch。
- Recorder 不跨主会话常驻；中央服务保存长期状态。
- Hook、fork、MCP 或网络故障不得阻断主任务；batch 留在 outbox 等待重放。
- 正确性不能依赖 prompt cache、模型是否记得调用工具或一次派发是否成功。

## 7. 项目识别

绝对 cwd 不能作为中央项目身份。中央 `project_id` 是稳定 UUID；客户端可以用下列 workspace keys
发现同一项目：显式 project marker、规范化 Git remote、团队配置映射。不同机器路径和 Git worktree
可以映射到同一项目。映射不确定时进入待确认状态，不能静默创建多个重复项目。

## 8. 数据流

数据流是可选派生视图，只来自 Node 上明确登记的 input/output artifact references。
不从自然语言自动猜生产者和消费者，不建立独立 Pipeline 模型。没有 artifact 关系的项目仍可完整使用。

## 9. 最小 MCP 接口

1. `trace_context`：发现项目，返回 Overview、Chapters、最近 Nodes、人工 corrections 和同步游标。
2. `trace_ingest`：幂等写入原始 events、sessions、agents 和 transcript chunks。
3. `trace_record`：幂等创建或更新通用 Node 及 Code Evidence。
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
- Node 可选 parent 的树/时间线视图、代码证据、图片和产物引用。
- Node 内联评论和修订历史。
- 原始 session/agent timeline 默认折叠，可从语义记录跳转。
- 跨项目全文搜索。
- outbox、Recorder 和 GitHub backup 健康状态。
- 数据流只在存在明确 artifact 关系时显示。

## 11. 并发与可靠性

- 原始 Event 和 transcript chunks 只追加。
- `event_id`、`batch_id` 和 Recorder idempotency key 必须去重。
- Overview、Chapter summary 和 Node 编辑使用版本号/optimistic concurrency，不能静默覆盖他人内容。
- 所有可编辑内容保留 revisions。
- 同一项目允许多台机器和多个 agent 并发追加。
- 语义记录与原始历史最终一致；UI 必须显示 Recorder 尚未处理的游标和可重试状态。

## 12. 本机 outbox

- 客户端无需配置 outbox 路径，使用 `${CLAUDE_PLUGIN_DATA}`。
- `pending/` 和 `awaiting_upload/` 永不自动删除。
- 只有中央服务确认存储的内容进入 `sent/`；`sent/` 默认保留 30 天。
- 达到磁盘阈值时告警，不得删除未确认内容腾空间。
- 卸载前显示未同步数量，并默认保留插件数据目录。

## 13. 中央存储与 GitHub 备份

- 中央服务永久保存原始历史和语义记录；默认无 30/90 天 TTL。
- SQLite WAL 只由单个服务实例访问；小附件按 SHA-256 内容寻址保存。
- 每日向专用 private GitHub repository 导出确定性的 JSON/JSONL、压缩 transcript chunks、
  小附件、manifest 和校验和；不提交运行中的 SQLite/WAL。
- 大产物只备份引用元数据。
- 常规备份只追加/正常 push，不 force-push；必须支持 verify 和从空数据库 restore。
- Git 接近容量阈值时告警，并支持按年份/容量分卷。

默认永久保存不等于无法清除敏感内容：管理员必须有紧急 purge 能力。紧急 purge 可以重写备份、
轮换仓库或加密密钥，并留下不含原文的审计记录。系统不自动脱敏，但提供暂停采集、项目排除和
显式 purge；界面提示命令与 transcript 可能含令牌和敏感路径。

## 14. 明确不做

- 不保留 L0–L4、复现 agent、审计 agent和双语副本。
- 不建立开发路径/定稿流程、Experiment 专用模型、Pipeline include/exclude。
- 不强制 Git branch、commit 或每轮 diff。
- 不自动判断共享工作区里每一行代码的 agent 作者。
- 不让 Overview 成为所有历史的追加式垃圾场。
- 不把 Markdown/Git 当在线数据库，也不直接提交 SQLite/WAL。

## 15. 验收标准

- 杀掉 Claude Code、断网或中央服务停机后，已触发 Hook 的原始内容仍在本机 outbox。
- 恢复后可重放，重复投递不产生重复 Event 或 Node。
- 主 agent 和所有子 agent 的可见历史可永久检索。
- Recorder 可以对无价值 batch 选择不建 Node。
- 想法、论文、数据理解、实验、关键实现和失败都能用同一 Node 表达。
- Chapter 之间无虚假的时间顺序；Chapter 内节点按时间显示。
- 人工 correction 会进入后续 Recorder 上下文且不会被自动摘要覆盖。
- 多人并发编辑不会静默丢内容。
- 关键代码记录包含可独立理解的 snippet/diff，而不是依赖可能消失的 branch。
- GitHub 备份可以从空数据库恢复并通过 manifest/hash 校验。

## 16. 迁移策略

- v1 保持只读，不继续叠加字段。
- v2 使用独立 package、数据库、API 和网页。
- importer 以后把 v1 Project/Chapter/step 转成 v2 Project/Chapter/Node；旧数据不原地改写。
