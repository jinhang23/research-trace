"""结构字段漏了要当场说出来，而且是说事实、不是拦请求。

背景：一个真实项目攒了 14 条记录，正文里连输入输出表格、绝对路径、体积都写全了，
而 parent_id 全空、artifact 一个没登记、source_event_ids 只有 3 条。结果结构图是
14 个孤儿、数据流视图整个不出现——信息全在，但没有一样落在系统能用的字段里。

协议文档在 fork 时读一次，tool schema 在调用时看一眼，两者都发生在「这一条到底填没填」
之前。这里补的是写完之后、下一条之前的那一次：说的是已经发生的事，不是规则。
"""
from __future__ import annotations

import pytest

from research_trace.storage import Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path)


def _project(store: Store) -> str:
    return store.create_project("结构测试")["id"]


def test_the_first_node_of_a_project_is_not_nagged_about_being_a_root(store):
    """第一条记录当然没有父亲。对着一个空项目喊「你没连上」是纯噪声。"""
    pid = _project(store)
    node = store.record_node(pid, idempotency_key="k0", title="起点", source_event_ids=["ev1"])
    gaps = " ".join(node.get("structure_gaps", []))
    assert "root" not in gaps


def test_a_later_node_without_a_parent_is_told_what_that_costs(store):
    pid = _project(store)
    store.record_node(pid, idempotency_key="k0", title="第一步", source_event_ids=["ev1"])
    node = store.record_node(pid, idempotency_key="k1", title="第二步", source_event_ids=["ev2"])

    gaps = " ".join(node.get("structure_gaps", []))
    assert "root" in gaps, "项目里已有记录，还不设 parent，必须说一声"
    # 说的是后果，不是「请填写此字段」
    assert "structure view" in gaps and "parent_id" in gaps
    # 但写入照样成功：这是回执，不是校验
    assert node["id"] and node["title"] == "第二步"


def test_setting_a_parent_silences_it(store):
    pid = _project(store)
    first = store.record_node(pid, idempotency_key="k0", title="第一步", source_event_ids=["ev1"])
    node = store.record_node(
        pid, idempotency_key="k1", title="第二步", parent_id=first["id"], source_event_ids=["ev2"],
    )
    assert "root" not in " ".join(node.get("structure_gaps", []))


def test_a_missing_source_event_id_is_reported(store):
    pid = _project(store)
    node = store.record_node(pid, idempotency_key="k0", title="起点")
    assert any("source_event_ids" in gap for gap in node.get("structure_gaps", []))


def test_a_project_that_never_registered_an_artifact_is_told_the_data_flow_stays_hidden(store):
    pid = _project(store)
    node = store.record_node(pid, idempotency_key="k0", title="起点", source_event_ids=["ev1"])
    assert any("data-flow" in gap for gap in node.get("structure_gaps", []))


def test_a_human_root_is_a_decision_and_not_a_gap(store):
    """人自己写的根节点是个决定。回执是给 Recorder 的，不是给人挑刺的。"""
    pid = _project(store)
    store.record_node(pid, idempotency_key="k0", title="第一步", created_by="human")
    node = store.record_node(pid, idempotency_key="k1", title="另起一条线", created_by="human")
    assert "structure_gaps" not in node


def test_context_reports_how_far_the_project_actually_got(store):
    """和 dataflow 的 unkeyed 是同一句话：视图会数出你漏掉的。

    一张空的结构图有两种读法——「这项目本来就是一堆互不相干的记录」，或者
    「没人填过 parent_id」。这三个数把两者分开，而且是在写下一条**之前**看到。
    """
    pid = _project(store)
    first = store.record_node(pid, idempotency_key="k0", title="第一步", source_event_ids=["ev1"])
    store.record_node(pid, idempotency_key="k1", title="孤儿")
    store.record_node(pid, idempotency_key="k2", title="第三步", parent_id=first["id"])

    structure = store.context(project_id=pid)["project"]["structure"]
    assert structure == {
        "nodes": 3,
        "linked_to_a_parent": 1,
        "with_source_event_ids": 1,
        "artifacts_registered": 0,
    }
