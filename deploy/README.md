# 部署到你的域名

架构：`steps/` 的唯一工作副本在服务器上，debounce 后自动 `git commit && git push`
到私有仓库。你在本地 `git pull` 拿到的就是完整、可 grep、可 diff 的文件树——
这是 G4（删掉所有程序，剩下的文件仍然可读）在"上了服务器"之后的兑现方式。

一台 1 核 1G 的机器足够。

---

## 1. 服务器准备

```bash
sudo useradd -r -m -d /srv/trace -s /usr/sbin/nologin trace
sudo -u trace git clone <你的私有仓库> /srv/trace
cd /srv/trace
sudo -u trace python3 -m venv .venv
sudo -u trace .venv/bin/pip install -r requirements.txt
```

## 2. 初始化

```bash
sudo -u trace .venv/bin/python trace_cli.py init --title "你的项目名"
```

输出里有两样东西，**都要另外记下来**（`config.json` 在 `.gitignore` 里，不会入库）：

- **访问路径** `/t/<space>/` — 只有知道这个路径的人能看到内容
- **写入令牌** — agent 和网页写入都要它

丢了可以 `trace_cli.py url` 再看一次；换掉就是编辑 `config.json` 里的两个字段后重启。

## 3. git push 凭据

```bash
sudo -u trace ssh-keygen -t ed25519 -f /srv/trace/.ssh/id_ed25519 -N ""
sudo -u trace ssh-keyscan github.com >> /srv/trace/.ssh/known_hosts
# 把 /srv/trace/.ssh/id_ed25519.pub 加到仓库的 Deploy Key（勾选写权限）
sudo -u trace git -C /srv/trace config user.email "trace@你的域名"
sudo -u trace git -C /srv/trace config user.name  "trace"
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

证书 Caddy 会自动申请和续期。

## 6. 验证

```bash
curl -s https://你的域名/healthz                      # → ok
curl -s -o /dev/null -w '%{http_code}\n' https://你的域名/   # → 404（不暴露站点存在）
curl -s https://你的域名/t/<space>/api/status | jq
```

浏览器打开 `https://你的域名/t/<space>/`，点右上角 🔒 把写入令牌存进本浏览器。

---

## 安全边界（说清楚，别自己骗自己）

按你选的方案，**读是公开的**——防的是爬虫和随手一猜，不防转发。含 space 的
完整 URL 出现在任何地方（聊天记录、书签同步、Referer 头、Caddy 访问日志）
就等于内容公开了。所以：

- Caddy 的访问日志按机密文件对待
- `Referrer-Policy: no-referrer` 已配好，防止点外链时泄漏
- 真正见不得人的东西（未发表的核心数据、合作方的保密材料）不要放进来，
  或者改成全站 token 认证（把 `require_token` 也加到读路由上，约五行改动）

**写**永远需要 Bearer 令牌，令牌只在 `config.json` 和你的浏览器 localStorage 里。

## 换机器 / 灾难恢复

`steps/` 是全部内容，且已经在 git 里。换机器就是：clone 仓库 → 装依赖 →
把旧的 `config.json` 拷过去（保持 space 和 token 不变，已有链接继续有效）→
起服务。不需要导出、迁移、或者任何数据库操作。
