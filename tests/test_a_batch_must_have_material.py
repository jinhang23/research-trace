"""恒为 0 产出的批次不该存在。

现场反馈：批次每两分钟出一个，内容全是 recorder plumbing，Recorder 每次正确地记下
0 条——但**判断「没东西可记」本身就要先付一次完整 fork**（实测首轮读入约 60 万 token）。
一轮多烧七十多万 token 换一个必然为空的结论。

此前的判据是 `events or chunks`：任何新东西都开一批。而 transcript 不能当判据——
Recorder 自己的回合就写在同一个 transcript 文件里，chunk 照样变长，scrub 只按 agentId
精确匹配丢行，漏一行就够开一批。于是「Recorder 跑完 → transcript 变长 → 新 chunk →
开批 → 再派一次 Recorder」自己转起来。

现在的判据是**事件**：用户说了话、调了工具、子 agent 跑完，这些都写事件；而 Recorder
那一段被 _is_trace_orchestration 挡在事件层之外，一个事件都不写。所以「只剩生命周期
事件」和「这段时间里只有 Recorder 在动」是同一件事。
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_trace_hook import H, PROTOCOL, bind, event, session_root


def batches(data: Path) -> list[Path]:
    """所有开过的批次，含已归档的：Recorder 收工时会把 manifest 挪进 batches/done/，
    只数还开着的那些会把「派过一次」误读成「没派过」。"""
    root = session_root(data) / "batches"
    return sorted(root.glob("*.json")) + sorted((root / "done").glob("*.json"))


def test_a_turn_where_only_the_recorder_moved_does_not_dispatch(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"

    H.handle(event("UserPromptSubmit", cwd, prompt="做点事"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL), "真有素材的一轮该派"
    assert len(batches(data)) == 1

    # Recorder 跑完一整轮：这些事件全被挡在事件层之外，一个都不落盘
    H.handle(event("PreToolUse", cwd, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", cwd, agent_id="rec-1"), data, PROTOCOL)
    H.handle(event("SubagentStop", cwd, agent_id="rec-1"), data, PROTOCOL)

    # 之后再来一个 Stop：这中间除了 Recorder 自己，什么都没发生
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None, \
        "只有 Recorder 动过的一轮不该再派一次 fork"
    assert len(batches(data)) == 1, "不该凭空多出一个必然 0 产出的批次"


def test_a_real_turn_still_dispatches_after_a_skipped_one(tmp_path: Path):
    """跳过不能变成「从此不再记录」。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("UserPromptSubmit", cwd, prompt="第一件事"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    H.handle(event("PreToolUse", cwd, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", cwd, agent_id="rec-1"), data, PROTOCOL)
    H.handle(event("SubagentStop", cwd, agent_id="rec-1"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None

    H.handle(event("UserPromptSubmit", cwd, prompt="第二件事"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL), "人又说了话，就该再派"
    assert len(batches(data)) == 2


def test_the_skipped_events_are_not_lost_but_ride_the_next_batch(tmp_path: Path):
    """跳过时不推进游标：那些事件留到下一批一起带上，一条都不丢。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)   # 只有生命周期
    assert not batches(data)

    H.handle(event("UserPromptSubmit", cwd, prompt="现在有事了"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    manifest = json.loads(batches(data)[0].read_text(encoding="utf-8"))
    # 先前那个被跳过的 Stop 也在这一批里
    assert manifest["event_count"] == 3, manifest["events"]


def test_an_unreadable_event_counts_as_material(tmp_path: Path):
    """读不出来的事件当作有内容：宁可多派一次，也不要静默漏记。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    broken = session_root(data) / "pending" / "9999999999999999999_claude-broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")

    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL), \
        "分不清的东西必须当成素材，漏记比多派一次贵得多"
