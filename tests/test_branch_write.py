"""分叉语义（`branch:` / `decision:`）的**写入侧**断言。

树上所有父子边现在长得一样，但它们说的不是一件事。这一版把三种分开：
互斥候选（A/B 只能选一条）、普通延伸（默认）、汇回（那是 `input:`，早就有了）。

这个文件守的是「写进去的东西是不是那个形状」，一条一条对应一种会**悄悄**发生的坏事：

  * 候选关系登记到兄弟身上（`alt: 012b`）→ 双真相源，上一代系统的死因；
  * 父节点上存一份孩子清单        → 同上，而且孩子一被 move_step 挪走立刻过期；
  * 默认值 `branch: extends` 落盘  → 164 条老记录在下一次编辑时集体多出一行 diff；
  * render_note 全量重写吃掉 `decision:` → 人在网页上点一下 done，那句唯一写不出来的话就没了；
  * 笔误 `alterative` 落盘         → 读侧只会静静退回 extends，页面上什么都不发生，
                                     而人以为自己标过了。

「一组候选有谁」「选中了谁」「这个岔路口还没决定」一律是**派生**的，写入侧一个
判据都不重写——所以这里的几条 round-trip 断言是拿 core.compute_branch_groups 收的。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_write as W


@pytest.fixture()
def steps(tmp_path: Path) -> Path:
    d = tmp_path / "steps"
    d.mkdir()
    return d


def note(d: Path, sid: str) -> str:
    return (d / W.load(d)[sid].dirname / core.NOTE_NAME).read_text(encoding="utf-8")


def front_matter(d: Path, sid: str) -> list[str]:
    return note(d, sid).split("\n---", 1)[0].split("\n")[1:]


def groups(d: Path) -> dict[str, dict]:
    """候选组，按分叉点索引。判据全在 core —— 这里只是把它拿来验收写出来的字节。"""
    by_id = W.load(d)
    return {g["at"]: g for g in core.compute_branch_groups(by_id, core.build_children(by_id))}


@pytest.fixture()
def fork(steps: Path) -> Path:
    """011 底下两个互斥候选 012 / 012b，外加 012 下面一条普通延伸。"""
    W.create_step(steps, title="类别不平衡", decision="类别不平衡怎么处理？只能选一条走下去")
    W.create_step(steps, parent="001", title="重采样", branch="alternative")
    W.create_step(steps, parent="001", title="focal loss", branch="alternative | 只动损失函数")
    W.create_step(steps, parent="002", title="调采样比例")
    return steps


# ------------------------------------------------ 互斥关系只写在候选自己身上


def test_each_candidate_declares_only_itself_and_the_parent_lists_nobody(fork: Path):
    """互斥是一组关系，登记在兄弟之间就要写 N×(N−1) 份，改一处漏一处。

    所以磁盘上只有每个候选自己那一行「我是一个候选」，父节点上**一个孩子 id 都没有**。
    这条断言就是在挡 `alt: 012b` / `options: 012, 012b` 那类写法回来。
    """
    assert "branch: alternative" in note(fork, "002")
    assert "branch: alternative | 只动损失函数" in note(fork, "002b")

    parent = note(fork, "001")
    assert "decision: 类别不平衡怎么处理？只能选一条走下去" in parent
    for line in front_matter(fork, "001"):
        assert "002" not in line, f"父节点上出现了孩子 id，这就是双真相源: {line}"


def test_the_group_is_derived_by_scanning_the_children(fork: Path):
    """写出去的字节要真能被 core 读回成一组候选——不然写入侧写了个寂寞。"""
    g = groups(fork)["001"]
    assert g["options"] == ["002", "002b"]
    assert g["decision"] == "类别不平衡怎么处理？只能选一条走下去"
    assert g["state"] == "open", "两个都还活着 ⇒ 这个岔路口还没做决定"
    assert "003" not in g["options"], "普通延伸不该混进候选组"


def test_choosing_one_needs_no_new_field_just_dead_on_the_others(fork: Path):
    """「选了哪个」现成就有：其余候选标 dead。

    另存一个「选中了谁」的字段就是双真相源——两处平时看着都对，只有改了其中
    一份才炸。这条钉住写入侧**没有**为「选中」新增任何东西。
    """
    W.update_step(fork, "002b", {"status": "dead", "body": "## 结论\nfocal loss 没有超过重采样"})
    g = groups(fork)["001"]
    assert (g["state"], g["chosen"]) == ("decided", "002")
    assert "chosen" not in note(fork, "001"), "选择结果绝不落盘，它是 status 派生出来的"


# ------------------------------------------------ 默认值不占一行


def test_a_plain_extension_never_costs_a_line(fork: Path):
    """`branch: extends` 和不写是同一个意思。存后者等于把一个派生默认值写死进文件，
    而且会让 164 条已有记录在下一次编辑时集体多出一行什么都没说的 diff。"""
    assert "branch:" not in note(fork, "003")

    W.update_step(fork, "003", {"branch": "extends"})
    assert "branch:" not in note(fork, "003"), "显式写 extends 也不该落盘"

    W.create_step(fork, parent="003", title="显式声明普通延伸", branch="extends")
    assert "branch:" not in note(fork, "004")


def test_an_extends_that_carries_a_note_keeps_its_line(steps: Path):
    """省略默认值的前提是「省掉之后一个字都没丢」。带了说明就不成立了——
    说明是人写的字，`extends | A/B 都试过之后接着走的那条` 是一句有信息的话。"""
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="接着走",
                  branch="extends | A/B 都试过之后接着走的那条")
    assert "branch: extends | A/B 都试过之后接着走的那条" in note(steps, "002")


# ------------------------------------------------ 回头标：最常见的用法


def test_two_existing_steps_can_be_marked_as_rivals_afterwards(steps: Path):
    """**最常见的用法**：两步先各自建出来，过几天回头才想明白「当时这两条是互斥的」。

    只在 create 时给，等于要求人在还没纠结完的时候就说清自己在纠结什么。
    """
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    W.create_step(steps, parent="001", title="B")
    assert "001" not in groups(steps), "还没标之前不该有候选组"

    W.update_step(steps, "002", {"branch": "alternative"})
    W.update_step(steps, "002b", {"branch": "alternative"})
    W.update_step(steps, "001", {"decision": "先做哪一半"})

    g = groups(steps)["001"]
    assert (g["options"], g["decision"]) == (["002", "002b"], "先做哪一半")


def test_unmarking_takes_the_line_back_out(fork: Path):
    """标错了要能改回来，否则一次手滑就永久留在那儿了（和 lang 同一条理由）。"""
    for undo in ("", "extends", None):
        W.update_step(fork, "002", {"branch": "alternative"})
        W.update_step(fork, "002", {"branch": undo})
        assert "branch:" not in note(fork, "002"), f"{undo!r} 应该等于撤回候选身份"


def test_a_decision_can_be_taken_back_too(fork: Path):
    W.update_step(fork, "001", {"decision": ""})
    assert "decision:" not in note(fork, "001")


# ------------------------------------------------ 笔误当场拒绝


@pytest.mark.parametrize("typo", ["alterative", "alt", "ALTERNATIVES", "候选", "extend"])
def test_a_misspelled_branch_kind_is_refused_at_the_door(fork: Path, typo):
    """读侧对未知取值是宽容的（报一声 bad_branch，退回 extends 继续建树）——
    十年后的日志一定是残缺的。写入侧相反：一个笔误落了盘，它既不算候选也不会在
    页面上留下任何痕迹，只是安静地变回普通延伸，而人以为自己标过了。
    """
    before = note(fork, "003")
    with pytest.raises(W.WriteError, match="alternative"):
        W.update_step(fork, "003", {"branch": typo})
    assert note(fork, "003") == before, "被拒绝时一个字节都没写"

    with pytest.raises(W.WriteError):
        W.create_step(fork, parent="001", title="拼错的候选", branch=typo)
    assert "004" not in W.load(fork), "被拒绝时连目录都不该留下"


# ------------------------------------------------ 全量重写不许吃掉这两行


def test_an_unrelated_edit_never_eats_the_decision(fork: Path):
    """render_note 是**全量重写** front-matter 的。漏掉一个键，就等于每次在网页上
    点一下 done 都把用户手写的那一行悄悄删掉——而「在决定什么」是整个功能里
    唯一推导不出来、只能人写的东西。"""
    for patch in ({"status": "done"}, {"title": "改个标题"}, {"body": "## 为什么\n换正文"},
                  {"tags": "a, b"}, {"add_repro": "verified | 2026-08-10 | human | 重跑"}):
        W.update_step(fork, "001", patch)
        assert "decision: 类别不平衡怎么处理？只能选一条走下去" in note(fork, "001"), patch


def test_an_unrelated_edit_never_eats_the_branch_line_or_its_note(fork: Path):
    for patch in ({"status": "dead"}, {"title": "换个说法"}, {"lang": "zh"}):
        W.update_step(fork, "002b", patch)
        assert "branch: alternative | 只动损失函数" in note(fork, "002b"), patch


def test_a_typo_already_on_disk_is_not_silently_rewritten(fork: Path):
    """core 把认不出来的 kind 退回 extends 并报一条 bad_branch——那是**给渲染用的
    临时修正**。照着它写回磁盘，等于在人还没来得及改好那个词之前，先把
    「这一步本来是个候选」永久删掉，连同那条本来在催人去改的警告一起。

    和悬空 parent 不被降级写回是同一条原则（见 _hydrate）。
    """
    path = fork / W.load(fork)["003"].dirname / core.NOTE_NAME
    path.write_text(note(fork, "003").replace("status:", "branch: alterative\nstatus:", 1),
                    encoding="utf-8")
    W.update_step(fork, "003", {"status": "done"})
    assert "branch: alterative" in note(fork, "003"), "人写下的那个词一个字都不该被改掉"


# ------------------------------------------------ P3：逐字节确定


def test_the_two_new_keys_sit_in_a_fixed_place(fork: Path):
    """P3 要求同样的输入两次构建逐字节一致，所以键序必须是死的。

    位置也不是随便挑的：`branch:` 限定的是**上面那条 parent 边**，
    连着读才是一句完整的话；`decision:` 和 title 一样是人写的自由文本。
    """
    keys = [l.split(":", 1)[0] for l in front_matter(fork, "002") if ":" in l]
    assert keys[:5] == ["id", "parent", "branch", "status", "title"]

    keys = [l.split(":", 1)[0] for l in front_matter(fork, "001") if ":" in l]
    assert keys[:4] == ["id", "status", "title", "decision"]


def test_rewriting_the_same_data_twice_is_byte_identical(fork: Path):
    once = note(fork, "002b")
    W.update_step(fork, "002b", {"branch": "alternative | 只动损失函数"})
    assert note(fork, "002b") == once


def test_a_branch_note_with_pipes_reads_back_unchanged(steps: Path):
    """说明里再有竖线一律留着不动——那是人写的字，重新拼装等于替人改文案。"""
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A",
                  branch="alternative | 便宜 | 但可能不够准")
    assert W.load(steps)["002"].branch_note == "便宜 | 但可能不够准"


# ------------------------------------------------ mark_alternatives：成组标


def test_marking_a_group_writes_only_each_child_s_own_line_plus_the_decision(steps: Path):
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    W.create_step(steps, parent="001", title="B")

    out = W.mark_alternatives(steps, ["002", "002b"], decision="用哪种口袋定义？",
                              notes={"002": "先试最便宜的"})
    assert out["group"]["options"] == ["002", "002b"] and out["group"]["state"] == "open"
    assert "branch: alternative | 先试最便宜的" in note(steps, "002")
    assert "branch: alternative" in note(steps, "002b")
    assert "decision: 用哪种口袋定义？" in note(steps, "001")
    for line in front_matter(steps, "001"):
        assert not line.startswith(("branch:", "options:", "alt:")), line


def test_marking_across_different_parents_is_refused(steps: Path):
    """这是这个入口存在的**主要**理由。update_step 一次只看得见一个孩子，于是把
    两个不同父节点下的步骤各标一次 alternative，得到的是两个各只含一个候选的组：
    一次都不报错，而人以为自己刚记下了一个岔路口。
    """
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    W.create_step(steps, parent="002", title="A 的下一步")

    with pytest.raises(W.WriteError, match="同一个父节点"):
        W.mark_alternatives(steps, ["002", "003"])
    assert "branch:" not in note(steps, "002") and "branch:" not in note(steps, "003")


def test_marking_one_step_alone_is_refused(steps: Path):
    """一个候选的「分叉点」不是分叉点（core 会报 lone_alternative）。
    真要单独补标第三个候选，走 update_step —— 那条路照样开着。"""
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    with pytest.raises(W.WriteError, match="至少要两步"):
        W.mark_alternatives(steps, ["002"])
    with pytest.raises(W.WriteError, match="至少要两步"):
        W.mark_alternatives(steps, ["002", "002", "  002  "])


def test_marking_roots_refuses_a_decision_that_has_nowhere_to_live(steps: Path):
    """两条互斥的开局没有共同的父节点，那句「在决定什么」没有地方写。

    默默丢掉它更坏：调用方以为写上了。成组本身是允许的（core 给根之间那一组
    单独成一组），只有 decision 这一条要说清楚。
    """
    W.create_step(steps, title="路线 A")
    W.create_step(steps, title="路线 B")
    with pytest.raises(W.WriteError, match="没有地方写"):
        W.mark_alternatives(steps, ["001", "002"], decision="整个课题从哪边入手")

    W.mark_alternatives(steps, ["001", "002"])
    assert groups(steps)[""]["options"] == ["001", "002"]


def test_nothing_lands_when_one_id_in_the_group_is_bad(steps: Path):
    """校验全部前置、整段在同一把项目锁里：中途退出不会留下「两个候选只标了一个」
    的半成品——而那个半成品恰好长得和 core 的 lone_alternative 诊断一模一样，
    人会收到一条根本不该出现的假诊断。"""
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    W.create_step(steps, parent="001", title="B")

    with pytest.raises(W.NotFound, match="999"):
        W.mark_alternatives(steps, ["002", "999"], decision="别写进去")
    assert "branch:" not in note(steps, "002"), "第一个也不该先落盘"
    assert "decision:" not in note(steps, "001")

    with pytest.raises(W.WriteError, match="notes"):
        W.mark_alternatives(steps, ["002", "002b"], notes={"003": "不在这一组里"})
    assert "branch:" not in note(steps, "002")


def test_marking_the_same_group_twice_changes_nothing(steps: Path):
    """出错重跑是常态，重复标必须是幂等的（错误信息里就是这么承诺的）。"""
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    W.create_step(steps, parent="001", title="B")
    W.mark_alternatives(steps, ["002", "002b"], decision="选哪个")
    snapshot = [note(steps, s) for s in ("001", "002", "002b")]
    W.mark_alternatives(steps, ["002", "002b"], decision="选哪个")
    assert [note(steps, s) for s in ("001", "002", "002b")] == snapshot


def test_marking_a_group_leaves_dead_candidates_alone(steps: Path):
    """已经废掉的那条也是这一组的成员——dead 是结论不是错误（P4），
    把它排除在组外，半年后就看不出「当时是在这两条里选」。"""
    W.create_step(steps, title="根")
    W.create_step(steps, parent="001", title="A")
    W.create_step(steps, parent="001", title="B")
    W.update_step(steps, "002b", {"status": "dead"})
    out = W.mark_alternatives(steps, ["002", "002b"], decision="选哪个")
    assert out["group"]["options"] == ["002", "002b"]
    assert out["group"]["state"] == "decided" and out["group"]["chosen"] == "002"
    assert W.load(steps)["002b"].status == "dead", "成组标候选不许顺手改状态"


# ------------------------------------------------ 既有的写入闸门一视同仁


def test_expect_guards_a_branch_change_like_any_other(steps: Path):
    """乐观并发控制不因为字段新就放行：人在网页上标候选的同时 agent 正在写正文。"""
    W.create_step(steps, title="根")
    s, _ = W.create_step(steps, parent="001", title="A")
    stale = W.digest_of(steps / s.dirname / core.NOTE_NAME)
    W.update_step(steps, "002", {"body": "## 结果\nagent 期间写进来的"})

    with pytest.raises(W.Conflict):
        W.update_step(steps, "002", {"branch": "alternative"}, expect=stale)
    assert "branch:" not in note(steps, "002")
    assert "agent 期间写进来的" in note(steps, "002"), "冲突时一个字节都不写"


def test_a_non_utf8_note_refuses_a_branch_change_too(steps: Path):
    """GBK 的 note.md 读出来是一串 U+FFFD，照写回去原始字节就永久没了。
    新字段和 status 一视同仁。"""
    W.create_step(steps, title="根")
    s, _ = W.create_step(steps, parent="001", title="A")
    path = steps / s.dirname / core.NOTE_NAME
    path.write_bytes("---\nid: 002\nparent: 001\nstatus: wip\ntitle: 中文\n---\n\n## 为什么\n中文\n"
                     .encode("gbk"))
    raw = path.read_bytes()
    with pytest.raises(W.WriteError, match="UTF-8"):
        W.update_step(steps, "002", {"branch": "alternative"})
    assert path.read_bytes() == raw

    sibling, _ = W.create_step(steps, parent="001", title="B")
    with pytest.raises(W.WriteError, match="UTF-8"):
        W.mark_alternatives(steps, ["002", sibling.id])
    assert path.read_bytes() == raw, "成组标候选走的是同一条写入路径，同一道闸门"


def test_branch_and_decision_are_in_the_mutable_list(steps: Path):
    """接缝：MUTABLE 是「agent 有没有办法用上这个字段」的唯一来源。
    不进这张表，PATCH 直接被当成不支持的字段拒掉，写入层做了也等于没有。"""
    assert "branch" in W.MUTABLE and "decision" in W.MUTABLE
    W.create_step(steps, title="根")
    with pytest.raises(W.WriteError, match="不支持的字段"):
        W.update_step(steps, "001", {"branch_note": "说明得跟着 branch 一起给"})
