from __future__ import annotations

import io
import json

import pytest

from research_trace import mcp
from research_trace.mcp import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    TOOLS,
    _manifest_payload,
    call_tool,
    force_utf8_stdio,
    handle,
    serve,
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


def test_a_notification_shaped_tools_call_neither_answers_nor_writes():
    """JSON-RPC §4.1：通知不回复。更要紧的是它不能执行——没有 id 的 tools/call
    以前会照常 POST 进中央库，调用方却永远拿不到回执。"""
    remote = FakeRemote()
    message = _call("trace_record", {"project_id": "p", "idempotency_key": "k", "title": "t"})
    message.pop("id")
    assert handle(remote, message) is None
    assert remote.calls == []


def test_request_id_must_not_be_null():
    """MCP 在 JSON-RPC 之上收紧了：请求的 id 不能是 null，官方客户端解析不了。"""
    response = handle(FakeRemote(), {"jsonrpc": "2.0", "id": None, "method": "ping"})
    assert response["error"]["code"] == mcp.INVALID_REQUEST
    assert response["id"] is None  # 错误响应的 id 只能是 null，这是允许的


def test_ping_is_answered_and_unknown_methods_use_method_not_found():
    assert handle(FakeRemote(), {"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}
    unknown = handle(FakeRemote(), {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    assert unknown["error"]["code"] == mcp.METHOD_NOT_FOUND
    # 同一个方法名作为通知发来时仍然不回复
    assert handle(FakeRemote(), {"jsonrpc": "2.0", "method": "resources/list"}) is None


@pytest.mark.parametrize("requested,expected", [
    ("2025-06-18", "2025-06-18"),
    ("2024-11-05", "2024-11-05"),
    ("1999-01-01", PROTOCOL_VERSION),
    (None, PROTOCOL_VERSION),
])
def test_initialize_negotiates_the_protocol_version(requested, expected):
    params = {} if requested is None else {"protocolVersion": requested}
    response = handle(FakeRemote(), {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    assert response["result"]["protocolVersion"] == expected
    assert expected in SUPPORTED_PROTOCOL_VERSIONS


def test_bad_arguments_are_invalid_params_and_tool_failures_are_isError():
    remote = FakeRemote()
    for bad in ([1, 2], "字符串", 42):
        response = handle(remote, _call(arguments=bad))
        assert response["error"]["code"] == mcp.INVALID_PARAMS
    assert remote.calls == []

    # 工具内部失败必须走 isError：回成 JSON-RPC error 会被客户端当成传输故障，
    # 模型看不到原因，也就没法自己纠正。
    failing = FakeRemote(error=RuntimeError("Research Trace HTTP 401"))
    response = handle(failing, _call())
    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "RuntimeError" in response["result"]["content"][0]["text"]


def test_serve_reports_parse_errors_and_skips_notifications_inside_a_batch():
    remote = FakeRemote()
    lines = "\n".join([
        "{ 这不是 JSON",
        json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "tools/call",
             "params": {"name": "trace_record", "arguments": {"title": "批量通知"}}},
        ]),
    ]) + "\n"
    sink = io.StringIO()
    serve(remote, io.StringIO(lines), sink)
    parse_error, batch = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert parse_error["error"]["code"] == mcp.PARSE_ERROR and parse_error["id"] is None
    assert [item["id"] for item in batch] == [1]
    assert remote.calls == []


# --------------------------------------------------------------- stdio 编码


def test_chinese_survives_the_protocol_channel_as_pure_ascii():
    """中文标题必须原样到达 HTTP 层；发回去的那一行必须是纯 ASCII，
    这样任何控制台/管道编码都改不了协议内容。"""
    remote = FakeRemote(result={"title": "检查 batch effect"})
    line = json.dumps(_call("trace_record", {
        "project_id": "p", "idempotency_key": "k", "title": "检查 batch effect", "body": "结论：可用",
    }), ensure_ascii=False)
    sink = io.StringIO()
    serve(remote, io.StringIO(line + "\n"), sink)
    assert remote.calls[0][2]["title"] == "检查 batch effect"
    out = sink.getvalue()
    out.encode("ascii")  # 非 ASCII 会在这里抛出来
    assert "检查 batch effect" in json.loads(out)["result"]["content"][0]["text"]


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
