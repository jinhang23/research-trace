# 上游源码与功能接管

alpha.27 延续被动取证边界，并以独立订阅 Recorder 替换 alpha.26 的主会话 fork 派发。
最大化复用不等于把实验执行也纳入产品。

方案 3 的源码级复用审计和实际接缝在 [Recorder 复用方案](RECORDER_REUSE_PLAN.md)。
没有安装 Claude-Mem 服务或引入它的 Redis/Postgres 部署；固定源码中的观察、隔离、分类和
限流方式被适配到现有持久 outbox 与 Node API。

| 项目 | 实际使用方式 | 仍由本项目负责 |
| --- | --- | --- |
| Entire CLI 0.10.5 + Git | 官方主会话导出、阶段 checkpoint、Git 版本与归档 | 可见内容过滤、采集边界、证据保存和研究节点关联 |
| 官方 MCP Python SDK | 协议握手、stdio 生命周期和工具调用 | 研究工具的业务语义 |
| SQLAlchemy + Alembic | 连接管理、事务内 schema 迁移 | 业务 SQL、项目隔离、人工修订规则 |
| Authlib | OAuth 客户端 | 角色与项目访问规则 |
| markdown-it + Dagre | Markdown 解析、图布局 | 原有 Web 阅读交互 |
| Claude-Mem 固定版本 | 适配观察者提示、输出分类、额度/overage 状态、无工具隔离与有界独立会话 | 研究字段、持久队列、严格 ID 校验、幂等写入及人工权威 |

Claude-Mem 的逐项来源、采用范围和未采用内容见 [Recorder 借鉴说明](RECORDER_DESIGN.md)。
运行时使用 Python 适配实现，不宣称直接运行 Claude-Mem 的 TypeScript 服务或缓存数据库。

Submitit 曾在 alpha.23 中实际接管 Slurm 执行接口；用户明确任务提交与原目录保持不变由研究
Agent 负责后，alpha.24 删除了 Submitit 依赖和调度适配器。没有保留另一套自研 Slurm 执行器。

Entire 源码已实际下载审阅，固定到
[`52207b6d2961009115f557d87aef5ff564aa810d`](https://github.com/entireio/cli/tree/52207b6d2961009115f557d87aef5ff564aa810d)：

- [`checkpoint_list.go`](https://github.com/entireio/cli/blob/52207b6d2961009115f557d87aef5ff564aa810d/cmd/entire/cli/checkpoint_list.go)：pending JSON 接口，最多返回 20 个 checkpoint。
- [`sessions.go`](https://github.com/entireio/cli/blob/52207b6d2961009115f557d87aef5ff564aa810d/cmd/entire/cli/sessions.go)：寻找会话文件、流式输出、排除尾部半行。
- [`explain_export.go`](https://github.com/entireio/cli/blob/52207b6d2961009115f557d87aef5ff564aa810d/cmd/entire/cli/explain_export.go)：已提交 checkpoint 的机器可读导出。

`entire_evidence.py` 调用这些公共接口；`workspace.py` 只保留代码取证及启用功能。
无 Entire 或手动采集时，Git 快照也只是历史证据，不生成执行目录或控制训练版本。
旧的无 Entire 安装和独立子 agent transcript 使用兼容读取，离线投递与研究整理仍由本项目负责。

前端官方发行包的版本、SHA256、npm integrity 与许可证保留在 `research_trace/static/`。
完整外部仓库不放入发行包。MLflow 和 Basic Memory 仍是可选来源/派生索引，W&B 只留曲线链接。

验证使用真实 Git、Entire CLI 与本地服务；Claude/Slurm 输入是明确标注的夹具。测试会拒绝记录
过程中发生的训练/Slurm 调用，并验证原代码、HEAD、暂存区及旧记录保持可用。真实 UF 宿主尚未验收。
