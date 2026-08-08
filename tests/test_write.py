"""写入路径的断言。重点是只追加原则（P2）和 id 分配永不重命名。"""

import datetime
import shutil
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


def test_the_server_fills_in_todays_date_when_nobody_passes_one(tmp_path: Path):
    """FORMAT.md 把 front-matter 定义为「机器记录」。日期不自动填的话，
    agent 建的步骤（占多数）永远没有时间坐标，「这条线是什么时候走的」就答不了。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="agent 建的一步")
    assert s.date == datetime.date.today().strftime("%Y-%m-%d")
    assert f"date: {s.date}" in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert W.load(d)[s.id].date == s.date, "要真的落进 note.md，不只是返回值好看"


def test_an_explicit_date_is_never_overwritten(tmp_path: Path):
    """补记半年前的实验时，人给的日期必须赢过服务端的今天。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="补记的一步", date="2026-01-09")
    assert s.date == "2026-01-09"


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


def test_a_duplicate_id_label_can_never_be_written_back(tmp_path: Path):
    """`001~dup2` 是 validate() 在发现两个同 id 目录时贴的**临时显示标签**，纯派生。
    让 PATCH 写下去的话，render_note 会把它写进 front-matter 的 id: ——
    派生信息变成存储信息（违反 P1），而且写进去的是个不合法的 id：
    它排不进 id 序、parent 跟着断、check 的退出码反而从 1 掉回 0。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="原始")
    dup = d / "001_从备份恢复的"
    dup.mkdir()
    shutil.copyfile(d / s.dirname / core.NOTE_NAME, dup / core.NOTE_NAME)

    marked = [sid for sid in W.load(d) if "~" in sid]
    assert marked == ["001~dup2"], "前提：core 确实会贴这个标签"

    with pytest.raises(W.Conflict, match="临时标签"):
        W.update_step(d, "001~dup2", {"status": "dead"})
    assert "id: 001\n" in (dup / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "~" not in (dup / core.NOTE_NAME).read_text(encoding="utf-8")


# ------------------------------------------------------------ 附件


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "C:/windows/x", "a/../../b", ".hidden", "note.md"])
def test_path_traversal_is_blocked(bad):
    with pytest.raises(W.WriteError):
        W.safe_relpath(bad)


# Windows 大小写不敏感、且会在打开文件前剥掉名字尾部的空格和点；
# NTFS 还把 `note.md:x` 解释成 note.md 的备用数据流。这四种写法在老实现里
# 全部通过校验，上传完那一步的整条记录就被文件字节替换掉了。
# （前导空格不在此列：实测 Windows 只剥尾部的空格和点，' note.md' 是另一个真文件。）
@pytest.mark.parametrize("alias", ["NOTE.MD", "Note.Md", "note.md ", "note.md.", "note.md:evil",
                                   "note.md. .", "NoTe.Md.."])
def test_note_md_cannot_be_overwritten_through_a_filename_alias(alias):
    with pytest.raises(W.WriteError):
        W.safe_relpath(alias)


def test_uploading_a_note_md_alias_leaves_the_record_untouched(tmp_path: Path):
    """走完整条上传路径确认一遍：需求 1 里 agent/脚本用的就是这个接口。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="半年的记录")
    note = d / s.dirname / core.NOTE_NAME
    before = note.read_bytes()
    for alias in ("NOTE.MD", "note.md."):
        with pytest.raises(W.WriteError):
            W.attach_file(d, s.id, alias, b"PWNED")
    assert note.read_bytes() == before
    assert W.load(d)[s.id].title == "半年的记录"


def test_a_trailing_dot_cannot_silently_rewrite_another_attachment(tmp_path: Path):
    """`report.txt.` 在 Windows 上落盘就是 report.txt：返回给调用方的 path
    和磁盘上真正被改写的文件不是同一个，等于一条静默的别名覆盖通道。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    W.attach_file(d, s.id, "report.txt", b"ORIGINAL")
    with pytest.raises(W.WriteError, match="空格或点结尾"):
        W.attach_file(d, s.id, "report.txt.", b"HIJACK")
    assert (d / s.dirname / "report.txt").read_bytes() == b"ORIGINAL"


@pytest.mark.parametrize("bad", ["CON", "con.txt", "nul", "COM1.log", "lpt9", "aux.png"])
def test_windows_device_names_are_refused(bad):
    """CON/PRN/AUX/NUL/COM1-9/LPT1-9（带扩展名也算）打开的是设备不是文件：
    写进去的内容凭空消失。"""
    with pytest.raises(W.WriteError, match="设备名"):
        W.safe_relpath(bad)


def test_a_note_md_inside_a_subdirectory_is_still_a_normal_attachment(tmp_path: Path):
    """挡的是步骤目录顶层的那一个——list_files 也只在顶层排除 note.md。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x")
    W.attach_file(d, s.id, "refs/note.md", "# 引用来源".encode("utf-8"))
    assert [f["path"] for f in core.compile_forest(d)["steps"][0]["files"]] == ["refs/note.md"]


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
