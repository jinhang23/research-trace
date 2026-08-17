# 部署：一台 VPS 跑服务，别的机器通过公网管理

架构：`projects/` 的唯一工作副本在 VPS 上，debounce 后自动 `git commit && git push`
到**私有**仓库。你在任何机器 `git pull` 拿到的就是完整、可 grep、可 diff 的文件树——
这是 G4（删掉所有程序，剩下的文件仍然可读）在"上了服务器"之后的兑现方式。

1 核 1G 的机器足够。

---

## 两个仓库，别混

| | 内容 | 可见性 | 在 VPS 上的位置 |
|---|---|---|---|
| 代码仓 | `trace_*.py` · `web/` · `tests/` · `deploy/` | 随你（本项目是公开的） | `/srv/trace` |
| **数据仓** | `projects/<项目>/steps/**` | **必须私有** | `/srv/trace-data` |

`config.json` 里的 `data_dir` 决定数据仓在哪，**自动 git 同步同步的也是那个目录**。
把数据仓和代码仓放成同一个 git 仓库、而 `origin` 又指向公开的代码仓，你的科研笔记
就会被自动推到公网上——这是最容易踩的坑。所以现在：

- `init` **默认不开**自动同步，要开必须显式 `--git`；
- 就算给了 `--git`，只要数据仓和代码仓落在同一个 git 工作区，`init` 会**直接拒绝**并返回 2。

`config.json` 本身（含写入令牌）留在**代码仓目录**里，且在 `.gitignore` 中，
两个仓库都不会带上它。

数据仓的 `.gitignore` 里加一行 `.trace-lock`（写入用的跨进程锁文件，
落在 `projects/<项目>/.trace-lock`，是运行期状态不是记录）。
不加的话 `git add -A` 会把它 commit 进去，每次同步都多一条噪声 diff。

---

## 1. 服务器账号与代码仓

```bash
# -M：不要建 home。useradd -m 会用 /etc/skel 往 /srv/trace 里塞
# .bashrc/.profile，随后 git clone 到这个非空目录会直接 fatal 退出。
sudo useradd -r -M -d /srv/trace -s /usr/sbin/nologin trace
sudo install -d -o trace -g trace -m 755 /srv/trace
sudo -u trace git clone https://github.com/jinhang23/research-trace /srv/trace

cd /srv/trace
sudo -u trace python3 -m venv .venv
sudo -u trace .venv/bin/pip install -e ".[server]"
```

（`git clone` 进一个**已存在但为空**的目录是允许的，进非空目录不行。）

## 2. push 凭据

**这一步必须排在克隆私有数据仓之前**——数据仓走 SSH，没有密钥就 clone 不下来。

```bash
sudo install -d -o trace -g trace -m 700 /srv/trace/.ssh
sudo -u trace ssh-keygen -t ed25519 -f /srv/trace/.ssh/id_ed25519 -N "" -q
sudo cat /srv/trace/.ssh/id_ed25519.pub
# ↑ 把这一行加到**数据仓**的 Deploy Key，并**勾选写权限**（Allow write access）

# 重定向 >> 是由你自己的 shell 执行的，写不进 trace 的 0700 目录。
# 要么像这样让 tee 以 trace 身份写，要么整条命令都在 root 下跑。
ssh-keyscan github.com | sudo -u trace tee -a /srv/trace/.ssh/known_hosts >/dev/null
```

`trace.service` 里把 `HOME` 指到 `/srv/trace`，所以 git 能读到上面这些，
同时 `ProtectHome=true` 仍然生效。

## 3. 数据仓

```bash
# -H 不能省：sudo 默认**不**把 HOME 换成目标用户的，ssh 会去翻你自己的 ~/.ssh，
# 找不到刚生成的那把 deploy key。服务那边由 trace.service 的 Environment=HOME 负责。
sudo -u trace -H git clone git@github.com:你/trace-data.git /srv/trace-data   # 私有

# 自动同步 commit 的是**数据仓**，所以 user.name / user.email 要配在它上面。
# 少了这两项，commit 根本建不出来（而且是在没人看着的时候失败）。
sudo -u trace git -C /srv/trace-data config user.email "trace@你的域名"
sudo -u trace git -C /srv/trace-data config user.name  "trace"

# .trace-lock 是运行期状态，不该进历史
printf '.trace-lock\n' | sudo -u trace tee -a /srv/trace-data/.gitignore >/dev/null
```

数据仓可以是空的，`init` 会在里面建出 `projects/`。

## 4. 初始化

一条命令就把数据仓指好：

```bash
cd /srv/trace
sudo -u trace .venv/bin/python trace_cli.py init \
    --title "你的项目名" \
    --data-dir ../trace-data \
    --project "第一个课题" \
    --git
```

`--data-dir` 是相对**代码仓根目录**解析的，所以 `../trace-data` = `/srv/trace-data`。
（它也是默认值，写出来只是为了让这条命令自解释。）

`--git` 不给就不开自动同步——那样这台机器上的记录只在本地磁盘上，没有任何备份。
`init` 会当场检查数据仓是不是 git 仓库、有没有那个 remote，缺了会直接告诉你。

输出里有两样东西，**都要另外记下来**（`config.json` 在 `.gitignore` 里）：

- **访问路径** `/t/<space>/` — 站点只在这个不可猜的路径下存在
- **写入令牌** — 管理端和 agent 写入时都要它

丢了可以 `trace_cli.py url` 再看一次。

## 5. systemd

```bash
sudo cp deploy/trace.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trace
sudo systemctl status trace
```

## 6. Caddy

把 `deploy/Caddyfile` 里的 `trace.example.com` 换成你的域名，DNS A 记录指过来：

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

证书 Caddy 自动申请和续期。SSE 需要 `flush_interval -1`，Caddyfile 里已经配好了。

## 7. 验证

```bash
curl -s https://你的域名/healthz                              # → ok
curl -s -o /dev/null -w '%{http_code}\n' https://你的域名/     # → 404，不暴露站点存在
curl -s https://你的域名/t/<space>/api/projects                # → 项目列表（读公开）
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     https://你的域名/t/<space>/api/projects                   # → 401，写要令牌
```

**再验一次 git 同步真的跑得起来**——它失败过去是完全无声的，而它是你唯一的备份：

```bash
# 起服务时就会做一次 preflight（查 git 身份、查 remote 在不在），结果在这里：
curl -s https://你的域名/t/<space>/api/git

# 带令牌多给一个 detail（git 的原样输出，含服务器路径和远端地址，所以公开视图里没有）
curl -s -H "Authorization: Bearer <令牌>" https://你的域名/t/<space>/api/git

# 不想等 debounce 就手动推一次，返回的是同一个结构
curl -s -X POST -H "Authorization: Bearer <令牌>" https://你的域名/t/<space>/api/sync
```

要看的是 `"ok": true`（只有 `pushed` / `clean` 算成功——`committed` 不算，
那说明文件还只在这一台机器上）。不 ok 时 `summary` 是一句人话、`hint` 是照着做就能修的命令。

失败也会打进服务日志：`sudo journalctl -u trace -p warning`（logger 名 `trace.git`）。
网页顶栏那个 ⇅ 图标同样会在失败时变红，点一下就是上面那个 `POST /api/sync`。

浏览器打开 `https://你的域名/t/<space>/`，右上角 🔒 存入写入令牌。

---

## 服务端会自己改 note.md：定期核对外部路径

这一条要在上线之前知道，否则第一次看见「没人动过的记录昨天有一次 commit」会以为出事了。

`path:` 记的是**仓库外面**的位置（`/blue/…` 的数据集、`/orange/…` 的 checkpoint），
而位置会失效。所以服务端**默认开着**一个周期任务：把本机看得见的路径逐条 `stat`
一遍，把结论写回那一行的 `checked=` / `missing=`。它会真的改写 `note.md`，
因此**会触发自动 git 同步**——这是有意的，「57 GB 那个目录三个月前就没了」
必须进历史。

```json
{ "paths": { "enabled": true, "every_hours": 24, "stale_days": 30,
             "budget": 500, "first_delay": 300 } }
```

写进 `config.json` 即可覆盖，缺的键按默认值合并。不想要就 `{"paths": {"enabled": false}}`。
每个参数为什么是这个值，写在 `trace_server.sweep_path_checks` 的 docstring 里。
三件事值得单独说：

- **只在结论会变或已经超过 `stale_days` 时才写盘。**否则每天给数据仓造一次
  164 个文件的提交，而里面一个事实都没变。
- **只 stat 这台机器看得见的绝对路径。**数据仓在 A 机、`/blue/…` 挂在 B 机（超算）
  的时候，服务端对那些路径**永远只会报「够不着」并且一个字节都不写**。那不是坏了：
  「够不着」和「没了」是两回事，把前者记成后者就造出了一条看起来像证据的假结论。
  那些路径要由跑在 B 机上的 agent（MCP 的 `trace_check_paths`）或者
  `python trace_cli.py paths --check` 来核对。
- **远端位置（`s3://` / `https://` / `//host`）一律不探测。**任何能写记录的人都能
  往 `path:` 里塞一个内网地址；服务端去发那个请求，等于把「从这台机器发起请求」的
  权力交给了他，而这台机器恰好看得见整个数据仓。它们的状态只能由人核对后写回。

```bash
# 只有「有位置从『在』变成『不在』」时才会打一行。没消息就是没有新的失效。
sudo journalctl -u trace | grep 路径核对
```

---

## 另一台机器怎么管

管理端**什么都不用装**——不用 clone，不用 pip，本地一份数据都不存：

```
/plugin marketplace add jinhang23/research-trace
/plugin install research-trace@research-trace
```

装的时候：**这台机器的角色**选 `client`，**远端服务地址**填
`https://你的域名/t/<space>`，**写入令牌**填上，**数据仓目录留空**。
Windows 上再把 **Python 解释器**改成绝对路径（用
`python -c "import sys; print(sys.executable)"` 打出来的那个）。

装完 `/research-trace:doctor` 会实跑一遍握手确认接通。它现在也会**试一次写**
（打一个必然 404 的 PATCH：令牌不对是 401，令牌对是 404，两种回答都不写一个字节），
所以"读得到但令牌漏填"这种情况不会再报成"全部通过"。

超算上的 agent 同理，只是 Python 解释器保持默认 `python3` 即可。

---

## 安全边界（说清楚，别自己骗自己）

**读是公开的**——防的是爬虫和随手一猜，不防转发。含 space 的完整 URL 出现在
任何地方（聊天记录、书签同步、Referer、Caddy 访问日志）就等于内容公开了。所以：

- Caddy 的访问日志按机密文件对待
- `Referrer-Policy: no-referrer` 和 `X-Robots-Tag: noindex` 已配好
- 未发表的核心数据、合作方的保密材料不要放进来

**写**永远需要 Bearer 令牌，令牌只在 VPS 的 `config.json`、你的浏览器 localStorage、
以及管理端的插件配置里（标了 `sensitive`，进安全存储不落 settings.json）。

`GET /api/git` 和 `GET /api/status` 的 `detail` / `root` 字段（含服务器绝对路径和
远端地址）**只在带令牌时**才返回。前提是你真的设了令牌——没设令牌就是"谁都能写"，
那时这两个字段对所有人可见。公网部署必须设令牌。

想改成读也要令牌：把 `require_token` 加到读路由上，约五行。

## 升级（服务端和管理端是两个地方，有先后）

**往 GitHub 推代码不会更新任何已经装好的东西。** 服务端跑的是它自己 clone 的
那一份，管理端装的是某个 commit 的插件快照——两边都得各自去拉。

顺序是**先服务端，后管理端**。反过来的话，新版插件会去调服务端还没有的端点，
症状是几个工具莫名其妙 404，而你刚更新完，第一反应会是"新版本坏了"。

### 服务端

```bash
sudo -u trace git -C /srv/trace pull
sudo -u trace /srv/trace/.venv/bin/pip install -r /srv/trace/requirements.txt
sudo systemctl restart trace
```

**然后确认新代码真的在跑**——这一步不能省，"拉错分支 / 旧进程还活着 /
服务压根没重启"三种情况的症状都是「看起来一切正常」：

```bash
curl -s https://<域名>/t/<space>/api/status | python3 -m json.tool
```

看 `software` 那个字段（比如 `"software": "1.7.0"`），它是**代码**版本。
旁边那个 `version` 是**内容**版本（文件一变就涨），两回事，别看错。

数据仓不用动。`projects/` 是纯文件，新版本加的键全是可选的——
旧记录一个字都不用改，照常读、照常评级。

### 管理端（另一台机器）

插件是从市场装的快照，用 Claude Code 的插件更新流程拉新版即可。装完自检：

```bash
python3 <插件目录>/trace_mcp.py --selfcheck
```

它会打印后端、角色、工具数，并真的握一次手。`--version` 只打版本号，
适合写进脚本比对两边。

### 两边版本不一致会怎样

写接口是**只加不改**的（新字段都可选，老字段语义不动），所以：

- **管理端旧、服务端新** —— 能用。旧插件不知道新工具，仅此而已。
- **管理端新、服务端旧** —— 新工具会 404。这就是要先更服务端的原因。

拿不准两边是什么版本时，一边 `curl .../api/status` 看 `software`，
一边 `trace_mcp.py --version`，两个数对上就行。

## 换机器 / 灾难恢复

`projects/` 是全部内容，且已经在私有数据仓里。换机器就是：clone 两个仓库 →
装依赖 → 把旧的 `config.json` 拷过去（保持 space 和 token 不变，已有链接和
已配好的管理端继续有效）→ 起服务。不需要导出、迁移、或者任何数据库操作。

**前提是同步真的在跑。** 自动同步只挂在 HTTP 写路由上：在这台机器上直接
`vim note.md`、或用 `trace_cli.py new` 建步骤，服务的后台轮询发现磁盘变了之后
也会触发一次同步；但服务没在跑的时候做的改动，要等下一次服务起来才会被带上。
定期看一眼 `curl {base}/api/git` 的 `last_ok_at`——"上一次真正推成功是什么时候"
是这套备份唯一值得盯的数字。
