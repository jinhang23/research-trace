from __future__ import annotations

import io
import json

import pytest

from research_trace import mcp
from research_trace.mcp import (
    TOOLS,
    _manifest_payload,
    call_tool,
    force_utf8_stdio,
)


class FakeRemote:
    """记下每一次出网请求，用来证明"不该发生的调用真的没有发生"。"""

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result if result is not None else {"ok": True}
        self.error = error

    def request(self, method, path, value=None):
        self.calls.append((method, path, value))
        if self.error:
            raise self.error
        return self.result


def _call(name="trace_context", arguments=None, request_id=1):
    return {
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": {} if arguments is None else arguments},
    }


def test_mcp_exposes_six_research_tools_plus_device_login():
    assert [tool["name"] for tool in TOOLS] == [
        "trace_context", "trace_ingest", "trace_record", "trace_curate",
        "trace_attach", "trace_search", "trace_login",
    ]


def test_recorder_tool_cannot_create_chapters_or_advertise_identity_knobs():
    """身份不是模型的旋钮：schema 里不该出现任何它以为自己能设的身份字段。

    服务端现在只从凭证推 actor_type/actor_id（见 tests/test_auth.py），所以这些字段
    留在 schema 里既没作用又误导——模型会以为自己能声明自己是谁。
    """
    record = next(tool for tool in TOOLS if tool["name"] == "trace_record")
    assert "chapter_name" not in record["inputSchema"]["properties"]
    assert "review_state" not in record["inputSchema"]["properties"]
    curate = next(tool for tool in TOOLS if tool["name"] == "trace_curate")
    for dead in ("actor_type", "actor_id"):
        assert dead not in curate["inputSchema"]["properties"], dead

    class Remote:
        def request(self, method, path, payload=None):
            return {"method": method, "path": path, "payload": payload}

    result = call_tool(Remote(), "trace_record", {
        "project_id": "project-1", "idempotency_key": "batch-1:0", "title": "Result",
        "chapter_name": "invented",
    })
    assert "chapter_name" not in result["payload"]


def test_instructions_do_not_send_the_recorder_at_raw_delivery():
    """投递权威是 trace-deliver。指令若还教模型调 trace_ingest，
    就等于把耐久性重新挂回「模型记不记得调工具」上（REQUIREMENTS §6.2）。"""
    from research_trace.mcp import INSTRUCTIONS

    assert "do not call trace_ingest" in INSTRUCTIONS
    assert "trace-deliver" in INSTRUCTIONS
    ingest = next(tool for tool in TOOLS if tool["name"] == "trace_ingest")
    assert "Manual backfill" in ingest["description"]


def test_trace_context_binds_a_directory_only_when_explicitly_asked(tmp_path):
    """§7 的 MCP 侧绑定入口。没传 bind_path 就一个 marker 都不许写——
    绑定是人的动作，agent 不能替用户决定录哪个目录。"""
    class Remote:
        def request(self, method, path, payload=None):
            self.last = payload
            return {"matched": True, "project": {"id": "proj-7", "name": "Batch effect"}}

    remote = Remote()
    call_tool(remote, "trace_context", {"workspace_keys": ["rt-ws-abc"]})
    assert not (tmp_path / ".research-trace.json").exists()

    result = call_tool(remote, "trace_context", {
        "workspace_keys": ["rt-ws-abc"], "bind_path": str(tmp_path),
    })
    assert "bind_path" not in remote.last, "bind_path must not be forwarded to the server"
    marker = json.loads((tmp_path / ".research-trace.json").read_text(encoding="utf-8"))
    assert marker["project_id"] == "proj-7"
    assert marker["workspace_key"] == "rt-ws-abc"
    assert marker["capture"] is True
    assert result["bound"]["marker"]["project_name"] == "Batch effect"


def test_trace_attach_tells_the_agent_that_a_key_is_the_only_way_onto_the_data_flow():
    """登记产物不给键就永远连不上边，而 agent 不会知道自己少给了什么（§8 / §10）。
    这条约定只能写在工具描述里——schema 没法把"三选一"表达成必填。"""
    attach = next(tool for tool in TOOLS if tool["name"] == "trace_attach")
    description = attach["description"]
    for required in ("sha256", "uri", "external_path", "machine"):
        assert required in description, required
    assert "never guesses" in description, "§8：不许从自然语言猜生产者和消费者"
    assert "relative" in description.lower() and "~" in description


def test_trace_context_can_ask_for_the_derived_dataflow_and_says_when_it_is_missing():
    """空图和"这台中央服务不会算"在响应里长得一样，必须说破——否则 agent 会把
    "服务端还没实现"报成"这个项目没有产物关系"。"""
    context = next(tool for tool in TOOLS if tool["name"] == "trace_context")
    assert context["inputSchema"]["properties"]["include_dataflow"]["default"] is False

    class OldServer:
        def request(self, method, path, payload=None):
            self.last = payload
            return {"matched": True, "project": {"id": "proj-7"}}

    old = OldServer()
    result = call_tool(old, "trace_context", {"include_dataflow": True})
    assert old.last["include_dataflow"] is True, "标志必须原样转发给服务端"
    assert "dataflow_unavailable" in result

    class NewServer:
        def request(self, method, path, payload=None):
            return {"matched": True, "project": {"id": "proj-7", "dataflow": {"edges": []}}}

    # 真的返回了空图就不许再加那句提示：空图是 §8 说的正常情况
    assert "dataflow_unavailable" not in call_tool(NewServer(), "trace_context", {"include_dataflow": True})


def test_manifest_loader_reads_raw_files_without_model_transcription(tmp_path):
    root = tmp_path / "session"
    (root / "batches").mkdir(parents=True)
    (root / "pending").mkdir()
    (root / "transcripts" / "pending").mkdir(parents=True)
    event = {
        "event_id": "event-1", "session_id": "session-1", "agent_id": "agent-1",
        "agent_type": "fork", "hook_event": "PostToolUse", "payload": {"ok": True},
    }
    (root / "pending" / "event.json").write_text(json.dumps(event), encoding="utf-8")
    (root / "transcripts" / "pending" / "chunk.jsonl").write_text(
        '{"message":"verbatim"}\n', encoding="utf-8"
    )
    manifest = {
        "batch_id": "batch-1",
        "session_id": "session-1",
        "project_dir": "/work/project",
        "events": ["pending/event.json"],
        "transcript_chunks": [{
            "path": "transcripts/pending/chunk.jsonl", "chunk_id": "chunk-1",
            "session_id": "session-1", "agent_id": "agent-1",
        }],
    }
    path = root / "batches" / "batch-1.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    value = _manifest_payload(str(path), "project-1")
    assert value["events"][0]["event_id"] == "event-1"
    assert value["transcript_chunks"][0]["content"].endswith("verbatim\"}\n")
    assert value["agents"][0]["id"] == "agent-1"

    # 投递器随时可能把同一批文件搬进 sent/：manifest 里的 pending/ 路径因此会失效。
    # 旧实现在这里抛 RuntimeError，所以一个归档的 batch 永远不可能被手工重投。
    (root / "sent").mkdir()
    (root / "transcripts" / "sent").mkdir()
    (root / "pending" / "event.json").rename(root / "sent" / "event.json")
    (root / "transcripts" / "pending" / "chunk.jsonl").rename(
        root / "transcripts" / "sent" / "chunk.jsonl"
    )
    after = _manifest_payload(str(path), "project-1")
    assert after["events"][0]["event_id"] == "event-1"
    assert after["transcript_chunks"][0]["content"].endswith("verbatim\"}\n")


def test_a_manifest_pointing_only_outside_the_session_is_still_refused(tmp_path):
    """回退查找只在 session 目录里找同名文件，不能变成任意读文件。"""
    root = tmp_path / "session"
    (root / "batches").mkdir(parents=True)
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"event_id": "leak"}), encoding="utf-8")
    path = root / "batches" / "b.json"
    path.write_text(json.dumps({
        "batch_id": "b", "session_id": "s", "events": ["../secret.json"],
    }), encoding="utf-8")
    with pytest.raises(RuntimeError):
        _manifest_payload(str(path))


# --------------------------------------------------------------- 协议一致性
# 手写 JSON-RPC 就得自己守住这些规矩，所以每一条都得有测试盯着。














# --------------------------------------------------------------- stdio 编码




def test_force_utf8_stdio_pins_both_ends_to_utf8(monkeypatch):
    """Windows 上默认按本地 code page 解码 stdin，中文在进入工具之前就已经是乱码。"""
    class FakeStream:
        def __init__(self):
            self.kwargs = None

        def reconfigure(self, **kwargs):
            self.kwargs = kwargs

    class Unreconfigurable:
        pass

    streams = {name: FakeStream() for name in ("stdin", "stdout", "stderr")}
    for name, stream in streams.items():
        monkeypatch.setattr(mcp.sys, name, stream)
    force_utf8_stdio()
    assert streams["stdin"].kwargs == {"encoding": "utf-8", "errors": "replace"}
    assert streams["stdout"].kwargs == {"encoding": "utf-8", "newline": "\n"}

    monkeypatch.setattr(mcp.sys, "stdout", Unreconfigurable())
    force_utf8_stdio()  # 流被换成不支持 reconfigure 的对象时不能炸掉进程
