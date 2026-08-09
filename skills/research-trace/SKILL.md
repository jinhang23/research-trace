---
name: research-trace
description: "记录与查询科研步骤树（trace 系统，支持多项目）。触发词：记一步、这步记进去、为什么放弃了 X、这个结果怎么来的、之前试过什么、溯源、步骤树、trace、新建步骤、标记 dead、这条路走不通、切到哪个项目、记一条洞察、复现、能不能重跑。开始一个新实验前应主动读现状，跑完一个实验后应主动提出记录。"
---

# trace — 科研步骤树

**如果 `trace_*` 这组 MCP 工具可用，优先用它们**——参数有 schema、不用自己拼请求、
中文不会撞终端编码。十一个工具：

| 工具 | 什么时候用 |
|---|---|
| `trace_projects` | 不确定该记到哪个项目时，先调它 |
| `trace_read` | **动手之前**先读。给 `step` 就读那一步全文 + 溯源链 + L0–L4 |
| `trace_search` | 「之前是不是试过 X」「为什么放弃了 Y」。不给 `project` 就搜全部项目 |
| `trace_new_project` | 建项目。同一个课题的不同尝试是**分叉的步骤**，不是新项目 |
| `trace_insight` | 项目级的沉淀：核心想法 / 有效 / 无效 / 坑 |
| `trace_new_step` | 建步骤。**开跑之前就建**（`status=wip`） |
| `trace_update_step` | 改状态 / 追加正文 / 追加产物路径 / 追加一条 `repro` |
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
| `id` | 服务端分配，永不变更。`003` 派生出 `004` / `004b` / `004c` |
| `parent` | 单父，**同项目内**。写下之后不可改，改了返回 409 |
| `status` | 只有三个：`wip` / `done` / `dead` |
| `title` `date` `commit` `author` `tags` | 展示用。`date` 不给的话服务端填当天 |
| `key` | 幂等键。见规矩 5 |
| `body` | 自由正文，按 `FORMAT.md` 的五个小节写 |
| `paths` | 外部产物的位置，`位置 \| 说明`。整组替换；`add_paths` 是追加 |
| `repro` | 复现记录，**只能追加**。见「复现记录」一节 |
| `digest` | `sha256(note.md 原始字节)[:12]`。用来做冲突检测，见「别覆盖掉别人的写入」 |
| `lang` | 只读。`note.md` **自己**是什么语言（`zh` / `en` …）。不写就是没声明，系统不猜；写入接口没有这个参数，要声明就手写进 `note.md` |
| `tr` | 这一步的全部译文，按语言码：`{"en": {"title": …, "body": …}}`。只读，改它走 `trace_translate` |

派生字段（`children` `backlinks` `files` `lineage` `lane` `row` `tree` `trace` `tr`）由服务端算出，
**不要试图写它们**。

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

优先用追加：`append`（正文）、`add_paths`（产物路径）、`repro`（复现记录）。
整组替换只在真的要重写整段时用。

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
| L2 | L1 + 记了 `commit` + 记了产物 `path` |
| L3 | 已经到 L2，且有一条 `repro: runnable` |
| L4 | 有一条 `repro: verified`（最后那条说了算） |

**等级受祖先制约**：001 没记数据在哪，004 写得再全，整条链也追不到底。
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

`id` · `parent` · `status` · `date` · `commit` · `author` · `tags` · `path` · `repro` · `key`

这十个键写进译文会被**一律忽略**，并产出一条警告。

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
| PATCH | `/api/projects/{项目}` — `{name}` / `{insights}` / `{add_insight:{kind,text}}` | Bearer |
| POST | `/api/p/{项目}/steps` — `{parent, title, status, body, date, commit, author, key, tags, paths}` | Bearer |
| PATCH | `/api/p/{项目}/steps/{id}` — `status` `title` `body` `date` `commit` `author` `tags` `paths` `add_paths` `add_repro`；可带 `expect` | Bearer |
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
| **409** 其它 | 改了 `id`/`parent`，或者幂等键/目录撞车。读错误正文，别硬来 |
| **400** `kind: name_too_long` | 文件名太长（Windows 整条路径 260、Linux 单个文件名 255 **字节**，一个中文 3 字节）。换个短名字 |
| **409** `kind: locked` / **403** `permission` | 文件被占用 / 权限不足。可以过一会儿重试 |
| **507** `disk_full` / **503** `unavailable` | 磁盘满 / 网络盘掉线。**告诉用户**，别静默重试 |
| **400** 其它 | 参数不合法（未知字段、`status` 不在三个值里、`repro` 状态写错）。读错误正文改参数 |

`written: false` 的意思是**这次写入一个字节都没发生**，磁盘上原来的内容完好——
可以放心地改参数重来，不用担心留下半份文件。

## 不要做的事

- 不要直接改服务器上的文件绕过 API（本地 clone 里手写 note.md 再 push 是可以的，
  但必须是 UTF-8，且照 `FORMAT.md` 的 front-matter 写）
- 不要试图改 `id` 或 `parent`——只追加是这套系统的地基
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
