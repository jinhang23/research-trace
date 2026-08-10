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


# ------------------------------------------------------------ 数据依赖（input:）


def test_inputs_record_a_dependency_that_the_tree_cannot_express(tmp_path: Path):
    """森林是单父树，数据流是 DAG。016 的输入同时来自 013 和 014，`parent` 只能挂一个
    ——这正是「树形是错的」那个 bug 的来源：结构被迫替数据流说话。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="口袋-配体配对",
                         inputs=["013 | pocket_composition.csv",
                                 {"step": "014", "note": "rmscore_pairs.csv"}])
    text = (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "input: 013 | pocket_composition.csv" in text
    assert "input: 014 | rmscore_pairs.csv" in text, "可重复，不是后写覆盖先写"
    back = W.load(d)[s.id]
    assert [i["step"] for i in back.inputs] == ["013", "014"]


def test_an_input_may_point_at_a_step_that_does_not_exist_yet(tmp_path: Path):
    """建立顺序不定：agent 常常先建 016（今天跑的这一步）再回头补 013b。
    要求先有后引，结果是大家干脆不写 input——那就什么都没记下。
    和 parent 的既有处理一致：悬空由**读侧**说出来，写入侧不拦。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="先建的那一步", inputs=["013b | 还没建的那份产物"])
    assert W.load(d)[s.id].inputs[0]["step"] == "013b"


def test_an_input_that_is_not_an_id_shaped_string_is_refused(tmp_path: Path):
    """悬空引用是可恢复的，笔误不是：`input: 十三` 永远不可能指向任何东西。"""
    d = mkroot(tmp_path)
    with pytest.raises(W.WriteError, match="形状"):
        W.create_step(d, title="x", inputs=["十三 | 某份产物"])


def test_add_inputs_appends_and_dedups(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", inputs=["013 | a.csv"])
    W.update_step(d, s.id, {"add_inputs": ["014 | b.csv", "013 | a.csv"]})
    got = W.load(d)[s.id].inputs
    assert [(i["step"], i["note"]) for i in got] == [("013", "a.csv"), ("014", "b.csv")]
    # 同一步的**另一份**产物不是重复，不能被去重吃掉
    W.update_step(d, s.id, {"add_inputs": ["013 | c.csv"]})
    assert len(W.load(d)[s.id].inputs) == 3
    W.update_step(d, s.id, {"inputs": []})
    assert W.load(d)[s.id].inputs == [], "整组替换要能清空"


# ------------------------------------------------------------ 代码位置（code:）


def test_code_can_point_at_a_snapshot_when_there_is_no_git(tmp_path: Path):
    """代码不在 git 里时（超算上直接改脚本、跑完打个快照目录留一份逐文件校验和），
    「代码在这里」在可溯源性上不比 commit 差。只认 commit 等于把这类记录永远压在 L1。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x",
                         code=["snapshot | /orange/lab/run_snapshots/20260809 | "
                               "manifest=MANIFEST.md5 n=43"])
    text = (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "code: snapshot | /orange/lab/run_snapshots/20260809 | " in text
    assert "manifest=MANIFEST.md5" in text
    c = W.load(d)[s.id].code[0]
    assert (c["kind"], c["attrs"]["n"]) == ("snapshot", "43")


def test_an_unknown_code_kind_is_refused_on_write(tmp_path: Path):
    """读侧对没见过的 kind 是宽容的（十年后多出一种形态很正常），
    写入侧当场就能问清楚——没有理由让一个笔误落盘。"""
    d = mkroot(tmp_path)
    with pytest.raises(W.WriteError, match="git/snapshot/container"):
        W.create_step(d, title="x", code=["svn | /some/where"])


def test_a_commit_is_never_duplicated_as_a_code_line(tmp_path: Path):
    """`commit: c1d2e3f` 等价于一条 `code: git`，但那是**派生**的。
    落盘第二份就是双真相源——上一代系统正是这么死的。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", commit="c1d2e3f")
    text = (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "commit: c1d2e3f" in text and "code:" not in text
    # 读侧派生出来的那条（from == "commit"）原样 PATCH 回来也不能多写一行
    derived = W.load(d)[s.id].to_dict()["code"]
    assert derived and derived[0].get("from") == "commit"
    W.update_step(d, s.id, {"code": derived})
    assert "code:" not in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")


# ------------------------------------------------------------ 结构化的 path


def test_a_legacy_path_line_is_written_back_byte_for_byte(tmp_path: Path):
    """向后兼容是硬要求：现存的 `位置 | 说明` 一个字都不用改。
    中文说明里有逗号、有 `sha256:…`，既不是 role 也不是纯 k=v，自然落进说明。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=[
        "/blue/g/u/data/agnews-clean | 去重后的训练集，12 GB",
        "/orange/g/u/ckpt/best.pt | 权重，265 MB，sha256:7d4e1a9c…",
        "/orange/g/u/run | lr=3e-4 的那次运行",
    ])
    lines = [l for l in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8").split("\n")
             if l.startswith("path:")]
    assert lines == [
        "path: /blue/g/u/data/agnews-clean | 去重后的训练集，12 GB",
        "path: /orange/g/u/ckpt/best.pt | 权重，265 MB，sha256:7d4e1a9c…",
        # 不是**全部** token 都是 k=v，所以整段还是说明——顺手写个等号不该把
        # 人写的说明变成机器字段。
        "path: /orange/g/u/run | lr=3e-4 的那次运行",
    ]
    assert W.load(d)[s.id].paths[2]["attrs"] == {}


def test_a_structured_path_survives_the_round_trip(tmp_path: Path):
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=[{
        "loc": "/orange/lab/pockets", "role": "output", "desc": "纯 RNA 口袋",
        "n": 4554, "size": 620756992, "md5": "7d4e1a9c", "checked": "2026-08-09"}])
    line = next(l for l in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8").split("\n")
                if l.startswith("path:"))
    assert line == ("path: /orange/lab/pockets | output | 纯 RNA 口袋 | "
                    "n=4554 size=620756992 md5=7d4e1a9c checked=2026-08-09"), \
        "属性顺序固定：P3 要求同样的输入两次构建逐字节一致"
    p = W.load(d)[s.id].paths[0]
    assert (p["role"], p["note"], p["size"], p["n"]) == ("output", "纯 RNA 口袋", 620756992, 4554)


def test_an_unknown_attribute_is_kept(tmp_path: Path):
    """半年后有人写了 `nodes=…`，系统不该把它吃掉——认不认得出来是程序的事，
    写下来的东西是人的事。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=[
        {"loc": "/blue/a", "attrs": {"nodes": "8", "size": "12"}}])
    assert "nodes=8" in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert W.load(d)[s.id].paths[0]["attrs"]["nodes"] == "8"


@pytest.mark.parametrize("bad,msg", [
    ({"loc": "/blue/a", "size": "12 GB"}, "整数"),
    ({"loc": "/blue/a", "checked": "上周"}, "YYYY-MM-DD"),
    ({"loc": "/blue/a", "role": "产物"}, "role"),
    ({"loc": "/blue/a", "attrs": {"note": "有 空格"}}, "空白或竖线"),
])
def test_machine_fields_are_validated_on_the_way_in(tmp_path: Path, bad, msg):
    """这几个是**机器字段**：写歪了就再也算不出「这条路径现在是什么状态」。
    带空白的值读回来还会变成说明文字——一次静默的语义漂移。"""
    d = mkroot(tmp_path)
    with pytest.raises(W.WriteError, match=msg):
        W.create_step(d, title="x", paths=[bad])


def test_an_unrelated_edit_does_not_erase_the_checksums(tmp_path: Path):
    """在网页上改一下标题，刚核对完的 164 条校验和不能就这么没了。
    render_note 是全量重写 front-matter 的，所以这条必须钉住。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=[
        {"loc": "/orange/lab/pockets", "role": "output", "desc": "口袋",
         "md5": "7d4e1a9c", "checked": "2026-08-09"}],
        inputs=["013 | a.csv"], code=["git | https://github.com/me/repo"])
    before = [l for l in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8").split("\n")
              if l.startswith(("path:", "input:", "code:"))]
    W.update_step(d, s.id, {"title": "换个标题", "status": "done"})
    after = [l for l in (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8").split("\n")
             if l.startswith(("path:", "input:", "code:"))]
    assert after == before


# ------------------------------------------------------------ 路径核对


def test_recording_a_missing_path_marks_it_without_deleting_it(tmp_path: Path):
    """需求的来历：这次核对发现三个目录已经被删了（其中一个 57 GB）。
    「没了」是溯源结论，和 dead 一样有价值——标出来，不删记录。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=[
        {"loc": "/blue/lab/cif_files", "role": "input", "desc": "原始 CIF",
         "size": 61203283968, "checked": "2026-07-01"}])
    out = W.record_path_check(d, s.id, "/blue/lab/cif_files", exists=False, date="2026-08-09")
    assert out["line"] == ("path: /blue/lab/cif_files | input | 原始 CIF | "
                           "size=61203283968 missing=2026-08-09")
    p = W.load(d)[s.id].paths[0]
    assert p["state"] == "missing" and p["checked"] == "", "checked 和 missing 互斥"
    assert p["size"] == 61203283968, "没了的那个有 57 GB，这正是要留下来的信息"
    assert p["role"] == "input" and p["note"] == "原始 CIF", "人写的判断，机器不许改"


def test_recording_a_present_path_clears_the_missing_mark(tmp_path: Path):
    """清掉了又被重建也是常事。最后一次核对是哪种结果，现在就是哪种状态。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=["/blue/a | 训练集"])
    W.record_path_check(d, s.id, "/blue/a", exists=False, date="2026-08-01")
    W.record_path_check(d, s.id, "/blue/a", exists=True, date="2026-08-09", size=1024, n=42)
    p = W.load(d)[s.id].paths[0]
    assert (p["state"], p["checked"], p["missing"]) == ("present", "2026-08-09", "")
    assert (p["size"], p["n"]) == (1024, 42)
    assert p["note"] == "训练集"


def test_a_path_check_cannot_invent_a_path(tmp_path: Path):
    """核对结果只写在已经记下来的路径上——凭空多出一条会让
    「这一步产出了什么」变成机器说了算。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="x", paths=["/blue/a | 训练集"])
    with pytest.raises(W.NotFound, match="没有记着"):
        W.record_path_check(d, s.id, "/blue/b", exists=True)


# ------------------------------------------------------------ 洞察的 id
#
# 这几条本该和 test_insights.py 放一起，但那个文件不归本轮的写入侧改。
# 断言的是写入侧的行为：分配、重锚、取代、绝不删除。


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path


def pbody(root: Path, name=core.PROJECT_NOTE) -> str:
    return (core.project_dir(root, "课题") / name).read_text(encoding="utf-8")


def test_every_new_insight_gets_an_id(project: Path):
    """要能指着某一条说「就是它」，才谈得上重锚和取代。"""
    a = W.add_insight(project, "课题", "pitfall", "PDBFixer 误杀 1,099 个")
    b = W.add_insight(project, "课题", "works", "去重之后所有模型都涨 0.4 个点")
    assert (a["id"], b["id"]) == ("p1", "p2")
    assert "- `p1` PDBFixer 误杀 1,099 个" in pbody(project)


def test_an_insight_can_be_re_anchored_without_changing_its_id(project: Path):
    """用户的原话：证据随内容搬到了 013b，锚点改不了。
    id 不变是关键——别处那句「· 取代 p1」还得指得对。"""
    W.add_insight(project, "课题", "pitfall", "PDBFixer 误杀 1,099 个，见 [[013]]")
    out = W.update_insight(project, "课题", "p1",
                           text="PDBFixer 误杀 1,099 个，见 [[013b]]")
    assert out["id"] == "p1"
    assert "见 [[013b]]" in pbody(project) and "见 [[013]]，" not in pbody(project)


def test_superseding_never_deletes_the_old_one(project: Path):
    """「1,099」那条是当时的判断。删掉它，就没人知道数字为什么变了。"""
    W.add_insight(project, "课题", "pitfall", "PDBFixer 误杀 1,099 个")
    out = W.add_insight(project, "课题", "pitfall",
                        "PDBFixer 误杀 944 个带修饰残基，见 [[013b]]", supersedes="p1")
    body = pbody(project)
    assert out["line"].endswith("· 取代 p1")
    assert "PDBFixer 误杀 1,099 个" in body, "被取代的那条永远留着"
    ins = core.parse_insights(body)["pitfall"]
    assert [i["id"] for i in ins] == ["p1", "p2"]
    assert ins[0]["superseded_by"] == ["p2"], "「p1 已被取代」是派生的，不写第二份"
    assert "取代" not in ins[0]["raw"]


def test_superseding_something_that_does_not_exist_is_refused(project: Path):
    """「· 取代 p9」是给读的人看的指路牌，指向一条不存在的记录比不写更糟。"""
    with pytest.raises(W.WriteError, match="p9"):
        W.add_insight(project, "课题", "pitfall", "x", supersedes="p9")


def test_editing_the_text_keeps_the_supersede_mark(project: Path):
    """改一句措辞不该顺手把「它取代了谁」抹掉。"""
    W.add_insight(project, "课题", "pitfall", "旧的")
    W.add_insight(project, "课题", "pitfall", "新的", supersedes="p1")
    W.update_insight(project, "课题", "p2", text="新的，更准确的说法")
    assert "- `p2` 新的，更准确的说法 · 取代 p1" in pbody(project)


def test_updating_an_unknown_insight_is_a_404(project: Path):
    with pytest.raises(W.NotFound, match="p7"):
        W.update_insight(project, "课题", "p7", text="x")


def test_the_translation_shares_one_id_space_with_the_original(project: Path):
    """同一条洞察在 project.md 和 project.en.md 里是同一个 id。
    只看一份就会撞号，而撞号之后「· 取代 p2」同时指向两条不同的记录。"""
    W.add_insight(project, "课题", "pitfall", "tokenizer 版本不一致会静默改分词")
    en = W.add_insight(project, "课题", "pitfall",
                       "Mismatched tokenizer versions silently change tokenization",
                       lang="en")
    assert en["id"] == "p2" and en["path"] == "project.en.md"
    assert "## Pitfalls" in pbody(project, "project.en.md"), "译文用译过的小节名"
    assert "- `p2` Mismatched" in pbody(project, "project.en.md")


def test_the_supersede_word_is_translated_in_the_translation(project: Path):
    W.add_insight(project, "课题", "pitfall", "old one", lang="en")
    W.add_insight(project, "课题", "pitfall", "new one", supersedes="p1", lang="en")
    assert "· supersedes p1" in pbody(project, "project.en.md")


def test_writing_insights_never_touches_the_deleted_section(project: Path):
    """`## 已删除` 是目录被删之后仅存的证据。洞察那条路碰不到它——
    中文版护得住、英文版护不住是说不过去的。"""
    d = core.steps_dir_of(project, "课题")
    s, _ = W.create_step(d, title="误建的一步")
    W.delete_step(d, s.id, "粘错了令牌")
    W.add_insight(project, "课题", "pitfall", "别把令牌粘进正文")
    W.update_project(project, "课题", insights="## 坑\n- 全换掉了")
    assert "粘错了令牌" in pbody(project)


def test_the_mcp_shaped_call_still_works_and_reports_the_id(project: Path):
    """既有调用方传的是 add=(kind, text) 这个二元组，不能因为多了 id 就断掉。"""
    p = W.update_project(project, "课题", add=("fails", "回译一直没用"))
    assert p.insight_id == "p1"
    assert "- `p1` 回译一直没用" in pbody(project)
    p2 = W.update_project(project, "课题",
                          add={"kind": "fails", "text": "回译一直没用，三次都在噪声内",
                               "id": "p1"})
    assert p2.insight_id == "p1" and "三次都在噪声内" in pbody(project)
    assert pbody(project).count("- `p1`") == 1, "更新是就地改一行，不是再追加一条"


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


# 翻译文件和 note.md 一样是**记录本身**。老守卫只认 note.md 的别名，于是传一个
# 叫 note.en.md 的附件照样能顶掉整份英文记录——而附件上传是公开写接口里最容易
# 被当成"随便传点什么"的那一个。别名的四种形状（大小写 / 尾随点空格 / ADS 冒号 /
# 组合）在这里逐一钉住。
@pytest.mark.parametrize("alias", [
    "note.en.md", "NOTE.EN.MD", "Note.En.Md", "note.zh-Hant.md",
    "note.en.md ", "note.en.md.", "note.en.md:evil", "note.EN.md. .",
    "project.md", "project.en.md", "PROJECT.EN.MD", "project.en.md.",
])
def test_a_translation_file_cannot_be_uploaded_as_an_attachment(alias):
    with pytest.raises(W.WriteError):
        W.safe_relpath(alias)


@pytest.mark.parametrize("ok", ["note.md.txt", "notes.en.md", "note..md", "note.en2.md.bak",
                                "figs/note.en.md"])
def test_the_translation_guard_does_not_eat_ordinary_attachments(ok):
    """守卫只认「顶层的记录文件名」这一种形状。扩得过宽会让正常附件传不上来，
    而 `sub/note.en.md` 和 `sub/note.md` 一样是别人的文件（既有断言钉着这条）。"""
    assert W.safe_relpath(ok)


def test_uploading_a_translation_alias_leaves_the_english_record_untouched(tmp_path: Path):
    """走完整条上传路径确认一遍：挡的不只是 safe_relpath 的返回值。"""
    d = mkroot(tmp_path)
    s, _ = W.create_step(d, title="半年的记录")
    W.write_translation(d, s.id, "en", title="Half a year of records", body="## Why\nbecause.")
    tr = d / s.dirname / "note.en.md"
    before = tr.read_bytes()
    for alias in ("note.en.md", "NOTE.EN.MD", "note.en.md."):
        with pytest.raises(W.WriteError):
            W.attach_file(d, s.id, alias, b"PWNED")
    assert tr.read_bytes() == before


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
