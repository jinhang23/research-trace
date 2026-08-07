"""解析与校验的断言。重点是**残缺输入必须仍能出图**——十年后的日志一定是残缺的。"""

from pathlib import Path

import trace_core as core


def codes(ws):
    return sorted(w["code"] for w in ws)


# ------------------------------------------------------------ front-matter


def test_title_may_contain_colons():
    """刻意不用 YAML 的理由：`title: 试了 3:1 采样` 在 YAML 里是语法错误。"""
    meta, body, ws = core.parse_note("---\nid: 001\ntitle: 试了 3:1 采样，AUC 0.82\n---\n正文\n")
    assert meta["title"] == "试了 3:1 采样，AUC 0.82"
    assert body == "正文"
    assert ws == []


def test_bom_and_crlf_are_tolerated():
    meta, body, ws = core.parse_note("﻿---\r\nid: 001\r\ntitle: x\r\n---\r\n\r\nhello\r\n")
    assert meta["id"] == "001" and body == "hello"
    assert ws == []


def test_quoted_values_are_unwrapped():
    meta, _, _ = core.parse_note('---\nid: 001\ntitle: "带引号: 的标题"\n---\n')
    assert meta["title"] == "带引号: 的标题"


def test_missing_front_matter_keeps_the_text_as_body():
    meta, body, ws = core.parse_note("就是一段没有 front-matter 的正文")
    assert meta == {} and body.startswith("就是一段")
    assert codes(ws) == ["no_front_matter"]


def test_unclosed_front_matter_does_not_eat_the_note():
    _, body, ws = core.parse_note("---\nid: 001\ntitle: x\n\n正文在这里")
    assert "正文在这里" in body
    assert codes(ws) == ["unclosed_front_matter"]


def test_missing_status_defaults_to_wip():
    step, ws = core.build_step("001_x", {"id": "001", "title": "x"}, "")
    assert step.status == "wip" and ws == []


def test_unknown_status_falls_back_with_a_warning():
    step, ws = core.build_step("001_x", {"id": "001", "title": "x", "status": "success"}, "")
    assert step.status == "wip"
    assert codes(ws) == ["bad_status"]


def test_front_matter_id_wins_over_directory_name():
    step, ws = core.build_step("007_x", {"id": "009", "title": "x"}, "")
    assert step.id == "009"
    assert codes(ws) == ["id_mismatch"]


# ------------------------------------------------------------ validate


def S(sid, parent=None, body=""):
    return core.Step(id=sid, parent=parent, title=sid, body=body, dirname=f"{sid}_x")


def test_dangling_parent_degrades_to_root_and_still_renders():
    by_id, ws = core.validate([S("001"), S("005", "999")])
    assert by_id["005"].parent is None
    assert codes(ws) == ["dangling_parent"]
    order = core.compute_order(by_id, core.build_children(by_id))
    assert order == ["001", "005"], "构建必须继续，不能拒绝工作"


def test_cycle_is_reported_and_broken_so_the_build_continues():
    by_id, ws = core.validate([S("001", "003"), S("002", "001"), S("003", "002")])
    assert codes(ws) == ["cycle"]
    assert "001 → 002 → 003" in ws[0]["message"] or "→" in ws[0]["message"]
    order = core.compute_order(by_id, core.build_children(by_id))
    assert len(order) == 3, "环被断开后三个节点都要出现在图上"


def test_duplicate_id_is_kept_visible_rather_than_dropped():
    a, b = S("003"), S("003")
    b.dirname = "003_other"
    by_id, ws = core.validate([a, b])
    assert codes(ws) == ["duplicate_id"]
    assert len(by_id) == 2 and "003~dup2" in by_id


def test_backlinks_replace_the_need_for_multi_parent():
    by_id, _ = core.validate([S("001"), S("003"), S("007", "001", body="综合了 [[003]] 的结论")])
    back = core.compute_backlinks(by_id)
    assert back["003"] == ["007"]
    assert back["001"] == []


def test_lineage_walks_to_the_root():
    by_id, _ = core.validate([S("001"), S("002", "001"), S("003", "002")])
    assert core.lineage(by_id, "003") == ["001", "002", "003"]


# ------------------------------------------------------------ compile


def test_compile_is_deterministic(tmp_path: Path):
    d = tmp_path / "steps"
    (d / "001_a").mkdir(parents=True)
    (d / "001_a" / "note.md").write_text("---\nid: 001\ntitle: a\n---\n为什么\n", encoding="utf-8")
    (d / "002_b").mkdir(parents=True)
    (d / "002_b" / "note.md").write_text("---\nid: 002\nparent: 001\ntitle: b\n---\n", encoding="utf-8")
    assert core.compile_forest(d) == core.compile_forest(d)


def test_directory_without_a_note_is_skipped_silently(tmp_path: Path):
    d = tmp_path / "steps"
    (d / "001_a").mkdir(parents=True)
    (d / "001_a" / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")
    (d / "scratch").mkdir()
    (d / "scratch" / "tmp.txt").write_text("x", encoding="utf-8")
    f = core.compile_forest(d)
    assert [s["id"] for s in f["steps"]] == ["001"]
    assert f["warnings"] == []


def test_files_are_derived_and_exclude_the_note(tmp_path: Path):
    d = tmp_path / "steps" / "001_a"
    d.mkdir(parents=True)
    (d / "note.md").write_text("---\nid: 001\ntitle: a\n---\n", encoding="utf-8")
    (d / "train.log").write_text("loss", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "note.md").write_text("nested", encoding="utf-8")
    f = core.compile_forest(tmp_path / "steps")
    assert [x["path"] for x in f["steps"][0]["files"]] == ["sub/note.md", "train.log"]
