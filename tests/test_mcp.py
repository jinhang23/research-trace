"""MCP 工具层的断言。

分两层：
  * 工具处理函数直接调（不经过协议），覆盖行为；
  * 最后一个用例真的起一个子进程，走 JSON-RPC 握手 → tools/list → tools/call，
    确认协议层没接错。
"""

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
    backend = M.LocalBackend(tmp_path)
    import trace_write as W

    W.create_project(tmp_path, "alpha")
    return backend


def call(be, tool, **args):          # 参数名不能叫 name —— 工具本身有 name 参数
    return M.dispatch(be, tool, args)


# ------------------------------------------------------------ 读


def test_projects_lists_counts(be):
    call(be, "trace_new_step", project="alpha", title="第一步", status="done")
    out = call(be, "trace_projects")
    assert "alpha" in out and "1 步" in out and "done 1" in out


def test_read_renders_an_indented_tree(be):
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="主线")
    call(be, "trace_new_step", project="alpha", parent="001", title="旁支", status="dead")
    out = call(be, "trace_read", project="alpha")
    lines = [l for l in out.splitlines() if "  0" in l or l.strip().startswith(("●", "○", "▣"))]
    assert any(l.startswith("○ 001") for l in lines), out
    assert any(l.startswith("  ○ 002") for l in lines), "子步骤要缩进一层"
    assert any(l.startswith("  ▣ 002b") for l in lines), "dead 用 ▣"


def test_read_a_single_step_shows_lineage(be):
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="中间")
    call(be, "trace_new_step", project="alpha", parent="002", title="末端", body="## 为什么\n因为要试试")
    out = call(be, "trace_read", project="alpha", step="003")
    assert "溯源: 001 → 002 → 003" in out
    assert "因为要试试" in out


def test_read_unknown_step_errors(be):
    with pytest.raises(M.ToolError):
        call(be, "trace_read", project="alpha", step="999")


def test_search_finds_body_only_words(be):
    call(be, "trace_new_step", project="alpha", title="试 focal loss", status="dead",
         body="## 结论\n正样本太少，MMseqs2 聚类之后再说。放弃这条路。")
    out = call(be, "trace_search", query="MMseqs2")
    assert "alpha/001" in out and "放弃这条路" in out


def test_search_reports_nothing_found(be):
    assert "没有搜到" in call(be, "trace_search", query="不存在的词")


# ------------------------------------------------------------ 写


def test_new_step_defaults_to_wip_and_agent_author(be):
    out = call(be, "trace_new_step", project="alpha", title="开跑")
    assert "[wip]" in out
    assert be.step("alpha", "001")["author"] == M.DEFAULT_AUTHOR


def test_new_step_nags_when_the_body_is_still_the_template(be):
    """「为什么」是唯一无法自动生成的字段，光建个空壳没有意义。"""
    assert "⚠" in call(be, "trace_new_step", project="alpha", title="空壳")
    assert "⚠" not in call(be, "trace_new_step", project="alpha", title="写了的",
                           body="## 为什么\n上一步发现验证集有重复样本，先确认污染比例。")


def test_idempotency_key_prevents_duplicates(be):
    call(be, "trace_new_step", project="alpha", title="扫参", key="sweep-1")
    out = call(be, "trace_new_step", project="alpha", title="扫参重试", key="sweep-1")
    assert "已存在同 key" in out
    assert len(be.forest("alpha")["steps"]) == 1


def test_update_append_keeps_the_existing_body(be):
    call(be, "trace_new_step", project="alpha", title="x", body="## 为什么\n先建着")
    call(be, "trace_update_step", project="alpha", step="001", status="done",
         append="## 结果\n准确率 0.951。")
    s = be.step("alpha", "001")
    assert s["status"] == "done"
    assert "先建着" in s["body"] and "0.951" in s["body"]


def test_update_rejects_body_and_append_together(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_update_step", project="alpha", step="001", body="a", append="b")


def test_update_cannot_change_parent(be):
    """只追加原则要一路守到 MCP 这层。"""
    call(be, "trace_new_step", project="alpha", title="根")
    call(be, "trace_new_step", project="alpha", parent="001", title="子")
    with pytest.raises(M.ToolError):
        M.dispatch(be, "trace_update_step", {"project": "alpha", "step": "002", "status": "done", "parent": None})


def test_update_with_nothing_to_change_errors(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_update_step", project="alpha", step="001")


# ------------------------------------------------------------ 附件


def test_attach_text_content(be):
    call(be, "trace_new_step", project="alpha", title="x")
    out = call(be, "trace_attach", project="alpha", step="001",
               text="epoch\tloss\n1\t0.42\n", name="train.log")
    assert "train.log" in out
    assert [f["path"] for f in be.step("alpha", "001")["files"]] == ["train.log"]


def test_attach_from_a_local_path(be, tmp_path: Path):
    call(be, "trace_new_step", project="alpha", title="x")
    p = tmp_path / "run.sh"
    p.write_text("python train.py --seed 0\n", encoding="utf-8")
    call(be, "trace_attach", project="alpha", step="001", path=str(p))
    assert [f["path"] for f in be.step("alpha", "001")["files"]] == ["run.sh"]


def test_image_without_a_caption_is_refused(be):
    """agent 看不到图里的内容，图注是唯一的信息来源——所以这里是硬拒绝。"""
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError, match="caption"):
        call(be, "trace_attach", project="alpha", step="001", text="fake", name="loss.png")


def test_image_with_a_caption_is_inserted_into_the_body(be):
    call(be, "trace_new_step", project="alpha", title="x", body="## 结果")
    call(be, "trace_attach", project="alpha", step="001", text="fake", name="loss.png",
         caption="第 12 轮之后验证集回升，再往后是纯过拟合")
    body = be.step("alpha", "001")["body"]
    assert '![](loss.png "第 12 轮之后验证集回升，再往后是纯过拟合")' in body


def test_attach_text_without_a_name_errors(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_attach", project="alpha", step="001", text="内容")


def test_attach_needs_path_or_text(be):
    call(be, "trace_new_step", project="alpha", title="x")
    with pytest.raises(M.ToolError):
        call(be, "trace_attach", project="alpha", step="001", name="a.txt")


def test_unknown_tool_errors(be):
    with pytest.raises(M.ToolError):
        M.dispatch(be, "trace_delete_everything", {})


def test_backend_requires_configuration(monkeypatch):
    monkeypatch.delenv("TRACE_URL", raising=False)
    monkeypatch.delenv("TRACE_DATA", raising=False)
    with pytest.raises(M.ToolError, match="TRACE_URL"):
        M.make_backend()


# ------------------------------------------------------------ 协议冒烟


def test_stdio_protocol_handshake_and_tool_call(tmp_path: Path):
    """真的起一个子进程，走完 initialize → tools/list → tools/call。"""
    import trace_write as W

    M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")

    env = dict(os.environ, TRACE_DATA=str(tmp_path), PYTHONIOENCODING="utf-8")
    env.pop("TRACE_URL", None)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "trace_mcp.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, encoding="utf-8", bufsize=1,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        assert line, "服务端没有回应（stderr: %s)" % proc.stderr.read()[:500]
        return json.loads(line)

    try:
        from mcp.types import LATEST_PROTOCOL_VERSION

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                         "clientInfo": {"name": "pytest", "version": "0"}}})
        init = recv()
        assert init["result"]["serverInfo"]["name"] == "trace"

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in recv()["result"]["tools"]]
        assert names == [t["name"] for t in M.TOOLS]

        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "trace_new_step",
                         "arguments": {"project": "alpha", "title": "从 MCP 建的一步",
                                       "body": "## 为什么\n验证协议链路是通的。"}}})
        res = recv()["result"]
        assert res.get("isError") is not True, res
        assert "alpha/001" in res["content"][0]["text"]

        # schema 校验：缺必填字段应当被客户端层拦下来，返回 isError 而不是崩掉
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "trace_new_step", "arguments": {"project": "alpha"}}})
        bad = recv()
        assert bad.get("error") or bad["result"].get("isError"), bad
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)

    assert (tmp_path / "projects" / "alpha" / "steps").is_dir()
