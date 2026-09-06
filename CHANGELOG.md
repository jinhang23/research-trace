# 2.0.0a32 — UF 第 5 轮：短会话的内容躺在 pending/ 里没人投

- **SessionEnd 的投递被 60 秒节流吞掉。** `claude -p` 从 SessionStart 到 SessionEnd 不到一分钟：SessionStart
  拉起的投递器跑在事件写入之前，Stop/SessionEnd 那两次被节流；这一轮的内容一直躺在 `pending/`，直到下一次
  会话或有人手动 `trace-deliver`，而 `--status` 全程 idle、`last_error=null`。现在节流只管 SessionStart，
  Stop / SessionEnd 一定拉起。
- **投递器撞锁不再立刻放弃。** 抢不到 outbox 锁时先等最多 15 秒（`DELIVER_LOCK_WAIT`）：hook 在 Stop 拉起的
  那个经常撞上 SessionStart 那个还没跑完的，以前直接退出，效果和上一条一样是静默残留。

# 2.0.0a31 — UF 第 4 批：Inbox 里的 parent 被当成格式错误

- **`parent is not in the selected Chapter` 让整批按 format 重试。** 提示词让模型「Inbox 时 chapter_id 留空」，而
  Inbox 里已有的 Node 带着 Inbox 的真实 chapter_id，模型选它做 parent 时校验判定章不一致，按 format
  退避重试——每次重试都是一次真实模型调用。它是间歇性的：模型不是每次都选那个 parent，UF 第 4 批
  第一次被拒、两分钟后重试就过了（多花一次调用；若模型每次都选，4 次后就是 `attempts_exhausted`）。现在：没选 Chapter 就跟着 parent 的 Chapter 走；选了别的 Chapter 就丢掉 parent（协议
  本来就允许缺链接），两种都不再让整批失败。
- **同版本号的新提交 pip 装不上。** `pip install --upgrade "research-trace @ git+…"` 看到本地已是
  2.0.0a30 就 `already satisfied`，新代码一个字节不装也不报错。规矩改为：每次推 main 都 bump 版本
  （a30 → a31 起执行）；安装说明加 `--force-reinstall --no-deps`。
- 提示词：标题不带用户加的会话/轮次前缀（「第四轮联调：」）——UF 第 3 条 Node 的标题原样带上了。

# 2.0.0a30 — UF 首次联调：全新安装的 hook 全部静默失败

- **全新安装一个字节都不采集。** `claude plugin install --config python=… --config url=…` 没传 `capture`，
  Claude Code 展开 hooks.json 里的 `${user_config.capture}` 时并不用 plugin.json 的 `"default": "on"`，
  每个 hook 都报 `Plugin option "capture" isn't set`；`claude -p` 照常 exit 0，只有 stderr 末尾一行，
  outbox 空目录。a24 为 `recorder_fork_window` 立的规矩「default 不算数」只考虑了升级安装，没考虑
  全新安装同样不填 default。修法：hooks.json 不再引用 `capture`，全局暂停改为环境变量 `TRACE_CAPTURE=off`
  （`--capture-enabled` 命令行参数保留给测试与脚本）；plugin.json 删除 `capture` 选项；`python`/`url`
  的说明写明必须显式 `--config`；守卫测试的 SAFE_TO_REFERENCE 缩到这两项。
- `trace-recorder --watch` 的日志是空的。stdout 重定向到文件时 Python 整块缓冲，每轮 `run_once` 的 JSON 报告
  一直留在缓冲区里；而且空转也打印整份报告，对日志来说全是噪音。改为每处理一个 batch 打一行并 flush，
  队列空时不写；非 watch 模式仍打印完整 JSON。文档写明「日志为空不等于没干活，看 `--status`」。
- UF HiperGator → https://aidd.rc.ufl.edu/trace（HTTPS 反代 + `/trace` 前缀 + OAuth 设备码）整条
  hook → outbox → 投递 → 中央 → Recorder（3 次真实 sonnet 调用，2 条 Node）链路首次在真实环境闭环；细节见 docs/TODO.md。

# 2.0.0a29 — 让独立 Recorder 在真实 CLI 上跑通；安全三档；包与格式标准化

## Recorder 管线（真实 CLI 上的修复）

- **相关旧记录此前从未进入模型。** worker 读 `/api/search` 的 `items`，服务端返回的键是 `hits`（测试里的 mock 写错了同一个键，所以一直是绿的）。改为读 `hits` 且只取 `scope=node` 的命中；comment / overview 的 id 不再可能被当成 parent。
- 召回不再用整句 prompt 做 `%query%` 子串匹配（永远搜不到旧 Node）：改为从 prompt 与最后回答里抽标识符（ESM-2、warmup、esm2_gnn、10Å）和重复出现的中文二元词，每个词各查一次再合并，已在 `recent_nodes` 里的不重复送。
- 搜索永远匹配不上 `10Å`：服务端用 Python `str.lower()` 折叠查询（`Å→å`），列那边是 SQLite 的 `lower()`（只折 ASCII）。改成两边只折 ASCII；网页搜索同样受益。
- 去掉 `--exclude-dynamic-system-prompt-sections`：官方帮助写明传 `--system-prompt` 时该参数被忽略，而 2.1.30 之前的 CLI 不认识它，每次调用直接 rc=1。
- `claude auth status` 预检对没有这个子命令的旧版 CLI 放行并记为 `unverified`，登录由模型调用本身判定；有回答时仍严格要求 claude.ai/OAuth 且 `apiProvider` 为 first-party。
- 模型调用改为无状态（`--no-session-persistence`），删除 12 轮 `--session-id`/`--resume` 轮换：每轮都带完整 packet，续接只会把旧 packet 重复送一遍（12 轮足以顶穿 200k 上下文）；固定前缀已实测命中缓存。
- 空输出/格式错误/超时类失败最多 4 次，之后批次转 `attempts_exhausted` 等 `--retry-blocked`；以前指数退避封的是间隔不是次数，一个永远解析不了的批次会无限烧额度。
- `ANTHROPIC_BASE_URL` 进入拒绝列表；调用前清理嵌套 Claude Code 会话变量（`CLAUDECODE`、`CLAUDE_CODE_*` 等），保留 `CLAUDE_CODE_OAUTH_TOKEN`。
- 新增 `artifact_refs`：Recorder 可为 Node 登记 W&B run 页面等外部产物 URI，URI 必须在本批证据中原样出现，Node 写入后经 `/api/attach` 以 `capture_key` 幂等登记，从此曲线链接是数据流的键而不只是正文里的一串字。
- 模型把 **Node 上的人工纠正 id** 放进章摘要的 `resolve_comment_ids` 时不再整批 format 失败（服务端的 409 闸门按目标算，列上外目标的 id 改变不了任何事），只丢掉该 id；编造的 id 仍是硬错误。schema 字段和 system prompt 补上了 `resolve_comment_ids` 的用法。
- `status` 只是模型自述，写什么由 `records` / `curations` 两个数组决定（模型对"只更新 Overview、零记录"答了 `status=skip`，旧的矛盾检查把一次正确的编辑判成 format 失败并重试）。labels 要求用原文语言并复用已有标签；章归属规则：名字/摘要明显匹配就归入，Inbox 只给真正拿不准的。
- 摘要不再每轮都改：curation 必须给出 `reason`（`first_summary` / `result_changed` / `plan_changed` / `direction_closed` / `correction_absorbed` / `milestone` / `progress_only`），`progress_only` 和「已有摘要的章再来一次 first_summary」在写入前丢弃，sidecar 记 `dropped_curations`。摘要是研究线当前的答案，不是步骤日志。
- `capture=off` 与 `trace-project disable` 暂停期间推进 transcript 游标，重新开启后不再补采暂停期间的内容——此前文档承诺如此而实现恰好相反。暂停期间才新开的会话仍从头采，文档写明。

## 安全与网络

- **安全配置三档，内网档一把钥匙。** 以前只有"读全开 + token 挡写"和 GitHub OAuth 两档。新增访问密钥模式：`TRACE_PROTECT_READS=true`（`--protect-reads`；`--host` 非回环且有 token 时默认开）让同一个密钥守读写，网页弹出"访问密钥 + 你的名字"登录（`POST /api/auth/token-login`，用密钥签名的无状态 cookie，纠正/确认署名为 `human`），`trace-server --init` 生成密钥和 0600 的 `server.env`，`--env-file` 启动；客户端 `trace-login --url … --token` 把密钥存进本机凭证文件，投递器、Recorder、MCP、trace-project 一起读——不必再在每个 shell `export TRACE_TOKEN`、也不必填插件的 `token`。可选 `TRACE_ALLOWED_NETWORKS` 按网段放行（可信代理后读 `X-Forwarded-For`）。未认证的 `/api/health` 只回最小信息。`/api/auth/config` 带 `mode: open|token|oauth`。
- **网络路径（服务端在远端）。** 投递器、MCP、Recorder 三个 HTTP 客户端共用一个拒绝重定向的 opener：`http://` 被 301 到 https 时以前 POST 变 GET、只见 405，现在直接报出重定向与正确做法，文件留在 pending。投递器 401 提示点名 `TRACE_TOKEN/--token`。代码取证 zip 的上传超时按体积放大（100 MiB 约 7 分钟，`--timeout` 是下限）。`trace-server --public-url` 之前是死参数，现已接入；对网络监听而读没锁时启动打印醒目警告。
- 插件的 `python` 默认是裸 `python3`，它多半不是 `pip install research-trace` 进去的解释器：以前 MCP 服务在 stdio 握手里留一条 `ModuleNotFoundError: mcp` traceback，Claude Code 只显示 `CONNECTION_CLOSED`，而 `trace_mcp.py --selfcheck` 根本不 import SDK 所以照样"通过"。现在两条路径都先检查 SDK 可导入，不行就打印一行可操作的提示并退出 2。

## 包、格式与代码风格

- 版本只剩一个来源（`research_trace/__init__.py`，pyproject 用 dynamic version 读它，server/mcp 引用 `PLUGIN_VERSION`），两份插件清单的 semver 写法由测试守着；pyproject 补齐 license/authors/classifiers/urls，extras 收成 `server` / `integrations` / `dev`，根目录三个入口 shim 不再打进 wheel；统一用 ruff（行宽 120）排版与检查，全仓库按它格式化。
- 新增 [docs/FORMATS.md](docs/FORMATS.md)：磁盘/网络上每一种格式标识、写入方、读取方、位置与兼容规则，安装后的目录布局，数值版本与迁移，环境变量；`tests/test_formats_are_documented.py` 守着代码里的每个 `research-trace.*.vN` 都在表里。
- README 从 334 行精简到约 190 行：只保留「一轮对话之后发生了什么」、三条边界、三层安装、日常使用和文档索引；独立 Recorder 的运行边界表、调用顺序和 Claude-Mem 逐文件借鉴表移到 `docs/RECORDER_REUSE_PLAN.md`。
- README / QUICKSTART 的安装说明改写成"中央服务 / Claude Code 插件 / 客户端包"三层，并给出 `claude plugin install --config python=… --config url=…` 的一步到位写法；`skills/research-trace/SKILL.md` 删除已不存在的 Stop block decision / fork 派发说明；`docs/RECORDER_DESIGN.md`、`docs/TODO.md` 标明 alpha.26 的派发上限已随派发机制删除；`docs/DESIGN.md` 不再把 GitHub 当灾备。

## 验证工具（见 scripts/README.md）

- `tests/test_server.py` 的 vendored JS 路径不再写死某台 Windows 机器的绝对路径（此前 9 个 Node 断言在任何其它机器上都 MODULE_NOT_FOUND）；接受 Node 20+ 的 `ℹ fail 0` 摘要；新增真调 CLI `--help` 的参数契约测试、hook 无网络 import 守卫。
- `scripts/simulate_research.py`：三天真实研究情境（真 hook、真投递、真中央、真 `claude --print`，合成研究内容），20 项评分；与 pytest、运维 battery 连跑 8 轮（64 次 sonnet 调用）管线零失败，第 4–8 轮连续全绿，记录数 / parent / 摘要次数跨轮稳定。
- `scripts/ops_battery.py`：运维角度 41 项（真 CLI、真 hook 进程、真中央；Recorder 失败路径经 `scripts/fake_claude.py` 走真子进程，不花额度；`REAL_WATCH=1` 加一次真 watcher 进程）。
- `scripts/net_battery.py`：服务端在远端的网络路径 15 项，在真 TLS（自签证书、非回环地址）上验证证书校验、`SSL_CERT_FILE` 私有 CA、错 token、重定向、代理、13 MB 分批、Recorder 与 selfcheck 走 TLS、暴露警告。

# 2.0.0a28 — Hook 与独立 Recorder 生命周期彻底分离

- 修正方案 3 的触发边界：Hook 只保存事件、transcript 增量和语义 batch，不启动、唤醒或管理模型进程。
- `trace-recorder --watch` 改为单独启动的长期消费者；空队列时继续等待，之后自动消费新增 batch。停止期间积压留在 outbox。
- `trace-project recorder-enable` 只写项目开关并明确提示单独启动 watcher，不隐式产生模型调用。
- README、快速开始、需求和协议明确区分持久采集与独立 AI 整理，不再把 Hook 的 fire-and-forget 与方案 3 混为一谈。

# 2.0.0a27 — 独立订阅 Recorder（方案 3）

- Stop 只原子落盘、生成语义 batch 并分离启动 `trace-recorder`；删除主 agent 的 fork、Agent/SendMessage、阻塞和复用窗口运行路径。
- 独立 Claude Code CLI 仅接收新增证据、精简项目背景、人工纠正、近期及相关旧记录；无工具、无 MCP、无项目设置，每项目复用 12 批后轮换的独立会话。
- 适配固定 Claude-Mem 源码的观察者提示结构、输出分类、额度事件和 hardened observer 边界；保留 Apache-2.0 LICENSE、NOTICE、固定提交与修改说明。CLI 启动方式参考 Entire 的隔离调用路径。
- 使用官方 `--json-schema` 输出；程序严格校验来源、Chapter、parent、run 和摘要版本，先保存计划，再用 `semantic:<batch>:<index>` 幂等写 Node。Overview/Chapter curation 用乐观版本，并能识别“中央已写、响应丢失”的重试。
- 调用前拒绝 API key、Bedrock、Vertex、Foundry 和非订阅认证；不配置 fallback。启用需确认账户 Extra usage 已关闭。quota 按 reset 时间等待，overage 自动重试永久停止，材料不删除。
- 本机及中央健康状态新增 Recorder 状态、暂停时间、最近处理、错误和真实 input/output/cache token 计数；现有 Web 增加状态显示。
- 新增独立 CLI、部分失败恢复、成功零记录、格式校验、quota/overage、会话轮换和无感 Stop 测试。测试未调用真实模型；UF 订阅、长会话和科学记录质量仍列为 P1。

# 2.0.0a26 — 限制 Recorder 异步反馈循环

- 修复缺少 agent_id 的 Recorder 读取批次/协议时被当成新研究材料的问题；已退休 Recorder 的迟到事件也按内部活动过滤。
- 同一次主用户请求内，每个批次最多派发一次，总共最多派发三次。后台完成、普通 Stop、会话生命周期和 Recorder 提示不会重置预算。
- 达到上限后不再唤起主 agent；未处理批次保持排队，原始采集和投递继续，新用户输入后恢复自动派发。
- 采集错误和暂停诊断保留在原始记录中，但不单独触发模型整理。修正 Stop 输出兼容性的旧注释。
- 使用模拟 hook 序列验证重复 Stop、身份缺失、迟到事件和暂停后恢复；未在 UF 的真实 Claude Code 会话中验证。

# 2.0.0a25 — 借鉴 Claude-Mem 的研究记忆提示词

- 对照固定版本的 Claude-Mem 提示词，重写 Recorder：独立可理解的事实、连贯的研究解释、与已有记忆比较、阶段摘要和按需读取。
- 保留未探索方向、失败与负结果、一组实验一个研究节点，以及来源 ID、人工纠正和被动取证边界。
- 不要求每轮生成摘要，不照搬软件类型枚举或所有文件清单；缩短与整理无关的协议说明。
- MCP 字段说明同步上述规则；复用 Recorder 时明确要求读取新增事件和对话，避免只凭旧上下文总结。
- 这是现有 Recorder 的提示词与派发指导改进；没有安装 Claude-Mem worker、替换存储或实现新的缓存机制。
- 来源映射和语义验收样例见 [Recorder 借鉴说明](docs/RECORDER_DESIGN.md)。

# 2.0.0a24 — 记录系统回归被动取证

- 按用户明确的职责边界，删除 Submitit 依赖与调度器，以及准备/执行/提交/轮询/重跑和运行目录复制代码。
- 移除 trace-run 执行入口，trace-code 只启用、保存和查看代码证据。
- Agent 使用原有 sbatch 并负责保持实验原目录和公共代码不变；hook 不再要求使用专门运行封装。
- 普通工具命令、输出、对话、Entire/Git 阶段证据继续通过原 outbox 保存，研究节点以来源 ID 关联。
- 保留旧 run、W&B 链接、日志、代码附件与节点的读取/投递兼容性。
- 以下 alpha.22/23 的执行管理说明仅为版本历史，已被本次边界调整撤回。

# 2.0.0a23 — 上游接管会话、阶段快照和 Slurm

- 直接使用 Submitit 的 SlurmExecutor / SlurmJob，删除自制提交脚本、sbatch 作业号解析和 sacct 文本解析。
- Entire 的 live session export 接管已配置项目的主会话来源；可记录没有代码改动的讨论和未探索方向。
- 复用 Entire 未提交/已提交 checkpoint，逐文件校验代码，固定 ref 并保存不含会话元数据的代码 ZIP。
- 训练启动前优先使用匹配的上游版本；当前代码更新后才进行必要的 Git 执行冻结，并明确记录原因。
- Entire 阶段捕获有短暂并发等待和明确缺口；失败的会话导出不推进游标、不发布半次导出的内容。
- 加入真实 Entire CLI 与 Submitit 序列化/执行验收；Slurm 命令边界模拟，尚未提交 UF 作业。
- 保持原有 Web、人工修订、W&B 曲线外链与新记录起点；GitHub 每日备份继续移除。

# 2.0.0a22 — 组件替换与实际运行记录

- 官方 MCP SDK、markdown-it、Dagre、SQLAlchemy/Alembic、Authlib 接管相应通用实现。
- 新增 trace-run：Entire/Git 捕获、共享代码冻结、独立本地/Slurm 运行、状态投递、W&B 外链、归档复跑。
- 节点可关联一组实验，展开代码与运行证据；原始来源按 ID 精确查询。
- 保留原有 Web 和人工修订规则，允许未实施想法和未知关系。
- GitHub 每日备份完全移出服务；旧自动备份环境设置不再生效。本地手动导出/恢复保留。
- 具体配置、验证范围和 UF 尚待验证部分见 [运行集成指南](docs/RUNTIME_INTEGRATION.md)。

以下为旧版本历史，不代表当前定时备份行为。

# 变更记录

版本号同时出现在六个地方（`pyproject.toml`、`research_trace/__init__.py`、
`research_trace/server.py`、`research_trace/mcp.py`、两个 `.claude-plugin/*.json`），
有测试守它们一致。**改动插件包里的东西之后必须 bump**，否则已安装的机器拿不到。

## 2.0.0-alpha.21

- 增加 Entire CLI 的只读证据适配器与隔离验证脚本：真实 Git/checkpoint 关联、公共代码版本固定、恢复后重跑，以及记录/修订/附件恢复。会话与研究摘要使用合成材料，尚未替换生产采集或接入 UF/SLURM。
- 可选 MLflow 官方 SDK 集成：将已绑定 experiment 的 run / trace 保存为不可变原始事件和 Node 证据附件；重复导入去重，内容变化保存新快照，过滤结构化隐藏推理字段。
- 可选 Basic Memory 官方 MCP 集成：增量同步 Overview / Chapter / Node、评论和证据引用，用 hybrid 检索召回；结果重新解析为中央当前版本，排除已 purge 内容。知识服务故障时回退本地关键词检索。
- 复用 Basic Memory MCP 连接，让写入后的后台向量任务有机会完成；两个框架均默认关闭，项目绑定由管理员配置，现有 hooks / 投递器不增加依赖或联网步骤。
- “附件 / 产物”增加 MLflow 导入；状态面板增加索引同步状态与重试入口，搜索支持 Chapter 定位、索引滞后和故障回退提示。
- 同步 Recorder 文档与现有 fresh 默认行为；提供集成需求映射、部署说明、真实框架模拟数据验证脚本及隔离/去重/人工修订保护测试。

## 2.0.0-alpha.20

- **Git 备份改成默认关闭。** 之前是反的：不配备份目的地就拒绝启动。
  当时的理由现在依然成立（唯一副本在一块盘上，通常要到盘坏了才被发现），但那不足以让它
  **默认开** —— 备份是把**完整原始 transcript** 推进一个 git remote，那是一件外向的、
  推上去就不完全在本地掌控之内的事，必须有人明确要求，不能因为「对你好」就自己跑起来。
- 现在：什么都不配 = 不备份、正常启动、在 stderr 上说一句代价和开法；
  `--backup-repo`（或 `TRACE_BACKUP_REPO`）= 开；`--no-backup` 的含义从
  「豁免那道关卡」变成「我知道没有备份，别再提醒」。
- 提醒里同时给出代价、开法和关掉提醒的办法 —— 只说代价不给出路的话，人只会去搜怎么把它
  静音，那就什么都没传达到。
- README / QUICKSTART 跟着改；`tests/test_backup_is_not_optional.py` 换成
  `tests/test_backup_is_opt_in.py`，其中一条专门盯着那道已撤掉的关卡别再回来。

**升级注意**：已经配了 `TRACE_BACKUP_REPO` 的部署行为完全不变，照常备份照常推送。

## 2.0.0-alpha.19

- **环的真正成因找到了：`agent_id` 在工具事件上根本不存在。** 现场数据库统计：
  `SubagentStop` 上 agent_id 有 110/110，而 `PreToolUse`/`PostToolUse`/`Stop`/
  `UserPromptSubmit` 上 **0/195**，`SubagentStart` 这个类型一条都没有过。
  而 `is_recorder` 和 `_recorder_tool_guard` 都靠 `payload["agent_id"] == recorder_id`
  判断 —— 于是 Recorder 自己调 `trace_attach` 时判不出来，那次调用被当成主 agent 的普通
  事件写进事件层；事件就是素材，素材就开新批，新批再派一个 Recorder。
  现场对得上：05:24–05:26 泄漏 8 次 `trace_attach`，attachments 恰好 276 → 284，
  修订记录里 actor 是 `recorder`。
- alpha.18 的 `_has_material` 拦不住它：`trace_attach` 是 `PreToolUse`，不是生命周期事件。
- **改法：Research Trace 自己的 MCP 调用一律不进事件层，且不看 agent_id、按工具名判。**
  记录系统在运转，不等于研究在推进。代价是主 agent 手动调 `trace_*` 时也不落事件 ——
  可以接受，那同样是记录系统在运转。
- 同一个洞还有第二个后果：`_recorder_tool_guard` 在这类宿主上**从未生效过**，
  Recorder 的只读边界形同虚设（它其实能用 Bash/Edit）。本版先断环；
  身份判定不依赖 agent_id 的加固另开一版。

## 2.0.0-alpha.18

- **恒为 0 产出的批次不再存在。** 现场报告：批次每两分钟出一个，内容全是 recorder
  plumbing，Recorder 每次正确地记下 0 条 —— 但**判断「没东西可记」本身就要先付一次完整
  fork**（首轮读入约 60 万 token）。一轮多烧七十多万 token，换一个必然为空的结论。
- 判据从 `events or chunks` 改成 `_has_material(events)`：**transcript 长度不能当判据**。
  Recorder 自己的回合就写在同一个 transcript 文件里，chunk 照样变长，而 scrub 只按
  `agentId` 精确匹配丢行 —— 漏一行就够开一批。于是「Recorder 跑完 → transcript 变长 →
  新 chunk → 开批 → 再派一次 Recorder」自己转起来。
- 改看**事件**：用户说话（`UserPromptSubmit`）、调工具（`Pre/PostToolUse`）、子 agent
  跑完（`SubagentStart/Stop`）都写事件；而 Recorder 那一段被 `_is_trace_orchestration`
  挡在事件层之外，一个事件都不写。所以「只剩生命周期事件」和「这段时间里只有 Recorder
  在动」是同一件事。跳过时**不推进游标**，那些事件留到下一批一起带上，一条都不丢；
  原始投递本来就不经过 batch，完全不受影响。
- 读不出来的事件一律当作有内容：分不清的时候多派一次，比静默漏记便宜得多。

## 2.0.0-alpha.17

- **给 Node 正文定了一个框架**：先回答三个问题，再写细节 —— **结论**（一到三句，能单独
  读懂的那个断言）、**依据**（凭什么这么说；没直接观察到的必须写明是推测还是假设）、
  **影响**（什么定了、什么还开着、推翻了此前哪一条，点名）。细节让记录**可复现**，
  这三条让记录**找得到、信得过**，后者不像细节那样可选。标题同理：写结果不写动作。
- **`trace_record` 的 schema 终于有说明了。** `parent_id` 此前是光秃秃一个
  `{"type": "string"}`，唯一的指导是协议文档里那句 “Set parent_id **only for** an actual
  continuation” —— 写成了限制而不是要求，还夹在讲 Chapter 的段落末尾。**协议文档在 fork
  时读一次，schema 在每次调用时都在眼前**，说明放错了地方。现在 `parent_id` / `body` /
  `title` / `source_event_ids` 各自说清了漏掉它的**后果**，并告诉模型去 `recent_nodes`
  拿候选 id。根节点仍然合法：写成「必须填」只会换来一堆胡乱认的父亲。
- **写完当场回执。** `trace_record` 的响应里多了 `structure_gaps`，点名这一条漏了什么。
  它是回执不是校验 —— 写入照样成功；一条 400 只会让 Recorder 去猜怎么讨好接口，而它该做的
  是判断这次到底有没有延续关系。`trace_context` 同时给出全项目的 `structure`（几条记录、
  几条连着父亲、几条带 source_event_ids、登记了几个 artifact），在写下一条**之前**看到。
  和数据流里的 `unkeyed` / `unlabeled_direction` 是同一句话：**视图会数出你漏掉的**。
- 人写的根节点不触发回执：那是一个决定，不是遗漏。

这一版起因于一个真实项目：14 条记录，正文里输入输出表格、绝对路径、体积全写全了，而
`parent_id` 全空、artifact 一个没登记、`source_event_ids` 只有 3 条。信息全在，
但没有一样落在系统能用的字段里 —— 结构图是 14 个孤儿，数据流视图整个不出现。
**把路径写进 markdown 表格不等于登记了它。**

## 2.0.0-alpha.16

- **结构图终于是一张图了。** alpha.15 把它做成「窄轨道 + 标题行」，而 v2 的**记录列表
  本来就长这样** —— 两个视图并排放着一模一样，切换按钮等于没有。根子在我读错了 v1：
  v1 的 `#rails` 是**列表左边的装订线**，真正的图是另一套 `#dedges` + `.card`，
  SVG 画边、HTML 卡片叠在上面。现在按后者重做。
- **布局换成 v1 的 Reingold–Tilford**（`trace_core.compute_tree` 的等价移植，
  常量照搬 176 宽、同层 20、层间 38、树间 56）。此前那版「每片叶子占一个新列」
  的列号全局只增不减，9 片叶子排成 2300px；RT 的下一个空位是**按层**记的，
  同一批数据只排到 10 个横向位置、总宽 1240px，一屏放得下整棵树。
- **加了缩放**（按钮 / ⌘+滚轮 / 适宽），默认按栏宽自动适配，只缩不放。
  没有它，一棵 14 层的树在 900px 视口里要滚三屏，而「一眼看出形状」正是这张图的
  全部理由 —— 滚着看等于回到列表。
- 卡片高度取 68 而不是 v1 的 58：58 装不下「一行元信息 + 两行标题」，第二行会被
  从中间切开。卡片里只放标题开头两行（完整的一条在右边详情里，鼠标停上去也有），
  图管形状，不管全文。
- **修一个 alpha.14 带进来的回归：数据流视图的卡片没有尺寸。** 那次改动删掉了
  `.graph-node` 的基础样式块（含 position/width/height），结构图因为改成内联尺寸没事，
  数据流却还在等 CSS 给它宽高。两张图现在都把宽高写在自己身上，由各自的布局说了算。
- **新增两条测试**：整段内联脚本必须能被 `node --check` 解析（此前所有前端测试都只
  切一小段函数出来跑，切下来那段自己闭合，上下文括号不平也照样全绿）；以及两张图的
  卡片都必须带着布局算出的尺寸。两条都验过：把对应的东西弄坏，它们会红。

## 2.0.0-alpha.15

- **结构图落到 v1 的真实形态：窄轨道 + 完整标题。** alpha.14 把节点缩成小圆点，
  形状是紧凑了，但标题全没了 —— 那不是 v1 的样子，v1 是「14px 的轨道槽在左，标题在右边的
  行里」。中间还试过「节点里放完整标题」：标题中位 50 字、p90 64 字（中文），要 240px 列宽，
  九列排出 2348px，而左栏只有 540px，屏幕上一个节点都看不见。**这两件事在一个窄栏里
  无法兼得，只有把标题挪出图外才成立。**
- **状态用 v1 的三色语言**（`#2f7d4f` 绿 / `#ab7716` 琥珀 / `#a8453a` 红），
  同时给颜色和线型：已确认 = 实线实心，未确认 = 虚线空心，已纠正 / 有待处理 = 点线空心。
  颜色和线型都给，是为了打印成黑白或看不清颜色时依然分得出来 —— v1 也是这么做的。
  轨道的点、边和序号徽章共用这一套。
- **标题不再被截断**：此前 `line-clamp: 2` 把中位 50 字的标题切成两行，
  这才是「文字显示不完整」的原因。行内三行覆盖到最长的 75 字，实测 27 条零截断。
- 结构图按 parent 深度优先排（孩子紧跟父亲），记录列表仍按时间排 —— 两个视图分工不同。

## 2.0.0-alpha.14

- **结构图改回 v1 那种点式轨道图。** 节点从 156×52 的卡片变成一个小圆点加序号，标题移到
  悬停浮层里。27 条记录的画布从 1904×1974 降到 **360×492** —— 整棵树一屏看完，分叉一眼可见。
  中间试过「点 + 内嵌标题」，但标题需要宽度会把列距撑到 210px，27 个节点排出 2042px 宽，
  反而更难看全；v1 也是把标题放在图外的（14px 的轨道槽在左，标题在右边的行列表里）。
  所以分工是：**结构图看形状，记录列表看标题**。点的填充沿用 v1 的语汇：实心 = 已确认，
  空心 = 未确认，红边 = 有待处理的纠正。数据流图共用同一套。

## 2.0.0-alpha.13

- **原始历史里不再显示 Research Trace 自己的调度记录。** 采集那一侧已经不采 Recorder 名下的
  行，但那只对新数据生效；库里存着的旧数据仍带着「I'll process research-trace batch … per the
  recorder protocol」这类回合，对读者毫无意义。显示这一侧也挡一道，只匹配本系统自己产生的
  字符串（`[research-trace-recorder]` / `[research-trace-batch` / 派发指令的首句），
  不去猜别人的措辞。整段都是调度记录时明说「已隐去」，而不是显示成「没有可读的对话」——
  后者会让人以为这一段是空的。
- **修时间线圆点压住文字。** `padding` 简写写在 `padding-left` 之后，把它重置成了 0。
  内边距现在只在一处定义。

## 2.0.0-alpha.12

- **采集量减半（45.7%），全部无损。** 一份 130 MB 的真实采集实测降到 71.0 MB。两块：
  - **不再采集编辑器/会话的运行期状态**（`file-history-snapshot`、`queue-operation`、
    `bridge-session`、`custom-title`、`mode`）—— 其中 `file-history-snapshot` 一项就占
    31.7 MB，内容是 343 个文件路径 × 一个 `backupFileName: null` 的空引用，指向一个
    根本不会上传的本机备份库；
  - **剥掉 `toolUseResult.file.base64`** —— 读图片时它和 `message.content` 里的图片块是
    同一份字节，实测 50 行、26.2 MB、**100% 有副本**，占整份采集的 20%。换成一条记录
    长度和 sha256 的占位，不直接删：「这里曾经有一张多大的图」本身也是溯源信息。
    只剥这一层，`structuredPatch` / `filePath` / `numLines` 原样保留 —— 那是「这次编辑
    改了什么」的证据。
  匹配不上时一律保留，失败方向故意选「多存噪声」而不是「误删内容」。
  **与提示缓存无关**：缓存在 API 侧按 prompt 前缀算，跟这里抄多少字节无关。
  顺带记一个查过但**不值得做**的：tool_result 按内容 hash 去重只能省 0.7%（31.3→31.0 MB）。

- **原始历史看得懂了。** 此前每一条都是 `JSON.stringify(payload)` 倒进 `<pre>`：东西确实
  都存下来了，但没人翻得动。现在按类型抽出真正承载信息的那个字段做摘要 —— 你说的话、
  跑的命令、命令的输出（带耗时）、这一轮的回答、哪个子任务结束了 —— 排成一条带类型色点
  的时间线，原始 JSON 收进 `<details>`，逐字核对时它一直在。
- **「对话原文」真的渲染成对话了。** 那一段此前显示的是 `search_text[:1000]`，一段按字符
  硬截断的 JSONL，连一个完整 JSON 行都不保证有（实测前 1000 字里换行数为 0）。解析改到
  服务端做（`Store._transcript_turns`），按角色渲染；Claude Code 里工具输出是 user 角色，
  所以单独标成「工具」，否则整页都是「你说：（工具输出）」。子 agent 的回合压低一档显示。
  解析不出对话时说明情况，**不再退回原始 JSON**。

## 2.0.0-alpha.11

- **修 alpha.9 引入的一个会让采集全停的故障。** 那一版把 `recorder_fork_window` 做成插件
  配置项并在 `hooks.json` 里引用 `${user_config.recorder_fork_window}`。这个展开发生在 hook
  启动**之前**，**未设置的选项会让整个 hook 执行失败** —— 不是降级、不是取默认值，是采集
  直接停掉；plugin.json 里写 `default` 也不算数，因为升级上来的机器 settings 里根本没有这个键。
  现场报错：`SessionEnd hook [...] failed: Plugin option "recorder_fork_window" isn't set.`
  改成放项目 marker（`.research-trace.json` 的 `recorder_fork_window`），hook 本来就要读它，
  缺键即默认值。并加一条守卫：`hooks.json` 只允许引用一个冻结的选项名单。
## 2.0.0-alpha.10

- **修一个自我维持的反馈环。** Recorder fork 的回合写在同一个 transcript 文件里，而
  transcript 采集**跑在** `_is_trace_orchestration` 之前 —— 事件层挡住了 Recorder，
  transcript 层照单全收。于是「recorder 跑完 → transcript 变长 → 新 chunk →
  `events or chunks` 成立 → 新 batch → 再派一个 fork」自己转起来：实测连转 8 圈，
  每圈约 70 万 token，产出恒为 `0 nodes (recorder plumbing only)`。
  现在 Recorder 名下的 transcript 行整行丢弃，和事件层同一条规矩。环在源头断掉 ——
  没有 chunk 就没有 batch。

## 2.0.0-alpha.9

- **Recorder 重新 fork 的间隔可配**（插件配置 `recorder_fork_window`，默认 1 = 每批）。
  依据是真实数据：一次 fork 首轮读入约 60 万 token（缓存命中率 99.7–99.9%），而很多批次
  的全部内容就是「某个子 agent 结束了」—— 一份样本里 137 个事件中 `SubagentStop` 占 56 个。
  为这种批次付一次完整 fork 不划算。`0` 等价于当时的旧复用环境开关。

## 2.0.0-alpha.8

- **结构图密度**：卡片 184×88 → 156×52，纵向步距 142px → 78px。此前 27 条记录的画布
  高 1974px，而它住在一个 ~640px 宽的栏里，一屏只看得到四个节点 —— 那就不是图了。
- **记录列表加了树形装订线**：左侧一条 git-graph 式的 rail，一条链一条车道，分叉时
  另开一条，走完就让出去。此前结构只体现在「延续记录 07」这种文字里，要人一边读一边
  在脑子里拼出树形。

## 2.0.0-alpha.7

- README 从 383 行精简到 188 行，中间补一段外行也能看懂的机制讲解（hook / 投递器 /
  Recorder / 中央服务各管什么）。设计理念移到 `docs/DESIGN.md`，没有删。
- 新增本文件，以及文档断链守卫。纯文档版本，没有行为变化。

## 2.0.0-alpha.6

- **Recorder 每一批都重新 fork**，拿当下的完整上下文。此前只有第一批享受到 fork 的
  好处：后续批次通过 `SendMessage` 只收到一个 manifest 路径，手里是 fork 那一刻的
  陈旧快照。重 fork 的前缀与主 agent 一致，本来就该命中提示缓存。
  当时也可用旧复用环境开关退回旧行为。
- 退休过的 Recorder agent id 不会被后到的 `PostToolUse` 复活 —— 否则下一批会被发给
  一个已经停掉的 Recorder。

## 2.0.0-alpha.5

- **不配备份目的地就拒绝启动**（`--backup-repo` / `TRACE_BACKUP_REPO`）。放弃备份要
  显式 `--no-backup`，并会打印醒目警告。
- **备份仓库可以有别的写入者**：push 前先 `fetch` 并把本轮备份 commit rebase 到远端之上。
  这让「记录与项目代码同仓」成立 —— 此前别人往同一分支推一次代码，备份就永久推不上去了。
- **正文按 markdown 渲染**。v2 重写时渲染器随 `web/` 一起被删，`##` 和表格一直以纯文本显示。
- **投递失败会留下痕迹**：`delivery-status.json` 在失败时也写，`--status` 因此能区分
  「从没启动过」和「一启动就死」。投递触发从 `SessionStart`/`SessionEnd` 扩到也含 `Stop`。
- **显式 token 会静默盖住设备凭证**这件事现在会被说出来：`trace-login` 登录成功即警告，
  `trace-project status` 直说哪份凭据在生效。
- 写给主 agent 的规则从 `RECORDER_PROTOCOL.md`（只有 Recorder 会读）移到它读得到的地方。
- 补上 v2 缺失的 `skills/research-trace/SKILL.md`。

## 2.0.0-alpha.4

- **服务可以挂在一个路径前缀下**（`--base-path`）。服务自己拥有整个前缀，前缀之外一律 404，
  所以绕过反向代理直连端口同样进不去；会话 cookie 也收敛到该前缀。
- `plugin.json` 不再声明 `hooks/hooks.json` —— 标准路径是自动加载的，重复声明会让插件
  整体加载失败。
- `test_backup` 不再假设节点的创建顺序（排序键在同一毫秒内由随机 id 决定，此前会随机失败）。
