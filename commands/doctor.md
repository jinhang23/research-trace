---
description: '检查 trace 的 MCP server 在这台机器上接通了没有，没通就说清楚该改哪一项'
---

诊断 trace 插件在**当前这台机器**上的接线状况。按顺序查，每一步都要**实际跑**，
不要根据配置文件推断。查完给一句结论 + 要改的具体项。

## 0. 先记住一件事：你和插件看到的环境不一样

插件把用户在 `/plugin` 里填的四项灌进 `TRACE_ROLE` / `TRACE_DATA` / `TRACE_URL` /
`TRACE_TOKEN`，但**只灌给 Claude Code 拉起的 MCP 子进程**。你的 Bash 工具是另一个进程，
里面这四个变量通常是空的。

后果是：在一台**配好了的**机器上，直接在 shell 里跑 `trace_mcp.py --selfcheck`
会报「没有配置后端」。**这是假阴性，不要据此让用户去改配置。**（自检自己也会说这句话。）

所以本诊断有两条互补的通路，都要走：

- **通路 A（地面真值）**：在这次会话里直接调 `mcp__plugin_research-trace_trace__trace_projects`。
  它跑在带着插件 env 的子进程里，回什么就是什么。
- **通路 B（细节）**：把用户在 `/plugin` 里填的值抄出来，喂给自检：

  ```bash
  <python> <插件根>/trace_mcp.py --selfcheck --role server --data "<数据仓目录>"
  <python> <插件根>/trace_mcp.py --selfcheck --role client --url "<远端地址>" --token "<写入令牌>"
  ```

  `--role/--data/--url/--token/--config` 只是把值写进本进程的 `TRACE_*`，
  之后走的是和真实运行时**逐字相同**的那条 `make_backend` 代码路径。

## 1. 解释器

插件的 `python` 配置项默认是 `python3`。在 Windows 上这经常是错的：PATH 上的
`python` 可能指向别的软件自带的 2.x，`python3` 可能只是个应用商店占位程序，
Claude Code 起子进程时用不了。

跑一下当前配置的解释器：

```bash
<配置里的 python> -c "import sys; print(sys.executable, sys.version_info[:2])"
```

要求 ≥ (3, 10)。不满足或者根本起不来，就去找一个能用的，候选：

- `python -c "import sys; print(sys.executable)"`（在用户平时用的那个环境里跑，把打印出来的绝对路径原样填回去）
- `where python3` / `which python3`（Windows 上确认拿到的是 `.exe`，不是无扩展名脚本）
- `$CONDA_PREFIX/bin/python`，Windows 上是 `$CONDA_PREFIX/python.exe`

找到之后告诉用户：跑 `/plugin` → 选 research-trace → 配置 → 把 **Python 解释器**
改成那个绝对路径。

## 2. 角色（`TRACE_ROLE`）与后端

**先看角色，再谈优先级。**角色是安装时那个「这台机器是服务端还是客户端」的落地物，
`make_backend()` 第一件事就是读它，它决定了另外两项里哪一项**会被直接丢掉**：

| 角色 | 生效的 | 被强制清空的 | 缺了必需项时 |
|---|---|---|---|
| `server` | 数据仓目录（本地读写文件） | **远端地址被忽略**，即使填了 | 没填数据仓目录 → 当场报错 |
| `client` | 远端地址 + 写入令牌 | **本地数据仓目录被忽略**，即使填了 | 没填远端地址 → 当场报错 |
| `auto`（默认） | 两样都填就**远端优先**；只填一样就用那一样 | — | 两样都空 → 报「没有配置后端」 |

所以「两样都填了远端优先」**只对 auto 成立**。最容易误判的一格是：
角色选了 `server`、远端地址又留着没删 —— 此时工具**不报错**，静默走本地；
如果你按「远端优先」去 `curl <url>/api/projects` 判断健康，远端不通就会误报
「后端连不上」，而它其实正在本地好好工作。

**怎么改角色**：`/plugin` → research-trace → 配置 → 角色（`server` / `client` / `auto`）。
命令行侧等价物是环境变量 `TRACE_ROLE`，或配置文件（`$TRACE_CONFIG` / `~/.trace.json` /
`~/.config/trace/config.json`）里的 `"role"`。环境变量优先于配置文件。

查完角色再查那一项本身：本地目录要能 `ls` 到（里面应该有 `projects/`），
远端地址要能 `curl -s -o /dev/null -w '%{http_code}' <url>/api/projects` 拿到 200。

**目录存在不等于目录对。** `--selfcheck` 现在会区分四种状态并如实报出来：
已经是数据仓 / 空目录 / 目录不存在（会现建）/ 目录里有别的东西但没有 `projects/`。
后三种都会带一行 ⚠。看到 ⚠ 且用户说「这台机器上本来就有记录」，那就是路径填错了 ——
自检还会列出同一层里长得像数据仓的目录名，照着问用户是不是想指那个。

## 3. 真跑一遍：读 + 写

这是唯一算数的检查。

```bash
<python> <插件根>/trace_mcp.py --selfcheck <把第 0 节通路 B 的参数带上>
```

它会依次验：Python 版本 → 角色语义 → 后端 → 数据仓状态 → 读 → **写** → JSON-RPC 握手。

**「写」这一项是必看的。** 服务端的读路由不要令牌，所以令牌漏填、填错、或被服务端
轮换过之后，只验读的检查会满屏 ✓ —— 直到 agent 真正开始记录、第一次写入撞上 401，
而那恰好是最不该卡住的时刻。自检的写探针不留任何垃圾：远端是 PATCH 一个必然不存在的
项目（令牌不对 → 401，令牌对 → 404），本地是建一个点开头的空文件再删掉。

写这一项 ✗ 时结论要说全：**能浏览、能读，但任何写入都会 401**，
去 `/plugin` → research-trace → 写入令牌，填服务端 `python trace_cli.py url` 打印的那串。

如果 `--selfcheck` 本身跑不起来（解释器不对、文件路径不对），退回到手工握手：

```bash
<python> - <<'PY'
import json, subprocess, sys
p = subprocess.Popen([sys.executable, r"<插件根>/trace_mcp.py"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, encoding="utf-8", bufsize=1)
def send(o): p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}})
print("initialize:", json.loads(p.stdout.readline())["result"]["serverInfo"])
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
print("tools:", [t["name"] for t in json.loads(p.stdout.readline())["result"]["tools"]])
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"trace_projects","arguments":{}}})
r = json.loads(p.stdout.readline())["result"]
print("isError:", r.get("isError"))
print(r["content"][0]["text"][:400])
p.stdin.close(); p.terminate()
PY
```

注意这个子进程继承的是**你的** shell 环境，不是插件的（见第 0 节），
所以它报「没有配置后端」只说明你的 shell 里没有 `TRACE_*`，不说明插件坏了。

插件根目录：`claude mcp list` 或 `~/.claude/plugins/cache/` 下找 research-trace。

**如果 `trace_projects` 回 `isError`，把错误原文念给用户** —— 它会直接说清是配置没读到、
目录不存在、还是连不上远端。不要自己猜。

## 4. 工具在不在

确认这次会话里能看到 `mcp__plugin_research-trace_trace__*` 这组工具。看不到的话：

- `/reload-plugins`，或者重启 Claude Code
- `/plugin` 里确认 research-trace 是 enabled

看不到工具时，症状通常**不是**「缺依赖」而是「工具列表里干脆没有 trace_*」——
那多半是第 1 节的解释器起不来。

## 结论怎么说

把结果压成一句话，加上要改的项。例如：

- 「通了。角色 server，数据仓 D:/research/trace-data，3 个项目 41 步，读写都正常。」
- 「读通了，写不了：角色 client、远端连得上，但写入令牌是空的 —— 现在只能浏览，
  agent 一记录就 401。去 `/plugin` → research-trace → 写入令牌填上。」
- 「没通。python 配的是 `python3`，但这台机器上它指向 Python 2.7.11。
  改成 `C:/ProgramData/anaconda3/python.exe` 就行（`/plugin` → research-trace → 配置）。」
- 「角色选的是 server，远端地址那栏其实没生效（server 会忽略它）；数据仓目录指向的
  `D:/trace-dataa` 是我刚建出来的空目录，同一层有个 `trace-data` —— 多半是多打了一个 a。」

不要在没跑过第 3 步的情况下说「应该没问题」，也不要因为你自己的 shell 里
`--selfcheck` 报「没有配置后端」就说插件没配好（见第 0 节）。
