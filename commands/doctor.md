---
description: 检查 trace 的 MCP server 在这台机器上接通了没有，没通就说清楚该改哪一项
---

诊断 trace 插件在**当前这台机器**上的接线状况。按顺序查，每一步都要**实际跑**，
不要根据配置文件推断。查完给一句结论 + 要改的具体项。

## 1. 解释器

插件的 `python` 配置项默认是 `python3`。在 Windows 上这经常是错的：PATH 上的
`python` 可能指向别的软件自带的 2.x，`python3` 可能只是个没有扩展名的 shell 脚本，
Claude Code 起子进程时用不了。

跑一下当前配置的解释器：

```bash
<配置里的 python> -c "import sys; print(sys.executable, sys.version_info[:2])"
```

要求 ≥ (3, 10)。不满足或者根本起不来，就去找一个能用的，候选：

- `where python3` / `which python3`（Windows 上确认拿到的是 `.exe`，不是无扩展名脚本）
- `$CONDA_PREFIX/bin/python`，Windows 上是 `$CONDA_PREFIX/python.exe`
- `C:/ProgramData/anaconda3/python.exe`、`~/miniconda3/bin/python`、`/usr/bin/python3`

找到之后告诉用户：跑 `/plugin` → 选 research-trace → 配置 → 把 **Python 解释器**
改成那个绝对路径。

## 2. 后端配置

server 按这个优先级找后端，**环境变量优先于配置文件**：

1. `TRACE_URL`（+ `TRACE_TOKEN`）→ 打远端服务
2. `TRACE_DATA` → 直接读写本地目录
3. `TRACE_CONFIG` 指向的文件，或 `~/.trace.json`，或 `~/.config/trace/config.json`

插件把用户配置的 **远端地址 / 写入令牌 / 本地数据仓目录** 分别灌进前三个环境变量。
两样都填了的话**远端优先**。

检查用户填的那一项是不是真的存在：本地目录要能 `ls` 到（里面应该有 `projects/`），
远端地址要能 `curl -s -o /dev/null -w '%{http_code}' <url>/api/projects` 拿到 200。

## 3. 真跑一遍握手

这是唯一算数的检查。用第 1 步确认过的解释器：

```bash
<python> - <<'PY'
import json, os, subprocess, sys
exe = sys.argv[0] if False else sys.executable
p = subprocess.Popen([exe, r"<插件根>/trace_mcp.py"],
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

插件根目录：`claude mcp list` 或 `~/.claude/plugins/cache/` 下找 research-trace。

**如果 `trace_projects` 回 `isError`，把错误原文念给用户** —— 它会直接说清是配置没读到、
目录不存在、还是连不上远端。不要自己猜。

## 4. 工具在不在

确认这次会话里能看到 `mcp__plugin_research-trace_trace__*` 这组工具。看不到的话：

- `/reload-plugins`，或者重启 Claude Code
- `/plugin` 里确认 research-trace 是 enabled

## 结论怎么说

把结果压成一句话，加上要改的项。例如：

- 「通了。本地模式，数据仓 D:/research/trace-data，3 个项目 41 步。」
- 「没通。python 配的是 `python3`，但这台机器上它指向 Python 2.7.11。
  改成 `C:/ProgramData/anaconda3/python.exe` 就行（`/plugin` → research-trace → 配置）。」
- 「解释器没问题，但后端没配：`data_dir` 和 `url` 都是空的。填其中一个。」

不要在没跑过第 3 步的情况下说「应该没问题」。
