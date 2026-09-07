# Research Trace

给 Claude Code 装上的研究记忆：你照常干活，它把**完整的原始过程**存到服务器，再由一个独立的 Recorder 把
**值得以后回看的认识**整理成按研究线组织、可搜索、可纠正的项目记录。论文、想法、数据理解、失败方案、
关键实现、指标、产物路径都算。当前版本 2.0.0a40（alpha）。

## 5 分钟上手

前提：Python 3.10+、Claude Code、一个中央服务地址（自己起一个见[部署服务端](#部署服务端)；已有的直接用）。
下面以 `https://trace.example.org` 为例，全部在**跑 Claude Code 的那台机器**上做。

```bash
# 1. 装客户端包，记下解释器的绝对路径（conda / HPC 上别用裸 python3）
python -m pip install --force-reinstall --no-deps "research-trace @ git+https://github.com/jinhang23/research-trace"
python -c "import sys; print(sys.executable)"

# 2. 装插件。python 和 url 必须显式传，缺一个所有 hook 都会静默失败
claude plugin marketplace add jinhang23/research-trace
claude plugin install research-trace@research-trace \
  --config python=/abs/path/to/python --config url=https://trace.example.org

# 3. 登录这台机器（二选一）
trace-login --url https://trace.example.org --token                 # 内网档：粘贴管理员给的访问密钥
trace-login --url https://trace.example.org --device-name my-box    # 团队档：打开网页 /device 输 8 位码

# 4. 绑定要记录的项目。不绑定的目录一个字都不会被记
cd /path/to/my-project
trace-project bind --url https://trace.example.org --create --name "我的项目"

# 5. 开语义整理：先在 claude.ai 账户设置里关掉 Extra usage，再确认一次
trace-project recorder-enable . --model sonnet --confirm-extra-usage-disabled

# 6. 常驻一个 Recorder（tmux / nohup / systemd 都行，一台机器一个）
trace-recorder --watch --data-dir ~/.claude/plugins/data/research-trace-research-trace \
  --url https://trace.example.org

# 7. 自检
trace-project status --url https://trace.example.org
```

自检末尾要看到 `capture: last event …` 且没有 `!!!`。然后正常用 Claude Code，第一轮对话结束后打开网页就能看到
原始历史，Recorder 处理完后出现记录。

## 日常怎么用

**什么都不用改。** 在绑定过的项目里照常和 Claude Code 干活，hook 在后台把每一轮存下来。

- **让它记某件事**：直接说「记一下这个结论」「更新一下 Overview」「登记这个产物」，插件自带的 skill 会让 agent
  调对应工具。问「之前试过什么」「为什么放弃了 X」「这个结果哪来的」也一样。
- **看记录**：打开中央网页，左侧是 Chapter，中间是结构图或按时间的列表，每条记录能回到它的来源对话和命令。
- **纠正 / 确认**：网页上直接改标题、正文、所属章、延续自谁；写纠正评论；确认一条记录。Recorder 之后不会覆盖你改过的版本。
- **建研究线**：Chapter 由人建（网页或 API），Recorder 不会自己建章；它拿不准归属就放 Inbox，你再挪。
- **补旧工作**：在绑定的项目里让 agent 分主题、**带日期**地把以前的工作总结一遍，这些对话被正常采到、整理成记录。
  不要一次贴几万字，每轮只有前 5 万字符进模型。
- **暂停采集**：某个项目 `trace-project disable .`；所有项目 `TRACE_CAPTURE=off claude`（处理令牌等敏感内容时）。
  暂停期间的内容不会补采。
- **控制额度**：Recorder 攒够约 2 万字符、或最老材料超过 20 分钟、或会话结束才调一次模型。改阈值：
  `trace-project recorder-enable . --model sonnet --confirm-extra-usage-disabled --batch-min-chars 40000 --batch-max-age-minutes 30`；
  `TRACE_BATCH_MIN_CHARS=0` 回到一轮一次。暂停模型调用但保留队列：`trace-project recorder-disable .`。
- **一轮里聊了两条线**：会写成两条记录分进各自的 Chapter，不用刻意拆对话。一轮一条都不写也正常。
- **`claude -p` 非交互模式**：hook 采集、投递、Recorder 都正常，只是 agent 拿不到 MCP 工具授权，「记一下」这类
  主动请求在交互式会话里才生效。

## 常用命令

| 想做什么 | 命令 |
|---|---|
| 看这个目录绑没绑、凭据是否生效、采集活着没 | `trace-project status --url <中央>` |
| 绑定 / 排除 / 恢复一个项目 | `trace-project bind --url <中央> --create --name <名>` / `trace-project disable .` / `trace-project bind .` |
| 开 / 关 Recorder | `trace-project recorder-enable . --model sonnet\|haiku --confirm-extra-usage-disabled` / `recorder-disable .` |
| 登录 / 续期 / 登出这台机器 | `trace-login --url <中央> --token` 或 `--device-name <名>` / `--renew` / `--logout` |
| 本机还有多少没传上去 | `trace-deliver --status --data-dir <插件数据目录>` |
| 手动传一次 / 常驻投递 | `trace-deliver --data-dir <插件数据目录> --url <中央>` / 加 `--watch` |
| Recorder 状态 / 常驻 / 解除卡住的批次 | `trace-recorder --status --data-dir <…>` / `--watch` / `--retry-blocked` |
| 检查插件用的解释器和连通性 | `<插件的python> trace_mcp.py --selfcheck --url <中央>` |
| 导出 / 校验 / 恢复备份 | `trace-backup export --data-dir <数据目录> --target <备份目录>` / `verify --source <备份目录>` / `restore --source <备份目录> --data-dir <空目录>` |
| 真删误采的敏感内容 | `trace-backup purge …`（留下不含原文的审计记录） |

插件数据目录默认是 `~/.claude/plugins/data/research-trace-research-trace/`；`trace-project status` 会自己找到它。
升级：`pip install --force-reinstall --no-deps …` 加 `claude plugin marketplace update research-trace && claude plugin update research-trace@research-trace`，
然后重启 Recorder 和 Claude Code。

## 出问题先看这里

这套系统出错时几乎不报错，只是安静地少做事，所以先跑 `trace-project status --url <中央>`，再对症：

| 现象 | 原因 | 处理 |
|---|---|---|
| 对话正常但网页上什么都没有 | 目录没绑定，或插件配置丢了 | `trace-project status` 说 `not bound` 就 bind；说 `!!! plugin not enabled` / `option "python" is not set` 就重跑 `claude plugin install … --config python=… --config url=…` |
| 会话结束时 stderr 有 `Plugin option "…" isn't set` | 装插件时没传 python / url | 同上 |
| Claude Code 显示 MCP `CONNECTION_CLOSED` | 插件的 `python` 指向的解释器没装 research-trace 或 mcp SDK | 用那个解释器跑 `trace_mcp.py --selfcheck`，按提示换解释器或重装 |
| `trace-deliver --status` 里 pending 一直不归零，或 401 / 403 | 这台机器没登录或凭据过期 | `trace-login …`；内网档用 `--token`，团队档用设备码；`--renew` 续期 |
| Recorder 状态 `auth` / `paid_credentials` | `claude` 没用订阅登录，或环境里有 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | `claude auth status`；无头机器用 `claude setup-token` 得到的 token 放进 `CLAUDE_CODE_OAUTH_TOKEN` 再起 Recorder；去掉 API key 变量 |
| Recorder 状态 `quota` / `overage` | 订阅额度用完 / 触发了额外用量 | quota 到点自动恢复；overage 永久停，去账户关掉 Extra usage 后 `trace-recorder --retry-blocked` |
| Recorder 状态 `attempts_exhausted` / `format` | 模型连续几次没给出合法计划 | 材料还在，`trace-recorder --retry-blocked` 再试；反复出现把 `--status` 发给维护者 |
| Recorder 的日志文件是空的 | 正常：只有处理了批次才写一行 | 看 `trace-recorder --status` 的 processed / written 计数 |
| `pip install --upgrade` 说 already satisfied 但代码没更新 | pip 只比版本号 | 加 `--force-reinstall --no-deps` |
| 升级后 hook 行为没变 | 插件要单独更新 | `claude plugin marketplace update … && claude plugin update …`，然后重启 Claude Code |
| 网页写入只算 `recorder`，确认不了记录 | 单机档没有人的身份 | 用内网档（密钥登录网页）或团队档（GitHub） |

## 部署服务端

一台机器装一次，所有客户端连它。安全分三档，按「谁能连到这个端口」选：

```bash
git clone https://github.com/jinhang23/research-trace && cd research-trace
python -m pip install -e ".[server]"

# 单机档：只听回环地址，读取公开，适合自己试
trace-server --data-dir /srv/research-trace/data --host 127.0.0.1 --port 8765

# 内网档：生成一把访问密钥同时守读写；把密钥发给每台客户端 trace-login --token
trace-server --init --data-dir /srv/research-trace/data --host 0.0.0.0
trace-server --env-file /srv/research-trace/data/server.env --host 0.0.0.0 --port 8765

# 团队档 / 公网：GitHub OAuth + HTTPS 反代，见 docs/QUICKSTART.md 第 0、2 节
```

- 挂在反代的路径前缀下：`--base-path /trace`，反代**不要**剥前缀，并设 `TRACE_TRUST_PROXY_HEADERS=true`；
  公网地址写进 `TRACE_PUBLIC_URL`。
- 只放行某些网段：`TRACE_ALLOWED_NETWORKS=10.0.0.0/8,1.2.3.4/32`。
- 升级：停服务 → `git pull` → `pip install -e ".[server]"` → 启动；数据库迁移自动跑。
- 数据在 `<数据目录>/trace.sqlite3` 和 `objects/`；备份格式版本为 3，版本 2 的旧导出仍可 verify 和 restore。

## 记录长什么样

```text
Project
├── Overview                当前认识、阶段结果、重要洞察与错误
├── Chapter: 基线配置        人定义的并列研究线
│   └── Node 01
├── Chapter: 消融实验
│   ├── Node 02 ─ 延续 → Node 01     延续关系可以跨 Chapter
│   └── Node 03 ─ 延续 → Node 02
├── Inbox                   Recorder 拿不准归属时先放这里
└── Raw history             完整原始过程，永久保留，按需展开
```

Recorder 建的记录一律「未确认」，人确认、纠正、改章之后它不会再覆盖。为什么这样设计见[设计理念](docs/DESIGN.md)。

## 开发

```bash
ruff format . && ruff check .        # 风格
python -m pytest -q                  # 单元与契约测试
python scripts/ops_battery.py        # 运维角度，真 CLI + 假 claude，不花额度
python scripts/net_battery.py        # 远端 TLS / 代理 / 重定向
python scripts/simulate_research.py  # 三天研究情境，8 次 sonnet 调用
```

改了插件包里的东西（`hooks/`、`scripts/`、`skills/`、`.claude-plugin/`、`trace_mcp.py`）必须 bump 版本，否则已装机器
拿不到更新；每次推 main 都 bump，pip 才会重装。版本唯一来源 `research_trace/__init__.py`。

## 文档

- [快速开始](docs/QUICKSTART.md) —— 完整部署、登录、绑定、网络检查清单、备份与紧急清除
- [设计理念](docs/DESIGN.md) · [完整需求](docs/REQUIREMENTS.md) · [格式清单](docs/FORMATS.md)
- [Recorder 协议](hooks/RECORDER_PROTOCOL.md) · [Recorder 复用方案](docs/RECORDER_REUSE_PLAN.md) · [验证脚本](scripts/README.md)
- [变更记录](CHANGELOG.md) · [待办](docs/TODO.md)

## License

[MIT](LICENSE)
