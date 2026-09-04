"""Research Trace's own MCP calls never become research evidence."""

from __future__ import annotations

import pytest

from tests.test_trace_hook import H, PROTOCOL, bind, event, session_root


def pending(data):
    roots = list((data / "outbox").glob("*/*/pending/*.json"))
    return sorted(roots)


@pytest.mark.parametrize("tool", [
    "mcp__plugin_research-trace_trace__trace_attach",
    "mcp__plugin_research-trace_trace__trace_record",
    "mcp__plugin_research-trace_trace__trace_context",
    "mcp__plugin_research-trace_trace__trace_curate",
    "mcp__research-trace__trace_search",
    "trace_ingest",
])
def test_a_trace_tool_call_never_becomes_an_event(tmp_path, tool):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("PreToolUse", cwd, tool_name=tool, tool_input={"project_id": "prj_1"}), data, PROTOCOL)
    H.handle(event("PostToolUse", cwd, tool_name=tool, tool_input={"project_id": "prj_1"}), data, PROTOCOL)
    assert not pending(data)


def test_trace_plumbing_cannot_create_a_semantic_batch(tmp_path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    for _ in range(8):
        H.handle(event(
            "PreToolUse", cwd,
            tool_name="mcp__plugin_research-trace_trace__trace_attach",
            tool_input={"sha256": "0" * 64},
        ), data, PROTOCOL)
        H.handle(event(
            "PostToolUse", cwd,
            tool_name="mcp__plugin_research-trace_trace__trace_attach",
            tool_input={"sha256": "0" * 64},
        ), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    root = session_root(data)
    assert not list((root / "batches").glob("*.json"))


def test_a_real_tool_call_still_counts_as_material(tmp_path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("PreToolUse", cwd, tool_name="Bash", tool_input={"command": "python train.py"}),
             data, PROTOCOL)
    assert pending(data)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    assert list((session_root(data) / "batches").glob("*.json"))