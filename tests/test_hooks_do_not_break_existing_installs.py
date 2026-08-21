"""hooks.json 引用的插件配置项，必须是老安装也一定有的那几个。

`${user_config.X}` 在 hook 启动前展开。**选项没设置时展开失败，整个 hook 执行失败** ——
不是降级、不是用默认值，是采集直接全停。plugin.json 里写了 `default` 也不算数：
那只影响安装时的取值，而升级上来的机器 settings 里根本没有这个键。

真实事故：2.0.0-alpha.9 往 hooks.json 里加了 `${user_config.recorder_fork_window}`，
所有升级上来又没手工配置的机器每次 hook 都失败：

    SessionEnd hook [...] failed: Plugin option "recorder_fork_window" isn't set.

所以新增的可配项要走**项目 marker**（hook 本来就要读它，缺键就用默认值），
或者环境变量 —— 不要走 hooks.json。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

#: 冻结的名单。往里加一项 = 让每一台已经装了插件的机器采集停摆，直到它手工配置。
#: 改这个集合之前，先确认新选项对**老安装**也一定有值。
SAFE_TO_REFERENCE = {"python", "capture", "url"}


def referenced_options() -> set[str]:
    found = set()
    for groups in HOOKS["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                blob = json.dumps([hook.get("command"), hook.get("args")], ensure_ascii=False)
                found.update(re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", blob))
    return found


def test_hooks_only_reference_options_every_install_already_has():
    extra = referenced_options() - SAFE_TO_REFERENCE
    assert not extra, (
        f"hooks.json 引用了 {sorted(extra)}；未设置的选项会让整个 hook 执行失败，"
        "老安装升级上来就是采集全停。新增可配项请走项目 marker 或环境变量。"
    )


def test_everything_hooks_reference_is_actually_declared():
    """反向：引用了但 manifest 没声明，同样展开失败。"""
    declared = set(MANIFEST.get("userConfig") or {})
    missing = referenced_options() - declared
    assert not missing, f"hooks.json 引用了未声明的选项: {sorted(missing)}"


def test_the_fork_window_is_not_a_plugin_option_any_more():
    """它是这条规矩的由来 —— 别让它悄悄回到 hooks.json 里。"""
    assert "recorder_fork_window" not in referenced_options()
    assert "recorder_fork_window" not in (MANIFEST.get("userConfig") or {})
