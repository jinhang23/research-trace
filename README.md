# trace — 科研记录与溯源

把科研过程记录成一棵**只追加的步骤树**：每个节点自带代码、日志、产物，和一句
"我为什么要做这一步"。从任何一个结果都能一路回溯到最初的问题。

多项目并行。人和 agent 都能写：人在网页上写，agent 走 REST API。

---

## 30 秒上手

```bash
pip install -r requirements.txt
python trace_cli.py init                            # 生成 config.json（含访问路径与写入令牌）
python trace_cli.py new-project --name "我的课题"
python trace_cli.py serve                           # 起服务，终端会打印访问地址
```

浏览器打开打印出来的地址，点右上角 🔒 把令牌存进浏览器，就能建步骤、改正文、拖文件了。

键盘：`↑↓` 移动 · `g` 切换图/列表 · `n` 从选中节点派生 · `e` 编辑 · `/` 搜索
编辑时：`Ctrl+B` 粗体 · `Ctrl+I` 斜体 · `Ctrl+Enter` 保存 · `Esc` 取消

## 写：插图和表格的成本要足够低

正文是 markdown + 实时预览，**不是所见即所得**——`note.md` 必须保持人能直接读、
能 grep、能 diff，所以不引入会生成 HTML 的富文本编辑器。代价由这几件事补回来：

- **截图直接 `Ctrl+V`** 粘贴到编辑框，自动上传到该步骤目录并在光标处插入。
  光标会落在 `![|](图名)` 的方括号里，接着打字就是图注。
  同一张图粘贴两次只存一份（按内容哈希命名）。
- **从 Excel / Google Sheets / 网页表格复制**的内容直接粘贴，自动转成 markdown 表格。
- 文件可以拖进编辑框（插在光标处）或拖到阅读页（追加到正文末尾）。
- 工具栏 12 个按钮：粗体 / 斜体 / 行内代码 / 标题 / 列表 / 任务 / 引用 / 代码块 /
  链接 / 图片 / 表格 / 分隔线。
- 编辑时左边的图会让位，整个工作区变成「编辑 | 预览」两栏——写「为什么」是这个
  系统的核心动作，值得一块像样的书写台。

## 读：给人看的排版

- **图**：独占一段的图片渲染成带图注的 `figure`（图注取 `![alt](src "图注")` 里的
  引号内容，没写就用 alt）。点击放大看原图。
- **表格**：`:---` / `---:` / `:---:` 控制列对齐；**没写对齐时，整列都是数字的列
  自动右对齐并用等宽数字**，小数点自然成列。宽表格在自己的容器里横向滚动，
  不会把版面撑坏。
- **代码块**：显示语言标记，悬停出现复制按钮。
- 正文行长限制在 76 字符——超过这个宽度人眼容易串行。
- 正文用衬线体，id / 日期 / commit / 文件名 / 命令用等宽体：一眼分清哪些是机器
  记录的、哪些是自己判断的。

## 两个视图

**图** —— 自上而下的树，节点是卡片。看结构：哪里分了叉、哪条是主线、哪条断了。
用 Reingold–Tilford 紧凑布局，父节点永远居中于它的子节点。卡片上会标出这一步有
几张图、几个附件。树大了用底部的缩放条，或 `Ctrl` + 滚轮。

**列表** —— git graph 那样的轨道图 + 定高行。看全貌：几百步时用它扫和搜最快。
主线恒在轨道 0，选主线用的是"这一支还要往下延伸多远"，不是"哪个子节点 id 最小"——
最早尝试的那条支往往是后来废掉的那条。

两个视图共用同一份数据和同一套选中逻辑。布局都是纯函数算好的，视图只负责画。

---

## 四条范式（所有设计都从这里推出来）

**P1 · 纯文件即数据库.** 一个项目 = 一个目录，一个 step = 一个目录，`note.md` 是唯一权威。
没有数据库、没有中心索引、没有 manifest。

> 于是不存在"索引和实际内容不一致"这种状态。新建一步就是新建一个目录——
> 不需要注册。你可以直接 `mkdir` + `vim note.md`，页面五秒内自己跟上。

**P2 · 只追加.** step 不删除，id 不重编号，父子关系一旦写下不再改。
可变的只有 `status` 和正文。改 `id` / `parent` 的 API 请求一律 409。

> 这是溯源能成立的前提：笔记里写的"见 002b"、论文脚注里的引用，永远有效。
> 项目的 slug 同理——改显示名不动目录名，已经发出去的链接不会失效。

**P3 · 编译，而不是同步.** 视图是文件系统的纯函数。构建无状态、幂等、可随时删掉重来。
实时推送推的是"版本变了，重新编译"信号，不是增量 patch。

**P4 · 失败是一等公民.** `dead` 不是错误状态，是一种结论。死胡同在图上必须可见，
不能折叠、不能默认隐藏，而且不能只靠"变灰"表达——变灰的语义是"不相关"，不是"结论为否"。

**最硬的约束是"没有这个工具也能读"。** 删掉全部程序后，`grep -r "放弃" projects/`
仍然能查到你半年前为什么放弃了某条路。和这条冲突的设计一律否决。

---

## 目录结构

```
trace/
├── trace_core.py     纯函数内核：scan/parse/validate/order/lanes/tree/compile（零依赖）
├── trace_write.py    唯一写入路径 —— CLI / 网页 / agent API 全走这里
├── trace_server.py   FastAPI：路由 + SSE + 鉴权
├── trace_git.py      debounce 自动 commit + push
├── trace_cli.py      init / projects / new-project / new / check / build / serve / url
├── web/              index.html · app.js · md.js · style.css（无构建步骤，不引 CDN）
├── tests/            81 个 Python 断言 + 25 个 markdown 渲染断言（node --test）
├── projects/         ← 你的数据（仓库里自带一个示例项目，数字均为虚构，删掉即可）
│   └── <slug>/
│       ├── project.md            可选，只有一个 name 字段
│       └── steps/<id>_<slug>/note.md + 任意文件
└── deploy/           Caddyfile · systemd unit · 部署说明
```

只有一个函数会创建 `note.md`。上一代系统的 bug 根源就是存在第二条写入路径
（绕过 API 直接写库），导致父子关系只写进了一半的地方。这里用结构杜绝。

**代码仓和数据仓建议分开。** `config.json` 里的 `data_dir` 指向哪，`projects/` 就在哪，
自动 git 同步也就同步哪个目录。公开这份代码、把数据放进另一个私有仓库：

```json
{ "data_dir": "../trace-data", "git": { "enabled": true, "remote": "origin" } }
```

## note.md 格式

```markdown
---
id: 007
parent: 005
status: done          # wip | done | dead
title: 加入标题字段，准确率 0.943 → 0.951
date: 2026-03-11
commit: c1d2e3f
author: agent:claude
tags: features, transformer
---

## 为什么      ← 承接上一步的什么发现，想验证什么假设
## 做了什么    ← 改了哪些文件，跑了什么命令
## 结果        ← 数字、图、观察
## 结论        ← 假设成立与否
## 下一步      ← 派生出哪些分支
```

正文格式不强制，但这五个小节对应科研步骤的完整生命周期。

**「为什么」是整个系统里唯一无法自动生成的字段。** 日志能自动存，commit 能自动记，
环境能自动 freeze，只有"我当时为什么决定试这个"必须人写。系统的全部设计目的，
就是让写这一段的成本低到你愿意每次都写。

front-matter 用手写解析器而不是 YAML：`title: 试了 3:1 采样` 在 YAML 里是语法错误，
而这类标题在科研记录里很常见。规则就是"冒号左边是键、右边整行是值"。

正文里写 `[[003b]]` 会渲染成跳转链接，并在 003b 的页面显示"被这些步骤引用"。
这是"多父 DAG"的廉价替代品——想说"本步综合了 A 线和 B 线"，写一句就够，
不必把森林升级成 DAG（那会让行序变成拓扑排序、轨道分配要处理合并边，复杂度约翻三倍）。

## id 规则

三位数字，分叉时加字母后缀：`003` 派生出 `004` / `004b` / `004c`。
兄弟共享数字，一眼看得出兄弟关系，而且**任何已有 id 都不会因为后来多出一个兄弟而改名**。
每个项目的 id 各自从 001 开始，互不影响。

---

## agent 读到的是什么

同一份 `note.md` 有两类读者，两边读到的东西**不一样**，这个差别值得说清楚：

| | 人 | agent |
|---|---|---|
| 正文 | 渲染后的排版 | `GET /api/p/{项目}/steps/{id}` 返回的 markdown 原文 |
| 表格 | 对齐好的表 | markdown 表格原文——LLM 读它比读 HTML 更省事 |
| 结构 | 树图 / 面包屑 | `parent` `children` `lineage` `backlinks` 都是结构化字段 |
| **图** | 看得见 | **只看得见 `![](loss_curve.png "……")` 这一行** |

所以：**图注不是装饰，是这张图对 agent（以及半年后的你）唯一的信息来源。**
把结论写进图注——"第 12 轮之后验证集回升，再往后是纯过拟合"——文本读者就拿到了
图里的判断，不必看图。`trace_cli.py check` 会对没有图注的图报警告。

有视觉能力的 agent 可以直接取原图（读是公开的，不需要令牌）：
`GET /p/{项目}/files/{id}/{文件名}` 返回原始字节。

## API（给 agent）

Base = `/t/<space>`。读公开，写要 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects` | 项目列表 + 各自的步骤数与状态分布 |
| POST | `/api/projects` | 建项目 `{name}` |
| GET | `/api/p/{项目}/forest` | 全量：steps（含 lane/row/children/backlinks/files）+ tree + warnings |
| GET | `/api/p/{项目}/steps/{id}` | 单步 + `lineage`（到根的完整路径） |
| GET | `/api/events` | SSE，推 `{version}` |
| POST | `/api/p/{项目}/steps` | 建步骤。带 `key` 则幂等——重试不会产生重复 |
| PATCH | `/api/p/{项目}/steps/{id}` | 改 status/title/body/date/commit/author/tags |
| PUT | `/api/p/{项目}/steps/{id}/files/{path}` | 上传附件到指定文件名（raw body） |
| POST | `/api/p/{项目}/steps/{id}/files` | 上传附件，服务端定名（`X-Filename` 可选；没给就按内容哈希命名，重复内容自动复用） |

```python
import json, urllib.request
req = urllib.request.Request(
    f"{BASE}/api/p/{PROJECT}/steps", method="POST",
    data=json.dumps({
        "parent": "004", "title": "去重后重训所有模型", "status": "wip",
        "author": "agent:claude", "key": "dedup-rerun-v1",   # 幂等键，重试不会产生重复步骤
        "body": "## 为什么\n上一步发现测试集有 2.3% 近重复样本，历史数字都要重新算……",
    }, ensure_ascii=False).encode("utf-8"),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
step = json.load(urllib.request.urlopen(req))
```

Claude Code 用户：`~/.claude/skills/research-trace/SKILL.md` 已写好接入规则，
设好 `TRACE_URL`、`TRACE_TOKEN`、`TRACE_PROJECT` 三个环境变量即可。

## 命令

```bash
python trace_cli.py projects                      # 列出项目
python trace_cli.py new-project --name "课题名"
python trace_cli.py new -P <项目> --title "..."    # 新建一步
python trace_cli.py check [-P <项目>]              # 校验不变量，打印警告
python trace_cli.py build --out dist              # 静态导出，file:// 可直接打开，断网可用
python trace_cli.py url                           # 打印访问地址与令牌
python -m pytest tests                            # 81 个断言（内核 / 布局 / 写入 / 多项目）
node --test tests/md.test.js                      # 25 个断言（markdown 渲染）
```

`build` 的产物是确定性的：同样的输入两次构建逐字节一致，所以 `dist/` 直接 gitignore。

旧的单项目布局（根目录下的 `steps/`）会在第一次运行时自动迁移到
`projects/default/steps/`，一次性完成，之后只有一条代码路径。

## 残缺输入

十年后的日志一定是残缺的，构建器必须能在残缺输入上产出**部分结果**，而不是拒绝工作：

| 情况 | 处理 |
|---|---|
| `parent` 指向不存在的 id | 降级为根 + 警告 |
| 检测到环 | 报错、指出环上节点、断开一条边继续构建 |
| 重复 id | 警告，重复的那个改挂 `id~dup2` 仍然显示，不丢数据 |
| 缺 `status` / 未知 `status` | 回退 `wip` + 警告 |
| 目录名 id ≠ front-matter id | 以 front-matter 为准 + 警告 |
| 目录里没有 `note.md` | 静默跳过（允许临时目录共存） |
| 项目目录里没有 `project.md` | 用目录名当项目名——目录就是项目 |

警告显示在页面顶栏，从不阻塞构建。

## 部署

见 [deploy/README.md](deploy/README.md)。

## 刻意不做的

多人协作与并发编辑 · 自动捕获（不 hook shell / git / 文件系统）· 重跑与复现执行 ·
大文件版本管理 · 全文索引服务（`grep` 就够）· 拖拽自由布局（布局一旦手工摆放，
就不再是文件系统的纯函数了）。

它们都会把"没有这个工具也能读"变成一句空话。
