# 格式与版本清单

Research Trace 在磁盘和网络上写的每一种格式都在这里登记：标识、谁写、谁读、放在哪、
怎么兼容。改任何一种格式先改这张表；`tests/test_formats_are_documented.py` 守着代码里出现的
每个 `research-trace.*.vN` 标识都必须在这里有一行。

## 版本号

- **唯一来源**：`research_trace/__init__.py` 的 `__version__`，PEP 440 写法（`2.0.0a29`）。
  `pyproject.toml` 用 dynamic version 读它，`server.py` / `mcp.py` 引用 `PLUGIN_VERSION`。
- **插件清单**：`.claude-plugin/plugin.json` 与 `marketplace.json` 用 semver 写法（`2.0.0-alpha.29`），
  是同一个版本的另一种拼法，由 `tests/test_version_is_consistent.py` 守着。
- **什么时候必须 bump**：`hooks/`、`scripts/`、`skills/`、`.claude-plugin/`、根目录 `trace_mcp.py`
  任何改动。`claude plugin update` 只看版本号，版本不动已装机器一个字节都拿不到。

## 安装后的目录

| 位置 | 内容 | 谁写 |
|---|---|---|
| `${CLAUDE_PLUGIN_ROOT}` | 插件安装目录 = 本仓库的一份拷贝（`hooks/hooks.json`、`skills/`、`scripts/trace_hook.py`、`trace_mcp.py`、`research_trace/` 源码） | `claude plugin install` |
| `${CLAUDE_PLUGIN_DATA}` | 插件数据目录：`outbox/`（下表）、`recorder-workspace/`（Recorder 模型的 cwd） | hook、投递器、Recorder |
| `<data_dir>`（中央） | `trace.sqlite3`、`objects/`（内容寻址附件）、`team-project-map.json`、集成配置 | `trace-server` |
| `.git/research-trace/` | 代码取证快照（zip + 固定 ref） | `trace-code` / hook 阶段捕获 |

## 本机 outbox（`${CLAUDE_PLUGIN_DATA}/outbox/`）

```text
outbox/
├── delivery-status.json            最近一轮投递结果（trace-deliver --status / 健康遥测）
├── recorder-status.json            research-trace.recorder-state.v1
└── <workspace-hash>/<session>/
    ├── state.json                  游标（transcript_offsets、batched_through、paused_through）
    ├── pending/*.json              research-trace.event.v1，等待投递
    ├── sent/*.json                 中央已确认（2xx）的事件
    ├── transcripts/pending|sent/   <key>_<start>_<end>_<sha16>.jsonl 可见 transcript 增量
    ├── transcripts/meta/*.json     增量元数据（chunk_id = claude-transcript-<sha256>）
    ├── batches/<id>.json           research-trace.batch.v1，语义 batch 清单
    ├── batches/state/<id>.json     research-trace.recorder-batch-state.v1，处理 sidecar
    └── batches/done/               处理完的清单与 sidecar
```

## 格式标识

| 标识 | 写入方 | 读取方 | 位置 | 兼容规则 |
|---|---|---|---|---|
| `research-trace.project.v1` | `trace-project bind / disable / recorder-enable`，`trace_context bind_path` | hook（采集开关与身份）、投递器（项目归属）、Recorder（`recorder` 配置）、`trace-code`（`code_capture`） | 项目根 `.research-trace.json` | 字段只增不删；`"capture": false` 为显式排除；`workspace_key` 是身份，绝对路径不是 |
| `research-trace.event.v1` | hook `_write_event` | 投递器 → `POST /api/ingest`；Recorder `_manifest_payload` | `outbox/…/pending|sent/*.json` | `event_id` 不可变，中央按它去重；`thinking` 在写入前剥离 |
| `research-trace.batch.v1` | hook `_ensure_batch`（Stop/SessionEnd） | `trace-recorder` | `outbox/…/batches/<id>.json` | 引用的事件在 `pending/` 或 `sent/` 两边找；投递不改变取材 |
| `research-trace.recorder-state.v1` | `trace-recorder` | `--status`、投递器遥测、网页健康卡 | `outbox/recorder-status.json` | 只含计数、状态、认证方式与 token 统计，无会话 |
| `research-trace.recorder-batch-state.v1` | `trace-recorder` | `trace-recorder`（恢复）、`--retry-blocked` | `outbox/…/batches/state/<id>.json` → `done/<id>.state.json` | 计划先于首次写入持久化；`completed_records` / `completed_artifacts` / `completed_curations` 决定重放 |
| `research-trace.code.v1` | `trace-code snapshot`、hook 阶段捕获 `capture_phase` | 投递器 `run_evidence`、网页代码证据 | 事件 payload `code_snapshot`；`.git/research-trace/<ws>/snapshots/<sha>/code.zip` + `refs/research-trace/checkpoints/<sha>` | 不动 HEAD 与工作区；zip 上传前校验 SHA-256 |
| `research-trace.team-map.v1` | 管理员 REST 或手工编辑 | `POST /api/context` 第三种发现方式 | `<data_dir>/team-project-map.json` | 规则 glob 至少 4 个字面字符；变更有 history |
| `research-trace.integrations.v1` | 管理员 | 中央服务（MLflow / Basic Memory 适配） | 集成配置文件 | 未配置时零 import |

## 数值版本

- **数据库 schema 版本为 4**（`storage.SCHEMA_VERSION`），迁移由 Alembic 管理：`0001_baseline`、`0002_run_index`。
  身份以 `alembic_version` 表为准，`schema_meta.schema_version` 是历史遗留。
- **备份格式版本为 3**（`backup.FORMAT_VERSION`），读取端接受版本 2 与 3（`SUPPORTED_FORMAT_VERSIONS`）：
  写入端只写当前格式，读取端永不退役。导出树 `volumes/<年>/…` + `index.json`。

## 网络与身份

- HTTP 路由前缀 `/api/*`；设备凭证前缀 `rtd_`；写入身份只来自凭证（浏览器会话 = `human`，机器 = `recorder`）。
- 三档访问模式（`/api/auth/config` 的 `mode`）：`open`（读匿名）、`token`（`TRACE_PROTECT_READS=true`，访问密钥守读写，网页 `POST /api/auth/token-login` 换一个用密钥签名的无状态 cookie `tk1.<name>.<expiry>.<hmac>`）、`oauth`（GitHub）。本机凭证文件 `credentials.json` 里 `kind: "token"` 的条目由 `trace-login --token` 写入，所有客户端通用。
- Recorder 幂等键：Node `semantic:<batch_id>:<index>`；产物引用 `capture_key = semantic:<batch_id>:<index>:artifact:<j>`；
  摘要更新走 `expect_version` 乐观并发。
- Recorder 模型 I/O：固定 `SYSTEM_PROMPT` + `OUTPUT_SCHEMA`（JSON Schema，`--json-schema`）；输入 packet 三段
  `batch` / `existing_memory` / `new_evidence`。改提示词或 schema 不需要迁移，但要重跑 `scripts/simulate_research.py`。

## 环境变量

`TRACE_URL`、`TRACE_TOKEN`（内网档的访问密钥 / 旧部署；OAuth 设备登录时留空）、`TRACE_PROTECT_READS`（同一密钥守读）、`TRACE_ALLOWED_NETWORKS`（放行网段）、`TRACE_TRUST_PROXY_HEADERS`、`TRACE_DATA_DIR`（覆盖 `CLAUDE_PLUGIN_DATA`）、
`TRACE_CREDENTIAL_FILE`、`TRACE_RECORDER_CLAUDE`（Recorder 用的 `claude` 可执行文件）、
`TRACE_RETAIN_SENT_DAYS`、`TRACE_HOOK_NO_SPAWN`（测试用，禁止 hook 拉起投递器）。
Recorder 会拒绝 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` 与云提供商变量；
只保留 `CLAUDE_CODE_OAUTH_TOKEN`。
