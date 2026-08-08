"""写入路径的断言。重点是只追加原则（P2）和 id 分配永不重命名。"""

from pathlib import Path

import pytest

import trace_core as core
import trace_write as W


def mkroot(tmp_path: Path) -> Path:
    d = tmp_path / "steps"
    d.mkdir()
    return d


def ids(d: Path):
    return sorted(W.load(d), key=core.id_key)


# ------------------------------------------------------------ id 分配


def test_ids_are_allocated_without_ever_renaming(tmp_path: Path):
    d = mkroot(tmp_path)
    root, _ = W.create_step(d, title="起点")
    assert root.id == "001"

    first, _ = W.create_step(d, parent="001", title="第一条支")
    assert first.id == "002", "父还没有子节点时，直接续号"

    second, _ = W.create_step(d, parent="001", title="第二条支")
    assert second.id == "002b", "分叉共享数字，用字母区分兄弟"

    third, _ = W.create_step(d, parent="001", title="第三条支")
    assert third.id == "002c"

    # 关键：加了兄弟之后，第一个子节点的 id 没有被改动
    assert (d / first.dirname / core.NOTE_NAME).is_file()
    assert W.load(d)["002"].title == "第一条支"

    deeper, _ = W.create_step(d, parent="002", title="继续走")
    assert deeper.id == "003", "另一层的续号取全局最大数字 + 1"
    assert ids(d) == ["001", "002", "002b", "002c", "003"]


def test_unknown_parent_is_rejected(tmp_path: Path):
    d = mkroot(tmp_path)
    with pytest.raises(W.NotFound):
        W.create_step(d, parent="999", title="x")


def test_empty_title_is_rejected(tmp_path: Path):
    d = mkroot(tmp_path)
    with pytest.raises(W.WriteError):
        W.create_step(d, title="   ")


def test_idempotency_key_prevents_duplicate_steps_on_agent_retry(tmp_path: Path):
    d = mkroot(tmp_path)
    a, created_a = W.create_step(d, title="sweep g2", key="sweep-focal-g2")
    b, created_b = W.create_step(d, title="sweep g2 重试", key="sweep-focal-g2")
    assert created_a is True and created_b is False
    assert a.id == b.id
    assert len(W.load(d)) == 1


# ------------------------------------------------------------ 只追加


def test_parent_cannot_be_changed(tmp_path: Path):
    d = mkroot(tmp_path)
    W.create_step(d, title="根")
    child, _ = W.create_step(d, parent="001", title="子")
    with pytest.raises(W.Conflict):
        W.update_step(d, child.id, {"parent": None})
    with pytest.raises(W.Conflict):
        W.update_step(d, child.id, {"id": "999"})


def test_status_and_body_are_mutable(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="试试 focal loss")
    W.update_step(d, s.id, {"status": "dead", "body": "## 结论\n正样本太少，放弃这条路。"})
    again = W.load(d)[s.id]
    assert again.status == "dead"
    assert "放弃这条路" in again.body
    assert again.parent is None and again.id == s.id


def test_unknown_status_is_rejected_on_update(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.update_step(d, s.id, {"status": "success"})


def test_unknown_field_is_rejected(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.update_step(d, s.id, {"metrics_json": "{}"})


def test_note_survives_a_write_read_roundtrip(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(
        d, title="含 3:1 采样的标题", body="## 为什么\n因为 [[001]] 说要试。",
        date="2026-08-07", commit="a3f9c21", author="agent:claude", tags=["loss", "imbalance"],
    )
    back = W.load(d)[s.id]
    assert back.title == "含 3:1 采样的标题"
    assert back.tags == ["loss", "imbalance"]
    assert back.commit == "a3f9c21" and back.author == "agent:claude"
    assert "[[001]]" in back.body


def test_title_change_does_not_rename_the_directory(tmp_path: Path):
    """目录改名会让所有已经发出去的相对链接失效。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="原标题")
    before = s.dirname
    W.update_step(d, s.id, {"title": "完全不同的新标题"})
    assert (d / before).is_dir()
    assert W.load(d)[s.id].title == "完全不同的新标题"


# ------------------------------------------------------------ 附件


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "C:/windows/x", "a/../../b", ".hidden", "note.md"])
def test_path_traversal_is_blocked(bad):
    with pytest.raises(W.WriteError):
        W.safe_relpath(bad)


def test_attachment_round_trip(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="有日志的一步")
    W.attach_file(d, s.id, "logs/train.log", b"epoch 1 loss 0.42\n")
    f = core.compile_forest(d)["steps"][0]["files"]
    assert [x["path"] for x in f] == ["logs/train.log"]
    W.delete_file(d, s.id, "logs/train.log")
    assert core.compile_forest(d)["steps"][0]["files"] == []


def test_oversized_attachment_is_refused(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.attach_file(d, s.id, "big.bin", b"0" * (W.MAX_FILE_BYTES + 1))


def test_attach_auto_keeps_a_real_filename(tmp_path: Path):
    """`train.log` 比一串哈希好读得多，有文件名就用文件名。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    info = W.attach_auto(d, s.id, b"loss 0.42\n", filename="train.log", mime="text/plain")
    assert info["path"] == "train.log" and info["reused"] is False


def test_attach_auto_names_clipboard_images_by_content_hash(tmp_path: Path):
    """剪贴板里的位图没有文件名，用内容哈希命名——于是同一张图粘贴两次只存一份。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    a = W.attach_auto(d, s.id, b"\x89PNG fake", mime="image/png")
    assert a["path"].startswith("img-") and a["path"].endswith(".png")

    b = W.attach_auto(d, s.id, b"\x89PNG fake", mime="image/png")
    assert b["path"] == a["path"] and b["reused"] is True
    assert len(core.compile_forest(d)["steps"][0]["files"]) == 1


def test_attach_auto_does_not_clobber_a_different_file_with_the_same_name(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    a = W.attach_auto(d, s.id, b"first", filename="fig.png", mime="image/png")
    b = W.attach_auto(d, s.id, b"second", filename="fig.png", mime="image/png")
    assert (a["path"], b["path"]) == ("fig.png", "fig-2.png")
    assert [f["path"] for f in core.compile_forest(d)["steps"][0]["files"]] == ["fig-2.png", "fig.png"]


def test_attach_auto_reuses_on_identical_name_and_content(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    W.attach_auto(d, s.id, b"same", filename="a.txt")
    again = W.attach_auto(d, s.id, b"same", filename="a.txt")
    assert again["reused"] is True
    assert len(core.compile_forest(d)["steps"][0]["files"]) == 1


def test_attach_auto_rejects_empty_and_oversized(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.attach_auto(d, s.id, b"")
    with pytest.raises(W.WriteError):
        W.attach_auto(d, s.id, b"0" * (W.MAX_FILE_BYTES + 1), filename="big.bin")


def test_attach_auto_blocks_path_traversal_in_the_filename(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError):
        W.attach_auto(d, s.id, b"x", filename="../../evil.txt")


def test_slugify_keeps_cjk_and_drops_path_hostile_chars():
    assert W.slugify("试了 3:1 采样 / AUC 0.82") == "试了-3-1-采样-auc-0-82"
    assert W.slugify("!!!") == "step"
