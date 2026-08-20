# 变更记录

版本号同时出现在六个地方（`pyproject.toml`、`research_trace/__init__.py`、
`research_trace/server.py`、`research_trace/mcp.py`、两个 `.claude-plugin/*.json`），
有测试守它们一致。**改动插件包里的东西之后必须 bump**，否则已安装的机器拿不到。

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
