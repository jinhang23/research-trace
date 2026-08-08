---
name: research-trace
description: "记录与查询科研步骤树（trace 系统，支持多项目）。触发词：记一步、这步记进去、为什么放弃了 X、这个结果怎么来的、之前试过什么、溯源、步骤树、trace、新建步骤、标记 dead、这条路走不通、切到哪个项目。开始一个新实验前应主动读现状，跑完一个实验后应主动提出记录。"
---

# trace — 科研步骤树

**如果 `trace_*` 这组 MCP 工具可用，优先用它们**（`trace_projects` / `trace_read` /
`trace_search` / `trace_new_step` / `trace_update_step` / `trace_attach`）——
参数有 schema、不用自己拼请求、中文不会撞终端编码。下面的 REST 用法是没有 MCP 时的退路。

REST API。配置来自三个环境变量：

- `TRACE_URL` — 形如 `https://你的域名/t/<space>`（含 space，不含末尾斜杠）
- `TRACE_TOKEN` — 写入令牌；读不需要
- `TRACE_PROJECT` — 默认项目 slug；没设就先 `GET /api/projects` 看有哪些，然后问用户

前两个没设就先问用户，不要猜。

## 数据模型

一个项目 = 一个目录，一个 step = 一个目录 + 一个 `note.md`。
**没有数据库、没有中心索引。**每个项目的 id 各自从 001 开始，互不影响。

| 字段 | 说明 |
|---|---|
| `id` | 服务端分配，永不变更。`003` 派生出 `004` / `004b` / `004c` |
| `parent` | 单父，**同项目内**。写下之后不可改，改了返回 409 |
| `status` | 只有三个：`wip` / `done` / `dead` |
| `title` `date` `commit` `author` `tags` | 展示用 |
| `body` | 自由正文 |

派生字段（`children` `backlinks` `files` `lineage` `lane` `row` `tree`）由服务端算出，
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
5. **`key` 幂等键**。凡是可能重试的写入都带上（如 `key: "dedup-rerun-v1"`）：
   同 key 重发返回既有步骤而不是造一个重复的。
6. **附件**：跑的脚本、日志、图都传到该 step 目录。大文件（checkpoint、数据集）
   留在仓库外，正文里记路径 + 校验和 + 大小。
7. **不确定该记到哪个项目就问**，不要随便挑一个，更不要新建项目。

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
`GET /p/{项目}/files/{id}/{文件名}`。

**表**：正常的 markdown 表格即可。渲染器会把**整列都是数字的列自动右对齐**并用等宽数字，
所以不必手写 `---:`；需要强制对齐时再写 `:---` / `---:` / `:---:`。
指标表把主指标放第一列之后，行按可比性排列。

## 外部产物的位置（溯源的另一半）

GB 级的东西不要传进来（数据集、checkpoint、中间特征），**只记它在哪**。
`paths` / `add_paths` 参数，每条写成 `位置 | 说明`：

```
/blue/<组>/<用户>/data/agnews-clean | 去重后的训练集，12 GB
/orange/<组>/<用户>/ckpt/run042/best.pt | 权重，265 MB，sha256:7d4e1a9c…
https://github.com/你/仓库/tree/9b7d112 | 跑这一步的代码
s3://bucket/exports/run042.parquet | 导出的预测结果
```

竖线右边是自由文本，校验和、大小、"在哪台机器上"、"已确认无用可删"都往里写。
类型（超算 / GitHub / Dropbox / 对象存储 / 数据仓库 …）由系统从位置形状自动识别，不用你标。

**跑完一个实验之后，产物落在哪一定要记下来**——半年后想复现这个结果，
光有代码和 commit 不够，还得知道那份数据和权重在哪。用 `add_paths` 追加最安全。

## 端点

| 方法 | 路径 | 鉴权 |
|---|---|---|
| GET | `/api/projects` — 项目列表 + 步骤数与状态分布 | — |
| GET | `/api/p/{项目}/forest` — 全量 steps + tree + warnings | — |
| GET | `/api/p/{项目}/steps/{id}` — 含 `lineage` `files` `backlinks` | — |
| POST | `/api/projects` — `{name}` | Bearer |
| POST | `/api/p/{项目}/steps` — `{parent, title, status, body, date, commit, author, key, tags}` | Bearer |
| PATCH | `/api/p/{项目}/steps/{id}` — 只能改 `status` `title` `body` `date` `commit` `author` `tags` | Bearer |
| PUT | `/api/p/{项目}/steps/{id}/files/{相对路径}` — raw body，自己定文件名 | Bearer |
| POST | `/api/p/{项目}/steps/{id}/files` — raw body，服务端定名；`X-Filename` 头可选（需 URL 编码），不给就按内容哈希命名 | Bearer |

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
call("PATCH", p(f"/steps/{step['id']}"), {"status": "done",
     "body": step["body"] + "\n## 结果\n去重后准确率 0.947（去重前 0.951）。\n"})
```

请求体必须是 UTF-8（Windows 终端默认 GBK，用上面的 Python 方式，别用 curl 拼中文）。

## 不要做的事

- 不要直接改服务器上的文件绕过 API（本地 clone 里手写 note.md 再 push 是可以的）
- 不要试图改 `id` 或 `parent`——只追加是这套系统的地基
- **不要用 `trace_delete_step` 处理失败的实验。** 试过、走不通，那是 `status=dead`，
  是研究结论，也是这套系统里最有价值的东西。删除只用于"这条记录本身就不该存在"
  （误建、测试数据、粘进去的令牌）。删了会有两个代价：id 可能被重用、
  子步骤变孤儿——工具会把实际发生的情况报给你
- 不要跨项目挂 `parent`——`parent` 必须是同一个项目里已存在的 id
- 不要用旧 logbook 的字段（`metrics_json` `params_json` `subproject_id`）或
  旧状态名（`draft` `ongoing` `success` `failed`），一律 400
- 不要把结果塞进 `title`——`title` 是一行摘要，数字放正文的「结果」小节
