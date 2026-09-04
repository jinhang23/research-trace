# 从 Claude-Mem 学习研究记录

alpha.25 改进现有 Recorder 的提示词和派发指导。用户希望学习 Claude-Mem 的提示词及其他
适合的实现思路；保留现有 Web、MCP 写入接口、原始证据、人工修订和被动记录边界。
alpha.26 在此基础上增加程序级的 Recorder 反馈循环限制。

## alpha.26 的防循环边界

fork 本身不等于递归。实际风险是 Recorder 的工具事件混入研究材料，它完成后唤起主
agent，下一次主 agent Stop 又派发 Recorder。`stop_hook_active` 只能拦住当前 Stop 的
续跑，不能单独覆盖后台完成引起的新回合。宿主字段含义见
[Claude Code hooks 文档](https://code.claude.com/docs/en/hooks)。

修复前的有界模拟只输入了一次用户请求：带正确 agent_id 的对照派发一次便结束；
缺少 ID 的 Recorder Read 被误当成研究材料，连续派发四次后仍准备继续。
审计结果在 `.integration-demo/recorder-loop-audit-acf698ee/report.json`。

现在的程序规则：

- 即使缺少 agent_id，读取当前会话 outbox 或 Recorder 协议也按内部活动过滤；已记住的
  退休 Recorder 事件继续过滤。普通研究子 agent 的操作仍保留。
- 每次主用户输入后，同一个 batch 最多派发一次，自动派发总数最多三次。
  后台完成、生命周期通知和带 Recorder 标记的提示不补充预算。
- 达到上限便停止唤起主 agent，留下一个 `RecorderDispatchPaused` 诊断。
  新的主用户输入恢复派发；未处理批次和原始材料保留，投递独立继续。
- 采集失败诊断本身不作为新研究材料触发模型；无法判明身份的项目文件操作保留，
  由派发上限控制反馈，避免为了消环丢掉真正的研究证据。

三次是保守的运行上限，不是自动识别研究进度的算法。代价是同一用户请求后，较晚到达的
真实后台研究成果可能等到下一次用户输入才被整理。这个限制只约束本插件的自动派发；
不保证宿主、其他插件或主 agent 自行编写的循环停止。测试使用合成事件，不代表已完成
真实 Claude 会话、模型摘要质量或费用评测。

alpha.26 的 hook、Recorder 指导、文档、MCP、组件和版本一致性共 61 项测试通过，结果在
`.integration-demo/a26-recorder-verified.txt`。其中 7 个新增回归用例覆盖重复派发、匿名
读取、硬上限、用户输入恢复、退休事件和两种采集诊断。UF 插件尚未升级。

## 核对的源码

Claude-Mem 固定到提交
[`be44b6c8e238a7e2bc5b3403c05afac071a59ead`](https://github.com/thedotmack/claude-mem/tree/be44b6c8e238a7e2bc5b3403c05afac071a59ead)。
实际阅读了以下文件，以及该提交的 LICENSE / NOTICE。此次以本项目的研究语义重新撰写
提示词，没有复制其 worker、数据库、模型客户端或 XML 解析器进入发行包。

| 上游来源 | 学到什么 | 本项目的具体适配 |
| --- | --- | --- |
| [code.json](https://github.com/thedotmack/claude-mem/blob/be44b6c8e238a7e2bc5b3403c05afac071a59ead/plugin/modes/code.json) | 面向未来会话的观察者；记录成果和发现；事实可独立理解，再用 narrative 解释意义 | 写研究发现及依据，不写 Recorder 自己的动作；补上实验条件、认识边界、未探索方向 |
| [prompts.ts](https://github.com/thedotmack/claude-mem/blob/be44b6c8e238a7e2bc5b3403c05afac071a59ead/src/sdk/prompts.ts) | 已有记忆作为续接上下文；不重复记录；阶段摘要区分已做、已知、下一步 | 写之前比较现有 Node/Overview；只记录认识变化；区别正在推进与搁置的想法 |
| [prompts.ts](https://github.com/thedotmack/claude-mem/blob/be44b6c8e238a7e2bc5b3403c05afac071a59ead/src/sdk/prompts.ts) | 过长字段保留首尾并标明省略 | 协议要求尊重截断/缺失标记，不能补造内容；这次没有引入新的截断器 |
| [observation.ts](https://github.com/thedotmack/claude-mem/blob/be44b6c8e238a7e2bc5b3403c05afac071a59ead/src/cli/handlers/observation.ts) | hook 送出事件，后台处理语义 | 保留已有采集与整理分离；没有替换为上游 worker，也没有增加网络 hook |
| [summarize.ts](https://github.com/thedotmack/claude-mem/blob/be44b6c8e238a7e2bc5b3403c05afac071a59ead/src/cli/handlers/summarize.ts) | Stop 提供最后的可见回答作为总结材料 | 现有批次已有可见对话；加强复用 Recorder 必须阅读新增材料的派发指令 |
| [分层读取文档](https://docs.claude-mem.ai/progressive-disclosure) | 先少量上下文，再按具体问题读取详情 | 通过现有 recent_limit 和语义搜索从小范围开始；没有声称新增索引模式或检索 API |

## 有意保留的差异

- 上游默认 code 模式侧重软件变更。研究记录还需要没有工具调用的想法、失败尝试和负结果。
- 上游 code 模式要求每个请求至少有一个进度摘要。本项目允许零记录，以免流水账不断累积。
- 不增加上游类型/概念枚举或所有文件清单；一个研究问题可以串起多组工具操作和实验。
- 上游的 XML 输出交给它自己的解析器。本项目继续使用现有 MCP 结构化参数，不平行维护
  一套新的 XML 中间表示。
- 固定提交的 worker 提示词和 server 单批生成是不同路径。历史上的持续 SDK 会话不代表
  所有运行模式，也不能证明共享主会话缓存。这次没有更改 fork 默认频率或新增模型后端。
- 新的“少读、先比较、合并语义”是模型行为指导。没有实现自动语义去重器或新的批次合并器；
  原始证据仍照常保存，模型可以返回零记录。

## 语义验收样例

以下是人工审阅/后续真实 Claude 会话的验收标准，不是已经跑过的模型评测或实验结果。
不使用字符串断言来声称提示词已经提高研究质量。

| 示例输入 | 合格记录 | 应避免 |
| --- | --- | --- |
| 连续列文件、安装成功、查询作业状态，没有新认识 | 零 Node | 每个操作生成记录 |
| 讨论换口袋截断距离，尚未执行 | 保留问题、已有动机、拟议比较和未测试状态 | 写成已经完成消融；编造延期原因 |
| 同一研究问题下多个配置与训练命令 | 一份连贯的研究解释，关联对应实际来源 | 按脚本/工具调用拆成大量碎片 |
| 作业因 OOM 退出，没有下游评价 | 资源失败及已知配置；方法效果仍未知 | 宣称假设被否定 |
| 预训练 loss 下降但无下游测试 | 已观察到的训练变化；下游收益待验证 | 宣称亲和力预测提升 |
| 旧 Node 已记录同一事实，这批仅重复检查 | 零新增结论；必要时定向检索旧记录 | 把检索命中当新发现 |
| 新证据推翻旧结论，旧 Node 已有人修订 | 新记录说明变化并引用旧 Node；保留人工版本 | 偷改历史或换幂等键避开冲突 |
| 复用 Recorder，用户在主会话改变了方向 | 先读本批新增对话，再按新决定记录 | 把旧 fork 上下文当成最新状态 |
| 输出明确有省略标记或来源暂时不可用 | 只陈述可见证据，注明具体缺口 | 用模型推测补齐数值/脚本内容 |

## 生效与验证边界

提示词在 [Recorder 协议](../hooks/RECORDER_PROTOCOL.md)，工具字段说明在
[mcp.py](../research_trace/mcp.py)，复用派发指导在 [trace_hook.py](../scripts/trace_hook.py)。
插件版本同步到 alpha.26，让安装后的版本升级能够识别变化。

源文件更新不会自动升级 UF 上已有的插件，也不会改变已经启动的 Recorder。
真实摘要质量、缓存命中和费用需要在实际 Claude Code 会话中观察；协议长度减少不等于
同等比例的会话 token 或订阅额度减少。

alpha.25 本地验证：Recorder 指导、文档、hook、MCP、组件和版本一致性共 54 项测试通过，结果在
`.integration-demo/a25-recorder-verified.txt`。首次沙箱执行受 Windows 临时目录/子进程权限
影响，随后在项目内新的测试目录完成验证。没有新增只匹配提示词字样的测试来模拟质量评估。
协议按空白分词由 2,264 词缩至 1,665 词（26.5%）；这是文本长度测量，不是模型 token 计量。
