# trace — 科研记录与溯源

把科研过程记录成一棵**只追加的步骤树**：每个节点自带代码、日志、产物，和一句
"我为什么要做这一步"。从任何一个结果都能一路回溯到最初的问题。

多项目并行。人和 agent 都能写：人在网页上写，agent 走 REST API。

---

## 30 秒上手

```bash
pip install -r requirements.txt
python trace_cli.py init --project "我的课题"        # 生成 config.json（含访问路径与写入令牌）
python trace_cli.py serve                           # 起服务，终端会打印访问地址
```

浏览器打开打印出来的地址，点右上角 🔒 把令牌存进浏览器，就能建步骤、改正文、拖文件了。

两个默认值值得知道，它们是为了让上面这三行**照抄也不会出事**：

- **数据放在 `../trace-data`**（`--data-dir` 的默认值），也就是这个代码仓的**外面**。
  项目自己的不变量是"代码仓公开、数据仓私有"，默认值必须先满足它。
- **自动 git 同步默认是关的。** 要开得显式 `--git`，而且数据仓和代码仓在同一个
  git 工作区时会被直接拒绝——`git add -A && git commit && git push` 一旦跑在公开
  代码仓上，未发表的实验记录 45 秒后就上了公网，而且推送成功时一个字都不打印。
  上线时怎么开见 [deploy/README.md](deploy/README.md)。

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
主线恒在轨道 0，选主线用的是"这一支还要往下延伸多远"——精确地说是**子树高度**
（叶子记 0，父 = 1 + 子的最大值），平局取 id 序最小的那个。不是"哪个子节点 id 最小"
（最早尝试的那条支往往是后来废掉的那条），也不是"哪个子树的节点最多"
（一个死胡同下面挂五个快速小试验，节点数能压过真正走下去的那条深链）。

窄屏（<760px）第一次打开默认走列表视图：图的画布是绝对像素，手机上等于一屏一个节点
全靠拖；列表的轨道只有十几像素宽，窄屏上完整可读。切到图视图会自动缩放到适应宽度，
选择会被记住。

两个视图共用同一份数据和同一套选中逻辑。布局都是纯函数算好的，视图只负责画。

两个视图上都能看到**可溯源性**：自身还停在 L0 的节点标一个 `L0`，最近一次复现失败的
标一个 `↺✕`。详情面板里有完整的一栏——自身等级、整条链的等级、最弱的那一环
（可点跳过去）、还缺什么的待办清单、从根到自己的等级链条、以及全部 `repro` 记录。

## 找得回来、写得不丢

- **跨项目搜索**：顶栏的搜索框可以在「本项目 / 全部项目」之间切。
  "之前好像在某个课题里试过对比学习，最后放弃了"——这是这套系统存在的理由之一，
  人不该被迫一个项目一个项目点进去各搜一遍（agent 一次 `trace_search` 就跨全部项目了）。
- **草稿**：正文、标题、外部路径边写边存进 localStorage。按 `Esc`、点面包屑、
  点自己刚写的 `[[007]]`、按后退键、关标签页——都不会让写了十分钟的记录蒸发。
  下次打开是一条"有草稿"的提示，恢复还是丢弃由你决定，不会自动替你套上。
- **保存冲突**：见下面 P2 那段。409 时把服务器版本和你正在编辑的版本并排摆出来、
  逐行标出差异，三个出口：回编辑器 / 保留服务器版本（你的留在草稿里）/ 用我的覆盖。
- **同步状态**：顶栏一个 ⇅ 图标。自动 git 同步失败或者根本没配好时它会变红，
  悬停给出原因和修法，点一下立刻重试一次。平时安静。

---

## 四条范式（所有设计都从这里推出来）

**P1 · 纯文件即数据库.** 一个项目 = 一个目录，一个 step = 一个目录，`note.md` 是唯一权威。
没有数据库、没有中心索引、没有 manifest。

> 于是不存在"索引和实际内容不一致"这种状态。新建一步就是新建一个目录——
> 不需要注册。你可以直接 `mkdir` + `vim note.md`，页面五秒内自己跟上。

**P2 · 只追加.** id 不重编号，父子关系一旦写下不再改。
可变的只有 `status` / `title` / `body` / `date` / `commit` / `author` / `tags` / `paths`。
改 `id` / `parent` 的 API 请求一律 409。`repro` 复现记录**只能追加**，没有替换那条路。

> 这是溯源能成立的前提：笔记里写的"见 002b"、论文脚注里的引用，永远有效。
> 项目的 slug 同理——改显示名不动目录名，已经发出去的链接不会失效。

"只追加"在**并发**下同样要成立，所以写接口有乐观并发控制：每一步都带一个
`digest = sha256(note.md 原始字节)[:12]`，`PATCH` 时把它放进请求体的 `expect`
或者 `If-Match` 头，对不上就 409 且一个字节都不写。409 的响应体里带着服务器
当前那一份完整内容，网页会把两边并排摆出来让人自己挑，而不是只弹一句"冲突了"。
不传 `expect` 就是不检查（agent 的追加式写入不受影响）。

> 挡的是这个：你在网页里编辑 004 的正文，同一时间你自己的 Claude 会话跑完实验
> 往 004 里追加了「结果」和产物路径。没有这道闸门的话，你一按保存，那份
> **打开编辑器那一刻**的旧快照整组盖回去，agent 写的东西无声消失，两次请求都是 200。

删除是这条原则**唯一的例外**，只用于"这条记录本身就不该存在"——误建、测试数据、
不小心粘进去的令牌。它和 `dead` 是两回事：`dead` 是研究结论，删掉真实的失败
等于抹掉后来人最需要的线索。删除必须写原因，原因记进项目的 `project.md`；
代价（id 可能被重用、子步骤变孤儿）每次都会明确报出来。详见 [FORMAT.md](FORMAT.md) 第 8 节。

**P3 · 编译，而不是同步.** 视图是文件系统的纯函数。构建无状态、幂等、可随时删掉重来。
实时推送推的是"版本变了，重新编译"信号，不是增量 patch。

**P4 · 失败是一等公民.** `dead` 不是错误状态，是一种结论。死胡同在图上必须可见，
不能折叠、不能默认隐藏，而且不能只靠"变灰"表达——变灰的语义是"不相关"，不是"结论为否"。

**最硬的约束是"没有这个工具也能读"。** 删掉全部程序后，`grep -r "放弃" projects/`
仍然能查到你半年前为什么放弃了某条路。和这条冲突的设计一律否决。

---

## 目录结构

```
research-trace/                     ← 仓库本身就是插件
├── .claude-plugin/
│   ├── plugin.json                 插件清单：skills / commands / agents / mcpServers / userConfig
│   └── marketplace.json            市场目录，于是 /plugin marketplace add 就能装
├── agents/                         子 agent
│   ├── trace-auditor.md            查证：路径还在不在、commit 解不解析得出来（只读，不重跑）
│   └── trace-reproducer.md         重跑：按商定范围复现并写回 repro 记录
├── skills/
│   ├── research-trace/SKILL.md     日常记录的规矩
│   └── trace-audit/SKILL.md        溯源评估的对话流程（派 agent、问用户、写回）
├── commands/doctor.md              /research-trace:doctor —— 诊断 MCP 接通没有
│
├── trace_core.py                   纯函数内核：scan/parse/validate/order/lanes/tree/traceability
├── trace_write.py                  唯一写入路径 —— CLI / 网页 / MCP 全走这里
├── trace_mcp.py                    MCP server（手写 JSON-RPC，零依赖）← 插件清单指向它
├── trace_server.py                 FastAPI：路由 + SSE + 鉴权
├── trace_git.py                    debounce 自动 commit + push（同步的是数据仓）
├── trace_cli.py                    init / projects / new-project / new / rm / check / paths / build / serve / url
├── web/                            无构建步骤，不引 CDN
├── tests/                          pytest（内核 / 布局 / 写入 / 服务 / MCP / 插件 / 文档）+ node --test（markdown 与前端纯函数）
├── FORMAT.md                       记录格式标准：note.md 和 project.md 写什么、怎么可视化、L0–L4 怎么判
├── deploy/                         Caddyfile · systemd unit · 部署说明
└── projects/                       ← 你的数据（上线时用 --data-dir 指到私有仓库）
```

Python 模块留在仓库根，因为这个仓库同时也是一个 pip 包（`pyproject.toml` 在根，
`trace-mcp` 是它的入口点）和一个能独立跑的服务；插件清单用
`${CLAUDE_PLUGIN_ROOT}/trace_mcp.py` 指过去，装的时候整份都会进插件缓存。

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
status: done
title: 加入标题字段，准确率 0.943 → 0.951
date: 2026-03-11
commit: c1d2e3f
author: agent:claude
tags: features, transformer
path: /blue/<组>/<用户>/data/agnews-clean | 去重后的训练集，12 GB
path: /orange/<组>/<用户>/ckpt/run042/best.pt | 权重，265 MB，sha256:7d4e1a9c…
path: https://github.com/你/仓库/tree/9b7d112 | 跑这一步的代码
repro: verified | 2026-08-08 | agent:claude | 干净 split 重跑 3 个种子，0.9506±0.0008
---

## 为什么
## 做了什么
## 结果
## 结论
## 下一步
```

| 小节 | 写什么 |
|---|---|
| `## 为什么` | 承接上一步的什么发现，想验证什么假设 |
| `## 做了什么` | 改了哪些文件，跑了什么命令 |
| `## 结果` | 数字、图、观察 |
| `## 结论` | 假设成立与否 |
| `## 下一步` | 派生出哪些分支 |

正文格式不强制，但这五个小节对应科研步骤的完整生命周期。
标题要和上面**逐字一致**——评级和 `check` 就是按这几个名字去正文里找内容的，
写成 `## 为什么（承接上一步）` 就等于没写。
完整的写法契约（每个键什么意思、指标表怎么写、图注怎么写、L0–L4 怎么判、
渲染器认哪些 markdown）在 [FORMAT.md](FORMAT.md)。

每个项目还有一个 `project.md`，装**项目级**的沉淀——「回译在这个数据集上一直没用」
是三次尝试之后的判断，挂在哪一步都不对。四个固定小节 `核心想法` / `有效` / `无效` /
`坑`，外加一个由系统自己写的 `已删除`（删掉一步之后，「为什么删的」只剩那一行）。
格式见 [FORMAT.md](FORMAT.md) 第 11 节。

**「为什么」是整个系统里唯一无法自动生成的字段。** 日志能自动存，commit 能自动记，
环境能自动 freeze，只有"我当时为什么决定试这个"必须人写。系统的全部设计目的，
就是让写这一段的成本低到你愿意每次都写。

front-matter 用手写解析器而不是 YAML：`title: 试了 3:1 采样` 在 YAML 里是语法错误，
而这类标题在科研记录里很常见。规则就是"冒号左边是键、右边整行是值"。

"右边**整行**"要当真：`status: done   # wip | done | dead` 里那段行尾注释是值的一部分，
会让 status 变成未知值退回 `wip`。整行以 `#` 开头才是注释。

正文里写 `[[003b]]` 会渲染成跳转链接，并在 003b 的页面显示"被这些步骤引用"。
这是"多父 DAG"的廉价替代品——想说"本步综合了 A 线和 B 线"，写一句就够，
不必把森林升级成 DAG（那会让行序变成拓扑排序、轨道分配要处理合并边，复杂度约翻三倍）。

## 外部产物的位置（溯源的另一半）

GB 级的东西——数据集、checkpoint、中间特征——不进仓库。仓库里只记**它在哪**：

```
path: /blue/<组>/<用户>/data/agnews-clean | 去重后的训练集，12 GB
path: /orange/<组>/<用户>/ckpt/run042/best.pt | 权重，265 MB，sha256:7d4e1a9c…
path: https://github.com/你/仓库/tree/9b7d112 | 跑这一步的代码
path: s3://bucket/exports/run042.parquet | 导出的预测结果
path: https://zenodo.org/record/1234567 | 论文附带的公开版本
```

格式就是 `path: <位置> | <说明>`，可以写任意多行。竖线右边是自由文本——
校验和、大小、"在哪台机器上"、"已确认无用可删"，想记什么记什么。

位置的类型自动识别（超算 `/blue//orange//red//scratch/` · GitHub · Dropbox ·
Google Drive · S3/GCS · Zenodo/figshare/OSF · HuggingFace/W&B · 本机盘符），
只是给个徽章，猜错也不影响任何东西。http(s) 的会渲染成可点链接，其余的一键复制。

```bash
python trace_cli.py paths                 # 列出所有产物在哪
python trace_cli.py paths --kind hpc       # 只看超算上的
grep -rn "^path:" projects/                # 删掉全部程序之后照样能查
```

新建子步骤时会**继承父步骤的路径**——同一条线上数据和代码的位置多半没变，改比重打省事。

## id 规则

三位数字，分叉时加字母后缀：`003` 派生出 `004` / `004b` / `004c`。
兄弟共享数字，一眼看得出兄弟关系，而且**任何已有 id 都不会因为后来多出一个兄弟而改名**。
每个项目的 id 各自从 001 开始，互不影响。

---

## 装成 Claude Code 插件（推荐）

```
/plugin marketplace add jinhang23/research-trace
/plugin install research-trace@research-trace
```

装的时候会弹一个配置框，**第一项先问这台机器是什么角色**，装完随时可以在
`/plugin` 里改：

| 角色 | 什么情况 | 要填 |
|---|---|---|
| **server** | 数据仓就在这台机器上，直接读写文件 | 数据仓目录 |
| **client** | 数据在另一台机器的域名后面，你通过公网管理 | 远端服务地址 + 写入令牌 |
| **auto**（默认） | 不确定就用它：填了地址走远端，只填了目录走本地 | 按需 |

选定角色之后**配错会当场报出来**，不会悄悄退回另一种模式 ——
「我选了客户端，怎么读到的是本地空目录」这种问题最难查。

| 配置项 | 填什么 |
|---|---|
| 这台机器的角色 | server / client / auto |
| 数据仓目录 | **含有** `projects/` 的那一层，不是 `projects/` 本身（指错一层会造出 `projects/projects`）|
| 远端服务地址 | `https://你的域名/t/<space>`，客户端才填 |
| 写入令牌 | 多数人不用填，见下 |
| Python 解释器 | 默认 `python3`。**Windows 上多半要改成绝对路径** |

**装完先自检。** 一条命令，不需要 Claude、不需要网络（本地模式下）：

```bash
python <插件根>/trace_mcp.py --selfcheck
```

它会报解释器版本、配置从哪读的、角色、后端、项目数，**试一次写**（远端打一个必然
404 的请求：令牌不对是 401、令牌对是 404，两种回答都不写一个字节；本地是建一个点
开头的探针文件再删掉），并**真跑一遍 JSON-RPC 握手**。通不通、哪一项要改，它自己会说。
插件根目录可以用 `claude mcp list` 或在 `~/.claude/plugins/cache/` 下找 research-trace。

**一个必须知道的不对称**：`/plugin` 里填的那些值是灌给 **MCP 子进程**的环境变量
（`TRACE_ROLE` / `TRACE_DATA` / `TRACE_URL` / `TRACE_TOKEN`），你在自己 shell 里跑
`--selfcheck` 是**看不到**它们的——那不代表插件坏了。自检发现这四个一个都没有时
会把这段说明打出来，并告诉你两条查真值的路：会话里直接调一次
`mcp__plugin_research-trace_trace__trace_projects`，或者从 `/plugin` 里把值抄出来重跑：

```bash
python <插件根>/trace_mcp.py --selfcheck --role client --url https://你的域名/t/<space> --token <令牌>
```

最后一项值得单说：`.mcp.json` 的 `command` 是静态字符串，而 `python` 在 Windows 上
经常指向别的软件自带的 2.x（作者机器上就是 MGLTools 的 Python 2.7.11），`python3`
又可能只是个没有扩展名的 shell 脚本、Claude Code 起子进程时用不了。所以它被做成
用户配置项，默认 `python3`（Linux / macOS / 超算上直接可用），需要时填绝对路径。

装完跑 `/research-trace:doctor` —— 它会实跑一遍 JSON-RPC 握手，通没通、
哪一项要改，都会直接说。

装了插件之后，把本地那份 `~/.claude/skills/research-trace/` 删掉，免得和插件里的重名。

## MCP（推荐给 agent 用）

`trace_mcp.py` 把 trace 暴露成 9 个 MCP 工具。比让 agent 自己拼 HTTP 请求好在：
参数有 schema（不合法的调用先被拦下）、不用生成 requests/curl 代码、
中文不会再撞上终端编码。

**零依赖。** MCP 是一份开放协议规范，`mcp` 那个 pip 包只是它的官方 Python SDK 之一。
stdio 侧要实现的就是换行分隔的 JSON-RPC 2.0 加四个方法（`initialize` / `tools/list` /
`tools/call` / `ping`），所以这里直接说协议，不依赖 SDK：

- 任何裸 Python 3.10+ 都能跑，HiperGator 上不用往 conda 环境里装东西
- 不会被 SDK 的破坏性改版牵连（`mcp` 2.0 就删掉了 1.x 的整套装饰器 API，
  协议本身一个字没变）

代价是协议细节得自己守住，所以测试里除了自测，还会**拿官方 SDK 的客户端连上来跑一遍
互操作**（`tests/test_mcp.py::test_interop_with_the_official_sdk_client`，
SDK 只是测试期依赖，装了才跑）。

### 装

装完会得到一个真正的 `trace-mcp` 命令（Windows 上是 `Scripts/trace-mcp.exe`），
配置里就不用写死某个 `.py` 的绝对路径了。

```bash
# 只要 MCP：不用 clone，也不会拉任何依赖
pip install "git+https://github.com/jinhang23/research-trace"

# 还要跑网页服务：clone 下来（web/ 和 projects/ 得在仓库里）
git clone https://github.com/jinhang23/research-trace && cd research-trace
pip install -e ".[server]"
```

### 配

```bash
# 本地模式：agent 和数据在同一台机器上，不需要起服务
claude mcp add trace -s user -e TRACE_DATA=/path/to/数据仓 -- trace-mcp

# 远端模式：agent 在 HPC 上，数据在你的域名后面
claude mcp add trace -s user \
  -e TRACE_URL=https://你的域名/t/<space> \
  -e TRACE_TOKEN=<写入令牌> \
  -- trace-mcp
```

`-s user` 是全局生效；不加的话默认是 `local`（只在当前项目目录下生效）。

也可以直接写进 `~/.claude.json` 的 `mcpServers`：

```json
"trace": {
  "type": "stdio",
  "command": "trace-mcp",
  "env": { "TRACE_DATA": "/path/to/数据仓" }
}
```

> **Windows 上如果提示找不到 `trace-mcp`**，把 `command` 换成绝对路径。查路径：
> `python -c "import shutil; print(shutil.which('trace-mcp'))"`
> （通常是 `C:/ProgramData/anaconda3/Scripts/trace-mcp.exe` 这种）

| 工具 | 干什么 |
|---|---|
| `trace_projects` | 列项目 + 步骤数与状态分布 |
| `trace_read` | 读整棵树（缩进树，比 JSON 省 token），或读单步全文 + 溯源链 + L0–L4 |
| `trace_search` | 在标题/正文/标签里搜——回答"之前是不是试过 X""为什么放弃了 Y"。不给 `project` 就搜全部项目 |
| `trace_new_project` | 建项目。数据仓为空时 `trace_new_step` 会自动建，不用先跑 init |
| `trace_insight` | 往项目洞察里记一条：核心想法 / 有效 / 无效 / 坑 |
| `trace_new_step` | 建步骤（支持幂等键、外部产物路径） |
| `trace_update_step` | 改状态/正文/路径；`append` 和 `add_paths` 追加比整组替换安全；`repro` 追加一条复现记录 |
| `trace_delete_step` | **真删**一步，必须写原因。只用于误建/测试数据/粘进去的令牌——失败的实验请标 `dead` |
| `trace_attach` | 传附件；**图片必须给 caption**，给了就自动在正文插入引用 |

规矩写在工具描述里，agent 调用时就能看到：先读后写、先建 wip 再开跑、
必须写「为什么」、失败标 dead 并写清放弃理由、改 `id`/`parent` 直接拒绝。

**格式标准怎么送到 agent 手里。** `pip install git+…` 那条路只打包三个 `.py`，
那台机器上根本不存在 `FORMAT.md`——所以 MCP 的 `initialize` 返回的 instructions 里
**内联**了 FORMAT.md 的可执行摘要（五个小节、指标表规则、图注、`[[交叉引用]]`、
`paths` 格式、L0–L4 判据、`repro` 三态）。那是唯一无论怎么装都一定送达的通道。
只有当 `FORMAT.md` 真的躺在 `trace_mcp.py` 旁边时，才会再追加一行指向它的**绝对路径**。

MCP 和 REST API 是同一套后端的两个门面，可以混用。

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
`GET {base}/p/{项目}/files/{id}/{文件名}` 返回原始字节。

## API（给 agent）

Base = `/t/<space>`。读公开，写要 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects` | 项目列表 + 各自的步骤数与状态分布 |
| POST | `/api/projects` | 建项目 `{name}` |
| PATCH | `/api/projects/{项目}` | 改显示名 `{name}` 和/或洞察：`{insights}` 整体替换那四个小节、`{add_insight: {kind, text}}` 追加一条。slug 不动 |
| GET | `/api/p/{项目}/forest` | 全量：steps（含 lane/row/children/backlinks/files/trace/digest）+ tree + warnings |
| GET | `/api/p/{项目}/steps/{id}` | 单步 + `lineage`（到根的完整路径） |
| GET | `/api/search` | 跨项目搜索。`?q=` 或 `?query=`，不给 `project` 就搜全部；回 `hits/total/truncated` |
| GET | `/api/status` | 标题、版本、项目数、步骤数、git 同步状态、`write_protected` |
| GET | `/api/git` | 自动 git 同步的状态：`ok/state/summary/hint/last_ok_at/pending`。带令牌多给 `detail`（git 原文，含服务器路径） |
| GET | `/api/events` | SSE，推 `{version}` |
| POST | `/api/p/{项目}/steps` | 建步骤。带 `key` 则幂等——重试不会产生重复 |
| PATCH | `/api/p/{项目}/steps/{id}` | 改 status/title/body/date/commit/author/tags/paths；`add_paths` `add_repro` 是追加。带 `expect` 或 `If-Match` 做冲突检测 |
| DELETE | `/api/p/{项目}/steps/{id}` | 真删目录。`{reason}` 必填 —— 只追加原则的唯一例外 |
| PUT | `/api/p/{项目}/steps/{id}/files/{path}` | 上传附件到指定文件名（raw body） |
| POST | `/api/p/{项目}/steps/{id}/files` | 上传附件，服务端定名（`X-Filename` 可选；没给就按内容哈希命名，重复内容自动复用） |
| DELETE | `/api/p/{项目}/steps/{id}/files/{path}` | 删掉一个附件 |
| POST | `/api/sync` | 立刻跑一次 git commit + push，返回和 `/api/git` 同样的结构 |

读接口一律公开（和 `forest` 一致），写接口一律要 `Bearer`。
另外两条不在 API 命名空间下、给人和有视觉能力的 agent 用的路径：
`{base}/p/{项目}/` 是网页，`{base}/p/{项目}/files/{id}/{文件名}` 直接返回附件原始字节（公开）。

**错误是可分支的，不是一坨 500。** 文件系统层的失败会带上 `kind` 和 `written: false`：

| `kind` | 状态码 | 什么时候 |
|---|---|---|
| `name_too_long` | 400 | 文件名/路径超长（Windows 整条路径 260，Linux 单个文件名 255 **字节**） |
| `locked` | 409 | 文件被别的程序占用 |
| `permission` | 403 | 权限不足、目录只读 |
| `disk_full` | 507 | 磁盘满 |
| `missing` | 404 | 路径上有一段不存在 |
| `unavailable` | 503 | NAS / 网络盘掉线 |
| `io_error` | 500 | 其余 |

`written: false` 是可以信的：记录本体和附件的写入都是「同目录临时文件 → fsync →
`os.replace`」，失败时磁盘上的原文一个字节都没动过。
令牌不对是 401，和上面这些**不共用**异常处理——以前共用一个，于是"文件被占用"
会被报成"需要写入令牌"，agent 拿到 401 会去重找令牌，而真正的问题在磁盘上。

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

Claude Code 用户：[`skills/research-trace/SKILL.md`](skills/research-trace/SKILL.md)
已写好接入规则（装插件时随插件一起分发）。走 REST 这条退路时设好 `TRACE_URL`、
`TRACE_TOKEN`、`TRACE_PROJECT` 三个环境变量即可；有 MCP 工具就直接用工具。
怎么写记录的契约在 [FORMAT.md](FORMAT.md)。

## 命令

```bash
python trace_cli.py projects                      # 列出项目
python trace_cli.py new-project --name "课题名"
python trace_cli.py new -P <项目> --title "..."    # 新建一步
python trace_cli.py rm -P <项目> <id> --reason "…" # 真删一步（原因必填）
python trace_cli.py check [-P <项目>] [--strict]   # 校验不变量 + 打印 L0–L4 与最弱一环
python trace_cli.py paths [-P <项目>] [--kind hpc] # 列出所有外部产物的位置
python trace_cli.py build --out dist              # 静态导出，file:// 可直接打开，断网可用
python trace_cli.py url                           # 打印访问地址与令牌
python trace_mcp.py --selfcheck                   # 自检：解释器 / 角色 / 后端 / 读 / 写 / JSON-RPC 握手
python -m pytest tests                            # 内核 / 布局 / 写入 / 服务 / MCP 协议 / 插件 / 文档
node --test "tests/**/*.test.js"                  # markdown 渲染 + 前端纯函数
```

`check` 的退出码默认只看结构性错误（重复 id、环这类逼着构建改动数据的问题）。
`--strict` 把内容层缺陷（`dead` 没写结论、图没图注、链卡在 L0/L1）也算成失败——
给 CI 用。默认不算，是因为 `wip` 天天红一片只会训练大家忽略警告。

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
| `note.md` 不是 UTF-8（GBK / UTF-16） | 读得下来（非法字节变 `�`）+ 一条 `not_utf8` **错误**级警告；**写入直接拒绝**，让你先转码 |

警告显示在页面顶栏，从不阻塞构建。

最后那一条值得单说：以前是静默 `errors="replace"`，于是 Windows 中文环境下
`cmd.exe` 的 `echo … > note.md`（GBK）或 PowerShell 5.1 的 `>`（UTF-16LE）写出来的笔记
一声不吭地显示成乱码，你在网页上点一下状态按钮，那份 `�` 就落盘了，原始字节不可逆丢失。
现在写入侧会拦住并告诉你哪个字节解不开。

## 部署

见 [deploy/README.md](deploy/README.md)。

## 刻意不做的

多人协作（账号、权限分级、评论、@提醒）· 实时协同编辑（OT / CRDT，两个人同时敲一段
正文）· 自动捕获（不 hook shell / git / 文件系统）· 重跑与复现执行 ·
大文件版本管理 · 全文索引服务（`grep` 就够）· 拖拽自由布局（布局一旦手工摆放，
就不再是文件系统的纯函数了）。

它们都会把"没有这个工具也能读"变成一句空话。

要和上面 P2 那段区分开：**并发写入的冲突检测是做了的**（`expect` / `If-Match` → 409 +
把两边内容摆出来）。做的是"别让后一次保存无声地吃掉前一次"，不做的是"两个人
同时编辑同一段正文还能自动合并"。前者是数据完整性，后者是协作产品。
