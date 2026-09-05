# 方案 3：独立 Recorder 的源码复用范围

用户已选择独立整理模型：新增材料 + 简短项目背景 + 相关旧记录；使用独立上下文/缓存，
默认不 fork 主会话。只使用现有 Claude Code 订阅内额度，额度不足则等待，不转 API 或
额外付费。保留现有 Web、研究节点、人工纠正、原始证据和 Entire/Git 关联。

alpha.28 已按用户再次确认的边界拆开生命周期：Stop hook 只落持久批次，不启动模型；另行启动的
`trace-recorder --watch` 在空队列时等待并消费后续批次。主 agent 不派发 fork。实现和测试没有
调用真实模型、迁移数据或升级 UF 插件；真实 UF 与订阅额度验证仍待执行。

## 已读的上游实现

固定 Claude-Mem 提交
[`be44b6c8e238a7e2bc5b3403c05afac071a59ead`](https://github.com/thedotmack/claude-mem/tree/be44b6c8e238a7e2bc5b3403c05afac071a59ead)。
源码保存在 `.integration-demo/upstream-audits/claude-mem-be44b6c/`，文件路径中的 `/`
以 `__` 编码。LICENSE 为 Apache-2.0；源码接入时保留许可证、NOTICE 和修改说明。
此处的“复用”列说明接入方式，不表示相应模块已进入发行包。

| 模块 | 源码事实 | 接入方式 |
| --- | --- | --- |
| `src/sdk/prompts.ts` | `buildInitPrompt`、`buildObservationPrompt`、`buildSummaryPrompt`、`buildContinuationPrompt`，通过 mode 配置组织观察者提示词 | 已将其观察者、独立事实、选择性记录和连续叙事结构适配为 `SYSTEM_PROMPT` + 每批 packet；研究字段和人工权威继续使用本项目规则 |
| `src/sdk/parser.ts` | `parseAgentXml` 解析 observation/summary/skip_summary；依赖 ModeManager 和 logger | 未复制宽松 XML parser；改用官方 CLI `--json-schema`，再由 Python 严格验证来源、Chapter、parent、run 和 curation 版本 |
| `src/sdk/output-classifier.ts` | 区分 XML、空响应、普通文字及额度/认证/上下文错误 | 已适配为 `classify_cli_output`；空输出、格式错误、认证、quota、overage 与成功 skip 分开处理 |
| `src/services/worker/RateLimitStore.ts` | 接受新旧限流事件形状，按额度窗口维护状态并提供暂停判断 | 已适配新旧字段、reset 时间及 overage 检测到持久 batch/global 状态；正常 allowed 事件不会被误判为耗尽 |
| `src/services/worker/ClaudeProvider.ts` | 独立 SDK observer，通过消息生成器接收增量，有模型选择、用量统计与上下文轮换 | 已适配为官方 Claude Code CLI 的无状态调用（`--no-session-persistence`）和 cache/input/output token 计数；未引入其多后端凭据层，也未采用会话轮换（每轮都带完整 packet，续接只会重复旧 packet） |
| `src/sdk/hardened-options.ts` | observer 没有工具、没有 MCP、不加载用户项目设置；文本输出交给程序处理 | 已映射为 `--tools ""`、`--setting-sources ""`、空 strict MCP、`dontAsk` 和独立 cwd；程序负责写入 |
| `src/services/worker/agents/ResponseProcessor.ts` | 解析后耦合上游数据库、广播、文件更新及通知 | 拆出解析/转换接缝，对接现有 Node/Overview/来源校验与人工修订规则，不能整体直接调用 |

## 不能直接照搬的部分

1. **本地缓冲不等于可靠历史。** `SessionMessageBuffer.ts` 明确是内存队列，替代旧 SQLite
   pending 队列；进程退出后缓冲丢失，恢复依赖 transcript replay。它的 toolUseId 去重也只
   在进程生命周期内有效。可以用于在线调度，不能取代当前离线 outbox、持久化处理边界和
   已保存来源。模型重放还必须防止重复新增 Node。
2. **server 队列是另一套部署。** `ServerJobQueue.ts` 是 BullMQ 的薄封装，需要 Redis；
   注释指出 PostgreSQL outbox 才是权威历史。不要把本地内存 worker 与 server 队列描述成
   同一种实现，也不为了复用这个文件自动引入 Redis/PostgreSQL。
3. **不是默认跨进程 resume。** 此固定提交的 `ClaudeProvider.ts` 传入
   `--no-session-persistence`，清除旧 memorySessionId，并将 shouldResume 置为 false。
   它在存活的 query 中增量处理，重启后重建；不能声称直接接入后就有持久化 Recorder 会话。
4. **调用方式影响计费。** server 的 `ClaudeObservationProvider.ts` 明确要求 API Key 并
   直接请求 Messages API，不符合本用户约束。本地 worker 也包含专门的凭据环境构造；
   不将其当作官方订阅登录的等价替代或直接复制凭据刷新代码。

已固定的 Entire 0.10.5 源码
[`generate.go`](https://github.com/entireio/cli/blob/52207b6d2961009115f557d87aef5ff564aa810d/cmd/entire/cli/agent/claudecode/generate.go)
可作为 CLI 调用适配的实现来源：它调用 `claude --print`、隔离配置并处理 JSON 错误。
其中 API Key/helper 支持必须按本用户的订阅限制排除。Go 内部方法不是可直接导入 Python
项目的运行依赖，若移植需要明确标为移植，不宣称已执行上游方法。

## 目标数据流和需要保留的业务适配

采集 hook → 当前持久化 outbox → 独立模型的增量材料 → 适配后的观察/分类模块 →
研究字段及来源验证 → 现有研究节点与 Web。

主 agent 不再因记录任务收到 Stop 阻塞指令，也不再负责 Agent/SendMessage 派发。
原始来源、代码附件和阶段快照的采集/投递独立继续，研究执行仍由研究 agent 完成。

薄适配层负责：项目绑定；纯讨论及未执行想法的材料输入；来源 ID 和代码/数据/W&B 链接；
人工修订优先；批次与输出幂等；额度暂停及恢复；将结果映射为现有 Node/Chapter/Overview。
未处理、额度不足、格式错误、等待补充证据与成功零记录必须有不同状态。

模型默认使用账号订阅内的 Sonnet 别名，也允许显式选 Haiku；具体 UF host、有效模型和计费路径需验证。
固定模型/提示词维护自己的缓存，不再追求主模型缓存；不能承诺具体订阅额度节省比例。
上下文重建使用已保存研究记忆和未处理增量，不自动导入启用前的一年历史。

## 接入验收

- 普通研究回合及后台完成不因记录而唤起主 agent；Recorder 不采集自己。
- 纯讨论、失败实验、未探索方向、用户纠正及并行模型版本能产生有来源的连贯记录。
- 没有新认识允许成功零记录；解析错误不能被认作零记录成功。
- 额度不足、进程退出、存储失败和重放保留原始材料，不重复写入、不覆盖人工修订。
- 固定为订阅内允许的模型和认证路径，账户额外用量关闭；无法确认时不调用付费后端。
- 实测实际输入、缓存读写、输出与限流事件，分别报告模型质量和额度观测；单元测试或
  源码接入不能证明真实摘要质量、缓存命中或 UF 长会话稳定性。
