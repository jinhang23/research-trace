"""项目洞察的断言。

这是**项目级**的沉淀：不属于任何单独一步的判断。「回译在这个数据集上一直没用」
是三次尝试之后的结论，挂在哪一步都不对。存在 project.md 的正文里，所以
删掉全部程序之后照样 grep 得到。
"""

import json
from pathlib import Path

import pytest

import trace_core as core
import trace_mcp as M
import trace_write as W


@pytest.fixture()
def root(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path


def body_of(root: Path, slug="课题") -> str:
    return next(p.body for p in core.scan_projects(root) if p.slug == slug)


def test_appending_creates_the_section_then_reuses_it(root: Path):
    W.update_project(root, "课题", add=("works", "标题字段 +0.8 个点"))
    W.update_project(root, "课题", add=("works", "去重之后差距更明显"))
    b = body_of(root)
    assert b.count("## 有效") == 1, "小节只该建一次"
    assert b.index("标题字段") < b.index("去重之后"), "后写的追加在后面"


def test_each_kind_lands_in_its_own_section(root: Path):
    for kind, text in [("idea", "一个想法"), ("works", "管用"),
                       ("fails", "不管用"), ("pitfall", "有坑")]:
        W.update_project(root, "课题", add=(kind, text))
    b = body_of(root)
    for heading in ("核心想法", "有效", "无效", "坑"):
        assert f"## {heading}" in b
    # 每条都落在自己的小节下，不会串。断言走 parse_insights 而不是逐字比对整行：
    # 洞察现在带 id（`p3`），把 id 的形状写进断言，等于以后改一次 id 格式就要改一遍测试。
    fails = core.parse_insights(b)["fails"]
    assert [i["text"] for i in fails] == ["不管用"]
    assert fails[0]["id"], "每条洞察都要分到一个 id，否则说不出「· 取代 p1」"


def test_unknown_kind_is_refused(root: Path):
    with pytest.raises(W.WriteError, match="pitfall"):
        W.update_project(root, "课题", add=("随便", "x"))


def test_empty_text_is_refused(root: Path):
    with pytest.raises(W.WriteError):
        W.update_project(root, "课题", add=("idea", "   "))


def test_newlines_are_flattened_so_bullets_stay_bullets(root: Path):
    W.update_project(root, "课题", add=("idea", "第一行\n第二行"))
    assert core.parse_insights(body_of(root))["idea"][0]["text"] == "第一行 第二行"
    assert "- `p1` 第一行 第二行" in body_of(root), "落盘仍然是一行，id 在行首的反引号里"


def test_renaming_keeps_the_insights(root: Path):
    W.update_project(root, "课题", add=("works", "别弄丢我"))
    W.update_project(root, "课题", name="新名字")
    ps = core.scan_projects(root)
    assert ps[0].name == "新名字" and "别弄丢我" in ps[0].body
    assert ps[0].slug == "课题", "slug 是 URL 的一部分，改名不能动它"


def test_replacing_the_whole_body_works(root: Path):
    W.update_project(root, "课题", add=("works", "旧的"))
    W.update_project(root, "课题", insights="## 坑\n- 全换掉了")
    b = body_of(root)
    assert "旧的" not in b and "全换掉了" in b


def test_insights_are_greppable_in_project_md(root: Path):
    """G4：删掉全部程序之后，`grep -rn 近重复 projects/` 仍然要能捞到这条判断。

    id（`p1`）挤在行首，但 grep 的是内容，不是行首——这一条钉的正是「加了 id
    之后那句人话仍然在同一行上，没有被拆到别处去」。
    """
    W.update_project(root, "课题", add=("pitfall", "测试集有近重复"))
    text = (core.project_dir(root, "课题") / core.PROJECT_NOTE).read_text(encoding="utf-8")
    assert "- `p1` 测试集有近重复" in text
    assert [ln for ln in text.split("\n") if "近重复" in ln] == ["- `p1` 测试集有近重复"]
    assert text.startswith("---\nname: 课题\n---")


# ------------------------------------------------------------ MCP


def test_mcp_insight_tool_links_back_to_the_step(root: Path):
    be = M.LocalBackend(root)
    out = M.dispatch(be, "trace_insight", {
        "project": "课题", "kind": "fails",
        "text": "回译只 +1.5 点，不如换模型的 +4.6", "step": "002c"})
    assert "无效" in out
    assert "—— [[002c]]" in body_of(root), "带 step 时要渲染成可跳转的 wikilink"


def test_mcp_read_puts_insights_before_the_tree(root: Path):
    be = M.LocalBackend(root)
    M.dispatch(be, "trace_new_step", {"project": "课题", "title": "第一步"})
    M.dispatch(be, "trace_insight", {"project": "课题", "kind": "pitfall", "text": "有个坑"})
    out = M.dispatch(be, "trace_read", {"project": "课题"})
    assert out.index("有个坑") < out.index("第一步"), "洞察要排在树前面，先看结论再看过程"


def test_mcp_insight_rejects_a_bad_kind(root: Path):
    be = M.LocalBackend(root)
    with pytest.raises(M.ToolError):
        M.dispatch(be, "trace_insight", {"project": "课题", "kind": "随便", "text": "x"})


def test_mcp_insight_tells_the_caller_which_id_it_got(root: Path):
    """不回传 id，agent 就永远说不出下一句「· 取代 p1」——而「取代了谁」只写在取代者身上，
    没有第二个地方能补。"""
    be = M.LocalBackend(root)
    out = M.dispatch(be, "trace_insight", {
        "project": "课题", "kind": "pitfall", "text": "PDBFixer 误杀 1,099 个带修饰残基"})
    assert "`p1`" in out
    assert core.parse_insights(body_of(root))["pitfall"][0]["id"] == "p1"


def test_mcp_insight_supersedes_folds_the_old_one_but_never_deletes_it(root: Path):
    """被取代的那条**留在磁盘上**。删掉它，半年后的人会以为一开始就查清楚了，
    然后重走一遍那条弯路——「当时是这么以为的」本身是信息。"""
    be = M.LocalBackend(root)
    M.dispatch(be, "trace_insight", {"project": "课题", "kind": "pitfall", "text": "误杀 1,099 个"})
    out = M.dispatch(be, "trace_insight", {
        "project": "课题", "kind": "pitfall", "text": "查清了，是 944 个", "supersedes": "p1"})
    assert "`p2`" in out and "p1" in out
    items = {i["id"]: i for i in core.parse_insights(body_of(root))["pitfall"]}
    assert "误杀 1,099 个" in items["p1"]["text"], "旧的那条一个字都不能少"
    assert items["p2"]["supersedes"] == ["p1"]
    assert items["p1"]["superseded_by"] == ["p2"], "反向关系是派生的，磁盘上只写在取代者身上"


def test_mcp_insight_can_reanchor_an_existing_line_in_place(root: Path):
    """重新锚定走的是「改那一行」，不是「再记一条」——id 不变，别处的「· 取代 p1」还指得对。"""
    be = M.LocalBackend(root)
    M.dispatch(be, "trace_insight", {"project": "课题", "kind": "fails", "text": "回译没用",
                                     "step": "013"})
    M.dispatch(be, "trace_insight", {"project": "课题", "id": "p1", "text": "回译没用",
                                     "step": "013b"})
    items = core.parse_insights(body_of(root))["fails"]
    assert len(items) == 1, "改写不该多出一条"
    assert "[[013b]]" in items[0]["text"] and "[[013]]" not in items[0]["text"].replace("[[013b]]", "")


def test_mcp_insight_needs_kind_and_text_unless_it_is_an_edit(root: Path):
    be = M.LocalBackend(root)
    with pytest.raises(M.ToolError, match="kind"):
        M.dispatch(be, "trace_insight", {"project": "课题", "text": "只给了文本"})


def test_mcp_insight_refuses_to_point_at_an_insight_that_does_not_exist(root: Path):
    """「· 取代 p9」指向一条不存在的记录，比不写更糟：它是给读的人看的指路牌。"""
    be = M.LocalBackend(root)
    with pytest.raises(M.ToolError):
        M.dispatch(be, "trace_insight", {"project": "课题", "kind": "works", "text": "x",
                                         "supersedes": "p9"})


def test_insight_kinds_match_between_schema_and_writer():
    """schema 里的枚举和写入层的小节表必须一一对应，否则会静默写丢。"""
    tool = next(t for t in M.TOOLS if t["name"] == "trace_insight")
    assert set(tool["inputSchema"]["properties"]["kind"]["enum"]) == set(W.INSIGHT_SECTIONS)
