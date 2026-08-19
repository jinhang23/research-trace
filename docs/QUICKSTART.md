# Research Trace v2 快速开始

v2 由三部分组成：一台中央服务、每台 Claude Code 机器上的插件，以及服务端的私有 Git
备份仓库。中央服务是在线真相源；插件 outbox 是断网缓冲；Git 仓库是可验证的灾备副本。

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

命令会打开浏览器；无桌面的 SSH/HPC 会打印完整链接和 8 位验证码。用获准的 GitHub 账号
登录并点击“批准此设备”，终端会自动完成。凭证只允许访问 Research Trace，并绑定当前 GitHub
用户、服务器 URL 和设备名称。

Claude Code 插件也提供 `trace_login`：直接对 Claude 说“登录 Research Trace”，它会返回批准
链接；批准后让它检查登录状态即可。以后 MCP/Recorder 自动读取本机凭证，不需要在每次会话中
登录。

默认凭证位置：

- Linux/HPC：`${XDG_CONFIG_HOME:-~/.config}/research-trace/credentials.json`
- Windows：`%APPDATA%\\ResearchTrace\\credentials.json`

文件使用原子写入，并在支持的平台设为仅当前用户可读。它不在项目、插件 outbox 或 Git 备份
中，也不会自动过期清理。可以从网页右上角“账户 → 管理已登录设备”撤销，或者在该机器运行：

```bash
trace-login --url https://trace.example.org --logout
```

管理员禁用一个 GitHub 用户时，其网页 session 和全部设备凭证会同时失效。服务端仍接受
`TRACE_TOKEN` 作为旧部署迁移兼容项，但新机器不需要配置它。

## 4. 安装 Claude Code 插件

```text
/plugin marketplace add jinhang23/research-trace
/plugin install research-trace@research-trace
```

在插件配置中填写：

- `url`：中央服务地址；
- `token`：旧部署兼容项；使用 GitHub 设备登录后留空；
- `python`：Python 3.10+ 解释器的绝对路径；
- `capture`：默认 `on`，临时处理敏感内容前可改为 `off`。

可先在仓库内验证 MCP 进程：

```bash
python trace_mcp.py --selfcheck
```

Hook 会把 event 与 transcript 增量先复制到 `${CLAUDE_PLUGIN_DATA}/outbox/`。`pending/` 和
`awaiting_upload/` 不自动清理；只有中央服务确认的数据才能进入 `sent/`。Recorder 是当前
Claude Code 主会话的 fork：首次继承当时上下文，之后按 agent id 恢复并只接收增量 batch；
它不跨主会话常驻，长期状态在中央服务里。Hook 会根据 recorder agent id 拒绝 Bash、Edit、
Write、Agent、外部搜索和无关 MCP，只允许只读检查与 Research Trace MCP。

Recorder 先调用 `trace_ingest(manifest_path=...)` 上传原始 batch，再按价值判断是否创建
语义 Node。一次 batch 创建零个 Node 完全正常。Chapter 由人创建并定义为“主实验”“消融实验”
等并列研究线；Recorder 只能选择已有 Chapter，不确定时进入 Inbox，且所建 Node 一律未确认。
插件暴露的工具固定为：
`trace_context`、`trace_ingest`、`trace_record`、`trace_curate`、`trace_attach`、
`trace_search`；另有只用于账号绑定的 `trace_login`。

## 5. 配置每日私有 Git 备份

先在服务器上克隆一个专用的 private repository，并配置好无需交互的 push 凭据。然后给
服务增加参数：

```bash
trace-server --data-dir /srv/research-trace/data \
  --backup-repo /srv/research-trace/private-backup \
  --backup-branch main --backup-interval-hours 24
```

服务启动后会先执行一次，随后按间隔导出、校验、仅在内容变化时 commit，并用普通 push
上传；不会 force-push。失败不会阻断记录服务，状态可在 `/api/v2/health` 查看。

也可以交给 cron/SLURM 定时任务单独执行：

```bash
trace-backup sync-git --data-dir /srv/research-trace/data \
  --repo /srv/research-trace/private-backup --branch main
```

备份包含确定性 JSONL、zlib transcript chunks、小附件、GitHub 用户/角色、设备名称与设备
凭证哈希、manifest 和 SHA-256。大产物只保存机器、路径、大小和校验和等引用，不复制产物
本身。运行中的 `trace.sqlite3`、WAL、待批准 device code、网页 session、设备凭证原文、
GitHub access token 和所有 secret 都不会进入备份。

## 6. 验证与从空库恢复

```bash
trace-backup verify --source /srv/research-trace/private-backup/research-trace-backup

trace-backup restore \
  --source /srv/research-trace/private-backup/research-trace-backup \
  --data-dir /srv/research-trace/restored-empty-data
```

Restore 只接受空数据目录，并在事务内检查外键。恢复成功后先以另一个端口启动服务并核对，
再切换生产数据目录。

## 7. 当前 alpha 边界

- Claude Code 已接入；Codex CLI / Desktop 适配尚未接入自动 Hook。
- 网页已有 Project、Overview、Comment/Correction、Chapter、Node、Chapter 内结构图/记录列表、
  附件显示、原始历史按需展开、全文搜索、GitHub OAuth 与团队角色。
- 网页 OAuth 与设备凭证均来自同一 GitHub 白名单和实时角色；旧的共享 `TRACE_TOKEN`
  只为迁移兼容，建议新部署不再配置。
- 默认永久保存可能包含命令、路径或 transcript 中的敏感信息。alpha 已支持暂停采集，但管理员
  emergency purge 与备份轮换流程尚未实现；在它完成前不要采集受监管的秘密数据。
