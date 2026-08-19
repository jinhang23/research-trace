# Research Trace 快速开始

系统由四部分组成：一台中央服务、每台 Claude Code 机器上的插件（hook + MCP）、
每台机器上的独立投递器 `trace-deliver`，以及服务端的私有 Git 备份仓库。
中央服务是在线真相源；插件 outbox 是断网缓冲；投递器负责把 outbox 送上去；
Git 仓库是可验证的灾备副本。

两件事先说清楚，它们决定了下面每一步：

- **采集是按项目 opt-in 的。** 装上插件不会记录任何东西，直到你对某个目录执行
  `trace-project bind`（第 4 节）。
- **hook 不上传。** hook 只把事件写进本机 `pending/`，把它送到中央、并且只在中央返回
  2xx 之后搬进 `sent/`，是 `trace-deliver` 这个独立进程的事（第 5 节）。

## 1. 启动中央服务

需要 Python 3.10+。服务端安装 FastAPI 依赖：

```bash
python -m pip install -e ".[server]"
trace-server --data-dir /srv/research-trace/data \
  --host 127.0.0.1 --port 8765 --token "<long-random-token>"
```

不配置 OAuth 时，浏览器打开 `http://127.0.0.1:8765/`；读取兼容旧的公开模式，写操作使用
Bearer token。团队部署应按下一节配置 GitHub OAuth 和 HTTPS。启用后，Project、原始历史、
搜索和附件都必须经过网页登录或机器 Bearer token，不再公开读取。

HiperGator 上先按集群要求加载 conda，再使用该环境的绝对 Python 路径：

```bash
module load conda
conda activate <env_name>
python -c "import sys; print(sys.executable)"
```

服务只允许一个进程访问这份 SQLite 数据目录，不要启动多个 uvicorn worker，也不要让多台
机器直接挂载同一个 SQLite 文件；所有机器都通过 HTTPS/MCP 连接这一个服务。

## 2. 配置 GitHub OAuth 网页登录

先在 GitHub 的 **Settings → Developer settings → OAuth Apps → New OAuth App** 创建一个 OAuth
App。假设公网地址是 `https://trace.example.org`，填写：

- Homepage URL：`https://trace.example.org`
- Authorization callback URL：`https://trace.example.org/auth/github/callback`

callback 必须与 `TRACE_PUBLIC_URL` 对应；生产环境必须是 HTTPS。然后在服务器的私密环境
配置（例如只对服务账号可读的 systemd `EnvironmentFile`）中设置：

```bash
TRACE_PUBLIC_URL=https://trace.example.org
TRACE_GITHUB_CLIENT_ID=<oauth-app-client-id>
TRACE_GITHUB_CLIENT_SECRET=<oauth-app-client-secret>
TRACE_SESSION_SECRET=<至少32字符的独立随机值>
TRACE_GITHUB_ADMINS=jinhang23
TRACE_GITHUB_ALLOWED_USERS=collaborator-a,collaborator-b
```

可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成 session secret。不要把
client secret、session secret、设备凭证或旧版机器 token 提交进仓库或 Git 备份。

成员准入可以组合使用：

- `TRACE_GITHUB_ADMINS`：首次登录即为 admin；至少建议保留一个；
- `TRACE_GITHUB_ALLOWED_USERS`：允许登录，首次为 member；
- `TRACE_GITHUB_ALLOWED_ORG=<org>`：允许该组织的 active member，会额外请求 `read:org`；
- `TRACE_GITHUB_ALLOW_ALL=true`：允许任意 GitHub 用户，不建议用于团队私有记录。

白名单里的用户名会在首次登录成功时**钉到该 GitHub 账号的数字 id** 上（记录在数据目录的
`identity-pins.json`），此后同一条配置只匹配那个 id：本人改名仍然有效，别人抢注这个已释放的
用户名则会被拒绝。也可以直接写显式形式 `TRACE_GITHUB_ADMINS=id:12345`（只有 `id:` 前缀算
数字 id，因为 GitHub 允许纯数字用户名）。

从白名单里移掉一个人会立即生效：他的网页 session 和设备凭证在下一次请求时就被拒绝，不用等过期。

其它可调项：

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `TRACE_DEVICE_CREDENTIAL_DAYS` | `90` | 设备凭证有效期；过期后需要重新登录或提前续期 |
| `TRACE_DEVICE_START_LIMIT` | `10` | 每个客户端在窗口内可发起的设备登录次数 |
| `TRACE_DEVICE_START_WINDOW_SECONDS` | `600` | 上面这个限流窗口 |
| `TRACE_TRUST_PROXY_HEADERS` | `false` | 是否按 `X-Forwarded-For` 识别客户端 |

**用反向代理时必须设 `TRACE_TRUST_PROXY_HEADERS=true`**（就是下面 Nginx/Caddy 那种部署）。
否则所有请求看起来都来自 `127.0.0.1`，按 IP 的限流会退化成一个全局桶。反过来，没有可信代理时
绝不要打开它，否则任何人都能伪造来源 IP 绕过限流。

启动命令本身无需携带 GitHub secret：

```bash
trace-server --data-dir /srv/research-trace/data \
  --host 127.0.0.1 --port 8765
```

由 Nginx/Caddy 等反向代理终止 HTTPS，并将请求转发到这个回环地址。登录采用 OAuth state、
浏览器绑定 nonce 和 PKCE；登录成功后只保存 Research Trace 自己的 HttpOnly session cookie。
GitHub access token 只用于回调时读取身份和可选组织成员状态，不落库。网页写入另外要求 CSRF
token。管理员从右上角账户菜单管理 `reader`、`member`、`admin` 和禁用状态；系统禁止禁用或
降级最后一个有效管理员。

仅在本机开发时，可以使用 `TRACE_PUBLIC_URL=http://127.0.0.1:8765` 和
`TRACE_INSECURE_COOKIES=true`。这个开关拒绝非 loopback 地址，不能用于跨机器部署。

## 3. 用 GitHub 账号登录工作站或 HPC

机器不保存 GitHub access token，也不需要管理员复制共享 Token。安装项目后运行：

```bash
trace-login --url https://trace.example.org --device-name hipergator-login-01
```

终端会打印服务器的 `/device` 地址和一个 8 位验证码。用获准的 GitHub 账号登录该页面，
**手工输入这个验证码**，确认页面上显示的设备名称确实是你刚才那台机器，再点“批准此设备”，
终端会自动完成。凭证只允许访问 Research Trace，并绑定当前 GitHub 用户、服务器 URL 和设备名称。

没有一键批准链接，这是故意的：设备码授权最常见的攻击就是把 `…/device?code=…` 这样的链接发给
别人点。**永远不要输入别人发给你的验证码**，只批准你自己刚刚在自己机器上运行 `trace-login`
产生的那一个。

凭证会过期（默认 90 天，见 `TRACE_DEVICE_CREDENTIAL_DAYS`）。在过期之前可以自助续期，不需要
再走一次人工批准：

```bash
trace-login --url https://trace.example.org --renew
```

过期之后就只能重新 `trace-login` 并由人再批准一次。网页“账户 → 管理已登录设备”里可以看到每台
设备的到期时间。

Claude Code 插件里还有一个 `trace_login` MCP 工具，它按同一套流程走：`action=start` 返回
verification URI 和 8 位验证码（不返回任何可点击的一键批准链接），你手工输码批准后再
`action=status` 完成；`status` 会一并报出凭证的到期时间。登录完成后 MCP、Recorder 和投递器
都会自动读取本机凭证，不需要在每次会话中登录。

默认凭证位置：

- Linux/HPC：`${XDG_CONFIG_HOME:-~/.config}/research-trace/credentials.json`
- Windows：`%APPDATA%\\ResearchTrace\\credentials.json`

文件使用原子写入，并在支持的平台设为仅当前用户可读。它不在项目、插件 outbox 或 Git 备份中。
文件本身不会被自动清理，但里面的凭证到期后服务端会拒绝它（到期时间同时写在这个文件里）。
可以从网页右上角“账户 → 管理已登录设备”撤销，或者在该机器运行：

```bash
trace-login --url https://trace.example.org --logout
```

管理员禁用一个 GitHub 用户、或把他从白名单里移除时，其网页 session 和全部设备凭证会失效。
服务端仍接受 `TRACE_TOKEN` 作为旧部署迁移兼容项，但新机器不需要配置它；共享 token 没有可
认证的主体，因此它的写入一律按 `recorder` 处理，不能确认或纠正记录。

## 4. 安装 Claude Code 插件

```text
/plugin marketplace add jinhang23/research-trace
/plugin install research-trace@research-trace
```

在插件配置中填写：

- `url`：中央服务地址；
- `token`：旧部署兼容项；使用 GitHub 设备登录后留空；
- `python`：Python 3.10+ 解释器的绝对路径；
- `capture`：全局暂停开关，默认 `on`；改成 `off` 会让所有项目都停止采集，暂停期间不补采。
  它**不是**采集的开关来源：采集本身要先绑定项目，见下一小节。

可先在仓库内验证 MCP 进程：

```bash
python trace_mcp.py --selfcheck
```

### 4.1 绑定要记录的项目（不绑定就什么都不录）

装好插件之后，hook 对每个目录做的第一件事是从当前目录向上找 `.research-trace.json`。
找不到就立即返回：**不建目录、不写文件、不读 transcript**。所以默认状态是「什么都不录」。

```bash
cd /path/to/my-project
trace-project bind --url https://trace.example.org
```

它会用 workspace key 去中央解析项目：

- 匹配到已有项目就把 `project_id` 写进 marker；
- 匹配不到时**拒绝静默新建**，列出候选并要求你用 `--project-id <已有 id>` 或 `--create` 明确表态；
- 中央不可达时写一份离线 marker（也可以直接 `--offline`），采集立刻生效，`project_id` 等下次
  绑定时补上；在补上之前，这段原始历史以未归属状态上传。

marker 跟着项目目录走，所以换一台机器、换一个绝对路径、或者开一个 Git worktree，都会解析到
同一个中央项目。绑定时默认还会把规范化的 Git remote 作为第二个 workspace key 写进去
（`--no-git` 关闭）。

```bash
trace-project status                 # 看这个目录到底绑没绑、绑到哪个项目
trace-project disable                # 保留 marker，但停止采集（项目排除）
trace-project bind                   # 想重新开启时再 bind 一次
```

也可以让 Claude 用 `trace_context` 帮你确认/创建中央项目，然后你自己执行
`trace-project bind --project-id <id>`。marker 由人写入，agent 不会替你绑定目录。

### 4.2 hook 做什么、不做什么

Hook 把 event 与 transcript 增量写进
`${CLAUDE_PLUGIN_DATA}/outbox/<workspace-hash>/<session>/pending/`，然后立即返回。
它**不发任何网络请求**，所以中央挂了、DNS 挂了、凭证过期了，都不会让你的主任务变慢或失败。

transcript 增量在落盘之前逐行剥掉 `thinking` / `redacted_thinking` 块：隐藏推理不进 outbox，
自然也不会上传中央或进入每日备份。

Recorder 是当前 Claude Code 主会话的 fork：首次继承当时上下文，之后按 agent id 恢复并只接收
增量 batch；它不跨主会话常驻，长期状态在中央服务里。Hook 会根据 recorder agent id 拒绝 Bash、
Edit、Write、Agent、外部搜索和无关 MCP，只允许只读检查与 Research Trace MCP。

**Recorder 不负责上传原始历史**，也不该为 hook batch 调用 `trace_ingest`：那是 `trace-deliver`
的事（第 5 节）。Recorder 只按价值判断是否创建语义 Node，一次 batch 创建零个 Node 完全正常。
Chapter 由人创建并定义为“主实验”“消融实验”等并列研究线；Recorder 只能选择已有 Chapter，
不确定时进入 Inbox，且所建 Node 一律未确认。

插件暴露的工具固定为：
`trace_context`、`trace_ingest`、`trace_record`、`trace_curate`、`trace_attach`、
`trace_search`；另有只用于账号绑定的 `trace_login`。其中 `trace_ingest` 只用于手动补录和没有
投递器的非 Claude 宿主。

## 5. 投递：`trace-deliver`

投递是一个独立进程，它是唯一有权把文件从 `pending/` 搬到 `sent/` 的角色，而且只在中央返回
2xx 之后才搬。失败的文件原样留在 `pending/` 等下一轮，不会被丢弃、不会被归档到别处。

```bash
trace-deliver --url https://trace.example.org      # 跑一轮就退出
trace-deliver --url https://trace.example.org --watch --interval 300
```

它扫的是整个 outbox 根：所有已绑定 workspace、所有 session 目录——包括早就退出或被 kill 掉的
session。所以「那次崩掉的会话」的残留有确定的重放路径，不需要那个会话还活着。

三种部署方式，按机器选一种：

- **什么都不配**：hook 会在 SessionStart 和 SessionEnd 各分离启动一次投递器（fire-and-forget，
  不等待、不看返回码，同一 session 60 秒内不重复拉起）。对日常工作站够用。
- **常驻**：`trace-deliver --watch --interval 300`，适合长期开机、希望积压更快清空的机器。
- **外部调度**：cron / systemd `--user` timer / SLURM 定时任务里跑 `trace-deliver`，
  并设 `TRACE_HOOK_NO_SPAWN=1` 让 hook 不再自己拉起进程，避免两套调度打架。

其它常用参数：`--data-dir`（默认取 `TRACE_DATA_DIR` 或 `CLAUDE_PLUGIN_DATA`）、
`--token`、`--credential-file`、`--timeout`、`--quiet`、`--retain-sent-days`
（默认 30，`TRACE_RETAIN_SENT_DAYS`，`0` 表示永不回收）、`--status`。

`trace-deliver --status` **不联网**，打印这台机器还有多少内容没送出去以及最近一次投递结果；
有 pending 时退出码为 1。卸载插件或换机器之前先跑它。

注意：投递器读的是设备凭证文件（`trace-login` 写的）或环境变量 `TRACE_TOKEN`。只在插件配置里
填了旧版 token 是不够的——那个值只注入 MCP 进程，投递器会拿到 401 并把内容全部留在 `pending/`，
`--status` 与 `delivery-status.json` 的 `last_error` 里能看到。

outbox 的状态只有两个：`pending/`（等待投递，永不自动删除）和 `sent/`（中央已确认存储）。
**没有 `awaiting_upload/`**：这个目录属于「hook 负责投递、失败要归档」的旧模型，已经取消；
磁盘上遗留的旧目录会在下一次投递时被自动搬回 `pending/` 并正常重投，不需要手工处理。

每轮投递的统计写在 `${CLAUDE_PLUGIN_DATA}/outbox/delivery-status.json`（`pending`、`sent`、
`delivered_batches`、`failed_batches`、`conflicts`、`reclaimed`、`last_error` 等），
同一份统计还会 `POST /api/telemetry/outbox` 上报给中央，出现在网页“状态”面板的
outbox 与 Recorder 两格里。

回收与告警：每轮结束时按 mtime 删除 `sent/` 与 `transcripts/sent/` 里超过保留期的文件——
保留期从中央确认那一刻起算。`pending/` 永远不参与回收，磁盘紧张时只告警（剩余低于 5% 或
512 MiB），绝不删除未确认内容腾空间。

`conflicts` 非零表示中央按 `event_id` / `chunk_id` 去重时发现同一个 id 但内容不同，保留了
它已经存下的那一份。这不是正常重放，说明发送端复用了 id，应当排查。

## 6. 配置每日私有 Git 备份

先在服务器上克隆一个专用的 private repository，并配置好无需交互的 push 凭据。然后给
服务增加参数：

```bash
trace-server --data-dir /srv/research-trace/data \
  --backup-repo /srv/research-trace/private-backup \
  --backup-branch main --backup-interval-hours 24
```

服务启动后会先执行一次，随后按间隔导出、校验、仅在内容变化时 commit，并用普通 push
上传；不会 force-push。失败不会阻断记录服务，状态可在 `/api/health` 查看。

也可以交给 cron/SLURM 定时任务单独执行：

```bash
trace-backup sync-git --data-dir /srv/research-trace/data \
  --repo /srv/research-trace/private-backup --branch main
```

备份包含确定性 JSONL、zlib transcript chunks、小附件、GitHub 用户/角色、设备名称与设备
凭证哈希、manifest 和 SHA-256。大产物只保存机器、路径、大小和校验和等引用，不复制产物
本身。运行中的 `trace.sqlite3`、WAL、待批准 device code、网页 session、设备凭证原文、
GitHub access token 和所有 secret 都不会进入备份。

## 7. 验证与从空库恢复

```bash
trace-backup verify --source /srv/research-trace/private-backup/research-trace-backup

trace-backup restore \
  --source /srv/research-trace/private-backup/research-trace-backup \
  --data-dir /srv/research-trace/restored-empty-data
```

Restore 只接受空数据目录，并在事务内检查外键。恢复成功后先以另一个端口启动服务并核对，
再切换生产数据目录。

备份格式版本现在是 **2**。旧版本导出的备份目录会被 `verify` 以
“unsupported backup format version” 拒绝：如果手上有旧格式的备份，需要先用旧代码 restore、
再用当前代码重新 export 一次。格式 2 的两个变化是：transcript 正文只保存 zlib 副本
（不再额外写一份明文 `search_text`），以及导出树里带一个 `.gitattributes`
（`core.autocrlf=true` 的机器克隆备份仓库后字节不会被改写，否则校验和会全部对不上）。

## 8. 紧急清除敏感内容

误采集了令牌、密钥或不该留的内容时，管理员可以真删除（不是标记删除），并留下不含原文的
审计记录：

```bash
trace-backup purge --data-dir /srv/research-trace/data \
  --actor <管理员标识> --reason "leaked token in transcript" \
  --project-id <id>          # 也可用 --session-id / --node-id / --event-id / --transcript-chunk-id
```

理由、操作者和选择器缺一不可，命令会拒绝执行没有说明的删除。purge 之后，下一次常规备份导出
就不再包含这些内容，但**备份分支里的旧 commit 还在**。要把它们也去掉：

```bash
trace-backup rewrite-history --data-dir /srv/research-trace/data \
  --repo /srv/research-trace/private-backup \
  --reason "purge 2026-08-19" --confirm
```

它把备份分支压成一个全新的根 commit 并 force-push（这是唯一允许 force-push 的场景）。

必须知道它管不到什么：远端托管方的旧对象要等它自己 GC，别人已经克隆过的备份副本也无法回收。
**涉及令牌或密钥时，purge 不能替代轮换密钥。** 网页上的管理员 purge 入口还没有实现，
目前只有上面这两条命令。

## 9. 当前 alpha 边界

- Claude Code 已接入；Codex CLI / Desktop 适配尚未接入自动 Hook。
- 采集按项目 opt-in，投递由独立的 `trace-deliver` 负责；hook 不联网，只写 `pending/`。
- 网页已有 Project、Overview、Comment/Correction、Chapter、Node、Chapter 内结构图/记录列表、
  附件显示、原始历史按需展开、修订历史、全文搜索、GitHub OAuth 与团队角色。
- 网页“状态”面板显示中央存储、GitHub 备份（含远端落后几个 commit）、以及各机器上报的
  outbox 与 Recorder 未处理量。从来没有机器上报过时显示“未上报”，不画假绿灯；
  本机情况随时可以用 `trace-deliver --status` 或 `outbox/delivery-status.json` 直接看。
- 网页 OAuth 与设备凭证均来自同一 GitHub 白名单和实时角色；设备凭证有到期时间；旧的共享
  `TRACE_TOKEN` 只为迁移兼容，建议新部署不再配置。
- 未配置 OAuth 时读取是完全公开的（含原始 transcript 和附件下载），启动时会打印醒目警告；
  此时网页自身的写入也只能算 `recorder`，无法产生 `human` 记录或确认。
- 默认永久保存可能包含命令、路径或 transcript 中的敏感信息。现在已经有三层控制（不绑定、
  `trace-project disable`、`capture=off`）、`sent/` 的保留期与磁盘告警，以及管理员紧急 purge
  （CLI 与 `POST /api/admin/purge`）；备份仍然是一棵全量树，按年份/容量分卷尚未实现。
- 数据流视图尚未实现：它要求登记产物时给出 `sha256` 或规范化 `uri` 作为可 join 的键。
  协议里已经这么要求了，但存量数据还没有，画出来只会是空图。
