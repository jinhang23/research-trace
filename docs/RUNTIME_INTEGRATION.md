# Claude Code / UF 被动记录

alpha.24 按用户确认的边界调整：**研究 Agent 负责实验，Research Trace 负责记录。**
保留原来的 Web。Submitit、任务提交/运行/重跑、执行目录复制、训练路径重写和 Slurm 轮询已删除。
GitHub 每日备份继续移除。

## 职责

研究 Agent 使用原有训练脚本与 `sbatch`，负责提交后保持实验原目录及其引用的公共代码不变。
后续探索如何安排目录、何时提交、是否重跑或取消，均由研究 Agent 决定。记录系统不会锁定文件、
创建运行副本、修改脚本或替 Agent 执行这些动作。

Claude hooks 保存实际观察到的命令、输出和对话；Entire 提供主会话与阶段代码 checkpoint。
原始 `sbatch` 命令和返回的 job ID 可以成为研究节点的来源，无需经过专门的提交接口。
结果、状态和 W&B 链接来自 Agent 实际读取到的材料；不会把提交成功当成训练完成。

代码归档只用于回看、核查和下载。它不改变原目录，也不声明自己强制保证了运行时版本。
每份归档保留采集时间、文件哈希、Git commit 和可用的 Entire 来源。外层公共代码包含在仓库级
取证范围内。研究节点可以汇总一组实验，也可记录尚未执行的想法；未知关系允许留空。

## 安装与启用

中央服务：

```bash
python -m pip install -e '.[server]'
trace-server --data-dir /path/to/trace-data --host 127.0.0.1 --port 8765
```

UF 上先加载 Conda，并安装 Python 包和更新后的 Claude 插件：

```bash
module load conda
conda activate <你的环境>
python -m pip install -e /path/to/research-trace
```

安装 [Entire CLI](https://docs.entire.io/cli/installation) 后，在整个项目的 Git 根目录绑定并启用：

```bash
trace-project bind /path/to/project --project-id prj_actual --url https://trace.example
trace-code --project /path/to/project init \
  --entire /absolute/path/to/entire \
  --outbox /path/to/claude-plugin-data --url https://trace.example
```

验收使用 Entire 0.10.5。省略 `--entire` 时自动使用 PATH 上的 Entire；未安装时保留兼容采集。
`--outbox` 与 Claude 插件数据目录保持一致。Entire 仅对当前仓库启用，会话 push 关闭。
重新打开 Claude 会话加载插件与 hooks；日常直接让 Agent 继续工作，不需要额外的运行命令。

首次启用记录当前基线并排除已有 checkpoint；已有 transcript 从当前边界继续。不会扫描一年的
历史实验。已有 alpha.22/23 配置升级后继续使用原始起点，不应重新初始化。历史补录只由用户请求。

手动查看代码证据可使用 `trace-code snapshot` / `trace-code list`，两者均不启动实验。
其中手动 snapshot 保存在本地；日常阶段证据通过 Claude hook 和独立投递器上传。
原 `trace-run` 执行入口退役，没有改成另一套提交封装。

## 整理和回看

Recorder 使用 `source_event_ids` 关联命令、结果与代码 checkpoint；`run_ids` 只用于已经存在的
历史/导入运行，不为普通 sbatch 伪造新 run。节点可展开来源与代码附件，长期来源按 ID 精确读取。
人工修订优先级不变。W&B 只保留实际曲线 URL，代码不上传到 W&B。

旧版已经保存的 run、代码 ZIP、日志、W&B 链接与节点关联继续可读；升级不删除这些材料。
从归档复现由研究 Agent 自行完成，记录系统不执行 replay。

## 保留方式与边界

代码证据存于 `.git/research-trace/` 并固定 Git ref；上传 ZIP 前校验 SHA256。记录系统不修改
HEAD 或用户暂存区。代码默认最多 100 MiB，大数据/权重用来源路径或已有 artifact 记录；
环境和外部数据不会自动封装为容器或另一个运行目录。符号链接/子模块暂不支持。

离线内容先进入本机 outbox，独立 `trace-deliver` 在中央确认后移动到 sent；不依赖 Recorder
是否生成了摘要。Entire 导出失败不推进游标。原生 hooks 并发时，阶段捕获短暂等待上游，仍未
就绪则保留采集缺口，不虚构对应版本。子 agent 的独立 transcript 暂保留兼容读取路径。
Entire 实时导出每次从头读取，长会话成本尚未在 UF 验证。

需要时手动导出和恢复记录：

```bash
trace-backup export --data-dir /path/to/trace-data --target /path/to/export
trace-backup verify --source /path/to/export
trace-backup restore --source /path/to/export --data-dir /path/to/new-empty-data
```

本地测试覆盖普通 sbatch hook 原文、代码取证不改动实验目录、代码上传/节点来源、旧 run 读取以及
真实 Entire 导出。Slurm 输出使用合成夹具，没有提交 UF 作业，也没有操作真实 W&B 账号。
此前 alpha.22/23 的运行隔离验收属于已经撤回的执行组件，不代表当前工作流。

复用组件和边界见[源码复用清单](SOURCE_REUSE.md)。
