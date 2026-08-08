"""删除的断言。

删除是「只追加」(P2) 的一处**有意例外**，只用来处理"这条记录本身就不该存在"
——误建、测试数据、不小心粘进去的令牌。它和 dead 是两件事：
dead 是研究结论，往里塞垃圾会毁掉这套系统最有价值的信号。

两个代价是明确接受的（用户拍板）：id 会被重用、子步骤会变孤儿。
测试的作用是保证这两件事**大声发生**，而不是悄悄发生。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_mcp as M
import trace_write as W


@pytest.fixture()
def proj(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


def test_a_reason_is_mandatory(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="x")
    for bad in ("", "   ", None):
        with pytest.raises(W.WriteError, match="原因"):
            W.delete_step(d, s.id, bad)
    assert (d / s.dirname).is_dir(), "报错之后不该已经删了"


def test_the_directory_and_its_attachments_are_really_gone(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="要删的")
    W.attach_auto(d, s.id, b"token=abc123", filename="secret.env")
    W.attach_auto(d, s.id, b"\x89PNG", filename="fig.png", mime="image/png")
    info = W.delete_step(d, s.id, "误建")
    assert info["files_removed"] == 3, "note.md + 两个附件"
    assert not (d / s.dirname).exists()
    assert list(d.rglob("secret.env")) == [], "敏感文件必须真的没了"


def test_deleting_a_nonexistent_step_is_a_404(proj):
    _root, d = proj
    with pytest.raises(W.NotFound):
        W.delete_step(d, "999", "随便")


def test_children_become_orphans_and_that_is_reported(proj):
    _root, d = proj
    a, _ = W.create_step(d, title="根")
    b, _ = W.create_step(d, parent=a.id, title="中间")
    c, _ = W.create_step(d, parent=b.id, title="孩子")
    info = W.delete_step(d, b.id, "误建")
    assert info["orphaned"] == [c.id]

    f = core.compile_forest(d)
    assert [s["parent"] for s in f["steps"] if s["id"] == c.id] == [None], "降级为根"
    assert any(w["code"] == "dangling_parent" for w in f["warnings"])
    assert len(f["steps"]) == 2, "构建不该崩，只是少了一步"


def test_dangling_wikilinks_are_reported(proj):
    _root, d = proj
    a, _ = W.create_step(d, title="被引用的")
    W.create_step(d, title="引用它的", body=f"## 为什么\n承接 [[{a.id}]] 的发现")
    info = W.delete_step(d, a.id, "误建")
    assert info["dangling_refs"] == ["002"]


def test_the_reason_survives_in_project_md(proj):
    """目录一删，「为什么删的」就只剩这一行。"""
    root, d = proj
    s, _ = W.create_step(d, title="误建的测试步骤")
    W.delete_step(d, s.id, "不是真实实验，手滑建的", by="human", date="2026-08-08")
    text = (core.project_dir(root, "课题") / core.PROJECT_NOTE).read_text(encoding="utf-8")
    assert "## 已删除" in text
    assert "`001`" in text and "不是真实实验" in text and "2026-08-08" in text
    assert "human" in text


def test_deletions_accumulate_instead_of_overwriting(proj):
    root, d = proj
    for i in range(3):
        s, _ = W.create_step(d, title=f"第{i}个")
        W.delete_step(d, s.id, f"原因{i}")
    text = (core.project_dir(root, "课题") / core.PROJECT_NOTE).read_text(encoding="utf-8")
    assert text.count("- `") == 3


def test_the_deletion_log_never_drives_id_allocation(proj):
    """它是给人看的历史，不是索引 —— 靠它分配 id 就等于引入了会漂移的中心状态（P1 禁止）。"""
    root, d = proj
    s, _ = W.create_step(d, title="最高号")
    W.delete_step(d, s.id, "误建")
    again, _ = W.create_step(d, title="新的")
    assert again.id == s.id, "删掉最高号之后 id 确实会被重用——这是明确接受的代价"


def test_insights_and_deletions_coexist_in_project_md(proj):
    root, d = proj
    W.update_project(root, "课题", add=("pitfall", "有个坑"))
    s, _ = W.create_step(d, title="x")
    W.delete_step(d, s.id, "误建")
    body = next(p.body for p in core.scan_projects(root))
    assert "## 坑" in body and "## 已删除" in body and "有个坑" in body


# ------------------------------------------------------------ MCP


def test_mcp_delete_reports_every_consequence(proj):
    root, d = proj
    be = M.LocalBackend(root)
    M.dispatch(be, "trace_new_step", {"project": "课题", "title": "根"})
    M.dispatch(be, "trace_new_step", {"project": "课题", "parent": "001", "title": "要删的"})
    M.dispatch(be, "trace_new_step", {"project": "课题", "parent": "002", "title": "孩子",
                                      "body": "## 为什么\n见 [[002]]"})
    out = M.dispatch(be, "trace_delete_step",
                     {"project": "课题", "step": "002", "reason": "误建的测试步骤"})
    assert "已删除" in out
    assert "003" in out and "降级为根" in out, "子步骤会变孤儿这件事必须说出来"
    assert "[[002]]" in out, "断掉的引用必须说出来"
    assert "重用" in out, "id 重用的风险必须每次都说出来"


def test_mcp_delete_needs_a_reason(proj):
    root, _d = proj
    be = M.LocalBackend(root)
    M.dispatch(be, "trace_new_step", {"project": "课题", "title": "x"})
    with pytest.raises(M.ToolError, match="原因"):
        M.dispatch(be, "trace_delete_step", {"project": "课题", "step": "001", "reason": ""})


def test_the_delete_tool_tells_agents_not_to_use_it_for_failed_experiments():
    """dead 是研究结论。把真实的失败删掉，等于抹掉后来人最需要的线索。"""
    d = next(t for t in M.TOOLS if t["name"] == "trace_delete_step")["description"]
    assert "dead" in d and "失败的实验" in d
    assert "id" in d and "重用" in d, "两个代价必须写在工具描述里"
