# 变更记录

版本号同时出现在六个地方（`pyproject.toml`、`research_trace/__init__.py`、
`research_trace/server.py`、`research_trace/mcp.py`、两个 `.claude-plugin/*.json`），
有测试守它们一致。**改动插件包里的东西之后必须 bump**，否则已安装的机器拿不到。

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
  为这种批次付一次完整 fork 不划算。`0` 等价于旧的 `TRACE_RECORDER_REUSE=1`。

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
  `TRACE_RECORDER_REUSE=1` 可退回旧行为。
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
