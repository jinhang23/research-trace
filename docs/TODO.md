# 待办与交付边界

状态（2.0.0a33）：方案 3 的独立 Recorder 已接入，hook 只保存、独立 watcher 消费；在本机把
真 hook → 投递 → Recorder → 网页整条链路跑通，安全分三档，包与格式已标准化。做过什么、
修过什么在 [CHANGELOG](../CHANGELOG.md)。这里只放还没做的。

## 还没验证的（P1）

- [x] **UF 真实环境。** 2026-09-06 在 HiperGator 登录节点（CLI 2.1.261，已登录，不需要 setup-token）
  绑定测试项目，`trace-recorder --watch` 用 setsid 常驻：3 个 batch 各一次真实 sonnet 调用，写出 2 条 Node，
  标题准确、没有拆成噪音 Node。没跑 `REAL_WATCH=1 ops_battery.py`；2.1.30 旧版仍未真跑。
- [x] **UF ↔ 服务器链路。** 2026-09-06 在 HiperGator 登录节点 → https://aidd.rc.ufl.edu/trace（HTTPS 反代，
  `/trace` 前缀，OAuth 设备码）跑通：selfcheck → bind → 真 `claude -p` → 投递 12 events + 8 chunks 零失败。
  顺手抓到 a29 的 `capture` 选项事故（见 CHANGELOG a30）。Recorder 在 UF 上还没启用。
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
