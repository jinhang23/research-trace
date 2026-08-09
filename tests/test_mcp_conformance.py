"""协议一致性的回归断言。

每一条都对应一次审计里**实际复现出来**的偏离。手写协议就得自己守住这些，
所以它们必须一直有测试盯着。
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import trace_mcp as M

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def be(tmp_path: Path):
    import trace_write as W

    backend = M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")
    return backend


# ------------------------------------------------------------ 通知一律不回也不执行
# JSON-RPC 2.0 §4.1: "The Server MUST NOT reply to a Notification."


@pytest.mark.parametrize("method,params", [
    ("initialize", {"protocolVersion": "2025-06-18"}),
    ("ping", {}),
    ("tools/list", {}),
    ("tools/call", {"name": "trace_projects", "arguments": {}}),
    ("notifications/initialized", {}),
    ("某个没听说过的方法", {}),
])
def test_no_response_to_any_notification(method, params):
    assert M.handle({"jsonrpc": "2.0", "method": method, "params": params}, M.Session()) is None


def test_a_notification_shaped_tools_call_must_not_write_anything(tmp_path: Path, be):
    """审计里真的复现过：没有 id 的 tools/call 既回了 id:null，又把步骤写进了磁盘。"""
    r = M.handle({"jsonrpc": "2.0", "method": "tools/call",
                  "params": {"name": "trace_new_step",
                             "arguments": {"project": "alpha", "title": "不该被创建"}}},
                 _session_with(be))
    assert r is None, "通知不该有回应"
    assert be.forest("alpha")["steps"] == [], "通知更不该产生副作用"


def test_null_id_is_rejected_instead_of_echoed():
    """MCP 在 JSON-RPC 之上收紧了：请求的 id 不能是 null。官方客户端解析不了 id:null。"""
    r = M.handle({"jsonrpc": "2.0", "id": None, "method": "ping"}, M.Session())
    assert r["error"]["code"] == -32600
    assert r["id"] is None            # 错误响应的 id 只能是 null，这是允许的


def test_notification_named_method_with_an_id_is_a_request():
    """带了 id 就是请求，不管方法名叫什么——原来只看方法名前缀，直接返 None。"""
    r = M.handle({"jsonrpc": "2.0", "id": 7, "method": "notifications/initialized"}, M.Session())
    assert r is not None and r["error"]["code"] == -32601


def test_batch_notifications_produce_no_entries(be):
    """JSON-RPC §4.1 特别点名了批量里的通知。"""
    sin = io.StringIO(json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "tools/call",
         "params": {"name": "trace_new_step", "arguments": {"project": "alpha", "title": "批量通知"}}},
    ]) + "\n")
    sout = io.StringIO()
    os.environ["TRACE_DATA"] = str(be.root)
    os.environ.pop("TRACE_URL", None)
    M.serve_stdio(sin, sout)
    out = json.loads(sout.getvalue().strip())
    assert [r["id"] for r in out] == [1], "批量里的通知不该产生条目"
    assert be.forest("alpha")["steps"] == []


# ------------------------------------------------------------ 工具异常一律 isError


def _session_with(backend):
    s = M.Session()
    s.backend = backend
    return s


def test_non_ToolError_exceptions_come_back_as_isError(be, monkeypatch):
    """官方 SDK 把工具里的任何异常都转成 isError；回 JSON-RPC error 会让
    客户端当成协议级故障抛给上层，模型既看不到原因也没法自我纠正。"""
    def boom(*a, **kw):
        raise RuntimeError("远端超时 / 代理返回 HTML / KeyError 都会走到这里")

    monkeypatch.setattr(be, "projects", boom)
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "trace_projects", "arguments": {}}}, _session_with(be))
    assert "error" not in r, "不该是 JSON-RPC error"
    assert r["result"]["isError"] is True
    assert "RuntimeError" in r["result"]["content"][0]["text"]


def test_unreadable_file_is_a_tool_error_not_a_protocol_error(be, tmp_path: Path):
    """读不动的文件（权限不足、被独占锁住）原来会漏成 -32603。"""
    import trace_write as W

    W.create_step(be._sd("alpha"), title="x")
    missing = tmp_path / "没有这个文件.log"
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "trace_attach",
                             "arguments": {"project": "alpha", "step": "001",
                                           "path": str(missing), "caption": "c"}}},
                 _session_with(be))
    assert "error" not in r and r["result"]["isError"] is True


def test_oversized_file_is_refused_before_being_read(be, tmp_path: Path, monkeypatch):
    """先看大小再读。原来是 read_bytes 之后才查，40 GB 的 checkpoint 会先把内存打爆。"""
    import trace_write as W

    W.create_step(be._sd("alpha"), title="x")
    big = tmp_path / "huge.bin"
    big.write_bytes(b"0" * 64)

    read_calls = []
    real_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (read_calls.append(self), real_read(self))[1])
    monkeypatch.setattr(M, "MAX_ATTACH_BYTES", 8)

    with pytest.raises(M.ToolError, match="上限"):
        M.dispatch(be, "trace_attach", {"project": "alpha", "step": "001",
                                        "path": str(big), "caption": "c"})
    assert big not in read_calls, "超限的文件根本不该被读进内存"


# ------------------------------------------------------------ 参数与注入


def test_boolean_does_not_pass_as_integer_or_number():
    """Python 里 bool 是 int 的子类，裸 isinstance 会放过 true。"""
    schema = {"type": "object", "properties": {"n": {"type": "number"}, "i": {"type": "integer"}}}
    for bad in ({"n": True}, {"i": False}):
        with pytest.raises(M.ToolError, match="boolean"):
            M.validate_args(schema, bad)


def test_non_object_arguments_gets_an_honest_error():
    """原来静默当成 {}，报出来的是"缺少必填参数 project"，和真正的毛病对不上。"""
    for bad in ([1, 2], "字符串", 42):
        r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "trace_projects", "arguments": bad}}, M.Session())
        assert r["error"]["code"] == -32602 and "对象" in r["error"]["message"]


def test_caption_cannot_break_out_of_the_markdown_syntax():
    """caption 和文件名都是外部输入，零转义拼进正文会把语法撑破。"""
    ref = M._md_ref("fig.png", '结果好\n") 恶意尾巴 [x](y', True)
    assert ref.count('"') == 2, ref
    assert "\n" not in ref


def test_paths_with_spaces_or_parens_are_wrapped():
    assert M._md_ref("my fig (1).png", "说明", True).startswith("![](<my fig (1).png> ")


def test_path_and_text_together_is_refused(be):
    """原来 text 被静默丢掉。"""
    import trace_write as W

    W.create_step(be._sd("alpha"), title="x")
    with pytest.raises(M.ToolError, match="只能给一个"):
        M.dispatch(be, "trace_attach", {"project": "alpha", "step": "001",
                                        "path": __file__, "text": "会被丢掉", "name": "a.txt"})


# ------------------------------------------------------------ 翻译走的是同一条协议路径


def test_a_translation_call_through_the_protocol_only_writes_the_translation(be):
    """协议层这一段也要钉一次：工具处理函数直连时对，不等于 tools/call 那条路对
    （参数校验、arguments 解包都在中间）。写错了的形状是「原文被译文盖掉」。"""
    import trace_write as W

    W.create_step(be._sd("alpha"), title="加入标题字段", body="## 为什么\n基线丢了词序")
    d = be._sd("alpha") / W.load(be._sd("alpha"))["001"].dirname
    before = (d / "note.md").read_bytes()

    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "trace_translate",
                             "arguments": {"project": "alpha", "step": "001", "lang": "en",
                                           "title": "Add title field",
                                           "body": "## Why\nThe baseline discards word order."}}},
                 _session_with(be))
    assert r["result"]["isError"] is False, r["result"]["content"][0]["text"]
    assert (d / "note.md").read_bytes() == before, "原文一个字节都不许动"
    assert (d / "note.en.md").is_file()


def test_a_translation_without_a_language_is_refused_by_the_schema(be):
    """lang 会直接变成文件名的一段，缺了它没有任何合理的默认值可以猜。"""
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "trace_translate",
                             "arguments": {"project": "alpha", "step": "001", "body": "x"}}},
                 _session_with(be))
    assert r["result"]["isError"] is True and "lang" in r["result"]["content"][0]["text"]


# ------------------------------------------------------------ 循环健壮性


def test_deeply_nested_json_does_not_kill_the_loop():
    """RecursionError 不是 JSONDecodeError 的子类，原来会一路把循环掀翻。"""
    bomb = "[" * 20000 + "]" * 20000
    sin = io.StringIO(bomb + "\n" + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
    sout = io.StringIO()
    M.serve_stdio(sin, sout)
    out = [json.loads(l) for l in sout.getvalue().splitlines() if l.strip()]
    assert out[-1]["id"] == 2, "解析炸弹之后连接必须还活着"


def test_official_sdk_client_accepts_every_response_shape(tmp_path: Path):
    """把各种响应形状喂给官方 SDK 的解析器，确认没有它读不了的。"""
    types = pytest.importorskip("mcp.types", reason="装了官方 SDK 才能跑")
    import trace_write as W

    M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")
    os.environ["TRACE_DATA"] = str(tmp_path)
    os.environ.pop("TRACE_URL", None)

    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "trace_read", "arguments": {"project": "没有这个项目"}}},   # isError
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},                        # -32601
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "不存在的工具", "arguments": {}}},                           # -32602
    ]
    sin = io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n")
    sout = io.StringIO()
    M.serve_stdio(sin, sout)

    lines = [l for l in sout.getvalue().splitlines() if l.strip()]
    assert len(lines) == len(msgs)
    for l in lines:
        types.JSONRPCMessage.model_validate_json(l)     # 解析不了就直接抛


# ------------------------------------------------------------ 配置来源
# 插件清单是静态的，而数据在哪每台机器都不同，所以必须能从配置文件读。


def test_config_file_is_used_when_env_is_absent(tmp_path: Path, monkeypatch):
    for k in ("TRACE_URL", "TRACE_TOKEN", "TRACE_DATA", "TRACE_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"data": str(tmp_path / "仓")}), encoding="utf-8")
    monkeypatch.setenv("TRACE_CONFIG", str(cfg))
    be = M.make_backend()
    assert isinstance(be, M.LocalBackend)
    assert (tmp_path / "仓" / "projects").is_dir()


def test_env_wins_over_the_config_file(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"url": "https://来自文件"}), encoding="utf-8")
    monkeypatch.setenv("TRACE_CONFIG", str(cfg))
    monkeypatch.setenv("TRACE_URL", "https://来自环境变量")
    assert M.make_backend().base == "https://来自环境变量"


def test_broken_config_file_does_not_crash(tmp_path: Path, monkeypatch):
    for k in ("TRACE_URL", "TRACE_TOKEN", "TRACE_DATA"):
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "cfg.json"
    cfg.write_text("{ 这不是 JSON", encoding="utf-8")
    monkeypatch.setenv("TRACE_CONFIG", str(cfg))
    with pytest.raises(M.ToolError, match="没有配置后端"):
        M.make_backend()
