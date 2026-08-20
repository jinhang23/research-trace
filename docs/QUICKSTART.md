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
  --backup-repo /srv/research-trace/private-backup \
  --host 127.0.0.1 --port 8765 --token "<long-random-token>"
```

**`--backup-repo` 是必填的**（或环境变量 `TRACE_BACKUP_REPO`）。不给就拒绝启动 ——
默认能跑起来的话，每个部署都会默默停在「唯一副本在一块盘上」这个状态，而这件事通常
要到盘坏了才被发现。那个仓库的 remote **必须是私有的**：导出里带完整原始 transcript。
只有指定的子目录会被 stage 和 commit，所以指向一个你已经在用的仓库不会把它其它改动卷进来。

本地试用或一次性实例可以明确说不要：`--no-backup`（或 `TRACE_NO_BACKUP=true`）。
它会在启动时打印一段醒目的警告，这是有意的。

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

## 1b. 挂在一个路径前缀下（可选）

没有独立域名、只能借用现有站点的一段路径时，用 `--base-path`：

```bash
trace-server --data-dir /srv/research-trace/data \
  --base-path /trace --host 127.0.0.1 --port 8765
```

**服务把整个前缀据为己有**：`/trace/...` 之外的一切都是 404，包括 `/api/health`。
所以反向代理**不要**剥掉前缀，原样转发即可：

```nginx
location /trace/ {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

这条「前缀之外一律 404」不只是整洁问题：它意味着绕过反向代理直连 `:8765` 的人
也进不去。要是让代理来剥前缀，那个端口在内网上就等于没有门。

配了 OAuth 时，`TRACE_PUBLIC_URL` **必须带上同一个前缀**（`https://example.org/trace`），
否则 GitHub 会把人回调到域名根上——那里通常是别的应用。前缀不一致时服务直接拒绝启动，
而不是等到有人点了登录才出问题。

会话 cookie 的作用域也会收敛到这个前缀。同一个域名下还跑着别的应用时这一点很重要：
`Path=/` 的 cookie 会被浏览器发给那些应用，而它们未必是自己人。

---

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

## 3b. 客户端接入清单（照着走一遍就能记录）

在一台新的工作站 / HPC 上，从零到「这个项目开始被记录」是这四步。顺序不能换：
绑定要用登录拿到的凭证，而登录要先有人在网页上批准。

```bash
# 0) pip 包（trace-login / trace-project / trace-deliver 都来自它；插件不提供这三个命令）
python -m pip install "research-trace @ git+https://github.com/jinhang23/research-trace"

# 1) 确认没有显式 token 在挡路 —— 见下面的「最常见的一个坑」
unset TRACE_TOKEN

# 2) 设备登录：打印 8 位码，你在 <服务地址>/device 上手工输入并批准
trace-login --url https://trace.example.org --device-name hipergator-login-01

# 3) 绑定要记录的目录（会自动用上一步的凭证）
cd /path/to/my-project
trace-project bind --url https://trace.example.org --create --name "我的项目"

# 4) 随时可以问「现在到底是哪份凭据在生效」
trace-project status --url https://trace.example.org
```

第 4 步会先打印一行 `auth for <url>: …`，然后才是绑定信息。不确定的时候先看这一行。

### 最常见的一个坑：显式 token 会**静默**盖住设备凭证

`auth_token()` 是显式 token 优先。所以只要下面任何一处还有值，`trace-login` 存下的凭证
就**根本不会被读取**：

- 环境变量 `TRACE_TOKEN`（包括 shell profile 里的 `export`）；
- Claude Code 插件配置里的 `token`；
- 命令行 `--token`。

而且这不会报错。表现是某次写入莫名其妙地 401 —— 尤其是服务端已经把那个旧的共享 token
删掉之后。`trace-login` 现在会在登录成功时就检查环境变量并警告，`trace-project status`
则会直接说出哪一份在生效。

### 三个前提

- **URL 三处必须一致。** 凭证在文件里是**按服务 URL 索引**的，`trace-login`、插件配置和
  `trace-deliver` 用的 URL 必须是同一个（尾随斜杠会被去掉，不用纠结）。带路径前缀的部署
  要把前缀带上，例如 `https://example.org/trace`。
- **先有人登录过网页。** 批准页要求一个已登录、且在白名单里的 GitHub 账号。
- **写入要 member 或 admin。** `reader` 角色的设备凭证读得到、写不了（403）。

### Recorder 多久重新 fork 一次

Recorder 以 fork 方式继承主 agent **此刻**的完整上下文 —— 这是它知道「刚才发生了什么」的
唯一途径。代价是每次 fork 首轮读入约 60 万 token（实测缓存命中率 99.7–99.9%，所以是便宜的
那种 token，但底数不是零）。

而实测下来很多批次的全部内容就是「某个子 agent 结束了」（一份真实样本里，137 个采集事件中
`SubagentStop` 占 56 个），为这种批次付一次完整 fork 不划算。插件配置项
`recorder_fork_window` 控制这个节奏：

| 取值 | 含义 |
|---|---|
| `1`（默认） | 每批都重新 fork，最新鲜 |
| `4` | 每 4 批 fork 一次，窗口内复用；上下文逐渐变旧，省下那些读取 |
| `0` | 整个会话只 fork 一次，最省也最旧 |

环境变量 `TRACE_RECORDER_FORK_WINDOW` 同样生效；旧的 `TRACE_RECORDER_REUSE=1` 继续认，
等价于 `0`。

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

- 匹配到已有项目（marker 里的 key 或规范化 Git remote 已经登记过）就把 `project_id` 写进 marker；
- 都没登记过时再过一遍**团队配置映射**（第 4.3 节）：命中唯一一条规则就直接绑到那个项目，
  并打印是哪条规则、谁加的；
- 团队映射同时命中多个项目时进入**待确认状态**：列出候选、退出码 2，**一个字都不写 marker**，
  也不会创建任何项目。按提示挑一个 `--project-id <id>` 再跑一次；
- 什么都匹配不到时**拒绝静默新建**，列出候选并要求你用 `--project-id <已有 id>` 或 `--create` 明确表态；
- 中央不可达时写一份离线 marker（也可以直接 `--offline`），采集立刻生效，`project_id` 等下次
  绑定时补上；在补上之前，这段原始历史以未归属状态上传。

workspace key 不能是路径：`/data/proj`、`C:\proj`、`~/proj`、`./proj`、`\\host\share`、
`file://…` 都会被拒绝（`--workspace-key` 直接报错，中央侧则丢掉这个 key 并在响应里回报
`rejected_workspace_keys`）。绝对路径是机器局部的东西，正是 §7 不允许当身份的那一类。

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

### 4.3 团队配置映射（让新机器不必手工填 `--project-id`）

前两种项目发现方式（marker 里的 `workspace_key`、规范化的 Git remote）都要求这个 key 已经在
中央登记过。团队里第一个人绑完之后，第二个人 clone 同一个仓库时 remote key 已经登记，直接就
能解析；但**换一个新仓库、或者一批机器还没有任何一台绑过**时，每台机器都得有人手工把
`--project-id` 抄进去。团队配置映射就是补这一段：管理员在中央写一条 glob 规则，把 workspace
key 指到已有项目。

规则表在 `<data_dir>/team-project-map.json`（`research-trace.team-map.v1`，原子写）：

```json
{
  "schema": "research-trace.team-map.v1",
  "rules": [
    {
      "id": "map_0a1b2c3d4e5f6071",
      "pattern": "https://github.com/team/*",
      "project_id": "prj_…",
      "note": "batch effect correction 的所有仓库",
      "created_by": "gh:jinhang23",
      "created_at": "2026-08-19T02:03:04.000Z"
    }
  ],
  "history": []
}
```

两条维护路径：

1. **REST**（推荐，会自动写审计 history）：
   - `GET /api/team/mapping?history=50` —— 读权限即可，同时是「把映射导出成一份团队配置文件
     发下去」的出口；
   - `POST /api/team/mapping` body `{pattern, project_id, note}`；
   - `DELETE /api/team/mapping/{rule_id}`。

   写操作要求**管理员的网页会话**（cookie + `X-CSRF-Token` 头），不接受机器 Bearer 凭证，
   也不接受旧的共享 `TRACE_TOKEN`；没有配置 GitHub OAuth 时这两个端点一律 404。
   网页上的管理界面还没有实现，所以现在通常是在已登录的管理员浏览器里发这两个请求
   （CSRF token 可从 `GET /api/auth/me` 读到）。
2. **直接编辑那个 JSON 文件**（未配置 OAuth 时这是唯一入口）。注意规则表在服务启动时读入
   一次，手工编辑之后要重启服务才会生效；REST 改动则立即生效。

几条规矩：

- `pattern` 是 glob，和 workspace key 走同一套规范化，并且必须含至少 4 个字面字符——一条 `*`
  会把所有工作区映射到同一个项目，那是「静默落进错误项目」，和「静默创建重复项目」一样糟。
- `pattern` 本身也不能是路径形态（`/srv/...`、`C:\...`、`~/...`）。
- 同一个 pattern 只能有一条规则：重复添加同一条是幂等的，指向另一个项目会 409，要求先删
  再加，而不是叠出一个必然歧义的第二条。
- `created_by` 取自凭证，请求体里自称的值被忽略；增删都进 `history`（含操作者和时间）。
- 规则指向的项目被 purge 掉之后不算命中，解析会退回「没有映射」，而不是让所有机器的
  `/api/context` 一起 404。
- 命中唯一项目时，中央会把本次的 workspace keys 登记到该项目上，所以**映射通常只被用一次**：
  下一个 clone 直接走前两种发现方式。
- 命中多个项目时进入待确认状态（HTTP 200，`pending_confirmation`），**即使调用方要求创建也
  不创建**；`trace-project bind` 会把候选连同「是谁在什么时候加的这条规则」一起打印出来。

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

### 和项目代码同仓

备份仓库不必是专用的空仓库 —— 指向项目自己的代码仓，记录和实现就住在一起，
`git clone` 一次同时拿到「怎么做的」和「为什么这么做」：

```bash
trace-server --data-dir /srv/research-trace/data \
  --backup-repo /srv/checkouts/my-project \
  --backup-subdirectory research-trace-backup --backup-branch main
```

两件事让它成立：

- **只有 `--backup-subdirectory` 那一个目录会被 stage 和 commit**，所以这个工作副本里
  别的改动不会被卷进备份提交（子目录等于仓库根时直接拒绝）。
- **push 之前会 `fetch` 并把本轮备份 commit rebase 到远端之上**，所以别的机器往同一个
  分支推代码不会让备份从此推不上去。真的发生冲突（有人手改了备份文件）时，rebase 会被
  中止、这一轮报错、本地 commit 保留 —— 宁可晚一轮备份，也不把别人的提交搅乱。

**代码仓必须是私有的。** 导出里带完整原始 transcript；推进一个公开仓库是不可逆的。
不确定的话就用专用私有仓库，别和公开代码混在一起。

也可以交给 cron/SLURM 定时任务单独执行：

```bash
trace-backup sync-git --data-dir /srv/research-trace/data \
  --repo /srv/research-trace/private-backup --branch main
```

### 6.1 分卷与容量阈值

导出树**先按年分卷、年内再按容量切分片**：

```text
research-trace-backup/
├── index.json                     每卷的 manifest 校验和、字节数、最大文件与行数
├── .gitattributes
└── volumes/
    ├── base/                      没有 created_at 的行（schema_meta）
    ├── 2025/
    │   ├── manifest.json
    │   ├── tables/events.0000.jsonl
    │   ├── tables/events.0001.jsonl
    │   ├── transcripts/<chunk_id>.zlib
    │   └── objects/<sha 前缀路径>
    └── 2026/…
```

按年切是为了让去年的卷写定之后**再也不被重写**：一行的 `created_at` 永不改变，所以 Git 不必
每天重新打包全部历史，而且某一年太大时可以整卷搬走。年内再按字节切分片，是因为托管方的限制
有两个量级：单文件 50 MiB 警告 / 100 MiB 拒绝 push，仓库 1 GiB 建议 / 5 GiB 附近受限；只按年
切压得住仓库增速，压不住「某一年的 events 表本身 300 MB」。分片预算默认 32 MiB。

容量告警随每次 `export` / `sync-git` 的结果返回（`capacity`），服务端把它写进 `/api/health`
的 `backup.capacity`，level 是 `warn` / `critical` 时另打一行 stderr。它**只报不拦**——容量
到顶时最不该做的事就是停止备份。

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `TRACE_BACKUP_PART_BYTES` | `33554432`（32 MiB） | 卷内每个分片文件的字节预算，也可用 `--part-bytes` |
| `TRACE_BACKUP_FILE_WARN_BYTES` | `52428800`（50 MiB） | 单文件告警线 |
| `TRACE_BACKUP_FILE_CRITICAL_BYTES` | `94371840`（90 MiB） | 单文件严重线（离 GitHub 的 100 MiB 硬拒留 10% 余量） |
| `TRACE_BACKUP_REPO_WARN_BYTES` | `1073741824`（1 GiB） | 仓库总量告警线（`git count-objects -v`，含历史） |
| `TRACE_BACKUP_REPO_CRITICAL_BYTES` | `4294967296`（4 GiB） | 仓库总量严重线 |

`trace-server` 没有单独的 `--backup-part-bytes` 参数，给服务进程设 `TRACE_BACKUP_PART_BYTES`
即可，备份模块自己读它。

**升级提示**：从旧的全量树升上来之后，第一次 sync 会删掉整棵旧树、写出分卷树，因此产生一个
体量很大的 commit（内容等价、路径全变）。这是一次性的；之后只有当年的卷会变。旧的备份仓库
不需要重建，旧 commit 里的旧格式树用当前代码 restore 依然可读。

备份包含确定性 JSONL、zlib transcript chunks、小附件、GitHub 用户/角色、设备名称与设备
凭证哈希、manifest 和 SHA-256。大产物只保存机器、路径、大小和校验和等引用，不复制产物
本身。运行中的 `trace.sqlite3`、WAL、待批准 device code、网页 session、设备凭证原文、
GitHub access token 和所有 secret 都不会进入备份。

## 7. 验证与从空库恢复

```bash
trace-backup verify --source /srv/research-trace/private-backup/research-trace-backup

# 只验一卷（大备份上快得多），或者直接把 --source 指到卷目录
trace-backup verify \
  --source /srv/research-trace/private-backup/research-trace-backup --volume 2025

trace-backup restore \
  --source /srv/research-trace/private-backup/research-trace-backup \
  --data-dir /srv/research-trace/restored-empty-data
```

整体 `verify` 校验根文件、每卷 manifest 的 SHA-256、逐卷内容，并核对 `volumes/` 下的目录集合
与 `index.json` 完全一致、各表行数逐卷求和等于索引里的总数——所以「少了一整卷」也会被发现，
而不只是「某个文件被改过」。

Restore 只接受空数据目录，并在事务内检查外键。它先把**所有卷**的所有表读进来合并，再一次性
写库，因此与卷的顺序无关（否则 2027 年的 Node 指向 2026 年的 Chapter 会撞外键）。恢复成功后
先以另一个端口启动服务并核对，再切换生产数据目录。

备份格式版本现在是 **3**（分卷树 + 顶层 `index.json`）。**版本 2 的旧全量树仍然可以 `verify`
和 `restore`**：写入端只写当前格式，读取端永不退役——备份的全部意义是「几年后还能读回来」，
一次不兼容的升级就把之前所有备份变成废纸。对旧目录原地重新 `export` 会把它升级成分卷结构，
并删掉根上的旧 `manifest.json`。

格式 2 引入、格式 3 保留的两点：transcript 正文只保存 zlib 副本（不再额外写一份明文
`search_text`），以及导出树里带一个 `.gitattributes`（`core.autocrlf=true` 的机器克隆备份
仓库后字节不会被改写，否则校验和会全部对不上）。

导出时如果某个附件对象在数据卷上已经不存在，导出不会中止：缺口记进卷 manifest 与 `index.json`
的 `missing_objects` 并继续（restore 同样跳过并报出来）。否则从那天起所有新增历史都永远进不了
备份。这个列表也会出现在 `/api/health` 的 `backup.missing_objects` 里。

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
**涉及令牌或密钥时，purge 不能替代轮换密钥。** 网页上还没有 purge 的操作界面；除了上面这两条
命令，另一条入口是管理员 REST（`POST /api/admin/purge`、`GET /api/admin/purges`，理由必填，
操作者取自凭证而不是请求体）。

## 9. 数据流视图（可选）

数据流是**派生视图**：它只把一个 Node 明确登记的 output 与另一个 Node 明确登记的 input 按
**同一个键**连起来，从不从对话文字里猜谁产出了什么、谁消费了什么，也不存在独立的 Pipeline
模型。没有 artifact 关系的项目照常使用，只是这张图是空的——空图是正常状态，不是错误。

### 怎么才能让边出现

1. 登记产物时用 `trace_attach` 给出 `direction`：`output`（这个 Node 产出的）或
   `input`（它消费的）。**默认是 `reference`，而 `reference` 两边都不参与连边**——
   登记它的人没有声明任何流向。
2. 给一个可比对的键，三选一（可以都给）：
   - `sha256`：恰好 64 位十六进制。截断的前缀、`sha256:` 这种前缀都不算；
   - `uri`：带 scheme 的绝对 URI（`s3://bucket/k`、`file:///data/out.csv`）。
     单字母 scheme 不算——`C:\data\x.csv` 语法上像「scheme 是 c」，其实是一条裸路径；
   - `machine` + 绝对 `external_path`：**成对**才算。两台机器上的 `/data/out.csv` 不是同一份
     产物，而没有机器的路径不是任何一块磁盘上的东西。

   相对路径、`~/…`、只给 `external_path` 不给 `machine`、以及截断的哈希，都等于没有键，
   这条登记**永远连不上边**。

### 怎么看

```bash
curl -s "$URL/api/projects/<project_id>/dataflow?limit=2000"
```

Agent 侧则是 `trace_context` 的可选参数 `include_dataflow: true`（默认关闭：context 是每个
batch 都要拉的热路径，多数项目这张图是空的，不该为它付一次全表 join）。

返回 `{project_id, nodes[], edges[], unkeyed[], stats{}}`：

- 每条边带 `key_kind`：`sha256` 是「同一份字节」，`uri` / `path` 只是「同一个位置」——
  同一个输出路径可能被后一次运行覆盖过，两者强度不同，不合并成匿名的「相同」。
- `stats.unkeyed` 与 `unkeyed[]` 用来区分「这个项目没有 artifact 关系」和「登记了产物但忘了
  给键」。图是空的时候先看这个数字。
- `stats.unlabeled_direction` 是另一种可修的空图：键给对了，但 `direction` 还是默认的
  `reference`，两边都不参与 join。图空而 `unkeyed` 也是 0 时看这个数字——它比缺键更常见。
- `stats.truncated` 表示边的生成量撞到了上限（一个被反复覆盖的 `latest.ckpt` 能让几百个 Node
  两两配对，是二次的）。
- 同一个 Node 既 output 又 input 同一份产物（原地覆盖）不产生自环。

网页上，项目视图的结构面板会多出第三个切换「数据流」——**只有真的连出边时它才出现**。
图上每条边都标着凭什么连的（`sha256` / `uri` / 机器+路径），图下有一份完整的依据清单；
一条边都没有但确实有产物登记漏了键时，结构面板会写明「N 个产物登记时没有可比对的键」；
键没问题、只是 `direction` 还停在默认的 `reference` 时，写的是「N 个产物的 direction 仍是
默认的 reference」。
数据流的边可以跨 Chapter（消融吃主实验的产物），但这不给 Chapter 之间引入任何顺序：
这个视图里没有 Chapter 容器，Chapter 只是节点卡片上的一行标签。

## 10. 当前 alpha 边界

- Claude Code 已接入；Codex CLI / Desktop 适配尚未接入自动 Hook。
- 采集按项目 opt-in，投递由独立的 `trace-deliver` 负责；hook 不联网，只写 `pending/`。
- 网页已有 Project、Overview、Comment/Correction、Chapter、Node、Chapter 内结构图/记录列表、
  存在 artifact 关系时的数据流视图、附件显示、原始历史按需展开、修订历史、全文搜索、
  GitHub OAuth 与团队角色。
- 网页“状态”面板显示中央存储、GitHub 备份（含远端落后几个 commit、容量告警与导出时缺失的
  附件对象数）、以及各机器上报的 outbox 与 Recorder 未处理量。从来没有机器上报过时显示“未上报”，不画假绿灯；
  本机情况随时可以用 `trace-deliver --status` 或 `outbox/delivery-status.json` 直接看。
- 网页 OAuth 与设备凭证均来自同一 GitHub 白名单和实时角色；设备凭证有到期时间；旧的共享
  `TRACE_TOKEN` 只为迁移兼容，建议新部署不再配置。
- 未配置 OAuth 时读取是完全公开的（含原始 transcript 和附件下载），启动时会打印醒目警告；
  此时网页自身的写入也只能算 `recorder`，无法产生 `human` 记录或确认。
- 默认永久保存可能包含命令、路径或 transcript 中的敏感信息。现在已经有三层控制（不绑定、
  `trace-project disable`、`capture=off`）、`sent/` 的保留期与磁盘告警，以及管理员紧急 purge
  （CLI 与 `POST /api/admin/purge`）。
- 备份已按年份/容量分卷并带容量阈值告警（第 6.1 节）。仍然没有的：把某一整卷搬去另一个仓库的
  搬迁工具（`index.json` 的结构允许，但没有 CLI），以及对已有备份仓库做历史瘦身——旧 commit
  里的全量树仍占仓库体积，唯一能重写历史的路径仍然只有 purge 之后的 `rewrite-history`。
  容量告警在 `/api/health`、服务日志和网页备份卡片上都能看到。
- 数据流已实现（第 9 节），网页上作为项目视图的第三种呈现方式出现。存量数据大多没有可比对的
  键，所以多数项目现在仍然是空图；`stats.unkeyed` 与 `stats.unlabeled_direction` 用来区分
  「没有产物」「登记时忘了给键」「键给对了但方向还是默认的 reference」三件事。
- 团队配置映射已实现（第 4.3 节），但网页上的映射管理界面与待确认状态界面尚未实现：
  维护规则目前靠 REST 或直接编辑 `<data_dir>/team-project-map.json`，
  `pending_confirmation` 在命令行上会列出候选，在网页上还没有落点。
