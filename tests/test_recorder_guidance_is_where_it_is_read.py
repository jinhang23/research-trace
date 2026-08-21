"""让 Recorder 填对字段的说明，必须放在它**会读到**的地方。

协议文档在 fork 时读一次，schema 在每次调用时都在眼前。此前 parent_id 在 schema 里
是光秃秃一个 {"type": "string"}，唯一的指导是协议文档里那句「Set parent_id **only
for** an actual continuation」——写成了限制而不是要求，还夹在讲 Chapter 的段落末尾。
结果一个项目 14 条记录一个 parent 都没有。

所以这条钉的是位置，不是措辞：说明必须在 schema 里，且必须说出后果。
"""
from __future__ import annotations

from research_trace.mcp import TOOLS


def _tool(name: str) -> dict:
    return next(item for item in TOOLS if item["name"] == name)


def _field(name: str, field: str) -> dict:
    return _tool(name)["inputSchema"]["properties"][field]


def test_the_fields_that_build_the_views_are_documented_in_the_schema():
    """三个字段，各自撑着一个视图。没有说明 = 模型不知道漏掉它意味着什么。"""
    for field in ("parent_id", "source_event_ids", "body", "title"):
        text = _field("trace_record", field).get("description", "")
        assert text.strip(), f"{field} 在 schema 里没有一个字的说明"


def test_the_parent_field_says_what_omitting_it_costs():
    text = _field("trace_record", "parent_id")["description"]
    # 后果，不是「请填写此字段」
    assert "structure view" in text
    assert "recent_nodes" in text, "得告诉它去哪儿拿候选 id，否则填不了"
    # 根节点仍然合法：把它写成「必须填」会换来一堆胡乱认的父亲
    assert "root" in text


def test_the_body_field_carries_a_framework_and_not_just_a_type():
    text = _field("trace_record", "body")["description"]
    for question in ("CLAIM", "BASIS", "CONSEQUENCE"):
        assert question in text, f"正文框架缺了 {question}"
    # 认识论状态是这套框架里唯一无法被下游发现的错误，必须点名
    assert "inference" in text and "observation" in text
    assert "own language" in text, "不能诱导模型把记录翻译成英文"


def test_the_protocol_document_still_carries_the_same_rule():
    """两处说的必须是同一件事。文档和 schema 各写一半，读者只会拿到一半。"""
    protocol = (__import__("pathlib").Path(__file__).resolve().parents[1]
                / "hooks" / "RECORDER_PROTOCOL.md").read_text(encoding="utf-8")
    assert "## What a Node looks like" in protocol
    assert "structure_gaps" in protocol, "文档得说清那个回执是什么"
    assert "built from\n   this field and nothing else" in protocol
    # 旧措辞会把「要求」读成「限制」，必须已经撤掉
    assert "Set `parent_id` only for" not in protocol
