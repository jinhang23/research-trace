---
name: research-trace
description: "记录与查询科研步骤树（trace 系统，支持多项目）。触发词：记一步、这步记进去、为什么放弃了 X、这个结果怎么来的、之前试过什么、溯源、步骤树、trace、新建步骤、标记 dead、这条路走不通、切到哪个项目、记一条洞察、复现、能不能重跑。开始一个新实验前应主动读现状，跑完一个实验后应主动提出记录。"
---

# trace — 科研步骤树

**如果 `trace_*` 这组 MCP 工具可用，优先用它们**——参数有 schema、不用自己拼请求、
中文不会撞终端编码。十四个工具：

| 工具 | 什么时候用 |
|---|---|
| `trace_projects` | 不确定该记到哪个项目时，先调它 |
| `trace_read` | **动手之前**先读。给 `step` 就读那一步全文 + 溯源链 + L0–L4 |
| `trace_search` | 「之前是不是试过 X」「为什么放弃了 Y」「`best.pt` 是哪一步产出的」。搜标题/正文/标签/`path:` 与 `code:` 的位置/各语言译文；不给 `project` 就搜全部项目 |
| `trace_flow` | 顺**数据依赖**看上下游：这个数字是从哪几步算出来的、改了它谁会跟着错 |
| `trace_new_project` | 建项目。同一个课题的不同尝试是**分叉的步骤**，不是新项目 |
| `trace_insight` | 项目级的沉淀：核心想法 / 有效 / 无效 / 坑。返回分配到的 id |
| `trace_new_step` | 建步骤。**开跑之前就建**（`status=wip`） |
| `trace_update_step` | 改状态 / 追加正文 / 追加产物路径 / 追加数据依赖或代码位置 / 追加一条 `repro` |
| `trace_move_step` | 这一步挂错了父节点。**`reason` 必填**，见「树形挂错了怎么办」 |
| `trace_check_paths` | 在**当前这台机器上**逐条核对外部路径还在不在，结果写回 |
| `trace_delete_step` | 真删。只用于误建、测试数据、粘进去的令牌 |
| `trace_attach` | 传附件。**图片必须给 `caption`** |
| `trace_translate` | 补一份译文。**唯一碰翻译的写入口，它永远不动原文** |
| `trace_untranslated` | 还欠哪些翻译（现算，没有待办表） |

下面的 REST 用法是没有 MCP 时的退路。配置来自三个环境变量：

- `TRACE_URL` — 形如 `https://你的域名/t/<space>`（含 space，不含末尾斜杠）
- `TRACE_TOKEN` — 写入令牌；读不需要
- `TRACE_PROJECT` — 默认项目 slug；没设就先 `GET /api/projects` 看有哪些，然后问用户

前两个没设就先问用户，不要猜。

## 格式标准在 FORMAT.md

**这份 SKILL 讲的是「怎么调」，`FORMAT.md` 讲的是「写什么、写成什么样」**——
每个 front-matter 键什么意思、五个小节各写什么、指标表和图注怎么写、
`project.md` 的四个洞察小节叫什么、L0–L4 怎么判、渲染器认哪些 markdown 语法。
下面只摘最容易写错的几条，**完整契约以 `FORMAT.md` 为准**。

拿不到那个文件的时候（`pip install git+…` 那条安装路径只装三个 `.py`，
机器上根本没有 `FORMAT.md`）：MCP 的 `initialize` instructions 里内联了它的
可执行摘要，你在会话开始时就已经收到了。

## 数据模型

一个项目 = 一个目录，一个 step = 一个目录 + 一个 `note.md`。
**没有数据库、没有中心索引。**每个项目的 id 各自从 001 开始，互不影响。

| 字段 | 说明 |
|---|---|
| `id` | 服务端分配，**永不变更、不重发**。`003` 派生出 `004` / `004b` / `004c`。改它返回 409 |
| `parent` | 单父，**同项目内**。**可以改，但要走 `trace_move_step` 并写清原因**。REST 上就是 `PATCH {parent, reason}`：不写原因 400，和别的字段混发也 400 |
| `status` | 只有三个：`wip` / `done` / `dead` |
| `title` `date` `commit` `author` `tags` | 展示用。`date` 不给的话服务端填当天 |
| `key` | 幂等键。见规矩 5 |
| `body` | 自由正文，按 `FORMAT.md` 的五个小节写 |
| `paths` | 外部产物的位置，`位置 \| 角色 \| 说明 \| k=v`。整组替换；`add_paths` 是追加。见「产物路径」一节 |
| `inputs` | **数据依赖**：`步骤 id \| 消费的是哪份产物`。整组替换；`add_inputs` 是追加。见「`parent` 和 `inputs`」一节 |
| `code` | 代码在哪：`kind \| 位置 \| k=v`，kind ∈ `git` / `snapshot` / `container`。整组替换；`add_code` 是追加 |
| `repro` | 复现记录，**只能追加**。见「复现记录」一节 |
| `moved` | 只读。这一步被挪过位置的审计，顺序即历史。只有 `trace_move_step` 写得了它 |
| `digest` | `sha256(note.md 原始字节)[:12]`。用来做冲突检测，见「别覆盖掉别人的写入」 |
| `lang` | `note.md` **自己**是什么语言（`zh` / `en` …）。不写就是没声明，系统不猜。可以在建步骤或 PATCH 时给 |
| `tr` | 这一步的全部译文，按语言码：`{"en": {"title": …, "body": …}}`。只读，改它走 `trace_translate` |

派生字段（`children` `consumers` `backlinks` `files` `lineage` `lane` `row` `tree` `trace` `tr`）
由服务端算出，**不要试图写它们**。`consumers`（谁消费了本步的产物）是 `inputs` 的反向边，
扫描现算——**别在被消费的那一步上再记一份**，那就是双真相源。

## 规矩

1. **先读**。动手之前 `GET /api/p/{项目}/forest`，看看这条线已经走到哪、有没有人试过。
   拿到一个 id 时用 `GET /api/p/{项目}/steps/{id}` 读 `lineage`，往上追 3–5 层弄清来龙去脉。
2. **先建 wip 再开跑**，跑完 `PATCH` 成 `done` 或 `dead`。别等跑完才记——
   跑挂了那一步就永远不存在了。
3. **必须写「为什么」**。日志能自动存，commit 能自动记，只有"我当时为什么决定试这个"
   必须写出来。没有这段，这条记录半年后就是废的。
4. **失败一定要记，标 `dead`，写清放弃的理由**。死胡同是这个系统最有价值的部分——
   半年后你会想知道为什么当时放弃了 X。
   标了 `dead` 不写结论，`check` 会报 `missing_conclusion`——那条警告就是冲你来的。
5. **`key` 幂等键**。凡是可能重试的写入都带上（如 `key: "dedup-rerun-v1"`）：
   同 key 重发返回既有步骤而不是造一个重复的。
6. **附件**：跑的脚本、日志、图都传到该 step 目录。大文件（checkpoint、数据集）
   留在仓库外，正文里记路径 + 校验和 + 大小。附件名**别带空格和括号**（理由见 FORMAT.md 第 5 节）。
7. **不确定该记到哪个项目就问**，不要随便挑一个，更不要新建项目。
8. **写完一步想一句**：这一步有没有产生**项目级**的教训？有就 `trace_insight` 记一条。
9. **输入不是只来自 parent 那一步就写 `inputs`**。读了 013 和 014 的文件，就写两条——
   树上只挂得下一个 parent，而「这个数字是从哪来的」要靠它才追得到。

## 别覆盖掉别人的写入

同一个步骤会被两边写：你（agent）和坐在网页前的人。整组替换 `body` 之前，
**带上冲突检测**：

```python
s = call("GET", p("/steps/004"))
call("PATCH", p("/steps/004"), {
    "body": s["body"] + "\n## 结果\n…\n",
    "expect": s["digest"],          # 或者 If-Match 头，二选一
})
```

- 不带 `expect` = 不检查，行为和以前一样（所以**追加式**的写入可以不带）。
- 摘要对不上 → **409**，响应体里有 `current`（服务器当前那一份完整 step，含正文）
  和 `digest`。这时**不要原样重试**：把你的改动合到 `current["body"]` 上，
  用新的 `digest` 再存一次。原样重试等于把这期间人写的东西抹掉。
- MCP 的 `trace_update_step` 用 `append` 参数就够了（它先读再拼，不整组替换）。

优先用追加：`append`（正文）、`add_paths`（产物路径）、`add_inputs`（数据依赖）、
`add_code`（代码位置）、`repro`（复现记录）。整组替换只在真的要重写整段时用。

## `parent` 和 `inputs` 是两件事（**最容易搞混的一对**）

> **`parent` 是「我当时接着哪一步想」，`inputs` 是「这些字节从哪来」。**

树是**单父**的，数据流是 **DAG**。一步的输入完全可能同时来自两步，而树上只挂得下一个：

```python
call("POST", p("/steps"), {
    "parent": "013b",                            # 我接着 013b 的判定往下想
    "title": "口袋-配体配对", "status": "wip",
    "inputs": ["013 | pocket_composition.csv",   # 但实际读的是这两份文件
               "014 | rmscore_pairs.csv"],
    "body": "## 为什么\n013b 给出了纯 RNA 口袋的判定阈值，接着要把口袋和配体配起来。\n",
})
```

建完之后再补也一样：`{"add_inputs": ["014 | rmscore_pairs.csv"]}`。

判断该写哪个，问自己一句：**我这一步真的读了那一步产出的文件吗？**

- 读了 → `inputs`（把文件名写在竖线右边，半年后就是它救场）
- 只是"接着那个结论往下想"、或者只是想指一下 → `parent` / 正文里的 `[[013b]]`

**别把 `inputs` 当成第二个 parent。**写 `inputs` 不改变树，也不改变面包屑。

三件跟着来的事：

1. **可溯源性沿数据依赖上溯。**013 什么都没记，你这一步的整链等级就被 013 压住，
   哪怕 013 根本不在你的 `lineage` 里。`trace_read` 返回的最弱一环会告诉你它是从
   哪条边找到的——补记录要从**那一步**补起。
2. **反向边是算出来的。**「013 的产物被谁用了」用 `trace_flow` 现查，
   不要去 013 上再记一份。
3. 目标步骤还不存在也允许写（建立顺序不定），只会得到一条 `dangling_input` 警告。

## 产物路径写细一点（`paths`）

最简写法 `位置 | 说明` 一直有效。要让机器读得到就再分段：

```python
call("PATCH", p("/steps/016"), {"add_paths": [
    "/orange/组/pockets | output | 纯 RNA 口袋 | n=4554 size=620756992 md5=7d4e1a9c",
]})
```

- 第 0 段永远是位置；整段**恰好**是 `input` / `script` / `output` / `evidence`
  之一 → 它是**角色**；整段的空白 token **全部**形如 `k=v` → 它们是**属性**；
  其余一律拼进**说明**
- 已知属性：`size`（**字节数**，不要写「12 GB」）· `n`（条目数）· `md5` / `sha256` ·
  `checked` / `missing`（`YYYY-MM-DD`）。认不出的属性照样保留
- `checked=` 是「最后一次确认它**存在**」，`missing=` 是「最后一次确认它**不存在**」。
  两个都在时看日期，晚的说了算

**这两个日期别手写。**去核对是 `trace_check_paths` 的事（它在**当前这台机器上**
逐条 stat）。跑在超算上就核对得了 `/blue/…`，跑在别处就全是「够不着」。
**够不着 ≠ 不存在**——把「我这儿看不见」写成「没了」，得到的是一条看起来像证据的
假结论，比没有结论有害得多。路径确认没了也**不删那一行**：「这份数据当年在这儿，
现在没了」是溯源结论。

## 代码不在 git 里的时候（`code`）

L2「可定位」要的是**代码找得回来**，不是非得有 commit。三条路任选：

```python
call("PATCH", p("/steps/016"), {"add_code":
     "snapshot | /orange/组/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43"})
```

| kind | 什么时候 |
|---|---|
| `git` | 代码在 git 仓库里（也可以继续只写 `commit`，两者等价） |
| `snapshot` | 超算上直接改的脚本，跑完打一个快照目录 + 逐文件校验和。**必须记目录位置** |
| `container` | 跑在容器里，记镜像地址或 `digest=` |

`commit` 会被自动折算成一条 `code: git`，**别再手写一条重复的**——同一个事实存两处
正是这套系统最忌讳的。有没有 `manifest` / 校验和不额外分级（那是 L3/L4 的事）。

## 树形挂错了怎么办（`trace_move_step`）

「只追加」的地基是**不丢历史**，不是不能改结构。所以：

- **`id` 永远不改。**`[[003b]]` 和论文脚注里的引用要一直有效。
- **`parent` 可以改**，但每改一次都会在 `note.md` 里永久留下一行
  `moved: 日期 | 原 parent | 新 parent | 谁 | 原因`。

```
trace_move_step(project="我的课题", step="016", parent="013b",
                reason="补原子的产物从未进过下游计算，016 真正接着的是 013b 的判定")
```

- **`reason` 必填**，空的直接拒绝。写清楚是**哪条数据依赖**决定了新的父子关系，
  别写「修正结构」——半年后看到一棵和创建顺序对不上的树，唯一能解释它的就是这句话
- 移动会带走**整棵子树**；不能挂到自己或自己的后代下面；目标必须在同一个项目里
- 移动**不改变 `inputs`**。树形改对了，数据依赖该怎么写还是怎么写

**绝对不要用「对调两个节点的正文」来修树形。**那会让创建日期和内容对不上号、
`[[013b]]` 悄悄指向另一个东西，而且一条记录都不留——移动加一句原因，历史反而完整。

## 复现记录（`repro`）

查证或重跑别人的步骤之后，把结论写回去。三种状态，**只追加，不覆盖**：

| 状态 | 什么意思 |
|---|---|
| `runnable` | 查过：命令、环境、种子都齐全，照着能跑 |
| `verified` | 真跑过，数字在容差内对上了 |
| `failed` | 试过，跑不起来或者对不上 |

```python
call("PATCH", p("/steps/004"), {"add_repro":
     "verified | 2026-08-08 | agent:claude | 干净 split 重跑 3 个种子，0.9506±0.0008"})
```

MCP 那边是 `trace_update_step(project=…, step="004", repro="verified | … | … | …")`。

**`failed` 和成功一样要写。**「/orange 上的 checkpoint 已被清理，跑不了」本身就是
溯源结论——它回答了「这个结果现在还能不能信」。别因为结论不好看就不写。

## 可溯源性等级（L0–L4）

`trace_read` 读单步时会告诉你这一步自己的等级、**整条链**的等级、以及**最弱的那一环**。

| 级 | 判据 |
|---|---|
| L0 | 「为什么」或「做了什么」空着，或有图没图注，或 `done`/`dead` 却没结论 |
| L1 | 上面这些都齐了 |
| L2 | L1 + **代码找得回来**（`commit` 或任意一条 `code`）+ 记了产物 `path` |
| L3 | 已经到 L2，且有一条 `repro: runnable` |
| L4 | 有一条 `repro: verified`（最后那条说了算） |

**等级受依赖制约，而依赖 = `parent` ∪ `inputs`**：001 没记数据在哪，004 写得再全，
整条链也追不到底；数据依赖同样算数，最弱的那一环可能根本不在 `lineage` 里。
所以被问到"要不要补记录"时，**从 `weakest` 指的那一步补起**，不是从最新那一步补起。

## 正文模板

```markdown
## 为什么
承接上一步的什么发现，想验证什么假设。

## 做了什么
改了哪些文件，跑了什么命令（贴完整命令，让别人不看原文件也能重跑）。

## 结果
数字、表、观察。

## 结论
假设成立与否。不确定就写不确定。

## 下一步
派生出哪些分支。
```

正文里写 `[[003b]]` 会渲染成跳转链接，并在 003b 页面显示"被这些步骤引用"。
想表达"本步综合了 A 线和 B 线的结论"就这么写——结构是森林，没有多父。

## 图和表的写法（这是给人读的，别偷懒）

**图**：先把图上传到该步骤目录，再在正文里独占一段引用，**并把结论写进图注**：

```markdown
![](loss_curve.png "训练/验证 loss。第 12 轮之后验证集回升，再往后是纯过拟合。")
```

**你（以及任何读这条记录的 agent）看不到图里的内容，只看得到这一行。**
所以图注不是装饰，是这张图对文本读者唯一的信息来源——要写的是"这张图说明了什么"，
不是"这是一张 loss 曲线"。`trace_cli.py check` 会对没有图注的图报警告。

需要真的看图时可以取原字节（读是公开的，不需要令牌）：
`GET {TRACE_URL}/p/{项目}/files/{id}/{文件名}`。

**表**：正常的 markdown 表格即可。渲染器会把**整列都是数字的列自动右对齐**并用等宽数字
并画一条底纹条，所以不必手写 `---:`；需要强制对齐时再写 `:---` / `---:` / `:---:`。
指标表把主指标放第一列之后，行按可比性排列（基线在最上面），最好的一行 `**加粗**`。
`0.943 ± 0.004` 这种带方差的写法**照样算数值列**（底纹按主值 0.943 算），
带单位的 `40 s` 算文字列——这是对的，不要为了对齐去掉单位。
单元格里的字面竖线写成 `\|`。

**公式**：`$…$` / `$$…$$` **不会**被渲染成排版公式（不引 KaTeX），而是原样保留成
等宽文本。所以人和你读到的都是同一份原式。指标不要写成公式，写成表格。

渲染器有意不支持的语法（引用式链接 `[a][ref]`、脚注 `[^1]`、setext 标题、
四空格缩进代码块）见 `FORMAT.md` 第 12 节——写了就是原样一坨文本。

## 项目洞察（`project.md`）

**不属于任何单独一步的结论写在这里。**「回译在这个数据集上一直没用」是三次尝试之后
的判断，挂在哪一步都不对。四个固定小节，标题一个字都不能差：

| 小节 | `trace_insight` 的 `kind` | 写什么 |
|---|---|---|
| `## 核心想法` | `idea` | 还没验证、但值得记下来的想法 |
| `## 有效` | `works` | 试过、确实管用的做法 |
| `## 无效` | `fails` | 试过、不管用的做法 |
| `## 坑` | `pitfall` | 会反复咬人的陷阱 |

```python
call("PATCH", "/api/projects/" + PROJ,
     {"add_insight": {"kind": "fails", "text": "回译在这个数据集上一直没用，三次都在噪声内"}})
```

一条一行，`- ` 开头，要能被 `grep` 一行捞出来。带上出处：正文里写 `[[004]]`。

### 每条洞察都有 id，别写重复的

`trace_insight` 会分配一个 id（`p1`、`p2`…，写在行首的反引号里）并在返回值里
告诉你。后来发现当时那条不准了，**不要再手写一条相似的**，两条路二选一：

| 情况 | 怎么做 |
|---|---|
| 同一件事说得更准了（数字更正、指回的步骤换了） | 给 `id`，就地改那一行 |
| 结论被**新的结论取代**了 | 新记一条，带上 `supersedes` |

```python
call("PATCH", "/api/projects/" + PROJ, {"add_insight": {
    "kind": "pitfall", "text": "PDBFixer 误杀 944 个带修饰残基", "supersedes": "p1"}})
```

落到文件里是 `` - `p2` PDBFixer 误杀 944 个带修饰残基 · 取代 p1 ``。

- **被取代的那条不删。**「当时以为是 1,099 个」本身是信息——删掉它，半年后的人
  会以为一开始就查清楚了，然后重走一遍那条弯路。界面上它折叠显示
- **「p1 已被取代」是派生的**，只写在取代者身上。别去 p1 那一行上再补一句
- id 在整个 `project.md` 内唯一（跨小节、跨译文），所以「见 p1」永远指得到同一条

`project.md` 里还有一个 `## 已删除` 小节，由系统在删除步骤时自己写。
**永远不要动它**——目录已经没了，那一行是「为什么删的」仅存的证据。

## 补翻译（`trace_translate`）

一条记录可以有多个语言版本：`note.md` 是原文（结构 + 正文），
`note.<lang>.md` 只带一个 `title` 和译文。项目笔记同理 `project.<lang>.md`。

### 什么时候补

**不要每写一步就顺手翻。**缺翻译不是缺陷——`check` 不为它报警告，评级也不受影响
（小节「任一语言写了就算写了」）。该补的时机是这几个：

- 用户明说了要（「把这几步翻成英文」「这个项目要给外面的人看」）
- 这个项目里已经有别的步骤有 `en` 版了——别让同一个项目一半有一半没有
- 一步已经收尾（`done` / `dead`）且结论重要。**`wip` 不要翻**，正文还会变，
  翻了就得跟着改

顺序上先 `trace_untranslated(project=…, lang="en")` 看还欠哪些，再逐个补。
它是「延迟翻译」唯一的落地方式：没有任何地方存着一张待办表，欠不欠是现算的
（`note.en.md` 这个文件在不在）。

### 怎么调

```
trace_untranslated(project="我的课题", lang="en")

trace_translate(project="我的课题", lang="en", step="007",
                title="Add title field, accuracy 0.943 → 0.951",
                body="## Why\nThe TF-IDF baseline discards word order.\n"
                     "## Result\n| Model | Accuracy |\n|---|---|\n| TF-IDF | 0.897 |\n")

trace_translate(project="我的课题", lang="en",          # 省略 step = 翻项目笔记
                title="My topic", body="## Ideas\n- …\n")
```

- `lang` 是短语言码：`en` / `ja` / `zh-Hant`，它直接变成文件名的一段
- `title` **走参数**，不要自己在 `body` 里拼 `---` 那一段
- 翻译只碰译文文件，`note.md` 一个字节都不动。所以
  `trace_new_step` / `trace_update_step` 上**没有** `body_en` 这类参数，别去找
- `expect` 对的是**译文自己**的 digest，不是 `note.md` 的

### 绝不要往翻译文件里写结构字段

`id` · `parent` · `status` · `date` · `commit` · `author` · `tags` · `path` · `repro` · `key` · `input` · `code` · `moved`

这十三个键写进译文会被**一律忽略**，并产出一条警告。

**理由不是洁癖。**上一代系统就是死在双真相源上：同一个事实存在两个地方，
写一处漏一处，页面上永远有一半是错的。要改状态、改 commit、加 `repro`，
一律走 `trace_update_step` 改 `note.md`。译文里只有 `title` 和正文。

### 小节名逐字照抄

评级和 `check` 是按小节名去正文里找内容的，写错一个字就等于没写：

| 语义 | 中文 | 英文 |
|---|---|---|
| 步骤正文 | `## 为什么` `## 做了什么` `## 结果` `## 结论` `## 下一步` | `## Why` `## What` `## Result` `## Conclusion` `## Next` |
| 项目洞察 | `## 核心想法` `## 有效` `## 无效` `## 坑` | `## Ideas` `## Works` `## Doesn't work` `## Pitfalls` |
| 删除审计 | `## 已删除` | `## Deleted` |

封闭词表目前只有中文和英文。翻成别的语言照样存得下、`grep` 得到，
但小节名不在表里，评级读不出内容（也不会掉级，原文写了就够了）。

**译文里的图也要写图注。**图注是**逐份文件**判的——中文版写了图注、英文版
`![](loss.png)` 光秃秃，读英文版的人和 agent 拿到的就是零信息，这一步的等级会真的掉。
其余小节不是这样（任一语言写了就算写了）。

完整规矩见 `FORMAT.md` 第 13 节。

## 跨项目搜索

「之前好像在某个课题里试过对比学习，最后放弃了」——不给 `project` 就是搜全部：

```
GET {TRACE_URL}/api/search?q=对比学习
GET {TRACE_URL}/api/search?q=对比学习&project=<slug>&limit=100
```

回 `hits`（每条带 `project` / `id` / `title` / `status` / `where` / `snippet`）、
`total`、`truncated`。MCP 那边是 `trace_search(query=…)`，参数名叫 `query`，
端点两个名字都收。

## 端点

Base = `TRACE_URL`（形如 `https://你的域名/t/<space>`）。

| 方法 | 路径 | 鉴权 |
|---|---|---|
| GET | `/api/projects` — 项目列表 + 步骤数与状态分布 | — |
| GET | `/api/p/{项目}/forest` — 全量 steps（含 `trace` `digest`）+ tree + warnings | — |
| GET | `/api/p/{项目}/steps/{id}` — 含 `lineage` `files` `backlinks` `trace` `digest` | — |
| GET | `/api/search` — 跨项目搜索，`?q=` 或 `?query=` | — |
| GET | `/api/p/{项目}/untranslated` — 还欠哪些译文，`?lang=en` | — |
| GET | `/api/status` — 版本、项目数、步骤数、git 同步状态、`write_protected` | — |
| GET | `/api/git` — 自动 git 同步的状态（`ok` / `summary` / `hint`） | — |
| POST | `/api/projects` — `{name}` | Bearer |
| PATCH | `/api/projects/{项目}` — `{name}` / `{insights}` / `{add_insight:{kind,text,supersedes?}}` 追加并回 id / `{add_insight:{id,text?}}` 就地改 | Bearer |
| POST | `/api/p/{项目}/steps` — `{parent, title, status, body, date, commit, author, key, tags, paths, inputs, code, lang}` | Bearer |
| PATCH | `/api/p/{项目}/steps/{id}` — `status` `title` `body` `date` `commit` `author` `tags` `lang` `paths` `inputs` `code` `add_paths` `add_repro` `add_inputs` `add_code`；可带 `expect` | Bearer |
| PATCH | `/api/p/{项目}/steps/{id}` — **移动**：`{parent, reason}`，`reason` 必填且这一次请求里不能夹带别的字段 | Bearer |
| POST | `/api/p/{项目}/steps/{id}/paths/check` — `{loc, exists, date?, size?, n?}`，`exists` 必须显式 true/false | Bearer |
| DELETE | `/api/p/{项目}/steps/{id}` — `{reason}` 必填 | Bearer |
| PUT | `/api/p/{项目}/steps/{id}/tr/{lang}` — 这一步的译文 `{title, body}`；可带 `expect`（对的是**译文自己**的 digest） | Bearer |
| DELETE | `/api/p/{项目}/steps/{id}/tr/{lang}` — 撤掉一个语言版本，原文不受影响 | Bearer |
| PUT | `/api/p/{项目}/tr/{lang}` — 项目笔记的译文 `{name, body}`；`body` 只替换那四个洞察小节 | Bearer |
| PUT | `/api/p/{项目}/steps/{id}/files/{相对路径}` — raw body，自己定文件名 | Bearer |
| POST | `/api/p/{项目}/steps/{id}/files` — raw body，服务端定名；`X-Filename` 头可选（需 URL 编码），不给就按内容哈希命名 | Bearer |
| DELETE | `/api/p/{项目}/steps/{id}/files/{相对路径}` — 删一个附件 | Bearer |
| POST | `/api/sync` — 立刻跑一次 git commit + push | Bearer |

```python
import json, os, urllib.request
BASE = os.environ["TRACE_URL"]
TOK  = os.environ["TRACE_TOKEN"]
PROJ = os.environ.get("TRACE_PROJECT", "")

def call(method, path, payload=None, raw=None):
    data = raw if raw is not None else (
        json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOK)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    return json.load(urllib.request.urlopen(req))

def p(path):                       # 项目作用域的路径
    return f"/api/p/{PROJ}{path}"

forest = call("GET", p("/forest"))          # 先读现状
step = call("POST", p("/steps"), {
    "parent": "004", "title": "去重后重训所有模型", "status": "wip",
    "author": "agent:claude", "key": "dedup-rerun-v1", "tags": ["data", "dedup"],
    "body": "## 为什么\n[[004]] 发现测试集有 2.3% 近重复样本，历史数字都要重新算……\n",
})
call("PUT", p(f"/steps/{step['id']}/files/run.sh"), raw=open("run.sh", "rb").read())
call("PATCH", p(f"/steps/{step['id']}"), {"status": "done", "expect": step["digest"],
     "body": step["body"] + "\n## 结果\n去重后准确率 0.947（去重前 0.951）。\n"})
```

请求体必须是 UTF-8（Windows 终端默认 GBK，用上面的 Python 方式，别用 curl 拼中文）。

## 报错了怎么办

不是所有失败都该重试。看返回体里的 `kind` 和 `written`：

| 情况 | 该做什么 |
|---|---|
| **401** | 令牌不对或没填。**不是**磁盘故障——文件系统的错误不会返回 401 |
| **409** + `expect`/`digest` | 冲突检测拦下了。重新读、合并、带新 `digest` 再存。**别原样重试** |
| **409** 其它 | 改了 `id`，把一步挂到了自己的后代下面，或者幂等键/目录撞车。读错误正文，别硬来 |
| **400** 移动相关 | 没写 `reason`、新旧 parent 一样（空操作）、或者这次请求里把移动和别的字段混在一起发了。按错误正文单独发一次移动请求 |
| **400** `exists 必须显式给` | 路径核对没给 `exists`。**「没给」不等于「不存在」**——够不着就别发这个请求 |
| **400** `kind: name_too_long` | 文件名太长（Windows 整条路径 260、Linux 单个文件名 255 **字节**，一个中文 3 字节）。换个短名字 |
| **409** `kind: locked` / **403** `permission` | 文件被占用 / 权限不足。可以过一会儿重试 |
| **507** `disk_full` / **503** `unavailable` | 磁盘满 / 网络盘掉线。**告诉用户**，别静默重试 |
| **400** 其它 | 参数不合法（未知字段、`status` 不在三个值里、`repro` 状态写错）。读错误正文改参数 |

`written: false` 的意思是**这次写入一个字节都没发生**，磁盘上原来的内容完好——
可以放心地改参数重来，不用担心留下半份文件。

## 不要做的事

- 不要直接改服务器上的文件绕过 API（本地 clone 里手写 note.md 再 push 是可以的，
  但必须是 UTF-8，且照 `FORMAT.md` 的 front-matter 写）
- 不要试图改 `id`——它永不变更，`[[003b]]` 和论文脚注靠它一直有效
- 改 `parent` 不要偷偷来：走 `trace_move_step`（REST 是 `{parent, reason}`）并写清原因。
  **更不要用「对调两个节点的正文」来修树形**——那会让创建日期和内容对不上号，
  而且一条记录都不留
- 不要把 `inputs` 当成第二个 `parent`。它不改变树，只说明「这些字节从哪来」
- 不要手写 `path` 的 `checked=` / `missing=`：那两个日期的意思是「**真去看过**」。
  去核对用 `trace_check_paths`，够不着的时候什么都别写
- 不要因为一条洞察说得不准就再写一条相似的：给 id 就地改，或者带 `supersedes` 取代它
- **不要用 `trace_delete_step` 处理失败的实验。** 试过、走不通，那是 `status=dead`，
  是研究结论，也是这套系统里最有价值的东西。删除只用于"这条记录本身就不该存在"
  （误建、测试数据、粘进去的令牌）。删了会有三个代价：id 可能被重用、
  子步骤变孤儿、正文里的 `[[006]]` 变成悬空引用——工具会把实际发生的情况报给你
- 不要跨项目挂 `parent`——`parent` 必须是同一个项目里已存在的 id
- 不要用旧 logbook 的字段（`metrics_json` `params_json` `subproject_id`）或
  旧状态名（`draft` `ongoing` `success` `failed`），一律 400
- 不要把结果塞进 `title`——`title` 是一行摘要，数字放正文的「结果」小节
- 不要整组替换 `paths` 或 `body` 而不带 `expect`——用 `add_paths` / `append`
- 不要往译文（`note.<lang>.md`）里写 `id` / `parent` / `status` / `commit` 这类结构字段，
  也不要把两种语言塞进同一份 `note.md`——结构只有 `note.md` 说了算，
  译文只有 `title` 和正文
- 不要因为「还没翻译」就去补一条警告或标记：那是派生状态，不是缺陷
