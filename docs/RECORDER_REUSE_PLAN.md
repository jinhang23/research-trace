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

以上是接入前的方案；下面两节记录接入后的实际状态，原来放在 README 里，alpha.29 移到这里以便 README 只讲流程。

## 接入后的运行边界：独立进程，不是独立 App

它是一个**独立运行的后台 worker，但不是另一套独立 App**。`trace-recorder`、投递器和中央服务
来自同一个 Research Trace Python 包，hook 来自同仓库、同版本的 Claude Code 插件；它没有自己的
网页、数据库、账号系统或常驻 HTTP 服务。这里的“独立”具体指：

| 边界 | 当前实现 |
|---|---|
| 操作系统进程 | `trace-recorder --watch` 由人或进程管理器单独启动并长期等待 outbox；hook 不创建、唤醒或管理这个进程 |
| 模型会话 | 每次调用都是全新的无状态 `claude --print --no-session-persistence`，输入就是完整 packet；不使用主 agent 的 session，不读取主 agent 完整上下文，也不续接自己的旧会话 |
| 工作目录 | 模型运行在 `${CLAUDE_PLUGIN_DATA}/recorder-workspace`，不进入研究仓库，因此不会触发项目 hook |
| 权限 | `--setting-sources ""`、`--tools ""`、空的 strict MCP 配置和 `dontAsk` 共同关闭项目设置、工具与 MCP；模型只能返回 JSON |
| 写入 | 模型不直接调用 Research Trace。Python 先校验 event、Chapter、parent、run、人工纠正和版本，再调用中央 API |
| 生命周期 | worker 可随时退出；未处理材料、模型计划和已完成写入序号都在 outbox sidecar 中，恢复不依赖模型记忆 |

一次调用的实际顺序是：

1. Stop/SessionEnd hook 把本轮新增 event 和可见 transcript 增量封成持久 batch，然后返回；即使 Recorder 没运行，batch 也留在磁盘上。
2. 单独运行的 worker 扫描 outbox。`--watch` 在空队列时继续等待；不需要常驻时也可以不带 `--watch` 只处理当前积压。
3. worker 检查项目仍启用 Recorder，并拒绝 API key、Bedrock、Vertex、Foundry、Azure 等可能转向额外计费的认证环境。
4. worker 运行 `claude auth status --json` 确认当前 Claude CLI 是 Claude 订阅/OAuth 登录；这个检查不发模型请求。旧版 CLI 没有这个子命令时预检按 `unverified` 放行，登录与否由模型调用本身判定（付费/云凭证的环境检查在此之前已经拒绝过）。
5. 调用 `claude --print --no-session-persistence ...`：每次都是无状态的全新调用，输入就是完整 packet。
6. 模型收到固定 system prompt、固定 JSON schema、新增证据、精简项目记忆和少量相关旧记录（`/api/search` 的 Node 命中），返回零个或多个 Node/摘要更新计划，可附带 `artifact_refs`（在证据里原样出现过的 W&B run、产物 URL）。
7. Python 保存计划，再逐项幂等写入 Node、产物引用和摘要；中途掉线时从 sidecar 继续，不会为了同一计划再次调用模型。
8. 空输出/格式错误/超时类失败最多重试 4 次（每次都是真实模型调用），之后批次转 `attempts_exhausted` 等操作者 `--retry-blocked`；quota 等到恢复时间；overage 永久停。

“自己的缓存”需要准确理解。Research Trace 保存的是
`${CLAUDE_PLUGIN_DATA}/outbox/recorder-status.json` 里每个项目的调用次数、模型和
`input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens` 统计；
实际 prompt cache 由 Claude 服务管理。Research Trace
**没有实现一份本地模型缓存，也不能保证缓存一定命中或承诺节省比例**。固定的 system prompt、schema
和模型是每次调用逐字相同的前缀，这就是缓存能命中的全部条件——真链路实测无状态调用同样命中。
早期版本用 `--session-id`/`--resume` 复用一个 12 轮的会话，已经去掉：每轮都带完整 packet，续接
只会把之前每一轮的 packet 再送一遍（12 轮就能顶穿 200k 上下文），换不来任何东西。
它不会继承或复用主 agent 的 cache。即使缓存完全不命中，持久队列、幂等和人工版本保护仍然成立。

## 逐文件借鉴结果

Recorder 的上游参考固定在 Claude-Mem 提交
[`be44b6c8e238a7e2bc5b3403c05afac071a59ead`](https://github.com/thedotmack/claude-mem/tree/be44b6c8e238a7e2bc5b3403c05afac071a59ead)，
许可证为 Apache-2.0。Research Trace 没有把 Claude-Mem 当作运行依赖，也没有启动它的服务；
这里的“适配”是读固定源码后，把适合本项目的逻辑重新接到现有 outbox、Node 和 Web 数据模型。

下面列出当前 Recorder 审计和借鉴过的全部 Claude-Mem 功能：

| Claude-Mem 源码 | 上游功能 | Research Trace 中的处理 |
|---|---|---|
| `plugin/modes/code.json` | 定义面向未来会话的 observer：结果既有可独立理解的事实，也有解释意义的 narrative | **已适配。** 改为研究记录语言：保留实验条件、比较、证据边界、失败、未探索方向和下一步验证；不记录 Recorder 自己的动作 |
| `src/sdk/prompts.ts` | 构造 init、observation、summary、continuation prompt；已有记忆用于续接、去重和形成连续叙事 | **已适配。** 固定 `SYSTEM_PROMPT` 加每批 packet；先比较 Overview、Chapter、近期 Node、人工纠正和相关旧记录，再决定写或成功跳过 |
| `src/cli/handlers/observation.ts` | hook 把工具事件交给后台 observer，而不是在前台同步总结 | **沿用这种职责分离。** Research Trace hook 只写本地 outbox；现有持久批次替代上游在线 worker 投递路径 |
| `src/cli/handlers/summarize.ts` | Stop 提供最后一段可见回答，允许后台形成阶段总结 | **已适配。** Stop/SessionEnd 封存从上次游标之后的可见 transcript 和事件；不阻塞主 agent，也不对每个工具调用单独生成 Node |
| `src/sdk/output-classifier.ts` | 先识别错误，再区分有效结构、空响应、普通文本、认证、限额和上下文错误 | **已适配。** `classify_cli_output` 将成功、成功零记录、empty、malformed、auth、quota、overage 和普通错误分开，避免把异常当作“没有内容值得记录” |
| `src/services/worker/RateLimitStore.ts` | 兼容不同 rate-limit event 形状，读取 reset 时间和 overage 状态，并决定暂停 | **已适配为持久状态。** 正常 `allowed` 不误判；quota 保留 batch 到恢复时间；overage 硬停止，必须人工修正账户设置后显式重试 |
| `src/services/worker/ClaudeProvider.ts` | 独立 observer provider、增量输入、模型选择、token 用量统计和会话轮换 | **适配了运行形状。** 使用官方 `claude --print`，每次调用无状态（`--no-session-persistence`），并保存 input/output/cache read/cache creation 统计；没有采用它的多 provider 凭据层，也没有采用会话轮换（见下文「自己的缓存」） |
| `src/sdk/hardened-options.ts` | observer 不加载用户设置、没有工具或 MCP，输出交给普通程序处理 | **直接映射为 CLI 隔离参数。** 空 settings、空 tools、空 MCP、`dontAsk`、独立 cwd；模型没有写数据库或运行命令的能力 |
| `src/sdk/parser.ts` | 解析宽松 XML observation、summary 和 `skip_summary` | **没有复制 XML parser。** 使用 Claude CLI `--json-schema`，再由 Python 严格验证来源 ID、结构关系和版本；零记录仍是显式 `status=skip` |
| `src/services/worker/agents/ResponseProcessor.ts` | 把解析结果连接到 Claude-Mem 的数据库、文件、广播和通知 | **只参考处理阶段划分。** 写入端改接 Research Trace 的 Node/Overview/Chapter API、幂等键和人工修订规则，没有复制其数据库耦合代码 |
| `src/services/worker/session/recycle-conversation.ts` | 上下文过大时丢弃 observer conversation，用已有 observations 重建新一代上下文，并限制连续重启 | **采用同一原则，未复制实现。** Research Trace 根本不保留 observer 对话：每次调用都从中央精选记忆重建上下文，而不是把旧完整对话塞回来 |
| `src/services/worker/retry.ts` | 按错误类别决定是否重试，rate limit 尊重恢复时间，瞬时错误指数退避 | **采用分类重试原则。** batch sidecar 保存 attempts/retry_at；认证、配置、quota、overage、网络和格式错误使用不同恢复方式；写入重放依赖幂等 |
| `src/services/worker/SessionManager.ts` | 管理每个内容会话的 observer 状态、pending 消息、累计 token 和重启状态 | **只借鉴状态边界。** Research Trace 使用每项目 session 状态和跨进程 JSON sidecar，不使用上游进程内 SessionManager |
| `src/services/worker/SessionMessageBuffer.ts` | 进程内 pending 队列与 toolUseId 去重，崩溃恢复依赖 transcript replay | **明确没有采用。** 内存队列不能满足 HPC 杀进程后的可靠性；现有磁盘 outbox、batch manifest 和中央幂等继续作为权威恢复路径 |
| `src/server/jobs/ServerJobQueue.ts` | BullMQ/Redis server job 队列，另以 PostgreSQL outbox 作为权威记录 | **明确没有采用。** 不为 Recorder 再部署 Redis/PostgreSQL，也不让它替换当前已经工作的磁盘队列 |
| `src/server/generation/providers/shared/prompt-builder.ts` | 将一组事件做隐私过滤和长度限制，构造单次 observer 请求，允许显式 skip | **适配了批量、截断和显式 skip 思路。** Research Trace 在落盘前过滤隐藏推理；Recorder packet 对单事件、transcript 和项目上下文分别设界限，并保留省略标记 |
| `src/server/generation/providers/ClaudeObservationProvider.ts` | 用 Anthropic API key 直接调用 Messages API | **明确没有采用。** 这会绕过“只用 Claude Code 订阅额度”的要求；Recorder 检测到 API/云 provider 凭据会在模型调用前停止 |

CLI 隔离还参考了 Entire CLI 固定提交
[`52207b6d2961009115f557d87aef5ff564aa810d`](https://github.com/entireio/cli/tree/52207b6d2961009115f557d87aef5ff564aa810d)
中 `generate.go` 的 `claude --print`、隔离配置与 JSON 错误处理方式。没有采用其中的 API key/helper
认证路径。上游许可证、NOTICE、逐文件接入范围和修改说明保存在
[第三方说明](../THIRD_PARTY_NOTICES.md)和本文上面几节。
