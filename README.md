# Research Trace

Research Trace 是一个面向科研与长期项目开发的 **Agent 工作记忆系统**。

它保存 Claude Code 能看到的完整对话、工具调用和子 Agent 历史，同时让一个受限的
Recorder 只把真正有长期价值的内容整理成简洁、可纠正、可搜索的项目记录。

它记录的不只是“实验”。论文搜索、想法讨论、数据理解、失败方案、关键代码实现、指标、
图片、产物路径和阶段性结论，都可以成为项目知识的一部分。

> 当前主线是 v2 alpha。v1 的 Step/Pipeline 文件树模型已移入
> [v1 归档参考](docs/V1_REFERENCE.md)，不再作为新用户的默认设计。

## 为什么做这个项目

研究过程很少是一份整齐的实验表：一次对话可能先查论文，再检查数据，随后尝试代码，
发现方向不成立，又回到新的假设。只保存最终结果，会丢掉大量以后仍然有价值的信息；
完整保存所有聊天，又会迅速变成无法阅读的日志堆。

Research Trace 同时保留两层信息：

- **原始历史层**：完整保存可获得的主对话、子 Agent、工具调用和 transcript 增量，按需查询。
- **语义记录层**：Recorder 只挑选值得长期记住的内容，整理成 Overview、Chapter 和 Node。

原始历史负责“不丢”，语义记录负责“好读”。两者不互相替代。

## 设计理念

### 1. 项目工作不等于实验

系统不为“论文阅读”“Idea”“数据处理”“实验”“代码实现”分别建立不同对象。它们统一使用
Node 表达，避免 Agent 先猜内容类型，再决定该写到哪里。

一个 Node 可以记录：

- 为什么做这件事；
- 使用的命令、参数和关键代码；
- 指标、图片和产物路径；
- 观察、推测、失败原因和结论；
- 支撑这条记录的原始事件或附件。

### 2. Chapter 表达研究线，不表达内容类型

Chapter 由人创建，用来表示项目中的并列研究线或实验组，例如：

- 主实验；
- 消融实验；
- 基线复现；
- 补充实验。

Chapter 之间没有时间先后关系。时间关系只存在于同一个 Chapter 的 Node 中，通过时间戳和
可选的 `parent` 表达。数据理解、实现和评估不是三个 Chapter，而是它们所服务研究线中的节点。

### 3. 人的判断高于 Recorder

Recorder 是整理助手，不是项目结构的权威：

- Recorder 不能创建 Chapter；
- 只能选择已有 Chapter，不确定时写入 Inbox；
- Recorder 创建的 Node 一律是“未确认”；
- 人可以移动 Chapter、修改 parent、确认、纠正和评论；
- 人工修改会形成新版本，Recorder 的重试不能覆盖更新的人类版本；
- 观察、推测和假设必须保持原来的不确定性，不能在整理时升级成事实。

这使 AI 可以大胆记录尚未证实的内容，同时不会把它伪装成最终结论。

### 4. 代码溯源只保留关键证据

Research Trace 不要求每次工作建立 Git branch，也不试图记录所有文件变化。并行子 Agent、
未提交修改和大量无价值 diff 会让这种记录看起来精确，实际却容易误导。

Recorder 只保存能解释研究结论的关键代码证据：文件路径、函数或符号、commit、必要的
snippet/diff、参数说明，以及指标、图片和产物引用。Git 继续负责代码版本；Research Trace
负责说明“哪一段实现为什么与这条结论有关”。

### 5. 界面首先服务阅读

项目主页只保留两个核心区域：左侧结构图或记录列表，右侧 Overview 或当前记录。搜索、原始
历史、附件、评论、确认状态和结构编辑按需展开。半透明、阴影和颜色只用于建立层级，不作为
装饰堆叠。

### 6. 中央服务是真相源，GitHub 是灾备

多台电脑、HPC 和团队成员都连接同一个中央 Research Trace 服务。服务端 SQLite 与对象目录是
在线真相源；私有 GitHub 仓库保存确定性导出，用于审计和恢复。

备份不会提交运行中的 SQLite/WAL、GitHub access token、网页 session、设备凭证原文或其它
secret，也不会 force-push。

## 整体架构

```mermaid
flowchart LR
    Main["Claude Code 主会话"] --> Hook["Claude Code Hooks"]
    Hook --> Outbox["本机持久 Outbox"]
    Main --> Recorder["完整上下文 Fork Recorder"]
    Outbox --> Recorder
    Recorder --> MCP["Research Trace MCP"]
    MCP --> Server["中央 Research Trace 服务"]
    Server --> Store["SQLite + 内容寻址附件"]
    Server --> UI["项目结构图 + 记录页面"]
    Server --> Export["确定性备份导出"]
    Export --> GitHub["私有 GitHub 仓库"]
```

Recorder 首次以 fork 方式继承主 Agent 当时的完整上下文；同一 Claude Code 会话中的后续批次
发送给同一个 Recorder agent id。它不会跨主会话永久驻留，长期状态保存在中央服务中。

Hook 根据 Recorder agent id 强制限制工具权限：Recorder 只能读取必要上下文，并调用 Research
Trace MCP；不能执行 Bash、编辑项目文件、启动其它 Agent 或自行进行外部研究。

## 信息模型

```text
Project
├── Overview                项目当前认识、阶段结果、重要洞察与错误
├── Comments / Corrections  人对 Overview 的评论、纠正和确认
├── Chapter: 主实验         人定义的研究线，Chapter 间无时间顺序
│   ├── Node 01
│   ├── Node 02 ─ parent → Node 01
│   └── Node 03 ─ parent → Node 02
├── Chapter: 消融实验
│   ├── Node 01
│   └── Node 02 ─ parent → Node 01
├── Inbox                   Recorder 无法可靠归类时的安全落点
└── Raw history             Session / Agent / Event / Transcript 原始历史
```

- **Project**：长期项目容器，可绑定多个工作区或机器路径。
- **Overview**：项目级的可持续修订认识，不承担完整时间线。
- **Chapter**：由人定义的并列研究线。
- **Node**：所有有长期价值的研究记录；在 Chapter 内按时间组织，可选 parent。
- **Comment / Correction / Confirmation**：不覆盖原文的人工反馈与语义状态变更。
- **Raw history**：完整底层证据，默认永久保留，页面按需加载。

## 当前能力

- Project、Overview、Chapter、Node 与 Inbox；
- Chapter 内结构图和记录列表并存；
- 评论、纠正、确认和版本冲突保护；
- 关键代码证据、附件、图片和外部产物引用；
- 跨项目全文搜索与原始历史查询；
- Claude Code Hook、持久 outbox 和受限 Recorder；
- GitHub OAuth 网页登录、团队白名单与角色；
- GitHub 账号批准的逐设备凭证，不共享机器 Token；
- 多机器连接一个中央服务；
- 每日确定性 Git 备份、校验和空库恢复。

## 快速开始

需要 Python 3.10+。

```bash
git clone https://github.com/jinhang23/research-trace
cd research-trace
python -m pip install -e ".[server]"

trace-v2-server --data-dir /srv/research-trace/data \
  --host 127.0.0.1 --port 8765
```

本地打开 `http://127.0.0.1:8765/`。团队部署应使用 HTTPS 和 GitHub OAuth，完整配置见
[v2 快速开始](docs/V2_QUICKSTART.md)。

### 安装 Claude Code 插件

在 Claude Code 中运行：

```text
/plugin marketplace add jinhang23/research-trace
/plugin install research-trace@research-trace
```

插件配置：

- `url`：中央服务地址；
- `python`：Python 3.10+ 解释器的绝对路径；
- `capture`：默认 `on`，处理敏感内容前可以临时设为 `off`；
- `token`：只用于旧部署兼容；使用 GitHub 设备登录后留空。

登录工作站或 HPC：

```bash
trace-v2-login --url https://trace.example.org --device-name hipergator-login-01
```

也可以直接让 Claude 调用 `trace_login`。机器保存的是 Research Trace 设备凭证，不是 GitHub
access token；凭证可从网页撤销。

### 配置私有 GitHub 备份

```bash
trace-v2-server --data-dir /srv/research-trace/data \
  --backup-repo /srv/research-trace/private-backup \
  --backup-branch main --backup-interval-hours 24
```

服务器启动时先备份一次，之后按间隔导出、校验、仅在内容变化时 commit 并普通 push。

## MCP 工具

Recorder 使用六个研究工具，另有一个登录工具：

| 工具 | 用途 |
|---|---|
| `trace_context` | 确认项目身份，读取 Overview、Chapter 和近期上下文 |
| `trace_ingest` | 把 outbox batch 的完整原始历史上传到中央服务 |
| `trace_record` | 创建精选 Node；不能创建 Chapter，且始终未确认 |
| `trace_curate` | 修订 Overview、Chapter 摘要或已有 Node |
| `trace_attach` | 保存小附件，或登记大产物的机器与路径 |
| `trace_search` | 搜索语义记录和原始历史 |
| `trace_login` | 使用 GitHub 账号批准当前设备 |

一次 batch 创建零个 Node 完全正常。Recorder 的目标不是“每轮都写”，而是不遗漏以后可能值得
回看的关键认识。

## 数据与备份

中央数据目录包含：

- `trace-v2.sqlite3`：在线数据库；
- `objects/`：内容寻址附件；
- transcript chunks、事件、版本和身份信息。

Git 备份包含确定性 JSONL、压缩 transcript chunks、小附件、manifest 和 SHA-256。大产物只
保存机器、路径、大小和校验和等引用，不复制大文件本体。

```bash
trace-v2-backup verify \
  --source /srv/research-trace/private-backup/research-trace-backup

trace-v2-backup restore \
  --source /srv/research-trace/private-backup/research-trace-backup \
  --data-dir /srv/research-trace/restored-empty-data
```

## Alpha 边界

- Claude Code 自动 Hook 已实现；Codex CLI / Desktop 的自动采集适配尚未实现。
- outbox 不会自动删除；当前也不提供跨 Claude 会话的独立自动重放 worker。异常批次仍保留在
  本机，可手动恢复或在后续版本处理。
- 默认永久保存的原始历史可能包含命令、路径和对话中的敏感信息。可临时关闭采集，但管理员
  emergency purge 与备份轮换流程尚未完成。
- 这是 alpha；部署团队数据前应使用私有仓库、HTTPS、OAuth 白名单和独立数据目录。

## 开发与验证

```bash
python trace_v2_mcp.py --selfcheck
python -m pytest -q
```

主要目录：

```text
research_trace_v2/          v2 中央服务、存储、MCP、OAuth、备份与网页
hooks/                      Claude Code Hook 清单与 Recorder 协议
scripts/trace_hook.py       本机 outbox、批次和 Recorder 调度
docs/V2_QUICKSTART.md       部署与登录
docs/V2_REQUIREMENTS.md     完整需求、不变量和验收标准
tests/                      Python 与前端回归测试
```

## 文档

- [v2 快速开始](docs/V2_QUICKSTART.md)
- [v2 完整需求](docs/V2_REQUIREMENTS.md)
- [Recorder 协议](hooks/RECORDER_PROTOCOL.md)

## License

[MIT](LICENSE)
