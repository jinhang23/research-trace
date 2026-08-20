"""每一批都重新 fork，拿当下的完整上下文。

fork 的唯一理由就是「继承主 agent 此刻的完整上下文」。而复用同一个 Recorder 时，
后续批次通过 SendMessage 送过去的只有一个 manifest 路径 —— Recorder 手里是 fork
那一刻的陈旧快照加它自己的记录历史，唯独没有这一批真正发生了什么。那等于保留了
昂贵的机制却只享受第一批的收益。

重 fork 的前缀与主 agent 完全一致，本来就该命中提示缓存，边际成本是缓存读取。
"""
import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("TRACE_HOOK_NO_SPAWN", "1")

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("rt_hook_fresh", ROOT / "scripts" / "trace_hook.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)

from tests.test_trace_hook import PROTOCOL, bind, event  # noqa: E402


def dispatch_text(output) -> str:
    return "" if not output else str(output)


def run_a_batch(project, data, agent_id="agent-1"):
    """走一遍：Stop 派发 → 起 fork → fork 结束。返回 Stop 那一下的 hook 输出。"""
    out = H.handle(event("UserPromptSubmit", project, prompt="做点事"), data, PROTOCOL)
    out = H.handle(event("Stop", project), data, PROTOCOL)
    H.handle(event("PreToolUse", project, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", project, agent_id=agent_id), data, PROTOCOL)
    H.handle(event("SubagentStop", project, agent_id=agent_id), data, PROTOCOL)
    return out


def test_the_second_batch_asks_for_a_new_fork_not_a_message(tmp_path):
    project, data = bind(tmp_path, "project-a"), tmp_path / "data"
    first = run_a_batch(project, data)
    assert "subagent_type='fork'" in dispatch_text(first)

    second = H.handle(event("Stop", project), data, PROTOCOL)
    text = dispatch_text(second)
    assert "subagent_type='fork'" in text, "第二批还是该重新 fork，拿当下的上下文"
    # 只看指令形态，别看那句「If fork or SendMessage is unavailable」的兜底说明。
    assert "Use SendMessage once with to=" not in text, \
        "复用旧 fork 等于只让第一批享受到 fork 的好处"


def test_reuse_can_be_restored_for_deployments_without_prompt_caching(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_RECORDER_REUSE", "1")
    project, data = bind(tmp_path, "project-b"), tmp_path / "data"
    run_a_batch(project, data)
    second = dispatch_text(H.handle(event("Stop", project), data, PROTOCOL))
    assert "Use SendMessage once with to=" in second
    assert "subagent_type='fork'" not in second


def test_a_running_recorder_is_still_not_disturbed(tmp_path):
    """还在跑的时候不要再派一个 —— 重 fork 不等于并发两个 Recorder。"""
    project, data = bind(tmp_path, "project-c"), tmp_path / "data"
    H.handle(event("UserPromptSubmit", project, prompt="做点事"), data, PROTOCOL)
    H.handle(event("Stop", project), data, PROTOCOL)
    H.handle(event("PreToolUse", project, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", project, agent_id="busy-1"), data, PROTOCOL)

    H.handle(event("UserPromptSubmit", project, prompt="又做点事"), data, PROTOCOL)
    again = H.handle(event("Stop", project, background_tasks=[{"id": "busy-1", "status": "running"}]),
                     data, PROTOCOL)
    assert again is None, "上一个 Recorder 还在跑，这一轮不该再派"


@pytest.mark.parametrize("post_tool_use_arrives_last", [False, True])
def test_the_dispatch_events_may_arrive_in_either_order(tmp_path, post_tool_use_arrives_last):
    """一次派发会产生多个事件，先后顺序由 harness 决定。

    SubagentStop 之后如果还收到那次派发的 PostToolUse，照原样写回 agent id 就会把
    「已经结束、下一批重新 fork」悄悄改回「复用这个已经停掉的 agent」—— 下一批于是
    被 SendMessage 发给一个死掉的 Recorder。只在某一种顺序下才正确的实现是脆的。
    """
    project, data = bind(tmp_path, f"order-{post_tool_use_arrives_last}"), tmp_path / "data"
    post = event("PostToolUse", project, tool_name="Agent",
                 tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"},
                 tool_response={"agent_id": "a1"})

    H.handle(event("UserPromptSubmit", project, prompt="做事"), data, PROTOCOL)
    H.handle(event("Stop", project), data, PROTOCOL)
    H.handle(event("PreToolUse", project, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", project, agent_id="a1"), data, PROTOCOL)
    if not post_tool_use_arrives_last:
        H.handle(post, data, PROTOCOL)
    H.handle(event("SubagentStop", project, agent_id="a1"), data, PROTOCOL)
    if post_tool_use_arrives_last:
        H.handle(post, data, PROTOCOL)

    H.handle(event("UserPromptSubmit", project, prompt="再做事"), data, PROTOCOL)
    text = dispatch_text(H.handle(event("Stop", project), data, PROTOCOL))
    assert "subagent_type='fork'" in text
    assert "Use SendMessage once with to=" not in text, "退休过的 Recorder 不能被复活"


def test_a_brand_new_dispatch_clears_the_retirement(tmp_path):
    """退休名单只针对上一个 agent，不能把之后的派发也永久拒掉。"""
    project, data = bind(tmp_path, "retire"), tmp_path / "data"
    run_a_batch(project, data, agent_id="a1")
    H.handle(event("UserPromptSubmit", project, prompt="再做事"), data, PROTOCOL)
    H.handle(event("Stop", project), data, PROTOCOL)
    H.handle(event("PreToolUse", project, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", project, agent_id="a2"), data, PROTOCOL)
    # a2 正在跑，这一轮不该再派
    again = H.handle(event("Stop", project, background_tasks=[{"id": "a2", "status": "running"}]),
                     data, PROTOCOL)
    assert again is None
