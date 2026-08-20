"""Recorder 每隔几个批次重新 fork 一次。

fork 的意义是继承主 agent **此刻**的完整上下文。但实测下来很多批次的全部内容就是
「某个子 agent 结束了」（137 个采集事件里 SubagentStop 占 56 个），为这种批次付一次
完整 fork 不划算：一次 fork 首轮读入约 60 万 token。缓存命中率 99.7–99.9%，
所以这是「多久付一次便宜的读取」，不是「算不算得起」。
"""
import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("TRACE_HOOK_NO_SPAWN", "1")
ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("rt_hook_window", ROOT / "scripts" / "trace_hook.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)

from tests.test_trace_hook import PROTOCOL, bind, event  # noqa: E402


def one_batch(project, data, agent_id, window=""):
    """跑完一个批次：Stop 派发 → fork 起来 → fork 结束。返回 Stop 那一下的指令。"""
    H.handle(event("UserPromptSubmit", project, prompt="做事"), data, PROTOCOL, "", window)
    out = H.handle(event("Stop", project), data, PROTOCOL, "", window)
    H.handle(event("PreToolUse", project, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL, "", window)
    H.handle(event("SubagentStart", project, agent_id=agent_id), data, PROTOCOL, "", window)
    H.handle(event("SubagentStop", project, agent_id=agent_id), data, PROTOCOL, "", window)
    return str(out or "")


def asks_for_fork(text):
    return "subagent_type='fork'" in text


@pytest.mark.parametrize("raw,expected", [
    ("", 1), ("1", 1), ("4", 4), ("0", 0), ("  3 ", 3), ("不是数字", 1), ("-2", 0),
])
def test_window_parsing(raw, expected, monkeypatch):
    monkeypatch.delenv("TRACE_RECORDER_REUSE", raising=False)
    monkeypatch.delenv("TRACE_RECORDER_FORK_WINDOW", raising=False)
    assert H._fork_window(raw) == expected


def test_the_old_reuse_switch_still_means_never_refork(monkeypatch):
    monkeypatch.setenv("TRACE_RECORDER_REUSE", "1")
    monkeypatch.delenv("TRACE_RECORDER_FORK_WINDOW", raising=False)
    assert H._fork_window("") == 0


def test_window_of_one_reforks_every_batch(tmp_path):
    project, data = bind(tmp_path, "w1"), tmp_path / "data"
    one_batch(project, data, "a1", "1")
    assert asks_for_fork(one_batch(project, data, "a2", "1"))


def test_window_of_three_reuses_twice_then_reforks(tmp_path):
    project, data = bind(tmp_path, "w3"), tmp_path / "data"
    first = one_batch(project, data, "a1", "3")
    assert asks_for_fork(first), "第一批总是 fork"
    second = one_batch(project, data, "a1", "3")
    third = one_batch(project, data, "a1", "3")
    assert "Use SendMessage once with to=" in second, "窗口内应当复用"
    assert "Use SendMessage once with to=" in third, "窗口内应当复用"
    fourth = one_batch(project, data, "a4", "3")
    assert asks_for_fork(fourth), "第 4 批开新窗口，应当重新 fork"


def test_window_of_zero_never_reforks(tmp_path):
    project, data = bind(tmp_path, "w0"), tmp_path / "data"
    one_batch(project, data, "a1", "0")
    for _ in range(3):
        assert "Use SendMessage once with to=" in one_batch(project, data, "a1", "0")
