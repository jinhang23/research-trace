# trace — 科研记录与溯源

把科研过程记录成一棵**只追加的步骤树**：每个节点自带代码、日志、产物，和一句
"我为什么要做这一步"。从任何一个结果都能一路回溯到最初的问题。

人和 agent 都能写：人在网页上写，agent 走 REST API。

---

## 30 秒上手

```bash
pip install -r requirements.txt
python trace_cli.py init                       # 生成 config.json（含访问路径与写入令牌）
python trace_cli.py new --title "基线复现"      # 新建第一步
python trace_cli.py serve                      # 起服务，终端会打印访问地址
```

浏览器打开打印出来的地址，点右上角 🔒 把令牌存进浏览器，就能建步骤、改正文、拖文件了。

键盘：`↑↓` 移动 · `n` 从选中节点派生 · `e` 编辑 · `/` 搜索 · `Ctrl+Enter` 保存

---

## 四条范式（所有设计都从这里推出来）

**P1 · 纯文件即数据库.** 一个 step = 一个目录，目录里的 `note.md` 是唯一权威。
没有数据库、没有中心索引、没有 manifest。父子关系写在各自的 note 里。

> 于是不存在"索引和实际内容不一致"这种状态。新建一步就是新建一个目录——
> 不需要注册。你可以直接 `mkdir` + `vim note.md`，页面五秒内自己跟上。

**P2 · 只追加.** step 不删除，id 不重编号，父子关系一旦写下不再改。
可变的只有 `status` 和正文。改 `id` / `parent` 的 API 请求一律 409。

> 这是溯源能成立的前提：笔记里写的"见 002b"、论文脚注里的引用，永远有效。

**P3 · 编译，而不是同步.** 视图是文件系统的纯函数。构建无状态、幂等、可随时删掉重来。
实时推送推的是"版本变了，重新编译"信号，不是增量 patch。

**P4 · 失败是一等公民.** `dead` 不是错误状态，是一种结论。死胡同在图上必须可见，
不能折叠、不能默认隐藏，而且不能只靠"变灰"表达——变灰的语义是"不相关"，不是"结论为否"。

**最硬的约束是"没有这个工具也能读"。** 删掉全部程序后，`grep -r "放弃" steps/`
仍然能查到你半年前为什么放弃了某条路。和这条冲突的设计一律否决。

---

## 目录结构

```
trace/
├── trace_core.py     纯函数内核：scan/parse/validate/order/lanes/compile（零依赖）
├── trace_write.py    唯一写入路径 —— CLI / 网页 / agent API 全走这里
├── trace_server.py   FastAPI：路由 + SSE + 鉴权
├── trace_git.py      debounce 自动 commit + push
├── trace_cli.py      init / new / check / build / serve / url
├── web/              index.html · app.js · md.js · style.css（无构建步骤，不引 CDN）
├── tests/            45 个断言，pytest tests
├── steps/            ← 你的数据（仓库里自带 7 步示例，数字均为虚构，删掉即可）
└── deploy/           Caddyfile · systemd unit · 部署说明
```

只有一个函数会创建 `note.md`。上一代系统的 bug 根源就是存在第二条写入路径
（绕过 API 直接写库），导致父子关系只写进了一半的地方。这里用结构杜绝。

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

## 图怎么读

- 主线永远在最左边的轨道 0。选主线用的是"这一支还要往下延伸多远"，
  不是"哪个子节点 id 最小"——最早尝试的那条支往往是后来废掉的那条。
- 轨道会被回收：一条支结束后，它的轨道让给后面的支。轨道总数只取决于
  最大同时活跃的分支数，和步骤总数无关。
- 线型（实线/虚线/点线）承载 status，不透明度承载"是否在选中的祖先链上"。
  颜色只作线型的补强，打印和色盲都能读。
- 搜索**只 dim 不 hide**。隐藏行会让行号不连续、破坏轨道对齐，也破坏图的形状——
  而图的形状本身就是信息（"这里分了三条支"）。搜索是为了定位，不是过滤。

---

## API（给 agent）

Base = `/t/<space>`。读公开，写要 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/forest` | 全量：steps（含 lane/row/children/backlinks/files）+ warnings |
| GET | `/api/steps/{id}` | 单步 + `lineage`（到根的完整路径） |
| GET | `/api/events` | SSE，推 `{version}` |
| POST | `/api/steps` | 建步骤。带 `key` 则幂等——重试不会产生重复 |
| PATCH | `/api/steps/{id}` | 改 status/title/body/date/commit/author/tags |
| PUT | `/api/steps/{id}/files/{path}` | 上传附件（raw body） |

```python
import json, urllib.request
req = urllib.request.Request(
    f"{BASE}/api/steps", method="POST",
    data=json.dumps({
        "parent": "004", "title": "去重后重训所有模型", "status": "wip",
        "author": "agent:claude", "key": "dedup-rerun-v1",   # 幂等键，重试不会产生重复步骤
        "body": "## 为什么\n上一步发现测试集有 2.3% 近重复样本，历史数字都要重新算……",
    }, ensure_ascii=False).encode("utf-8"),
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
step = json.load(urllib.request.urlopen(req))
```

Claude Code 用户：`~/.claude/skills/research-trace/SKILL.md` 已写好接入规则，
设好 `TRACE_URL` 和 `TRACE_TOKEN` 两个环境变量即可。

## 命令

```bash
python trace_cli.py check                 # 校验不变量，打印警告
python trace_cli.py build --out dist      # 静态导出，file:// 可直接打开，断网可用
python trace_cli.py url                   # 打印访问地址与令牌
python -m pytest tests                    # 45 个断言
```

`build` 的产物是确定性的：同样的输入两次构建逐字节一致，所以 `dist/` 直接 gitignore。

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

警告显示在页面顶栏，从不阻塞构建。

## 部署

见 [deploy/README.md](deploy/README.md)。

## 刻意不做的

多人协作与并发编辑 · 自动捕获（不 hook shell / git / 文件系统）· 重跑与复现执行 ·
大文件版本管理 · 全文索引服务（`grep` 就够）。

它们都会把"没有这个工具也能读"变成一句空话。
