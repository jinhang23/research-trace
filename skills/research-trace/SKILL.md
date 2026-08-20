---
name: research-trace
description: 科研项目记忆与溯源（Research Trace）。读项目 Overview / Chapters / 最近记录与未处理纠正，检索既有结论和原始历史，登记外部产物，绑定项目开始采集。触发词：之前试过什么、为什么放弃了 X、这个结果怎么来的、这条结论哪来的、溯源、项目现状、看看项目记忆、这个项目记了啥、上次跑到哪了、记一下这个、把这条记进去、更新 Overview、总结本阶段、登记这个产物、开始记录这个项目、绑定项目、连接 Research Trace、没登录；project memory、provenance、what did we try、why was X abandoned、why did we drop X、record this、log this decision、write this down、where did this result come from、artifact lineage、start tracking this project、bind this repo、update the overview、trace login。在已绑定的项目里开始一段新工作前，应主动用 trace_context 读现状。
---

# Research Trace：主 agent 侧

Research Trace 有两层：**原始历史层**（hook 自动把宿主暴露的事件与 transcript 写进本地 outbox，
由独立进程 `trace-deliver` 上传）和**语义记录层**（Project / Overview / Chapter / Node）。

这份 skill 只写给**主 agent**。后台 Recorder 有自己的一份协议文件（`hooks/RECORDER_PROTOCOL.md`），
它由 hook 在 Stop 时派发，你不需要读那份文件，也不需要替它工作。

主 agent 的介入点有这几个：**开工前读现状**、**按需检索**、**帮用户完成绑定**、
**Stop 时照 hook 的指令派发一次 Recorder（然后就停）**、
**在用户明确要求、或某个值只有你拿得到时亲自写一条记录**。

## 七个工具，主 agent 的用法

| 工具 | 你什么时候用 |
|---|---|
| `trace_context` | 开工前读项目记忆；解析项目 id；用户要求绑定时（且只有那时）传 `bind_path` |
| `trace_search` | 用户问"之前试过什么""为什么放弃了 X""这个结果怎么来的" |
| `trace_record` | 用户说"记一下这个"；或这条记录依赖只有你能取到的值（见 §5） |
| `trace_curate` | 用户说"更新一下 Overview""总结本阶段" |
| `trace_attach` | 登记外部产物（权重、数据集、图、大文件），或挂一个小附件 |
| `trace_login` | 用户说要连接/登录，或某个工具报了 401 |
| `trace_ingest` | **主 agent 基本用不到。** 只用于手工补录，以及没有投递器的非 Claude 宿主；绝不用于 hook batch |

## 1. 开工前先读项目记忆

在一个已绑定的项目里开始任何一段有实质内容的研究工作（跑实验、改方法、读一批论文、
定方案）之前，先读现状。这是这份 skill 里价值最高的一条：项目记忆里往往已经有
"这条路试过了""这个指标口径是这样定的""上次的结论被人纠正过"。

怎么拿到项目身份：从 cwd **逐级向上**找最近的一个 `.research-trace.json`（marker 写在项目根，
而你多半在某个子目录里；只看当前目录会误判成"没绑定"，然后把用户拖去重走一遍绑定流程——
那正是这套设计最想避免的重复项目）。marker 里有 `project_id` 和 `workspace_key`。
把 `project_id` 传给 `trace_context` 最直接；只有 `workspace_key` 时传
`workspace_keys: ["rt-ws-…"]`，也可以把 `git remote get-url origin` 的输出**原样**加进去——
`git@…` 形态、结尾的 `.git`、大小写都由服务端统一归一化，你不用自己处理。

找到的 marker 里 `capture: false` 表示这个项目被**显式排除**了（`trace-project disable` 干的），
不是没绑过，别把它当成未绑定去重新走绑定流程。

**绝对路径不是身份。** `/abs`、`C:\…`、`~/…`、`./…`、`\\UNC`、`file://` 这些形态会被
服务端拒掉并在响应里回报 `rejected_workspace_keys`，全部是路径形态时直接 400。

读回来之后重点看四样：
- `project.overview` 与 `project.overview_version` —— 项目当前认识。
- `project.chapters[]` —— 人定义的并列研究线（主实验 / 消融 / 基线复现之类），
  每个带 `id`、`name`、`summary`、`summary_version`，`is_inbox=1` 的那个是内建 Inbox。
- `project.recent_nodes` —— 最近的语义记录，**默认只回 20 条**（`recent_limit` 可调，上限 100）。
  要通盘回顾就把它调大，或者改用 `trace_search`；只拿到默认那 20 条时，
  别对用户说"项目里就这些"。
- `project.unresolved_corrections` —— **人写的、还没关掉的纠正。优先级最高**，
  它直接说明"记录里的这句话是错的"。跟它冲突的结论不要拿来当依据。

需要看产物依赖时加 `include_dataflow: true`（默认 false，它是一次全表 join，别每次都要）。
空图是正常状态，不是错误：边只从明确登记过键的 artifact join 出来，系统绝不从文字里猜谁产出谁消费。
如果返回里出现顶层的 `dataflow_unavailable`，那是中央版本太旧不会算数据流，不是项目没有产物。

## 2. 用户来问既有结论：trace_search

必填只有 `query`。`scope` 三选一：

- `scope: "semantic"` —— 搜 Node 的标题与正文、Project Overview、以及 Comments
  （包括人工 correction / confirmation 的正文——§1 说的那批最高优先级材料，
  它们的内容只能靠这里命中）。**用户问结论、问决定、问"之前试过什么"时用这个。**
  但注意 **Chapter 摘要不在搜索范围内**，想看它只能走 `trace_context` 的 `project.chapters[]`。
  所以"搜不到"不等于"没记过"：答案可能就写在某个 Chapter 摘要里，别据此下结论说项目里没有。
- `scope: "all"`（MCP 侧的默认值）—— 语义 + 原始历史。代价是会把原始事件片段拉进你的上下文，
  贵且噪声大。只在语义层确实搜不到、需要翻当时的实际操作时才用。
- `scope: "raw"` —— 只翻原始历史，用于"当时那条命令到底怎么写的"。

`limit` 服务端夹到 1..200，不传是 50。带 `project_id` 可以限定在一个项目内，不带就是跨项目。
返回里有 `totals` / `omitted` / `truncated`，被截断时如实告诉用户还有多少条没显示，别假装搜全了。

## 3. 采集是自动的，Stop 时的那条指令照做即可

原始事件、transcript、投递、batch 归档全部自动，没有你的介入点：

- 没有 `.research-trace.json` marker 的目录，hook 在建任何目录之前就返回，一个字节都不写。
  **装上插件不等于开始记录这台机器上的所有项目。**
- `thinking` / `redacted_thinking` 在写进 outbox 之前就被剥掉了，不出本机。你不用管这件事。
- 上传由独立进程 `trace-deliver` 负责，hook 在 SessionStart / SessionEnd fire-and-forget 拉起它。
  没确认的文件留在 `pending/` 下次重试。**不要去看 outbox 目录，不要手工搬文件，不要汇报上传状态。**

会话结束时（Stop）hook 会用一个 block decision 把派发指令直接发到你的上下文里：派 `fork` 还是
对已有 Recorder `SendMessage`、任务提示怎么写、为什么必须是 fork、派完就停不要等、
派不出去时不要本轮重试——那条消息里全都有。**照它的字面做，只做一次。**
这里只补三件那条消息里没有的：

- hook 会按 agent id 把 Recorder 限死在 Read / Grep / Glob 和六个研究工具
  （`trace_context` / `trace_ingest` / `trace_record` / `trace_curate` / `trace_attach` / `trace_search`，
  **不含 `trace_login`**）。**这条限制只对 Recorder 生效，对你完全没有影响**——
  你的 Edit / Write / Bash 一切照旧。
- 语义判断是 Recorder 的职责：**不要自己去读那份 batch manifest。**
- 如果连着几轮都派不出去（这个宿主没有 fork / SendMessage），除了不重试之外，还要告诉用户
  **语义层现在没人写**，并按 §5 在他要求时自己 `trace_record`。原始历史不受影响，照常入队上传。

## 4. 用户说"开始记录这个项目"

绑定是**人的决定**，你只负责把项目解析出来、把命令给用户。

1. 调 `trace_context`：有 marker 就传里面的 workspace key；没有 marker 就传
   `git remote get-url origin` 的输出；**两个都没有**（HPC scratch 目录、刚 mkdir 的实验目录）
   就一个 key 都不传直接调——返回照样是 `matched: false` + `projects` 全量列表，按第 2 步走。
   `create_if_missing` 保持默认 `false`。**绝不要自己编一个 `rt-ws-…` 键**：那个键由
   `trace-project bind` 生成，你编出来的键会在中央留下一个此后再没有任何机器发送的孤儿。
2. 看返回：
   - `matched: true` → 拿到 `project.id`，跳到第 4 步。
   - `matched: false`（HTTP 200，**不是错误**）→ 响应里带 `projects` 全量列表。
     把候选念给用户，让他选一个已有项目，或者明确说"新建一个"。
   - `pending_confirmation: true` + `reason: "team_mapping_ambiguous"`（同样 200，**不是错误**）→
     团队映射同时命中多个项目。此时即使传了 `create_if_missing` 也不会创建任何东西。
     **不要重试，不要改成新建。** 把 `candidates` 报给用户，让他挑一个。
3. 只有用户明说要新建时，才带 `create_if_missing: true` 再调一次（可以同时给 `project_name`）。
4. 把命令交给用户，让他自己在项目根跑：
   `trace-project bind --project-id <id>`（他也可以用 `trace-project status` / `trace-project disable`）。

**唯一可以由你写 marker 的情况**：用户明确说"你直接帮我绑定这个目录"。这时才给 `trace_context`
传 `bind_path`。它是纯客户端参数，中央看不到它，只在本地写 `.research-trace.json`；
`matched` 为 false 时（含 pending_confirmation）什么都不写。成功时返回里多一个 `bound` 键。
注意刚 bind 出来的新 workspace key 这一次并不会登记到中央项目上，要等下次带着它请求才建立映射。

除此之外：**不要主动传 `bind_path`，不要自己去写 `.research-trace.json`，
不要给一个已经有中央项目的 workspace 再建第二个项目。**

## 5. 你自己写记录

默认情况下语义记录由 Recorder 写。它是 `fork`，你这一整段会话的上下文它都有，所以
"Recorder 不知道这件事"通常是假的。主 agent 亲自写只有这几种场合：

- **用户明确要求**（"记一下这个""更新 Overview"）——这时必须执行，不要自己判断值不值得记。
- **这个值必须执行才能拿到，而对话里又没打印出来。** Recorder 只有 Read / Grep / Glob，
  跑不了 Bash：sha256、文件大小、绝对路径、`git rev-parse` 出来的 commit hash，只有你能提供。
  这类 `trace_attach` / `code_evidence` 由你写。
- **这段会话被压缩过**，而你判断关键细节已经不在上下文里了（fork 继承的是压缩后的那一份）。
- **这份产物不是本次会话产生的**：历史结果、在别处跑出来的、用户直接贴进来的。

反过来：**本次会话里正常产出的产物交给 Recorder 登记，不要抢在它前面。** 你先登记一遍、
Recorder 稍后又登记一遍，数据流图上就是同一个键下两条来源不同的条目。

还有一件你办不到的事：主 agent 写的 Node 没有 `source_event_ids`（event id 只在 batch manifest 里，
而你不读它）。这条 Node 因此**回不到原始历史**——网页上点它的"原始历史"只会退化成
"这个项目最近的事件"。这是默认让 Recorder 写记录的原因之一，也是不要抢着自己写的另一条理由。

### trace_record

必填 `project_id`、`idempotency_key`、`title`。常用可选：`body`、`chapter_id`、`code_evidence`。

- `idempotency_key` 是**项目内**唯一，不是全局唯一。用可复现的语义键（比如
  `manual-ablation-lr-2026-08-19`），不要用随机 UUID。同 key 同内容重投是 200 且不升版；
  内容变了就地升一版。它不是防撞用的，它是"重试不会写出第二条"的保证。
- `chapter_id` 只能填 `trace_context` 里已有的那个 id。**不确定就省略，落进 Inbox，不要猜归属。**
  Chapter 是人定义的，你不能创建，也没有 `chapter_name` 这种参数（传了会在发出前被丢掉）。
  填一个不存在的 `chapter_id` 是 404，不会自动建章。
- `parent_id` 只在"**同一个 Chapter 内、这条是上一条的直接续做或修正**"时才填。新想法就是根节点，
  不确定就省略。**不要拿它去搭一棵步骤树或时间/因果链**——Node 列表不是 pipeline，
  服务端还会要求 parent 与本节点同 Chapter。
- 另外两个可选项：`occurred_at` 是 ISO-8601（不传取当前时间）；`labels` 是自由文本，
  先照抄这个项目里已经在用的词，别自己发明一套。
- 你建的 Node 一律是 `created_by: "recorder"` / `review_state: "unreviewed"`。这不是 bug：
  写入身份只从凭证推导，只有浏览器里的真人会话算 human。确认和纠正只能人在网页上做。
- `body` 里写清**为什么做、关键方法、重要命令和参数、指标及口径、产物位置、结论、
  失败原因、下一步**。失败不是特殊类型，就是一条正常 Node，原因写在 body 里。
  观察、推测、假设保持原来的不确定性，不要在整理时升级成事实。
  **用原文语言写**：用户用中文讨论的就用中文记，不要翻译成英文，也不要中英各存一份——
  系统里没有双语副本这条路。
- `code_evidence` **先挑再填**：只挂核心算法、关键流程、接口、配置、数据格式、重要 bug
  或可复用实现的那几处，**不要把这轮改过的文件全铺上去**。每项必填 `file_path`，可选
  `repo_url` / `commit_hash` / `symbol` / `start_line` / `end_line` / `snippet` / `diff` /
  `annotation` / `content_sha256` / `attribution`（enum：`exact` / `reported` / `ambiguous` / `unknown`）。
  共享 working tree 下归因不清就写 `ambiguous`，**绝不要编一个作者出来**。

### trace_curate

必填 `project_id`、`target_type`（只有 `overview` 和 `chapter` 两种）、`body`、`expect_version`。

- `expect_version` 是**当前**版本号，不是你想写成的那个。overview 取 `project.overview_version`，
  chapter 取对应 `chapters[i].summary_version`，都从 1 起，成功后返回 current+1。
- `target_type: "chapter"` 时 `target_id` 必填（漏了会得到一句冒号后面空着的 404）；
  `target_type: "overview"` 时 `target_id` 被忽略。
- Overview 写的是项目**当前认识**，不是追加式流水账。不要把每轮对话往里堆，不要画时间树。
  旧版本靠 revision 看，重要节点传 `milestone: true`。
- **不要覆盖未处理的人工纠正。** 目标上有未处理 correction 时 curate 会 409。
  **只有当你新写的 `body` 已经把那条纠正的内容吸收进去之后**，才把它的 id 放进
  `resolve_comment_ids`。只为了过掉 409 而把 id 一股脑塞进去，是把这道专门拦你的闸拆了。
  而且它只是 acknowledge：机器写不了 `resolved_at`——纠正在网页和下一轮 `trace_context` 里
  仍然是未处理的，直到真人关掉它。别跟用户说"已解决"。

### trace_attach

必填 `project_id`、`target_type`（`overview` / `chapter` / `node`）、`target_id`、`name`。

键（`sha256` / 带 scheme 的绝对 `uri` / `machine` 加绝对 `external_path`）和 `direction`
的具体规则见这个工具自己的 description。这里只说它为什么会咬人：**服务端只在四者全空时才 400**，
其余情况一律 200 通过，然后在数据流里永远 unkeyed、永远连不上任何一条边；`direction` 不写
默认是 `reference`，而 reference 两端都不参与 join。也就是说**"登记成功"和"登记了等于没登记"
在返回值里长得一模一样**，没有任何东西会提醒你——这是静默失败，只能靠发之前自己检查。

小文件可以用 `local_path`，MCP 进程会在本地读成 base64 传上去（服务端默认上限 10 MiB），
`sha256` 与 `size` 由服务端按内容重算。`name` 是必填的，不会被文件名覆盖。

## 6. 报错了怎么办

工具级失败一律是 `isError: true` + 一行 `类型: 消息`，不是 JSON-RPC error，不用按错误码分流传输层。

| 现象 | 含义 | 你该做什么 |
|---|---|---|
| `HTTP 401 … Run trace-login or trace_login to reconnect.` | 没登录或凭证失效 | 调 `trace_login`（`action: "start"`），把 `verification_uri` 和 `user_code` 念给用户，让他**手工输码**。然后 `action: "status"` 轮询。不要循环重试原来那个工具 |
| `HTTP 403` | 机器凭证在做只有真人能做的事：发 confirmation/correction、改已有 Node 的 `review_state`、关掉一条纠正 | 这三件事都只在网页 REST 端点上，**七个 MCP 工具一个都碰不到**。万一你看到它，别绕，告诉用户去网页上做 |
| `HTTP 404: chapter not found: …` | `chapter_id` 不存在 | 省略它落 Inbox，或重新 `trace_context` 取正确的 id。不要试图创建 Chapter |
| `HTTP 409: … curation version changed: expected N, current M` | 有人在你之前改了 | 重新 `trace_context` 读回最新 body，把你的改动合并上去，用新的 `expect_version` 再来。**不要直接用 M 覆盖** |
| `HTTP 409: … unresolved human corrections must be acknowledged …` | 目标上有未处理的人工纠正 | 先把纠正内容读给用户 → **按纠正改写 `body`** → 再把被吸收的那几条 id 放进 `resolve_comment_ids`。只为过闸而带 id 不行 |
| `HTTP 409: node has a newer human revision; recorder cannot overwrite it.` | 人已经改过这条 Node | 停手。人的版本优先，需要补充就新建一条 Node |
| `Research Trace unavailable at <url>: …` | 中央不可达 | 告诉用户中央连不上。**原始历史仍然安全地留在本机 outbox，投递器会重试**，不会丢。不要重试循环 |
| `trace_login` 返回 `DeviceLoginError: GitHub OAuth is not enabled` | 这台中央没配 OAuth | 用户需要用旧的 `TRACE_TOKEN` 部署方式，或让管理员开 OAuth |

`matched: false` 和 `pending_confirmation: true` **不是错误**（HTTP 200 / `isError: false`），
按第 4 节处理，不要当失败上报。

### 用户说"怎么没记上"

三个各自独立的原因，都有只读的查法。**仍然不要去翻 outbox。**

1. 让用户在项目目录跑 `trace-project status`——只读，直接打印 `not bound`（这一路向上没有 marker）、
   `excluded`（marker 里 `capture: false`）或完整的绑定详情。
2. 让他确认插件设置里的 `capture` 开关。它是全局暂停开关，设成 off 时连已绑定的项目也停，
   而且暂停期间不补采。
3. 调一次 `trace_login`（`action: "status"`）确认这台机器还连着——没登录时投递器一直 401，
   文件会一直堆在 `pending/`。
4. 三条都正常，就告诉他原始历史在本机队列里、投递器会重试；语义记录要等 Recorder 处理完那一批。

## 7. 不会报错、只会静默失败的几件事

- **不要漏 `direction`**，也**不要给 artifact 留一个不可比对的键**——两者都 200 通过，
  然后永远连不上边。→ 见 §5
- **不要把 `cwd` 或任何绝对路径当作 workspace key。** → 见 §1
- **不要从叙述文字推断数据流上下游。** 没有明确登记的键就没有边，空图是正常状态。→ 见 §1
- **不要用 `parent_id` 去搭时间链或因果链。** → 见 §5
- **不要试图设置 `review_state`、`created_by`、`actor_type`**，这些旋钮不存在，
  请求体里写了会被静默忽略（不是 403）。→ 见 §5
