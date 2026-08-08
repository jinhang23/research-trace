---
description: 把 trace 的 MCP server 接到这台机器上（探测 Python、写配置、注册、验证）
---

把这个插件带的 MCP server 在**当前这台机器**上接好。

为什么要有这一步：插件清单是静态的，而两样东西每台机器都不同——Python 解释器
在哪、数据仓在哪。所以它们不能写死在 `.mcp.json` 里。

按顺序做，每一步都要**实际验证**，不要假设：

## 1. 找一个能用的 Python

`${CLAUDE_PLUGIN_ROOT}/trace_mcp.py` 零依赖，只要 3.10+ 就能跑。但**别默认 `python`
就是对的**——Windows 上它经常指向别的软件自带的 Python 2.x。

依次试这些候选，对每一个跑 `<候选> -c "import sys; print(sys.version_info[:2])"`，
取第一个 ≥ (3, 10) 的：

- `python3`、`python`、`py -3`
- conda 环境里的：`$CONDA_PREFIX/bin/python`（Windows 上是 `$CONDA_PREFIX/python.exe`）
- 常见位置：`/usr/bin/python3`、`C:/ProgramData/anaconda3/python.exe`、
  `~/miniconda3/bin/python`、`~/anaconda3/bin/python`

**记下绝对路径**。Windows 上尤其重要：PATH 上的 `python3` 可能是个 bash 脚本，
不是 Windows 可执行文件，Claude Code 启动子进程时用不了。用
`where python3` / `which python3` 拿到真实路径，并确认它是可执行文件而不是脚本。

## 2. 问用户数据在哪

两种模式，问清楚是哪一种：

- **本地**：agent 和数据在同一台机器上，不需要起服务。要一个目录路径。
- **远端**：数据在域名后面的服务上。要 `https://域名/t/<space>` 和写入令牌。
  令牌可以在服务器上跑 `python trace_cli.py url` 拿到。

## 3. 写配置文件

写到 `~/.trace.json`（Windows 上是 `%USERPROFILE%\.trace.json`）：

```json
{ "data": "/path/to/数据仓" }
```

或者远端模式：

```json
{ "url": "https://域名/t/<space>", "token": "写入令牌" }
```

**这个文件含令牌，权限收紧**（POSIX 上 `chmod 600`），并提醒用户别提交进 git。

## 4. 注册 MCP server

```bash
claude mcp add trace -s user -- <第1步的绝对路径> ${CLAUDE_PLUGIN_ROOT}/trace_mcp.py
```

`-s user` 是全局生效；不加默认只对当前项目目录生效。

## 5. 验证（不要跳过）

起一个子进程，走一遍真实的 JSON-RPC 握手，确认三件事：initialize 回来了、
6 个工具都在、真的能读到数据。

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
print("isError:", r.get("isError"), "|", r["content"][0]["text"].strip().splitlines()[0])
p.stdin.close(); p.terminate()
PY
```

如果 `trace_projects` 回 `isError`，**把错误原文念给用户**——它会直接说是配置没读到、
路径不存在、还是连不上远端，不要自己猜。

最后告诉用户：重启 Claude Code 或跑 `/reload-plugins` 让 MCP server 生效。

## 顺带

如果用户还没有数据仓，问要不要现在建一个：

```bash
git clone https://github.com/jinhang23/research-trace <某处>
cd <某处> && python trace_cli.py init --project "第一个课题"
```

**提醒一句**：`config.json` 里有令牌，而自动 git 同步默认关着。要开之前
先确认 remote 指向**私有**仓库——否则科研笔记会被推到公开仓上去。
