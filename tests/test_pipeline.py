"""定稿流程：从「哪一步是成果」派生出「给别人照着做的那条链」。

同一批文件上有两条路径——**开发路径**（现在这棵树的全部，含走不通的、含岔路口）
和**定稿流程**（真正产出成果的那一条链）。后者全部是算出来的，磁盘上只多一行
`result:`。这个文件里的每一条断言都对应「成员清单一旦落盘会怎么烂掉」里的一种。
"""

import json
import time
from pathlib import Path

import trace_core as core

FULL = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
# 记全了位置的那几行（够 L2）。status 分开写：front-matter 是后写覆盖先写，
# 把 `status: done` 焊进 GOOD 会让 "status: dead\n" + GOOD 悄悄变成 done。
LOC = "commit: abc\npath: /blue/x | output | 数据\n"
GOOD = "status: done\n" + LOC
DEAD = "status: dead\n" + LOC


def mk(root: Path, sid: str, fm: str = "", body: str = FULL) -> Path:
    d = root / "steps" / f"{sid}_x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "note.md").write_text(f"---\nid: {sid}\ntitle: t{sid}\n{fm}---\n{body}",
                               encoding="utf-8", newline="\n")
    return root / "steps"


def project(root: Path, front: str = "") -> None:
    """写 project.md（`result:` 就住在这里，它是项目级的事实）。"""
    (root / "project.md").write_text(f"---\nname: p\n{front}---\n\n",
                                     encoding="utf-8", newline="\n")


def codes(ws) -> list[str]:
    return [w["code"] for w in ws]


# ------------------------------------------------------ ① 没声明成果 = 完全无感


def test_a_project_that_never_declared_a_result_gets_no_new_field_no_new_warning(tmp_path: Path):
    """现存项目一个 `result:` 都没有，它们的 forest 必须逐字节和从前一样。

    防的是「算一个空流程挂上去」：那会给每个项目每个步骤各加一个字段值、
    给顶栏加一条「你还没声明成果」。功能没上线就先让所有人多看一条警告，
    人从此略过警告栏——真正的缺口（dead 没写结论）反而更难被发现。
    """
    steps = mk(tmp_path, "001", GOOD)
    project(tmp_path)                                  # 有 project.md，只是没 result
    f = core.compile_forest(steps)
    assert "pipeline" not in f, "没声明成果就不该有这个键"
    assert "pipeline" not in f["steps"][0], "步骤上也不许多一个字段"
    assert f["warnings"] == [], "一条警告都不许多"


def test_no_project_note_at_all_is_the_same_silence(tmp_path: Path):
    steps = mk(tmp_path, "001", GOOD)
    f = core.compile_forest(steps)
    assert "pipeline" not in f and f["warnings"] == []


def test_asking_for_the_pipeline_with_nothing_declared_teaches_instead_of_blaming(tmp_path: Path):
    """「一个 result 都没声明」是常态，不是缺陷。所以这句话只在**有人主动问起**
    流程时才说（compute_pipeline 的返回值里），而且写的是怎么办，不是你错了。

    措辞是硬要求：写成责备，人就会随手指一步当成果好让界面干净——那是拿假结论
    换绿色，和「为了消掉 undecided_fork 随手标一个 dead」一模一样。
    """
    p = core.compute_pipeline({}, [])
    assert p["declared"] is False and p["order"] == []
    (d,) = p["diagnostics"]
    assert d["code"] == "pipeline_no_result" and d["level"] == "info"
    assert "result:" in d["message"] and "project.md" in d["message"]
    assert "input:" in d["message"] and "parent" in d["message"], "要说清流程是怎么算出来的"
    for blame in ("应该", "必须写", "错误"):
        assert blame not in d["message"], f"这条不许带责备的语气（出现了「{blame}」）"


# ------------------------------------------------------ ② 声明成果，其余全部派生


def test_the_only_thing_written_down_is_which_step_is_the_outcome(tmp_path: Path):
    """成员清单一个字都不存。落盘的只有 project.md 里那一行 `result:`。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "003", "parent: 002\n" + GOOD)
    project(tmp_path, "result: 003 | 主结果：AUC 0.91\n")
    f = core.compile_forest(steps)
    assert f["pipeline"]["order"] == ["001", "002", "003"]
    assert f["pipeline"]["results"] == [
        {"step": "003", "note": "主结果：AUC 0.91", "members": ["001", "002", "003"]}]
    raw = (tmp_path / "project.md").read_text(encoding="utf-8")
    assert "001" not in raw and "002" not in raw, "上游成员绝不许写进 project.md"


def test_moving_a_step_changes_the_flow_without_touching_project_md(tmp_path: Path):
    """一份落盘的成员清单会在这里过期而没人发现。派生的会自己跟着变。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "003", "parent: 002\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    assert core.compile_forest(steps)["pipeline"]["order"] == ["001", "002", "003"]

    mk(tmp_path, "003", "parent: 001\n" + GOOD)        # 挂到别处去
    assert core.compile_forest(steps)["pipeline"]["order"] == ["001", "003"], \
        "改一次 parent，流程要自己跟着变——不需要人去改 project.md"


def test_several_result_lines_are_all_read(tmp_path: Path):
    """`result:` 在 project.md 里可以重复。折成一个值就会静默丢掉一半成果。"""
    text = "---\nname: p\nresult: 023 | 主结果\nresult: 031 | 图 4 的消融\n---\n"
    assert core.parse_results(text) == [{"step": "023", "note": "主结果"},
                                        {"step": "031", "note": "图 4 的消融"}]


def test_result_lines_outside_the_front_matter_are_not_declarations(tmp_path: Path):
    """正文里提一句「result: 003」是人话，不是声明。"""
    assert core.parse_results("---\nname: p\n---\n\n## 核心想法\n- result: 003 | 不是声明\n") == []


def test_a_result_pointing_at_a_missing_step_is_reported_not_silently_dropped(tmp_path: Path):
    steps = mk(tmp_path, "001", GOOD)
    project(tmp_path, "result: 999 | 打错的 id\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["declared"] is True and p["order"] == []
    (d,) = p["diagnostics"]
    assert d["code"] == "dangling_result" and d["vars"]["id"] == "999"


# ------------------------------------------------------ ③ 闭包沿 input，退回 parent


def test_the_closure_follows_inputs_and_ignores_a_parent_that_gave_no_bytes(tmp_path: Path):
    """有 `input:` 就只沿 input：016 树上挂在 013b 底下（我接着那个判定想的），
    实际读的是 013 的产物。定稿流程要的是后者。"""
    mk(tmp_path, "013", GOOD)
    mk(tmp_path, "013b", GOOD)
    steps = mk(tmp_path, "016", "parent: 013b\ninput: 013 | pocket_composition.csv\n" + GOOD)
    project(tmp_path, "result: 016 | 配对结果\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["013", "016"]
    assert "013b" not in p["order"], "parent 只说明我接着谁想，没有字节流过来"


def test_a_step_with_no_input_line_falls_back_to_its_parent(tmp_path: Path):
    """绝大多数步骤不写 `input:`——不是没有输入，是输入就是上一步，写出来是废话。
    真按「没写就是没有上游」算，现存每个项目的流程都只剩成果那一步。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 002 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "002"]
    assert [(e["from"], e["to"], e["kind"]) for e in p["edges"]] == [("001", "002", "parent")]


def test_a_declared_but_dangling_input_stops_the_closure_instead_of_guessing(tmp_path: Path):
    """`input:` 写了却指向不存在的步骤 ⇒ 这一步**声明过**输入，不再退回 parent。
    拿 parent 顶上等于替人猜一个来源，而悬空本身已经由 dangling_input 报过了。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\ninput: 999 | 早就删掉的那一步\n" + GOOD)
    project(tmp_path, "result: 002 | r\n")
    f = core.compile_forest(steps)
    assert f["pipeline"]["order"] == ["002"]
    assert "dangling_input" in codes(f["warnings"]), "断在哪儿仍然说得出来"


# ------------------------------------------------------ ④ dead 是结论，但要说出来


def test_a_dead_step_upstream_of_the_result_is_dropped_and_named(tmp_path: Path):
    """「我的结果建立在一条我自己判定走不通的路上」是必须说出来的事（warn 级、指名）。

    同时：020 的上游 013 不能跟着消失。它的字节确实流进了成果，只是路上经过一段
    已经放弃的路——边照接，`via` 记下路过了谁。
    """
    mk(tmp_path, "013", GOOD)
    mk(tmp_path, "020", "parent: 013\nstatus: dead\ninput: 013 | a.csv\n")
    steps = mk(tmp_path, "023", "parent: 020\ninput: 020 | b.csv\n" + GOOD)
    project(tmp_path, "result: 023 | r\n")
    p = core.compile_forest(steps)["pipeline"]

    assert p["order"] == ["013", "023"], "dead 不进流程"
    assert p["dead"] == ["020"] and p["excluded"] == [{"step": "020", "why": "dead"}]
    (e,) = p["edges"]
    assert (e["from"], e["to"], e["via"]) == ("013", "023", ["020"]), \
        "上游不许跟着丢：那会让 013 变成一个和成果毫无关系的孤点"
    d = next(x for x in p["diagnostics"] if x["code"] == "pipeline_dead_step")
    assert d["level"] == "warn" and d["vars"] == {"ids": "020", "n": "1"}


def test_spliced_edges_do_not_borrow_the_note_of_the_step_they_skipped(tmp_path: Path):
    """`input: 020 | b.csv` 那行说明写的是「我消费了 020 的 b.csv」。
    接过一段之后它描述的已经不是这条边了，挂上去就是一句错话。"""
    mk(tmp_path, "013", GOOD)
    mk(tmp_path, "020", "parent: 013\nstatus: dead\n")
    steps = mk(tmp_path, "023", "input: 020 | b.csv\n" + GOOD)
    project(tmp_path, "result: 023 | r\n")
    (e,) = core.compile_forest(steps)["pipeline"]["edges"]
    assert e["via"] == ["020"] and e["notes"] == []


def test_a_direct_input_edge_keeps_its_note(tmp_path: Path):
    mk(tmp_path, "013", GOOD)
    steps = mk(tmp_path, "023", "input: 013 | pocket_composition.csv\n" + GOOD)
    project(tmp_path, "result: 023 | r\n")
    (e,) = core.compile_forest(steps)["pipeline"]["edges"]
    assert e["notes"] == ["pocket_composition.csv"] and e["kind"] == "input"


def test_a_result_that_is_itself_dead_still_anchors_its_flow(tmp_path: Path):
    """`result:` 说的就是「这是产出」，比「dead 一律剔掉」这条一般规则具体。
    把它剔掉会得到一条没有终点的流程，而原因（成果自己是 dead）一个字都看不见。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + DEAD)
    project(tmp_path, "result: 002 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "002"]
    assert p["dead"] == ["002"], "照样要指名——它是那条必须说出来的话"


# ------------------------------------------------------ ⑤ 每一步自己的例外


def test_include_and_exclude_are_declared_on_the_step_itself(tmp_path: Path):
    """和 `branch:` 同一个套路：声明写在这一步自己身上，项目上永远没有成员清单。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\npipeline: exclude | 探索性的，成功了但没进最终流程\n" + GOOD)
    mk(tmp_path, "003", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "004", "parent: 003\npipeline: include | 闭包够不到，但它是流程的一环\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "003", "004"]
    assert p["included"] == ["004"]
    assert "002" not in p["order"], "exclude 的那一步本来也不在闭包里，这里只是确认它没被拉进来"


def test_an_excluded_step_is_dropped_even_though_the_closure_reaches_it(tmp_path: Path):
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\npipeline: exclude | 只是顺手试了一下\n" + GOOD)
    steps = mk(tmp_path, "003", "parent: 002\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "003"]
    assert p["excluded"] == [{"step": "002", "why": "declared"}]
    assert [(e["from"], e["to"], e["via"]) for e in p["edges"]] == [("001", "003", ["002"])]


def test_an_included_step_brings_its_own_upstream_with_it(tmp_path: Path):
    """一个输入不在流程里的成员，写进 Methods 就是一句断了的话。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "005", "parent: 001\n" + GOOD)
    mk(tmp_path, "006", "parent: 005\npipeline: include | 建库脚本，成果没直接吃它的产物\n" + GOOD)
    steps = mk(tmp_path, "003", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "003", "005", "006"]


def test_excluding_a_step_the_flow_still_eats_from_is_a_contradiction(tmp_path: Path):
    """两句话不能同时成立，程序不替人裁决——它只把矛盾摆出来并指名双方。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\npipeline: exclude | 不进流程\n" + GOOD)
    steps = mk(tmp_path, "003", "input: 002 | features.parquet\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    d = next(x for x in p["diagnostics"] if x["code"] == "pipeline_excluded_consumed")
    assert d["vars"] == {"id": "002", "ids": "003", "n": "1"}
    assert d["where"] == "002_x", "要指到能改的那个文件"


def test_a_result_that_also_says_exclude_is_reported_and_the_result_wins(tmp_path: Path):
    steps = mk(tmp_path, "003", "pipeline: exclude | 旧的一行\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["003"], "否则这条流程连终点都没有"
    assert "pipeline_excluded_result" in codes(p["diagnostics"])


def test_an_unknown_pipeline_value_falls_back_to_not_declared_and_says_so(tmp_path: Path):
    """和 `status:` / `branch:` 同一条路：报一声、当没写、继续。一个拼错的词不该
    悄悄改掉论文 Methods 里有哪几步。"""
    steps = mk(tmp_path, "001", "pipeline: excluded | 手滑\n" + GOOD)
    project(tmp_path, "result: 001 | r\n")
    f = core.compile_forest(steps)
    w = next(x for x in f["warnings"] if x["code"] == "bad_pipeline")
    assert w["vars"] == {"pipeline": "excluded"} and w["where"] == "001_x"
    assert f["steps"][0]["pipeline"]["rule"] == ""
    assert f["steps"][0]["pipeline"]["note"] == "手滑", "说明是人写的字，取值拼错了也不许丢"


def test_pipeline_lines_round_trip(tmp_path: Path):
    p = core.parse_pipeline("exclude | 探索性的，成功了但没进最终流程")
    assert p == {"rule": "exclude", "note": "探索性的，成功了但没进最终流程"}
    assert core.format_pipeline(p) == "exclude | 探索性的，成功了但没进最终流程"
    assert core.format_pipeline({"rule": "include", "note": ""}) == "include"
    assert core.format_result({"step": "023", "note": "主结果"}) == "023 | 主结果"


# ------------------------------------------------------ ⑥ 顺序、多成果、环


def test_the_order_is_topological_and_ties_break_by_id(tmp_path: Path):
    """平局必须有个说法：同一份文件在两台机器上排出两种顺序，Methods 的段落顺序
    就跟着变，而 P3 要求逐字节确定。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "003", "input: 001 | a.csv\n" + GOOD)          # 和 002 平局
    mk(tmp_path, "002", "input: 001 | b.csv\n" + GOOD)
    steps = mk(tmp_path, "004", "input: 002 | c\ninput: 003 | d\n" + GOOD)
    project(tmp_path, "result: 004 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "002", "003", "004"]
    pos = {sid: i for i, sid in enumerate(p["order"])}
    for e in p["edges"]:
        assert pos[e["from"]] < pos[e["to"]], "拓扑序：依赖一定排在消费者前面"


def test_two_results_share_one_dag_and_each_still_knows_its_own_members(tmp_path: Path):
    """合并成一张 DAG，不是几条独立的链：共用的那一步只出现一次。
    拆开画会让同一步在图上和 Methods 里各来一遍，读的人得自己对着 id 去重。"""
    mk(tmp_path, "001", GOOD)                                   # 两个成果共用的数据准备
    mk(tmp_path, "023", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "031", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 023 | 主结果\nresult: 031 | 图 4 的消融\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["order"] == ["001", "023", "031"], "001 只出现一次"
    assert [r["members"] for r in p["results"]] == [["001", "023"], ["001", "031"]]


def test_a_cycle_in_the_data_dependencies_neither_hangs_nor_disappears(tmp_path: Path):
    """脏边不许让整条流程算不出来，也不许悄悄少几步。"""
    mk(tmp_path, "001", "input: 002 | a\n" + GOOD)
    mk(tmp_path, "002", "input: 001 | b\n" + GOOD)
    steps = mk(tmp_path, "003", "input: 001 | c\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert sorted(p["order"]) == ["001", "002", "003"], "环上的步骤照样都在"
    d = next(x for x in p["diagnostics"] if x["code"] == "pipeline_cycle")
    assert "001" in d["vars"]["ids"] and "002" in d["vars"]["ids"]


# ------------------------------------------------------ ⑦ 等级：白拿的三个判断


def test_the_level_of_the_whole_flow_is_its_weakest_step_and_names_it(tmp_path: Path):
    """一个数回答「别人能不能照着做出来」，并指名是哪一步拖的后腿。

    取的是**成员自己**的等级，不是整链等级：chain 会把被剔掉的 dead / exclude
    祖先算进来，而流程说的正是「那些不算方法的一部分」。
    """
    mk(tmp_path, "001", "status: done\n", "")                   # L0：正文空着
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)         # L2
    project(tmp_path, "result: 002 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["levels"] == {"001": "L0", "002": "L2"}
    assert p["level"] == "L0" and p["weakest"] == "001"
    assert p["level"] == core.traceability(
        core.validate(core.scan(steps)[0])[0]["001"])["level"], "复用同一套判据"


def test_a_dead_step_that_the_flow_dropped_does_not_drag_the_level_down(tmp_path: Path):
    """整链等级会被 020 压住，流程等级不会——那正是两条路径分家的意义。"""
    mk(tmp_path, "013", GOOD)
    mk(tmp_path, "020", "parent: 013\nstatus: dead\n", "")      # L0，且不进流程
    steps = mk(tmp_path, "023", "parent: 020\n" + GOOD)
    project(tmp_path, "result: 023 | r\n")
    f = core.compile_forest(steps)
    assert f["pipeline"]["level"] == "L2"
    assert {s["id"]: s["trace"]["chain"] for s in f["steps"]}["023"] == "L0", \
        "开发路径那边照旧：整链等级仍然被 020 压住"


def test_steps_the_reader_cannot_run_are_named_before_submission(tmp_path: Path):
    mk(tmp_path, "001", "status: done\n", FULL)                 # L1：没记 commit / 产物
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 002 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["weak"] == ["001"]
    d = next(x for x in p["diagnostics"] if x["code"] == "pipeline_weak_step")
    assert d["vars"] == {"ids": "001", "n": "1", "level": "L1", "id": "001"}
    assert "L0/L1" in d["message"]


def test_a_flow_everyone_can_rerun_says_nothing(tmp_path: Path):
    """全 L2 以上、没有 dead、没有矛盾 ⇒ 一条诊断都不该有。
    没事也念叨两句的诊断，人会连有事那次一起略过。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 002 | r\n")
    assert core.compile_forest(steps)["pipeline"]["diagnostics"] == []


def test_every_member_says_why_it_is_in_the_flow(tmp_path: Path):
    """「这一步凭什么算进 Methods」是读者第一个会问的问题。答案从 edges 反查得出来，
    但「哪个下游算数」得有个确定的挑法——挑一次放这儿，胜过每个出口各挑一次。"""
    mk(tmp_path, "001", GOOD)                                   # 被 002 按 parent 拉进来
    mk(tmp_path, "002", "parent: 001\n" + GOOD)                 # 被 003 按 input 拉进来
    mk(tmp_path, "004", "parent: 001\npipeline: include | 建库脚本\n" + GOOD)
    steps = mk(tmp_path, "003", "input: 002 | x.csv\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    p = core.compile_forest(steps)["pipeline"]
    assert p["why"] == {
        "001": {"kind": "parent", "id": "002"},
        "002": {"kind": "input", "id": "003"},
        "003": {"kind": "result", "id": ""},
        "004": {"kind": "include", "id": ""},
    }


# ------------------------------------------------------ ⑧ 两条路径互相跳得回去


def test_every_step_can_answer_whether_it_is_in_the_pipeline(tmp_path: Path):
    """开发路径上要标出哪些步骤属于定稿流程——反向的跳转是这件事的另一头。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\n" + DEAD)
    steps = mk(tmp_path, "003", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 003 | r\n")
    by = {s["id"]: s["pipeline"] for s in core.compile_forest(steps)["steps"]}
    assert by["001"] == {"member": True, "result": False, "index": 0, "rule": "", "note": ""}
    assert by["003"] == {"member": True, "result": True, "index": 1, "rule": "", "note": ""}
    assert by["002"]["member"] is False and by["002"]["index"] is None


def test_the_fork_that_produced_a_pipeline_step_is_still_reachable(tmp_path: Path):
    """「这一步当时有 3 个候选，为什么选了它」正是两条路径都留着的意义：
    流程只给 012b，开发路径那边 011 的 fork 一个字都没少。"""
    mk(tmp_path, "011", "decision: 类别不平衡怎么处理？\n" + GOOD)
    mk(tmp_path, "012", "parent: 011\nbranch: alternative | 只调采样权重\n" + DEAD)
    steps = mk(tmp_path, "012b", "parent: 011\nbranch: alternative | 换 focal loss\n" + GOOD)
    project(tmp_path, "result: 012b | r\n")
    f = core.compile_forest(steps)
    assert f["pipeline"]["order"] == ["011", "012b"]
    fork = {s["id"]: s["fork"] for s in f["steps"]}["011"]
    assert fork["options"] == ["012", "012b"] and fork["chosen"] == "012b"


# ------------------------------------------------------ ⑨ 不许弄坏的东西


def test_the_layout_is_not_moved_by_a_single_number(tmp_path: Path):
    """布局（order / lanes / tree）是几何，定稿流程是语义。声明一个成果不许让
    图上任何一个坐标动一下——否则「加一行 result 之后图变了」会被当成布局 bug 查。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    mk(tmp_path, "002b", "parent: 001\n" + DEAD)
    steps = mk(tmp_path, "003", "parent: 002\ninput: 002b | x.csv\n" + GOOD)
    before = core.compile_forest(steps)
    project(tmp_path, "result: 003 | r\n")
    after = core.compile_forest(steps)

    for key in ("order", "lanes", "lane_count", "tree", "branch_groups", "merges", "row_h", "lane_w"):
        assert json.dumps(before[key], ensure_ascii=False) == \
            json.dumps(after[key], ensure_ascii=False), f"{key} 不许因为定稿流程而变"
    assert [(s["lane"], s["row"]) for s in before["steps"]] == \
        [(s["lane"], s["row"]) for s in after["steps"]]
    assert before["warnings"] == after["warnings"], "诊断走 pipeline.diagnostics，不进顶栏"


def test_declaring_a_result_adds_exactly_one_key_and_nothing_else(tmp_path: Path):
    """森林和步骤各只多一个 `pipeline` 键，别的一个都不许动。

    写成「差集恰好是这一个」而不是逐字列出全部键：别人往 forest 里加键时这条
    不该假报警，但「定稿流程顺手改了别的字段」必须当场炸。
    """
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    before = core.compile_forest(steps)
    project(tmp_path, "result: 002 | r\n")
    after = core.compile_forest(steps)

    assert set(after) - set(before) == {"pipeline"} and set(before) - set(after) == set()
    for a, b in zip(after["steps"], before["steps"]):
        assert set(a) - set(b) == {"pipeline"} and set(b) - set(a) == set()
        assert {k: v for k, v in a.items() if k != "pipeline"} == b, "别的字段一个值都不许变"


def test_the_pipeline_is_byte_deterministic(tmp_path: Path):
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\n" + DEAD)
    mk(tmp_path, "003", "input: 002 | a\ninput: 001 | b\n" + GOOD)
    steps = mk(tmp_path, "004", "input: 003 | c\npipeline: include | x\n" + GOOD)
    project(tmp_path, "result: 003 | 主结果\nresult: 004 | 消融\n")
    assert json.dumps(core.compile_forest(steps), ensure_ascii=False) == \
        json.dumps(core.compile_forest(steps), ensure_ascii=False)


def test_deriving_the_pipeline_does_not_slow_the_compile_down(tmp_path: Path):
    """闭包 + 拓扑排序都是线性的，不许把 compile_forest 拖回二次复杂度。"""
    d = tmp_path / "steps"
    d.mkdir()
    for i in range(1, 1001):
        sd = d / f"{i:03d}_x"
        sd.mkdir()
        (sd / "note.md").write_text(
            f"---\nid: {i:03d}\nparent: {'' if i == 1 else f'{i-1:03d}'}\nstatus: done\n"
            f"title: t\ncommit: abc\npath: /blue/a | output | 数据\n"
            f"input: {max(1, i - 2):03d} | x.csv\n---\n{FULL}",
            encoding="utf-8", newline="\n")
    project(tmp_path, "result: 1000 | r\n")
    core.compile_forest(d)                          # 预热：量算法，不量冷文件系统
    t0 = time.perf_counter()
    f = core.compile_forest(d)
    elapsed = time.perf_counter() - t0
    # 每一步都写了 `input: i-2`，所以闭包**只沿数据依赖**跳着走，隔一步取一个
    # （1000 / 998 / 996 … / 002 加上根 001）。树上那条 parent 链在这里一步都不算数
    # ——正是「定稿流程沿的是字节，不是树」这句话在 1000 步上的样子。
    assert f["pipeline"]["order"] == ["001"] + [f"{i:03d}" for i in range(2, 1001, 2)]
    assert elapsed < 3.0, f"1000 步编译用了 {elapsed:.2f}s"


# ------------------------------------------------------ ⑩ 双语


def test_result_and_pipeline_are_structural_keys(tmp_path: Path):
    """译文里写一份，中英两个页面会导出两条不同的 Methods，而两边看着都像对的
    ——双真相源最难查的那一种。"""
    assert "result" in core.TR_STRUCT_KEYS and "pipeline" in core.TR_STRUCT_KEYS


def test_a_translation_cannot_change_which_steps_are_in_the_flow(tmp_path: Path):
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    (steps / "001_x" / "note.en.md").write_text(
        "---\ntitle: T\npipeline: exclude\n---\n\n## Why\na\n", encoding="utf-8", newline="\n")
    project(tmp_path, "result: 002 | r\n")
    f = core.compile_forest(steps)
    assert f["pipeline"]["order"] == ["001", "002"], "译文里的 pipeline: 一个字节都不许生效"
    hit = [w for w in f["warnings"] if w["code"] == "translation_structural_key"]
    assert any("`pipeline:`" in w["message"] for w in hit), "而且必须说出来，不能静默丢"
