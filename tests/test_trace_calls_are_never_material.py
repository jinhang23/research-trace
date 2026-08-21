"""Research Trace 自己的 MCP 调用永远不进事件层。

现场证据（服务端数据库，2026-08-21）：

    事件类型            有 agent_id
    SubagentStop        110 / 110
    PreToolUse 等         0 / 195
    SubagentStart         该类型一条都没有

`PreToolUse` 上**根本没有 agent_id**，而 `is_recorder` 和 `_recorder_tool_guard` 都靠
`payload["agent_id"] == recorder_id` 判断。于是 Recorder 自己调 trace_attach 时判不出来，
那次调用被当成主 agent 的普通事件写进事件层——而事件就是素材，素材就开新批，新批再派
一个 Recorder。05:24–05:26 之间 8 次 trace_attach 泄漏，attachments 恰好 276 → 284，
修订记录里 actor 是 recorder。环就是这么闭合的。

`_has_material` 拦不住它：trace_attach 是 PreToolUse，不是生命周期事件。

所以这一条**不看 agent_id**——身份判不出来的时候，按工具名判。记录系统在运转，
不等于研究在推进。
"""
from __future__ import annotations

import pytest

from tests.test_trace_hook import H, PROTOCOL, bind, event, session_root


def pending(data):
    return sorted((session_root(data) / "pending").glob("*.json"))


@pytest.mark.parametrize("tool", [
    "mcp__plugin_research-trace_trace__trace_attach",
    "mcp__plugin_research-trace_trace__trace_record",
    "mcp__plugin_research-trace_trace__trace_context",
    "mcp__plugin_research-trace_trace__trace_curate",
    "mcp__research-trace__trace_search",
    "trace_ingest",
])
def test_a_trace_tool_call_never_becomes_an_event(tmp_path, tool):
    """哪怕 agent_id 是 None——现场就是 None——也必须认出来。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("PreToolUse", cwd, tool_name=tool, tool_input={"project_id": "prj_1"}), data, PROTOCOL)
    H.handle(event("PostToolUse", cwd, tool_name=tool, tool_input={"project_id": "prj_1"}), data, PROTOCOL)
    assert not pending(data), f"{tool} 泄漏成了事件，它会开出一个新批次并再派一次 Recorder"


def test_the_loop_closes_even_when_agent_id_is_missing(tmp_path):
    """完整复现现场：Recorder 干完活（工具事件上 agent_id 全空），不该因此再开一批。

    注意 SubagentStop 上 agent_id 是**有**的（现场 110/110），所以批次照常关得掉；
    判不出来的只有工具调用那一段——而那正好是开新批的那一段。
    """
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("UserPromptSubmit", cwd, prompt="做点事"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)

    # 主 agent 按指示 fork 出 Recorder
    H.handle(event("PreToolUse", cwd, tool_name="Agent",
                   tool_input={"prompt": f"{H.RECORDER_MARKER} 干活"}), data, PROTOCOL)
    H.handle(event("SubagentStart", cwd, agent_id="rec-1"), data, PROTOCOL)

    # Recorder 在干活：现场那 8 次 trace_attach，工具事件上 agent_id 一个都没有
    for _ in range(8):
        H.handle(event("PreToolUse", cwd, tool_name="mcp__plugin_research-trace_trace__trace_attach",
                       tool_input={"sha256": "0" * 64}), data, PROTOCOL)
        H.handle(event("PostToolUse", cwd, tool_name="mcp__plugin_research-trace_trace__trace_attach",
                       tool_input={"sha256": "0" * 64}), data, PROTOCOL)

    H.handle(event("SubagentStop", cwd, agent_id="rec-1"), data, PROTOCOL)   # 批次在这里关掉
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None, \
        "Recorder 自己的活动不该把自己再派一遍"


def test_a_real_tool_call_still_counts(tmp_path):
    """别把整条工具路径都关掉：真活儿仍然是素材。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("PreToolUse", cwd, tool_name="Bash", tool_input={"command": "python train.py"}),
             data, PROTOCOL)
    assert pending(data), "Bash 是真的在干活，必须记下来"
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL), "有真素材就该派"
