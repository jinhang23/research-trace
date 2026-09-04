# Entire + Git 隔离验证

2026-09-03（本地时间）。结论：**Entire 可以承担已提交代码与 Claude Code 会话的关联；Git 可以保存并恢复模型、配置及外层公共代码。研究语义整理仍由 Research Trace 承担。** 当前交付是可重复执行的局部验证，未接入用户的 UF 项目。

实现位于 [只读证据适配器](../research_trace/entire_evidence.py) 和 [完整验证脚本](../scripts/entire_smoke.py)。页面沿用现有 Web，没有新增前端框架或改变目录开发方式。

## 实际使用的组件

- 官方 [Entire CLI v0.10.5](https://github.com/entireio/cli/releases/tag/v0.10.5)，Windows amd64。下载包 SHA256：`153d9edd4cf026a69d18b8a256adc72c306a7311fb3a3819ea8c1a620a5d93a6`，已与官方校验清单核对。
- Git 原生提交、`archive`、`bundle` 和 checkpoint refs；没有另造版本对象或差异存储。
- 现有 Research Trace 的原始历史、Node、附件、修订保护与备份恢复接口。

Entire 只在新建的合成 Git 仓库内启用：`--agent claude-code --no-init-repo --local --skip-push-sessions --telemetry=false --absolute-git-hook-path`。不导入历史、不创建远端、不推送会话，不修改全局 Claude 配置。合成 Git 身份使用 `test@example.invalid`。

调用的上游接口为实际安装版本的 `checkpoint explain <id> --json` 和 `--transcript --session-index <n>`。适配器要求完整 Git SHA，以及已在本地保存的 checkpoint ref；缺失时明确失败，不自动改取最新工作目录或联网补来源。

## 样例与结果

开发目录保持用户描述的形式：

```text
workspace/
├── common/features.py
├── model_a/train.py + config.json
└── model_b/train.py + config.json
```

先保存 A，此时公共代码 `SCALE=1`；修改公共代码为 2，再保存 B。随后把开发目录改为 999，才从两个已导出的代码目录启动本地程序。两个进程分别读取 1 和 2。它们是计算几个数字的标准库程序，没有真实蛋白质–配体训练意义。

| 验证内容 | 已观察到的结果 |
| --- | --- |
| Entire 绑定会话与代码 | 真实官方 hooks 为两次提交生成 checkpoint；可导出元数据和合成会话 |
| 外层公共代码变化 | A、B 分别使用保存时的公共代码和配置，开发目录后续变更未影响它们 |
| 代码与会话恢复 | 仅从保留的代码分支与 checkpoint refs 建立 Git bundle，在新仓库恢复，代码归档和会话内容一致 |
| 恢复代码后重跑 | 恢复后的 A 再执行，输出与原运行一致 |
| 研究语义恢复 | 使用现有备份接口恢复到空 Store，节点、人工修订及四个附件内容一致 |
| 一组运行的阅读体验 | 两次运行归入一条中文记录，下挂两个代码包和两份运行清单 |
| 未实施想法 | 单独保存有来源的想法节点，没有新代码提交，也没有编造 parent |
| 重放与人工修订 | 同一原始批次重放判重；旧 Recorder 版本无法覆盖较新的人工修订 |

最后一次完整验证保留在 `.integration-demo/entire-final/`：

- `report.json`：真实版本、commit、checkpoint、运行输出和验收范围。
- `commands.json`：这次合成试验的命令输出。
- `evidence.bundle`：代码历史与两个持久 checkpoint refs。
- `record-backup/`：研究记录、原始历史、修订和附件的现有格式备份。
- `restored/`、`restored-run/`、`restored-trace-data/`：从备份恢复并检查后的内容。
- `trace-data/`：Web 演示所用数据。

上述目录被 Git 忽略。完整脚本通过，新增适配器测试 13 项通过。Web 中已核对节点、代码片段和附件入口。

## 可以复现的命令

先从官方来源取得相同版本 Entire，并使用安装了本项目和服务依赖的 Python 环境。在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/entire_smoke.py --entire .integration-demo/tools/entire-v0.10.5/entire.exe --directory .integration-demo/entire-new-run
.\.venv\Scripts\python.exe -X utf8 -m research_trace.server --data-dir .integration-demo/entire-new-run/trace-data --host 127.0.0.1 --port 8767 --no-backup
```

每次选择新的 `--directory`；脚本拒绝覆盖已有运行。`--no-backup` 仅关闭服务后台远端备份；脚本已经在本地执行显式导出和恢复。

下载节点代码包并解压到新目录后，在解压根目录运行清单中的命令，例如 `python -B model_a/train.py --config model_a/config.json`。清单包含 Python 版本、配置对应的代码归档 SHA256 及实际输出。此样例只依赖 Python 标准库。

## 尚未证明的部分

1. **会话输入和摘要是预置测试材料。** Entire/Git 和本地程序实际执行；没有启动真实 Claude 模型，也没有测出 Recorder 的自动整理质量。两个预置节点只能验证存储与显示契约，不能证明自动去冗余或归组能力。
2. **Entire checkpoint 依赖提交。** 试验脚本显式提交 A/B；未提交想法由研究系统独立保存。还没有让真实 Claude 自动在合适时机留存版本，也未退役原来的采集、投递和 Recorder。
3. **运行目录是独立的代码副本。** 尚无不可变权限保证；不覆盖外部数据、环境、Git LFS 内容、子模块和仓库外依赖。归档受 Git `export-ignore` 等规则影响，不能将任意项目的归档直接等同于完整运行环境。当前适配器拒绝符号链接，限制代码包为 10 MiB；这不是 UF 大项目的最终存储方案。
4. **UF/SLURM 与真实 W&B run 尚未接入。** 运行清单中 job ID、W&B URL 为 null；没有伪造曲线链接。未验证集群排队后启动、作业数组、公共目录并发修改或多个 Agent 同时提交。
5. **过滤发生在导入研究系统时。** 已测试移除结构化隐藏内容；尚未证明 Entire 在第一次保存原始会话前就过滤。合成输入不含用户真实会话，生产启用需要先明确这个边界。
6. **这次恢复是本地完整性验证。** 记录备份暂沿用已有实现；没有接入 restic，没有验证异地灾备、跨版本恢复、断网投递或 GitButler 共存。备份中的代码对象需要实际保留，只有 SHA 不能代替副本。

恢复测试发现：把带冒号的来源键直接作为 transcript chunk ID，会导致现有备份导出在 Windows 上生成非法文件名。样例导入使用来源键的稳定 SHA256 作为 chunk ID，保留事件内的原始来源键。本次没有把此修正宣称为既有备份系统的全面修复。

## 下一步的接管范围

在 UF 的新建小项目中接入真实 Claude Code，先核实项目根目录覆盖公共代码，再验证“提交前留存、固定代码目录运行、job ID/W&B URL 回链、未执行想法自动整理”。只有自动采集、持久性及语义质量通过后，才删除相应的旧采集实现。Entire 的代码/会话关联不需要再由本项目重新实现；中文研究总结、人工纠正和未探索方向仍是需要保留的产品能力。
