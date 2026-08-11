"""定稿流程的**写入侧**断言：`result:`（项目上）和 `pipeline:`（步骤上）。

这一版把「记录」和「方法」分了家：开发路径是整棵树（含走不通的、含岔路口），
定稿流程是真正产出成果的那条链。落盘的只有两样东西，其余全部派生：

  * project.md 的 `result: 023 | 说明` —— 整件事里唯一推导不出来的那一件；
  * note.md 的 `pipeline: include|exclude | 理由` —— 某一步自己的例外声明。

所以这个文件钉的全是「**别的东西没被存下来**」和「**这两行别被弄丢**」：
成员清单一个字都不存（存了就是会漂移的中心索引，P1），而 front-matter 是被
全量重写的，漏掉一个键就等于每次改项目名删一次成果声明。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_write as W


@pytest.fixture()
def proj(tmp_path: Path):
    """一个真项目：root、steps 目录，外加一条 001 → 002 的数据依赖。"""
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    steps = W.resolve_project(tmp_path, "课题")
    W.create_step(steps, title="清洗数据")
    W.create_step(steps, parent="001", title="训练", inputs=[{"step": "001", "note": "train.jsonl"}])
    return tmp_path, steps


def note_text(root: Path, slug: str = "课题") -> str:
    return (core.project_dir(root, slug) / core.PROJECT_NOTE).read_text(encoding="utf-8")


def step_text(steps: Path, sid: str) -> str:
    return (steps / W.load(steps)[sid].dirname / core.NOTE_NAME).read_text(encoding="utf-8")


# ------------------------------------------------------------ result:


def test_the_result_line_lands_in_project_md_as_one_greppable_line(proj):
    """G4：删掉全部程序之后，`grep -rn "^result:" projects/` 仍然要能答出
    「这个项目的成果是哪一步」。所以它是 front-matter 里一行完整的人话，
    不是一份需要程序才解得开的结构。"""
    root, _steps = proj
    out = W.set_result(root, "课题", "002", "主结果：亲和力预测 AUC 0.91")
    assert out["line"] == "result: 002 | 主结果：亲和力预测 AUC 0.91"
    assert out["line"] in note_text(root).split("\n")


def test_results_are_repeatable_and_keep_the_order_they_were_declared_in(proj):
    """可重复是硬需求（主结果 + 图 4 的消融），而顺序是人声明的顺序——
    导出里 Methods 一节一个成果，顺序换了读者就得重新找主线。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.set_result(root, "课题", "001", "图 4 的消融")
    assert [r["step"] for r in core.parse_results(note_text(root))] == ["002", "001"]


def test_declaring_the_same_step_twice_rewrites_that_one_line(proj):
    """声明的身份**就是**那个步骤 id。同一步两行会产出两个一模一样的闭包，
    而说明不同的那两句话没人知道该信哪句。所以第二次是改写，位置不动。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.set_result(root, "课题", "001", "消融")
    out = W.set_result(root, "课题", "002", "主结果：AUC 0.91")
    assert out["created"] is False
    got = core.parse_results(note_text(root))
    assert [r["step"] for r in got] == ["002", "001"], "改写不该把它挪到末尾"
    assert got[0]["note"] == "主结果：AUC 0.91"


def test_dropping_one_result_leaves_the_others_alone(proj):
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.set_result(root, "课题", "001", "消融")
    assert W.drop_result(root, "课题", "001")["results"] == [{"step": "002", "note": "主结果"}]
    assert "result: 001" not in note_text(root)
    assert "result: 002 | 主结果" in note_text(root)


def test_dropping_something_that_was_never_declared_says_what_is_declared(proj):
    """撤销一条不存在的声明要当场说清现在声明的是谁——否则调用方唯一能做的
    就是再猜一次 id。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    with pytest.raises(W.NotFound, match="002"):
        W.drop_result(root, "课题", "001")


def test_a_result_pointing_at_a_step_that_does_not_exist_is_refused(proj):
    """这一条**故意比 `input:` 严**。悬空的 input 只是让一条数据边不参与计算
    （读侧警告一声）；悬空的 result 让整条定稿流程从一个不存在的起点出发，
    结果是空——页面上一个字都不报，只是那一栏没了。"""
    root, _steps = proj
    with pytest.raises(W.NotFound, match="009"):
        W.set_result(root, "课题", "009", "不存在的一步")
    assert "result:" not in note_text(root), "被拒绝的写入一个字节都不该落盘"


def test_a_result_with_a_malformed_step_id_is_refused(proj):
    root, _steps = proj
    with pytest.raises(W.WriteError, match="形状"):
        W.set_result(root, "课题", "第二步", "主结果")


def test_a_dead_step_cannot_be_declared_as_the_result(proj):
    """「我的主结果是一条我自己判了此路不通的线」不是任何人想打出来的话。
    反过来是允许的（见下一条）：写入侧挡住新犯的错，读侧说清已经发生的事。"""
    root, steps = proj
    W.update_step(steps, "002", {"status": "dead"})
    with pytest.raises(W.WriteError, match="dead"):
        W.set_result(root, "课题", "002", "主结果")


def test_a_declared_result_may_still_be_marked_dead_afterwards(proj):
    """P4：dead 是结论不是错误。拦住这一步只会逼人先撤销声明再标 dead，
    而「成果所在的那条线后来被判死了」正是最该留下来的一段历史——
    它由读侧以 warn 级点名报出来，不是靠写入侧拒绝。"""
    root, steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.update_step(steps, "002", {"status": "dead"})
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": "主结果"}]


def test_a_result_needs_no_note(proj):
    """说明是可选的：步骤自己的标题已经说了它是什么。成果声明是整件事里最该
    被写下来的一行，不该为了一句可有可无的说明抬高它的门槛。"""
    root, _steps = proj
    assert W.set_result(root, "课题", "002")["line"] == "result: 002"
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": ""}]


def test_a_pipe_inside_the_note_survives_the_round_trip(proj):
    """竖线右边整段都是人写的字。重新拼装等于替人改文案。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果 | 见图 3")
    assert core.parse_results(note_text(root))[0]["note"] == "主结果 | 见图 3"


# --------------------------------------- 别的写入路径不许把成果声明弄丢

def test_renaming_the_project_keeps_the_result_lines(proj):
    """project.md 的 front-matter 是被**全量重写**的。漏掉一个键就等于每次改
    项目名删一次成果声明——而那次 diff 看起来只是改了个名字。`lang` 当年就是
    这么漏掉的，这一次让「忘记传」等于「保留」。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.rename_project(root, "课题", "新名字")
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": "主结果"}]


def test_adding_an_insight_keeps_the_result_lines(proj):
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.update_project(root, "课题", add=("works", "去重之后都涨点"))
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": "主结果"}]


def test_replacing_all_insights_keeps_the_result_lines(proj):
    """整体替换那条路碰的是正文的四个洞察小节，front-matter 一个字都不该动。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.update_project(root, "课题", insights="## 坑\n- 全换掉了")
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": "主结果"}]
    assert "全换掉了" in note_text(root)


def test_deleting_a_step_keeps_the_result_line_and_reports_that_it_now_dangles(proj):
    """机器不替人撤那一行：撤掉之后「定稿流程曾经指向一步被删的记录」就再也
    没人看得见了。而不报出来更糟——id 会被重用，下一个拿到 002 的步骤会无声地
    变成论文的主结果。"""
    root, steps = proj
    W.set_result(root, "课题", "002", "主结果")
    out = W.delete_step(steps, "002", reason="误建")
    assert out["dangling_results"] == ["result: 002 | 主结果"]
    assert "result: 002 | 主结果" in note_text(root)


def test_deleting_an_unrelated_step_reports_no_dangling_result(proj):
    root, steps = proj
    W.set_result(root, "课题", "002", "主结果")
    assert W.delete_step(steps, "001", reason="误建")["dangling_results"] == []


def test_the_deleted_section_is_still_preserved_verbatim_next_to_the_results(proj):
    """`## 已删除` 逐字保留那条闸门不能因为 front-matter 多了一个键就松掉——
    目录没了之后，那一行是「002 是什么、为什么删的」仅存的证据。"""
    root, steps = proj
    W.set_result(root, "课题", "001", "主结果")
    W.delete_step(steps, "002", reason="粘错了令牌")
    W.update_project(root, "课题", insights="## 坑\n- 把已删除也贴回来了\n## 已删除\n- 伪造的一行")
    body = note_text(root)
    assert "粘错了令牌" in body and "伪造的一行" not in body
    assert core.parse_results(body) == [{"step": "001", "note": "主结果"}]


# --------------------------------------- 双语：成果声明只有一份

def test_results_never_leak_into_the_project_translation(proj):
    """`result:` 是结构键。译文里写一份，中文页面和英文页面会导出两条不同的
    Methods，而两边看着都像对的——正是双真相源最难查的那一种。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.write_project_translation(root, "课题", "en", name="Topic", body="## Ideas\n- something")
    tr = (core.project_dir(root, "课题") / "project.en.md").read_text(encoding="utf-8")
    assert "result:" not in tr
    assert core.parse_results(tr) == []


def test_writing_a_translation_does_not_touch_the_results_in_project_md(proj):
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.write_project_translation(root, "课题", "en", name="Topic", body="## Ideas\n- x")
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": "主结果"}]


def test_adding_an_insight_to_a_translation_does_not_touch_project_md(proj):
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.add_insight(root, "课题", "works", "it works", lang="en")
    assert core.parse_results(note_text(root)) == [{"step": "002", "note": "主结果"}]


def test_a_declared_lang_survives_alongside_the_results(proj):
    """两个键都在 front-matter 里被全量重写，谁都不该把谁挤掉。"""
    root, _steps = proj
    d = core.project_dir(root, "课题")
    (d / core.PROJECT_NOTE).write_text("---\nname: 课题\nlang: zh\n---\n\n", encoding="utf-8")
    W.set_result(root, "课题", "002", "主结果")
    text = note_text(root)
    assert "lang: zh" in text and "result: 002 | 主结果" in text


# --------------------------------------- P3：逐字节确定

def test_the_front_matter_key_order_is_fixed_and_byte_identical_across_writes(proj):
    """P3：视图是文件系统的纯函数，同样的数据两次写出同样的字节。
    键序固定（name → lang → result…），否则 `git diff` 每次都在洗牌。"""
    root, _steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.set_result(root, "课题", "001", "消融")
    first = note_text(root)
    assert first.startswith("---\nname: 课题\nresult: 002 | 主结果\nresult: 001 | 消融\n---")
    W.rename_project(root, "课题", "课题")
    assert note_text(root) == first, "什么都没改的一次回写不该产出不同的字节"


def test_a_non_utf8_project_note_is_refused_before_anything_is_written(proj):
    """既有的保护一视同仁：GBK 的 project.md 直接改会把原文替换成一串问号。"""
    root, _steps = proj
    note = core.project_dir(root, "课题") / core.PROJECT_NOTE
    raw = "---\nname: 课题\n---\n\n## 坑\n- 用 cmd 的 echo 写出来的\n".encode("gbk")
    note.write_bytes(raw)
    with pytest.raises(W.WriteError, match="UTF-8"):
        W.set_result(root, "课题", "002", "主结果")
    assert note.read_bytes() == raw, "拒绝之后原始字节一个都不许动"


# ------------------------------------------------------------ pipeline:


def test_pipeline_can_be_declared_when_the_step_is_created(proj):
    """有时候动手之前就知道「这一步是拿来试试看的」。逼人建完再 PATCH 一次，
    那句理由大概率就永远空着了。"""
    _root, steps = proj
    s, _ = W.create_step(steps, parent="001", title="试试看",
                         pipeline="exclude | 探索性的，成功了但没进最终流程")
    assert "pipeline: exclude | 探索性的，成功了但没进最终流程" in step_text(steps, s.id)
    assert W.load(steps)[s.id].pipeline == "exclude"


def test_pipeline_is_mutable_because_you_only_know_afterwards(proj):
    """「这一步到底进不进最终流程」天然是回头才定的：先跑完、先看见结果，
    才知道它是主线还是一次探索。只能在 create 时给等于要求人预判。"""
    _root, steps = proj
    W.update_step(steps, "001", {"pipeline": "include | 闭包够不到它，但数据是它准备的"})
    assert W.load(steps)["001"].pipeline == "include"
    W.update_step(steps, "001", {"pipeline": "exclude | 想清楚了，它不算方法的一部分"})
    assert W.load(steps)["001"].pipeline == "exclude"
    assert step_text(steps, "001").count("pipeline:") == 1, "改写不该再追加一行"


def test_writing_an_empty_pipeline_revokes_it_and_leaves_no_empty_line(proj):
    """标错了要能改回来，否则一次手滑就永久留在那儿。撤销之后**不留一行空的**：
    一行没有取值的 pipeline 什么都没说，读侧看不见它，人却以为自己声明过。"""
    _root, steps = proj
    W.update_step(steps, "001", {"pipeline": "exclude | 试试看"})
    W.update_step(steps, "001", {"pipeline": ""})
    assert "pipeline" not in step_text(steps, "001")
    assert W.load(steps)["001"].pipeline == ""


def test_pipeline_only_accepts_include_or_exclude(proj):
    """写入侧当场拒一个笔误。落了盘的 `pipeline: exclud` 既不生效也不留痕迹
    （读侧报一声 bad_pipeline 就当没写），而人以为自己声明过了。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="include"):
        W.update_step(steps, "001", {"pipeline": "exclud | 打错了"})
    assert "pipeline" not in step_text(steps, "001")


def test_pipeline_without_a_reason_is_refused(proj):
    """这是它和 `branch:` 的唯一分歧。候选组在树上看得见，理由写没写图上都摆着；
    而 pipeline 除了改变一份导出之外**不留任何痕迹**——没有那半句话，半年后
    没人分辨得出这是想清楚的决定还是一次误点，而受害的是论文的 Methods。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="理由"):
        W.update_step(steps, "001", {"pipeline": "exclude"})
    with pytest.raises(W.WriteError, match="理由"):
        W.create_step(steps, title="x", pipeline={"rule": "include"})


def test_a_note_without_a_rule_is_refused_instead_of_being_swallowed(proj):
    """只写说明不写取值，最坏的处理是悄悄当成「撤销」——那句说明是人写的字。"""
    _root, steps = proj
    with pytest.raises(W.WriteError, match="没有取值"):
        W.update_step(steps, "001", {"pipeline": "| 我以为这样就够了"})


def test_pipeline_accepts_the_structured_form_too(proj):
    """网页/MCP 把整份步骤 PATCH 回来时给的是 {rule, note}，不是拼好的一行。"""
    _root, steps = proj
    W.update_step(steps, "001", {"pipeline": {"rule": "include", "note": "数据是它准备的"}})
    assert "pipeline: include | 数据是它准备的" in step_text(steps, "001")


def test_pipeline_survives_an_edit_that_never_mentions_it(proj):
    """render_note 是**全量重写** front-matter 的。漏掉一个键，就等于每次在网页上
    点一下 done 都把这句声明悄悄删掉一次。"""
    _root, steps = proj
    W.update_step(steps, "001", {"pipeline": "exclude | 探索性的"})
    W.update_step(steps, "001", {"status": "done", "body": "## 结论\n就这样"})
    assert "pipeline: exclude | 探索性的" in step_text(steps, "001")


def test_pipeline_survives_a_move(proj):
    """move_step 走的是另一条代码路径，同样全量重写 front-matter。"""
    _root, steps = proj
    W.update_step(steps, "002", {"pipeline": "exclude | 探索性的"})
    W.move_step(steps, "002", None, reason="它其实不接着 001 想")
    assert "pipeline: exclude | 探索性的" in step_text(steps, "002")


def test_pipeline_survives_a_path_check(proj):
    """record_path_check 只该动机器字段，人写的判断一个字都不能碰。"""
    _root, steps = proj
    W.update_step(steps, "001", {"paths": ["/blue/x | output | 权重"],
                                 "pipeline": "exclude | 探索性的"})
    W.record_path_check(steps, "001", "/blue/x", exists=True, date="2026-08-09")
    text = step_text(steps, "001")
    assert "pipeline: exclude | 探索性的" in text and "checked=2026-08-09" in text


def test_a_typo_already_on_disk_is_not_silently_erased_by_an_unrelated_edit(proj):
    """和悬空 parent、笔误的 branch 是同一条原则：读侧的临时修正（core 把认不出的
    取值当没写）不许被写回磁盘。照着修正过的值回写，等于在人还没来得及改好那个词
    之前，先把「这一步本来声明过什么」永久删掉，连同那条本来在催人去改的警告。"""
    _root, steps = proj
    d = steps / W.load(steps)["001"].dirname / core.NOTE_NAME
    d.write_text(d.read_text(encoding="utf-8").replace(
        "status:", "pipeline: exclud | 打错了\nstatus:", 1), encoding="utf-8")
    W.update_step(steps, "001", {"title": "改个标题"})
    assert "pipeline: exclud | 打错了" in step_text(steps, "001")


def test_pipeline_is_not_written_when_nobody_declares_it(proj):
    """绝大多数步骤都不声明。把默认值写进文件就是把派生写成数据，
    而且已有的每一条记录都会在下一次编辑时多出一行什么都没说的 diff。"""
    _root, steps = proj
    W.update_step(steps, "001", {"status": "done"})
    assert "pipeline" not in step_text(steps, "001")


def test_pipeline_is_not_inherited_by_a_child_step(proj):
    """例外声明是**这一步自己**的判断。跟着继承下去，人只会看到一片莫名其妙的
    exclude，而每一条都带着它父亲的理由。"""
    _root, steps = proj
    W.update_step(steps, "001", {"pipeline": "exclude | 探索性的"})
    s, _ = W.create_step(steps, parent="001", title="接着做")
    assert "pipeline" not in step_text(steps, s.id)


# ------------------------------------------------------------ 接缝

def test_the_two_declarations_are_the_only_thing_stored(proj):
    """整件事的地基：磁盘上**只有**这两行，成员清单、等级、哪一步拖后腿全是现算的。
    存一份清单的代价不是「多一个文件」，是那份清单看起来永远是对的——
    它只在你改完结构之后才开始说谎，而那正是你最信它的时候。"""
    root, steps = proj
    W.set_result(root, "课题", "002", "主结果")
    W.update_step(steps, "001", {"pipeline": "include | 数据是它准备的"})
    pipeline = core.compute_pipeline(W.load(steps), core.parse_results(note_text(root)))
    assert [m for m in pipeline["order"]] == ["001", "002"]
    on_disk = note_text(root) + step_text(steps, "001") + step_text(steps, "002")
    for derived in ("order", "members", "weakest", "level:"):
        assert derived not in on_disk, f"{derived} 是派生的，一个字都不该落盘"


def test_the_writer_and_the_reader_agree_on_the_result_lines(proj):
    """写侧拼出来的一行，读侧必须原样读得回来。读写各写一份解析，
    「写得进去、读不回来」只会在某个边角上悄悄发生，而磁盘上那一行看着完全正常。"""
    root, _steps = proj
    rows = [{"step": "002", "note": "主结果：AUC 0.91"}, {"step": "001", "note": ""}]
    for r in rows:
        W.set_result(root, "课题", r["step"], r["note"])
    assert core.parse_results(note_text(root)) == rows


def test_the_pipeline_rule_vocabulary_is_cores_not_a_second_copy(proj):
    """取值词表只有一份。写入侧再抄一遍，某一天多出一种取值时两边就会不一致——
    表现是「写得进去、读侧当没写」，而磁盘上那一行看着完全正常。"""
    assert W.PIPELINE_RULES is core.PIPELINE_RULES
