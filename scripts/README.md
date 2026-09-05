# scripts/

两类东西放在一起，别混：**插件运行时**（随 `claude plugin install` 装到每台机器上）和
**验证脚本**（开发者在本机或 UF 上手动跑）。

## 插件运行时

| 文件 | 谁调用 | 做什么 |
|---|---|---|
| `trace_hook.py` | Claude Code 的 12 个 hook（`hooks/hooks.json`） | 把事件和可见 transcript 增量写进本机 outbox，Stop 时封 batch；不联网、不启动模型、fail-open。改它必须 bump 版本 |

## 验证脚本（不进插件）

| 文件 | 花额度？ | 覆盖什么 | 怎么跑 |
|---|---|---|---|
| `simulate_research.py` | 8 次 sonnet（≈3 分钟） | 三天研究情境：讨论→噪声→提交→结果+W&B→人工纠正→OOM→重复→Overview→并行模型目录；真 hook / 投递 / 中央 / `claude --print`，20 项评分 | `python scripts/simulate_research.py`（普通终端；`SIM_MODEL=haiku` 省额度） |
| `ops_battery.py` | 否（`REAL_WATCH=1` 时一次 haiku） | 运维角度 41 项：bind/status/offline、recorder-enable、selfcheck、25 MB transcript、120 并发 hook、fail-open、投递宕机/恢复/重复/锁、Recorder 全部失败路径（经 `fake_claude.py`）、备份导出/校验/恢复、插件加载 | `python scripts/ops_battery.py`；**上 UF 后第一条命令**：`REAL_WATCH=1 python scripts/ops_battery.py` |
| `net_battery.py` | 否 | 服务端在远端：真 TLS（自签证书、非回环地址）上的证书校验、`SSL_CERT_FILE` 私有 CA、错 token、http→https 重定向拒绝、代理/`NO_PROXY`、13 MB 分批、Recorder 与 selfcheck 走 TLS、暴露警告 | `python scripts/net_battery.py`（需要 `openssl`） |
| `fake_claude.py` | 否 | 假 `claude` 可执行文件：按 `FAKE_CLAUDE_MODE`（success / malformed / empty / quota / overage / auth / noauthcmd / hang）产出 stream-json，供上面两套走真子进程路径 | 由 battery 调用，也可 `--claude scripts/fake_claude.py` 交给 `trace-recorder` |
| `entire_smoke.py` | 否 | 真 Entire CLI + Git 的代码证据采集（需要单独安装 Entire） | 见文件头 |
| `recording_smoke.py` | 否 | 合成 Claude/Slurm 输入下的代码证据与投递 | 见文件头 |
| `framework_smoke.py` | 否 | MLflow / Basic Memory 可选集成 | 见文件头 |
| `vendor_web.py` | 否 | 按 npm integrity 拉取并校验 vendored 的 markdown-it / Dagre | 更新前端依赖时 |

三个 battery 用运行它们的解释器旁边的 console scripts（`trace-project` 等），所以要先
`pip install -e ".[server,dev]"` 到那个解释器。输出文件 `*.log` / `*.json` 已在 `.gitignore`。
