# 待办与交付边界

状态（2.0.0a29）：方案 3 的独立 Recorder 已接入，hook 只保存、独立 watcher 消费；在本机把
真 hook → 投递 → Recorder → 网页整条链路跑通，安全分三档，包与格式已标准化。做过什么、
修过什么在 [CHANGELOG](../CHANGELOG.md)。这里只放还没做的。

## 还没验证的（P1）

- [ ] **UF 真实环境。** 在 HiperGator 新建一个测试项目，按顺序确认：`claude --version`（2.1.30 与
  2.1.261 行为不同，代码已兼容但没在旧版真跑）；`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`
  无头登录；`REAL_WATCH=1 python scripts/ops_battery.py`（一次验掉版本、登录、watcher）；再用真项目
  跑 `trace-recorder --watch`。
- [ ] **UF ↔ 服务器链路。** 防火墙、代理策略、证书是谁签的——按 [QUICKSTART 4b](QUICKSTART.md) 走一遍，
  `python trace_mcp.py --selfcheck` → `trace-deliver` 一次 → `/api/health`。
- [ ] **记录质量。** 纯讨论、未探索方向、失败结果、一组实验、用户纠正、并行模型版本，用真实项目
  读正文判断。`scripts/simulate_research.py` 的合成情境已稳定（8 轮全绿），但它检查的是管线行为。
- [ ] **真实额度。** 每轮约 5–9k 新输入 + 2.5k 缓存前缀 + 1–4k 输出，一天 30 个回合约 20–40 万 token
  订阅用量；用真实账户的 `usage_totals` 与限额事件核对。
- [ ] **中断与恢复。** 进程中断、网络断开、额度耗尽后材料完整、无重复节点、无人工记录覆盖——
  运维 battery 用假 claude 覆盖了失败路径，真实额度耗尽事件的形状仍需一次实测。
- [ ] 按实际科研目录核对代码 / 共享代码、数据引用与 W&B 曲线链接；Entire 路径未测（Git-only 已验证）。
- [ ] Windows 中央存储的超长路径支持（附件临时文件可能超过传统路径上限）。

## 明确不做

- Codex CLI / Desktop 宿主适配。
- 团队配置映射与紧急 purge 的网页管理界面（REST 与配置文件路径已有）。
- GitHub 定时备份（手动导出/校验/恢复保留）。

## 验证工具

三套都在 `scripts/`，说明见 [scripts/README.md](../scripts/README.md)：`simulate_research.py`
（研究情境，8 次 sonnet）、`ops_battery.py`（运维 41 项，不花额度）、`net_battery.py`（网络 15 项，真 TLS）。
Claude-Mem worker 不是运行依赖；原始证据库继续保留，不会用上游内存缓冲替代长期存储。
