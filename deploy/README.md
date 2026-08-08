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
把 `data_dir` 留成默认的 `"."`、同时 `origin` 又指向公开的代码仓，你的科研笔记会被
自动推到公网上——这是最容易踩的坑，`init` 现在会检查并警告。

`config.json` 本身（含写入令牌）留在**代码仓目录**里，且在 `.gitignore` 中，
两个仓库都不会带上它。

---

## 1. 服务器

```bash
sudo useradd -r -m -d /srv/trace -s /usr/sbin/nologin trace
sudo -u trace git clone https://github.com/jinhang23/research-trace /srv/trace
sudo -u trace git clone git@github.com:你/trace-data.git /srv/trace-data   # 私有
cd /srv/trace
sudo -u trace python3 -m venv .venv
sudo -u trace .venv/bin/pip install -e ".[server]"
```

数据仓可以是空的，`init` 会在里面建出 `projects/`。

## 2. 初始化

一条命令就把数据仓指好：

```bash
sudo -u trace .venv/bin/python trace_cli.py init \
    --title "你的项目名" \
    --data-dir ../trace-data \
    --project "第一个课题"
```

输出里有两样东西，**都要另外记下来**（`config.json` 在 `.gitignore` 里）：

- **访问路径** `/t/<space>/` — 站点只在这个不可猜的路径下存在
- **写入令牌** — 管理端和 agent 写入时都要它

丢了可以 `trace_cli.py url` 再看一次。命令末尾会检查数据仓是不是 git 仓库、
有没有配 remote，缺了会直接告诉你。

## 3. push 凭据

自动同步 commit 的是**数据仓**，所以 user.name / user.email 要配在它上面：

```bash
sudo -u trace ssh-keygen -t ed25519 -f /srv/trace/.ssh/id_ed25519 -N ""
sudo -u trace ssh-keyscan github.com >> /srv/trace/.ssh/known_hosts
# 把 /srv/trace/.ssh/id_ed25519.pub 加到**数据仓**的 Deploy Key（勾选写权限）
sudo -u trace git -C /srv/trace-data config user.email "trace@你的域名"
sudo -u trace git -C /srv/trace-data config user.name  "trace"
```

`trace.service` 里把 `HOME` 指到 `/srv/trace`，所以 git 能读到上面这些，
同时 `ProtectHome=true` 仍然生效。

## 4. systemd

```bash
sudo cp deploy/trace.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trace
sudo systemctl status trace
```

## 5. Caddy

把 `deploy/Caddyfile` 里的 `trace.example.com` 换成你的域名，DNS A 记录指过来：

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

证书 Caddy 自动申请和续期。SSE 需要 `flush_interval -1`，Caddyfile 里已经配好了。

## 6. 验证

```bash
curl -s https://你的域名/healthz                              # → ok
curl -s -o /dev/null -w '%{http_code}\n' https://你的域名/     # → 404，不暴露站点存在
curl -s https://你的域名/t/<space>/api/projects                # → 项目列表（读公开）
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     https://你的域名/t/<space>/api/projects                   # → 401，写要令牌
```

浏览器打开 `https://你的域名/t/<space>/`，右上角 🔒 存入写入令牌。

---

## 另一台机器怎么管

管理端**什么都不用装**——不用 clone，不用 pip，本地一份数据都不存：

```
/plugin marketplace add jinhang23/research-trace
/plugin install research-trace@research-trace
```

装的时候：**远端服务地址**填 `https://你的域名/t/<space>`，**写入令牌**填上，
**数据仓目录留空**。Windows 上再把 **Python 解释器**改成绝对路径。

装完 `/research-trace:doctor` 会实跑一遍握手确认接通。

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

想改成读也要令牌：把 `require_token` 加到读路由上，约五行。

## 换机器 / 灾难恢复

`projects/` 是全部内容，且已经在私有数据仓里。换机器就是：clone 两个仓库 →
装依赖 → 把旧的 `config.json` 拷过去（保持 space 和 token 不变，已有链接和
已配好的管理端继续有效）→ 起服务。不需要导出、迁移、或者任何数据库操作。
