# 集成 MLflow 与 Basic Memory

Research Trace 保存团队研究记录的权威版本。MLflow 提供实验参数、指标和会话调用证据；Basic Memory 提供可重建的知识索引。二者均为可选集成，未配置时不加载相关 SDK，也不连接外部服务。

本文描述 alpha.21 的可选适配器。alpha.22 的基础组件替换、Entire/Git、Slurm 和 W&B 运行流程见
[运行集成指南](RUNTIME_INTEGRATION.md)；它们不要求启用 MLflow 或 Basic Memory。

## 安装与绑定

在中央服务环境安装 `pip install -e '.[server,integrations]'`。客户端 `trace-mcp` 和 hooks 仍可使用原有轻量环境。部署中央服务时使用单 worker，与原 SQLite 服务的进程内任务模型保持一致。

Basic Memory 单独安装、单独管理项目，避免其 MCP 服务端依赖与客户端版本冲突。本轮实际验证：Python 3.12、MLflow Skinny 3.15.2、MCP 客户端 1.29.1、Basic Memory 0.23.2。Basic Memory 0.23.2 的 FastMCP 依赖包含预发布版本，测试安装使用 `pip install --pre 'basic-memory==0.23.2'`，因此不建议将它混入生产应用环境。正式部署应固定完整依赖或使用独立服务镜像。

用 Basic Memory 的 `basic-memory project add research-index /srv/research-index --local` 创建专用索引项目。将 [配置示例](../examples/integrations.example.json) 复制为不入库的 `integrations.local.json`，替换：

- `command`：Basic Memory 可执行文件绝对路径；Windows 示例 `C:/.../.venv-basic-memory/Scripts/basic-memory.exe`。
- `env.BASIC_MEMORY_CONFIG_DIR`：Basic Memory 实际配置与索引数据库目录。
- `projects` 的 key：Research Trace 的稳定项目 ID。
- `memory_project`：Basic Memory 中已经存在的专用项目名。
- `mlflow_experiment_ids`：允许该项目导入的 MLflow experiment ID 字符串列表。

也可以将 `basic_memory` 配为 `{"url":"https://your-memory-service/mcp","token_env":"BM_TOKEN","timeout_seconds":30}`，通过环境变量提供凭证。`url` 和 `command` 二选一。MLflow 认证使用其官方 SDK 支持的环境变量。接口请求不能临时指定任意后端地址。

启动中央服务前设置 `TRACE_INTEGRATIONS_CONFIG` 为配置文件路径。Linux 示例：

```bash
export TRACE_INTEGRATIONS_CONFIG=/srv/research-trace/integrations.local.json
trace-server --data-dir /srv/research-trace/data --host 127.0.0.1 --port 8765
```

PowerShell 示例：

```powershell
$env:TRACE_INTEGRATIONS_CONFIG = (Resolve-Path integrations.local.json).Path
trace-server --data-dir .trace-data --host 127.0.0.1 --port 8765
```

只需一个框架时删除另一个配置块即可。绑定修改后重启服务；不需要数据库迁移。停用集成不影响已导入证据或中央记录。

## 使用

**导入证据**：打开一条记录 → 附件 / 产物 → MLflow 实验证据 → 选择 Run / Trace 并填写 ID。仅已配置 MLflow experiment 绑定的项目显示该选项。Run 快照包含 SDK 返回的参数、最新指标和标签等；它不下载整个 artifact 目录，也不是全部指标历史。Trace 快照包含 SDK 返回的可见调用内容。结构化 `thinking`、`reasoning_content` 等隐藏字段会被剔除。

MLflow 证据是带来源的原始快照，不会自动成为结论，也不修改 Node 正文、版本或确认状态。同一来源同一内容重复导入不增加事件或附件；来源内容变化则追加新的快照。源端失效后，已保存的 JSON 附件仍可下载。

现有 `trace_attach` MCP 工具直接支持：

```json
{
  "project_id": "prj_...", "target_type": "node", "target_id": "node_...",
  "name": "对照实验", "integration": "mlflow", "external_kind": "run", "external_id": "..."
}
```

也可 POST `/api/integrations/mlflow/evidence`，请求体包含 `project_id`、`kind`、`external_id` 和可选 `node_id`；省略 Node 时仅存原始证据。写入身份与原 API 一致。

**知识检索**：在原搜索框或 `trace_search` 中提问。服务先执行本地搜索，再向 Basic Memory 请求 hybrid 召回；召回的 ID 必须匹配已同步的本项目记录，正文一律重新读取中央当前版本。人工编辑后即使索引尚未更新，也不会返回旧正文。索引已删除的记录不会再进入 Research Trace 搜索结果。

**同步状态**：顶部状态入口显示两个集成的状态、已同步数量和最近同步时间，可以手动触发同步。未成功的变更留待下轮扫描，默认每 60 秒重试。同步状态的“可用”表示文本同步已确认，不代表向量任务已经完成；向量由 Basic Memory 异步生成。首次下载模型时应增大 timeout，或先在 Basic Memory 环境预热模型。

## 存储、故障与维护

- 中央 SQLite、不可变事件和 CAS 附件仍是权威数据；Basic Memory 中 `research-trace/<project-id>/` 下的笔记是自动生成的副本。人工编辑请回到 Research Trace，副本修改会在下次源内容变化时被覆盖。
- `integration-index-state.json` 仅记已确认的 permalink 和内容指纹，可重建；不存凭证。备份仍由现有备份机制负责，默认关闭。
- Basic Memory 断线、超时或不兼容时，搜索返回本地结果并标记 fallback；中央写入和采集继续工作。进程连接在服务生命周期内复用，工具调用失败后下次请求重新连接。
- 同一后端上的解绑/项目更名会在后续同步删除原 managed notes。切换后端地址或配置目录后，原服务中的副本需要管理员单独清理。紧急 purge 后中央立即排除内容，远端副本在下一次成功同步删除；Basic Memory 自己的历史、备份和外部缓存不在此删除操作范围内。
- 进程被强行停止可能中断 Basic Memory 的后台向量任务。如其日志提示 embeddings EMPTY / missing，在独立 Basic Memory 环境运行 `basic-memory reindex --embeddings`。长期部署建议使用独立常驻的 Basic Memory 服务。模型和中文领域召回质量需用团队真实问题另行评估。
- 混合搜索的 `totals/total/omitted` 保留本地关键词计数兼容性，`retrieval.totals_scope` 明确这一范围；`returned` 为实际返回条数，`retrieval.candidate_omitted` 表示当前候选截断。不要把本地 total 当作向量检索的精确总量。

## 重跑真实集成验证

在 Research Trace 环境补装 `pip install -e '.[server,integrations,demo]'`，然后：

```bash
python scripts/framework_smoke.py --basic-memory /absolute/path/to/basic-memory --directory .integration-demo/new-run
```

脚本仅创建模拟数据：独立 MLflow SQLite、Basic Memory 项目、Research Trace 数据库和本地嵌入模型缓存。不会读取已有研究数据。每次使用新目录，避免覆盖上次结果。

脚本验证 run / trace 导入、重复附件去重、隐藏内容过滤、真实 MCP 写入与召回、无共同字面关键词的问句召回，以及人工修改后的当前正文返回。输出 `report.json` 和可用于演示服务的 `integrations.json`。`--keyword-only` 可跳过向量依赖，但该模式不会声称验证了语义召回。模拟数据上的成功不等于中文领域检索质量评测。

使用演示目录中的 `trace-data` 和 `integrations.json` 启动原 `trace-server` 即可打开网页检查；演示服务绑定本机，正式团队部署继续使用已有 OAuth/角色配置。

## 上游接口

- [MLflow Client API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html)
- [Basic Memory MCP tools](https://docs.basicmemory.com/reference/mcp-tools-reference)
- [Basic Memory semantic search](https://docs.basicmemory.com/concepts/semantic-search)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

通过 SDK / 协议组合使用上游，没有复制其实现源码；各框架按各自许可证独立分发。
