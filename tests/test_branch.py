"""分叉语义：互斥候选（`branch:` / `decision:`）与汇回边（`input:` 的一个子集）。

树上每条父子边都长得一样，但它们说的不是同一件事。这一组断言钉的是**语义**：
哪些孩子是同一个问题的候选、这个岔路口定了没有、哪条 input 边是「支线的产物
汇回主路径」。几何（order / lanes / tree）一个数都不该动——那条由
tests/test_layout.py::test_branch_semantics_do_not_move_a_single_coordinate 钉。
"""

from pathlib import Path

import trace_core as core


def codes(ws):
    return sorted(w["code"] for w in ws)


def mk(root: Path, sid: str, fm: str = "", body: str = "## 为什么\nx\n") -> Path:
    d = root / "steps" / f"{sid}_x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "note.md").write_text(f"---\nid: {sid}\ntitle: t{sid}\n{fm}---\n{body}",
                               encoding="utf-8", newline="\n")
    return root / "steps"


def S(**kw):
    """手工造一个 Step（纯演算用，不落盘）。"""
    d = dict(id="001", status="done", title="t", body="", dirname="001_x")
    d.update(kw)
    return core.Step(**d)


def forest_of(*pairs, **_):
    """pairs: (id, parent, branch, status, decision, inputs)。返回 compile 不到的纯结构。"""
    by_id = {}
    for sid, parent, branch, status, decision, inputs in pairs:
        by_id[sid] = S(id=sid, parent=parent, branch=branch, status=status,
                       decision=decision, dirname=f"{sid}_x",
                       inputs=[{"step": t, "note": ""} for t in inputs])
    children = core.build_children(by_id)
    order = core.compute_order(by_id, children)
    return by_id, children, order


# ══════════════════════════════════════════════ ① branch: 的解析


def test_branch_defaults_to_extends_when_nobody_wrote_it():
    """绝大多数边都是普通延伸，所以它必须是**不写**就成立的那一个。
    要求每一步都写 `branch: extends` 等于给最常见的情况加税。"""
    step, ws = core.build_step("001_x", {"id": "001"}, "")
    assert step.branch == "extends" and step.branch_note == ""
    assert ws == []


def test_branch_carries_its_own_note_after_the_pipe():
    """沿用既有的 `位置 | 说明` 惯例：说明是**这个候选自己的角度**，
    和分叉点那句 `decision:`（在决定什么）是两句不同的话。"""
    step, ws = core.build_step(
        "012_x", {"id": "012", "branch": "alternative | 先试最便宜的：只调采样权重"}, "")
    assert step.branch == "alternative"
    assert step.branch_note == "先试最便宜的：只调采样权重"
    assert ws == []


def test_a_note_may_contain_more_pipes_and_is_not_reassembled():
    """说明是人写的字。按竖线切开再拼回去等于替人改文案，一次就够让人不信任它。"""
    step, _ = core.build_step("012_x", {"id": "012", "branch": "alternative | a | b"}, "")
    assert step.branch_note == "a | b"


def test_an_unknown_branch_value_warns_and_falls_back_instead_of_killing_the_step():
    """和 `status:` 完全同一条路。`alterative` 这种笔误一旦让整步解析失败，
    图上会**少一个节点**——那比一条警告危险得多。"""
    step, ws = core.build_step("012_x", {"id": "012", "branch": "alterative"}, "")
    assert step.branch == "extends"
    assert codes(ws) == ["bad_branch"]
    assert ws[0]["vars"] == {"branch": "alterative"}


def test_an_empty_branch_line_is_not_an_error():
    """`branch:` 后面什么都没写＝没写。为一行空值报警只会训练人忽略警告。"""
    step, ws = core.build_step("012_x", {"id": "012", "branch": "   "}, "")
    assert step.branch == "extends" and ws == []


def test_decision_is_free_text_not_a_vocabulary():
    """「在决定什么」是人话。给它一张词表就等于要求人先把问题翻译成枚举值。"""
    step, ws = core.build_step(
        "011_x", {"id": "011", "decision": "类别不平衡怎么处理？只能选一条走下去"}, "")
    assert step.decision == "类别不平衡怎么处理？只能选一条走下去"
    assert ws == []


def test_branch_and_decision_survive_to_dict():
    """网页和 MCP 读到的是 to_dict() 的结果，不是 Step 实例。"""
    d = S(branch="alternative", branch_note="只调采样", decision="怎么处理不平衡？").to_dict()
    assert d["branch"] == "alternative"
    assert d["branch_note"] == "只调采样"
    assert d["decision"] == "怎么处理不平衡？"


def test_parse_and_format_branch_round_trip():
    """读侧写侧共用这一对函数。各写一份解析迟早出现「写得进去、读不回来」的候选。"""
    for raw in ("alternative", "alternative | 先试最便宜的", "extends"):
        assert core.format_branch(core.parse_branch(raw)) == raw


def test_branch_and_decision_are_structural_keys_in_translations():
    """译文里写一份 `branch:`，页面就会按两套边画同一棵树——双真相源的原样重演。"""
    assert "branch" in core.TR_STRUCT_KEYS and "decision" in core.TR_STRUCT_KEYS
    _data, ws = core.parse_translation(
        "---\ntitle: T\nbranch: alternative\ndecision: which?\n---\n\n## Why\na\n")
    assert codes(ws) == ["translation_structural_key", "translation_structural_key"]


# ══════════════════════════════════════════════ ② 候选组是派生的


def test_alternative_siblings_form_one_group_without_registering_each_other():
    """互斥是一组关系。写成兄弟之间互相登记（`alt: 012b`）要写 N×(N−1) 份，
    改一处漏一处——同一个事实存在多处正是上一代系统的死因。
    磁盘上只有每个孩子自己那句「我是一个候选」。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "类别不平衡怎么处理？", []),
        ("012", "011", "alternative", "wip", "", []),
        ("012b", "011", "alternative", "wip", "", []),
        ("013", "011", "extends", "wip", "", []),          # 普通延伸，不进这一组
    )
    (g,) = core.compute_branch_groups(by_id, children)
    assert g["at"] == "011"
    assert g["options"] == ["012", "012b"]
    assert g["decision"] == "类别不平衡怎么处理？"


def test_a_plain_tree_has_no_groups_at_all():
    """没人写 alternative 时这个功能必须完全隐形——既有记录一个字都不用改。"""
    by_id, children, _ = forest_of(
        ("001", None, "extends", "done", "", []),
        ("002", "001", "extends", "done", "", []),
    )
    assert core.compute_branch_groups(by_id, children) == []


def test_the_decision_is_read_off_status_not_off_a_chosen_field():
    """「选了 A」写下来**就是**「B 标 dead」。再存一个 chosen 字段就是第二份真相。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "怎么处理不平衡？", []),
        ("012", "011", "alternative", "done", "", []),
        ("012b", "011", "alternative", "dead", "", []),
    )
    (g,) = core.compute_branch_groups(by_id, children)
    assert g["state"] == "decided" and g["chosen"] == "012" and g["live"] == ["012"]


def test_two_live_options_means_the_fork_is_still_open():
    """这条是整个功能真正的收益：研究者最需要知道「我还有几个岔路口没做决定」。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "怎么处理不平衡？", []),
        ("012", "011", "alternative", "wip", "", []),
        ("012b", "011", "alternative", "done", "", []),
    )
    (g,) = core.compute_branch_groups(by_id, children)
    assert g["state"] == "open" and g["chosen"] == "" and g["live"] == ["012", "012b"]


def test_all_options_dead_is_a_conclusion_not_an_error():
    """P4：全废＝「这个问题的答案是都不行」。它是结论，不是坏数据，
    所以有自己的状态名，而不是被并进 open 或者报成错误。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "怎么处理不平衡？", []),
        ("012", "011", "alternative", "dead", "", []),
        ("012b", "011", "alternative", "dead", "", []),
    )
    (g,) = core.compute_branch_groups(by_id, children)
    assert g["state"] == "abandoned" and g["chosen"] == "" and g["live"] == []
    assert g["state"] in core.BRANCH_STATES


def test_roots_can_be_alternatives_too_and_form_the_group_with_an_empty_at():
    """两条互斥的开局没有共同的父节点。不给它成组，那两句 `branch: alternative`
    就写了却什么都不发生——静默失效比报错难查得多。"""
    by_id, children, _ = forest_of(
        ("001", None, "alternative", "wip", "", []),
        ("001b", None, "alternative", "wip", "", []),
    )
    (g,) = core.compute_branch_groups(by_id, children)
    assert g["at"] == "" and g["options"] == ["001", "001b"] and g["decision"] == ""


def test_groups_come_out_in_a_fixed_order():
    """P3：静态导出要逐字节确定，所以组的顺序不能跟着 dict 的插入顺序走。"""
    by_id, children, _ = forest_of(
        ("001", None, "alternative", "wip", "", []),
        ("001b", None, "alternative", "wip", "", []),
        ("020", "001", "extends", "wip", "都试哪个？", []),
        ("021", "020", "alternative", "wip", "", []),
        ("021b", "020", "alternative", "wip", "", []),
        ("010", "001", "extends", "wip", "先做谁？", []),
        ("011", "010", "alternative", "wip", "", []),
        ("011b", "010", "alternative", "wip", "", []),
    )
    ats = [g["at"] for g in core.compute_branch_groups(by_id, children)]
    assert ats == ["", "010", "020"], "根那一组在最前，其余按分叉点 id 序"


# ══════════════════════════════════════════════ ③ 三条诊断


def test_a_single_alternative_child_is_flagged_because_one_option_is_not_a_choice():
    """一个候选不成其为选择：多半是另一条支漏标了，或者它其实是普通延伸。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "怎么处理不平衡？", []),
        ("012", "011", "alternative", "wip", "", []),
    )
    ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    assert codes(ws) == ["lone_alternative"]
    assert ws[0]["level"] == "warn"
    assert ws[0]["where"] == "011_x"


def test_a_lone_alternative_does_not_also_get_the_missing_decision_warning():
    """两条一起报会让人以为要补两样东西，而实际只有一个问题：那一组根本还不成立。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "", []),
        ("012", "011", "alternative", "wip", "", []),
    )
    ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    assert codes(ws) == ["lone_alternative"]


def test_a_fork_without_a_decision_is_flagged_because_that_sentence_cannot_be_derived():
    """候选有谁、选中了谁都算得出来，唯独「当时在纠结什么」只能人写。
    半年后看到两条并排的支线，没有这句话就只剩猜。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "", []),
        ("012", "011", "alternative", "dead", "", []),
        ("012b", "011", "alternative", "done", "", []),
    )
    ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    assert codes(ws) == ["fork_without_decision"]
    assert ws[0]["vars"] == {"id": "011", "n": "2", "options": "012 / 012b"}


def test_the_root_group_is_never_asked_for_a_decision_it_has_nowhere_to_write():
    """`decision:` 得写在分叉点上，而根之间那一组没有分叉点。
    报一条改不动的警告只会训练人忽略警告。"""
    by_id, children, _ = forest_of(
        ("001", None, "alternative", "dead", "", []),
        ("001b", None, "alternative", "done", "", []),
    )
    ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    assert codes(ws) == []


def test_an_undecided_fork_is_reported_as_a_reminder_not_as_a_mistake():
    """同时开几条线是研究的常态。措辞如果读起来像责备，人会去关掉警告而不是去做决定。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "怎么处理不平衡？", []),
        ("012", "011", "alternative", "wip", "", []),
        ("012b", "011", "alternative", "wip", "", []),
        ("012c", "011", "alternative", "dead", "", []),
    )
    ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    assert codes(ws) == ["undecided_fork"]
    w = ws[0]
    assert w["level"] == "warn", "未决不是错，绝不能升级成 error"
    assert w["vars"] == {"id": "011", "n": "2", "options": "012 / 012b"}
    assert "不是错" in w["message"]


def test_a_decided_or_abandoned_fork_is_silent():
    """做完决定的岔路口不该再出声——常驻警告等于没有警告。"""
    for a, b in (("done", "dead"), ("dead", "dead")):
        by_id, children, _ = forest_of(
            ("011", None, "extends", "done", "怎么处理不平衡？", []),
            ("012", "011", "alternative", a, "", []),
            ("012b", "011", "alternative", b, "", []),
        )
        ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
        assert codes(ws) == [], f"{a}/{b} 不该报警"


def test_none_of_the_three_diagnostics_is_ever_an_error():
    """分叉写法有问题时树照样画得出来，所以一条都不许降级到 error。"""
    by_id, children, _ = forest_of(
        ("011", None, "extends", "done", "", []),
        ("012", "011", "alternative", "wip", "", []),
        ("012b", "011", "alternative", "wip", "", []),
    )
    ws = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    assert codes(ws) == ["fork_without_decision", "undecided_fork"]
    assert {w["level"] for w in ws} == {"warn"}


# ══════════════════════════════════════════════ ④ 汇回边


def test_a_side_branch_product_feeding_back_into_the_other_line_is_a_merge():
    """用户要的第三种关系：011 分叉出 012 / 012b，012b 底下的 013 产出的东西
    后来参与了 012 那条线上的 014。判据只看树的形状，不猜「主线是哪条」。"""
    by_id, _c, order = forest_of(
        ("011", None, "extends", "done", "怎么处理不平衡？", []),
        ("012", "011", "alternative", "done", "", []),
        ("012b", "011", "alternative", "dead", "", []),
        ("013", "012b", "extends", "done", "", []),
        ("014", "012", "extends", "done", "", ["013"]),
    )
    (m,) = core.compute_merges(by_id, order)
    assert m["from"] == "013" and m["to"] == "014"
    assert m["at"] == "011", "带上两条线分家的那个岔路口，画曲线和写说明都要它"


def test_an_input_from_an_ancestor_is_a_plain_data_dependency():
    """生产者在消费者的祖先链上时，这条数据依赖和树边走同一条路——
    树已经把它画出来了，再叠一条曲线只是把主干描粗一遍。"""
    by_id, _c, order = forest_of(
        ("001", None, "extends", "done", "", []),
        ("002", "001", "extends", "done", "", []),
        ("003", "002", "extends", "done", "", ["001"]),
    )
    assert core.compute_merges(by_id, order) == []


def test_an_input_from_a_descendant_is_not_a_merge_either():
    """消费者在生产者上方（回头补一条 input）同样落在祖先链上，判不了汇回就别硬凑。"""
    by_id, _c, order = forest_of(
        ("001", None, "extends", "done", "", ["003"]),
        ("002", "001", "extends", "done", "", []),
        ("003", "002", "extends", "done", "", []),
    )
    assert core.compute_merges(by_id, order) == []


def test_an_input_across_two_separate_trees_is_not_a_merge():
    """两棵树没有共同祖先，两端从来没在同一条线上过——谈不上「汇回」。
    孤儿和跨项目造成的那些边老实算普通数据依赖。"""
    by_id, _c, order = forest_of(
        ("001", None, "extends", "done", "", []),
        ("009", None, "extends", "done", "", ["001"]),
    )
    assert core.compute_merges(by_id, order) == []


def test_a_dangling_or_self_input_never_becomes_a_merge():
    """validate_inputs 已经报过它们了；这里再造一条边只会在图上画出一条通向空气的线。"""
    by_id, _c, order = forest_of(
        ("001", None, "extends", "done", "", ["001", "999"]),
    )
    assert core.compute_merges(by_id, order) == []


def test_row_order_is_deliberately_not_part_of_the_criterion():
    """防的是拿 order/row 当「先后」：order 是按 id 的前序遍历，不是时间轴。
    上面那棵树的前序是 011 012 014 012b 013 —— 生产者 013 的**行号比消费者 014 大**。
    要求 row(生产者) < row(消费者) 会把最典型的那个汇回判掉。"""
    by_id, _c, order = forest_of(
        ("011", None, "extends", "done", "d", []),
        ("012", "011", "alternative", "done", "", []),
        ("012b", "011", "alternative", "dead", "", []),
        ("013", "012b", "extends", "done", "", []),
        ("014", "012", "extends", "done", "", ["013"]),
    )
    assert order.index("013") > order.index("014")
    assert [m["from"] for m in core.compute_merges(by_id, order)] == ["013"]


def test_several_input_lines_between_the_same_pair_collapse_into_one_edge():
    """图上画的是一条边，说明可以有好几条。每行各画一条会在同一对节点之间叠出重影。"""
    by_id = {
        "011": S(id="011", parent=None, dirname="011_x"),
        "012": S(id="012", parent="011", dirname="012_x"),
        "012b": S(id="012b", parent="011", dirname="012b_x"),
        "013": S(id="013", parent="012b", dirname="013_x"),
        "014": S(id="014", parent="012", dirname="014_x",
                 inputs=[{"step": "013", "note": "a.csv"},
                         {"step": "013", "note": "b.csv"},
                         {"step": "013", "note": "a.csv"}]),
    }
    children = core.build_children(by_id)
    order = core.compute_order(by_id, children)
    (m,) = core.compute_merges(by_id, order)
    assert m["notes"] == ["a.csv", "b.csv"]


# ══════════════════════════════════════════════ ⑤ 接进 compile_forest


def test_the_forest_exposes_groups_merges_and_per_step_handles(tmp_path: Path):
    """网页照着画的就是这几个键。少一个，那一版的图就画不出来。"""
    mk(tmp_path, "011", "decision: 类别不平衡怎么处理？只能选一条走下去\n")
    mk(tmp_path, "012", "parent: 011\nbranch: alternative | 只调采样\nstatus: done\n")
    mk(tmp_path, "012b", "parent: 011\nbranch: alternative | 换损失函数\nstatus: dead\n",
       "## 为什么\nx\n## 做了什么\nx\n## 结论\n不行\n")
    mk(tmp_path, "013", "parent: 012b\nstatus: done\n")
    steps = mk(tmp_path, "014", "parent: 012\nstatus: done\ninput: 013 | scores.csv\n")

    f = core.compile_forest(steps)
    by = {s["id"]: s for s in f["steps"]}

    (g,) = f["branch_groups"]
    assert g == {"at": "011", "decision": "类别不平衡怎么处理？只能选一条走下去",
                 "options": ["012", "012b"], "live": ["012"],
                 "state": "decided", "chosen": "012"}
    assert by["011"]["fork"] == g, "分叉点自己也要拿得到整组，画括弧不用回头翻总表"
    assert by["012"]["fork"] is None and by["012"]["branch"] == "alternative"
    assert by["012"]["branch_note"] == "只调采样"

    assert f["merges"] == [{"from": "013", "to": "014", "at": "011",
                            "notes": ["scores.csv"]}]
    assert by["014"]["merge_in"] == ["013"] and by["014"]["merge_out"] == []
    assert by["013"]["merge_out"] == ["014"] and by["013"]["merge_in"] == []


def test_every_step_always_carries_the_new_keys_even_with_nothing_declared(tmp_path: Path):
    """P3：键的有无不能跟着内容变，否则静态导出的字节会随记录内容抖动。"""
    steps = mk(tmp_path, "001")
    (s,) = core.compile_forest(steps)["steps"]
    for k in ("branch", "branch_note", "decision", "fork", "merge_in", "merge_out"):
        assert k in s
    assert s["fork"] is None and s["merge_in"] == [] and s["merge_out"] == []


def test_inputs_stay_a_verbatim_mirror_of_the_file(tmp_path: Path):
    """「是不是汇回」是从树的形状算出来的，会因为别人被移动而改变归类。
    把它塞进 inputs 里那几条记录，读的人就分不清哪些字段是文件里写着的——
    移动一步之后 inputs 看着变了，而磁盘上一个字节没动。"""
    mk(tmp_path, "011")
    mk(tmp_path, "012", "parent: 011\n")
    mk(tmp_path, "012b", "parent: 011\n")
    mk(tmp_path, "013", "parent: 012b\n")
    steps = mk(tmp_path, "014", "parent: 012\ninput: 013 | scores.csv\n")
    by = {s["id"]: s for s in core.compile_forest(steps)["steps"]}
    assert by["014"]["inputs"] == [{"step": "013", "note": "scores.csv"}]


def test_the_three_branch_diagnostics_reach_the_forest_warnings(tmp_path: Path):
    """诊断只有进了 warnings 才看得见——check 和网页顶栏读的就是这一个列表。"""
    mk(tmp_path, "011")
    mk(tmp_path, "012", "parent: 011\nbranch: alternative\n")
    steps = mk(tmp_path, "012b", "parent: 011\nbranch: alternative\n")
    got = codes(core.compile_forest(steps)["warnings"])
    assert "fork_without_decision" in got and "undecided_fork" in got


def test_compiling_twice_gives_byte_identical_output(tmp_path: Path):
    """P3：静态导出逐字节确定。新加的组和边都参与序列化，顺序不能靠 dict 的运气。"""
    import json

    mk(tmp_path, "011", "decision: 走哪条？\n")
    mk(tmp_path, "012", "parent: 011\nbranch: alternative\n")
    mk(tmp_path, "012b", "parent: 011\nbranch: alternative\nstatus: dead\n")
    mk(tmp_path, "013", "parent: 012b\n")
    steps = mk(tmp_path, "014", "parent: 012\ninput: 013 | a.csv\n")
    a = json.dumps(core.compile_forest(steps), ensure_ascii=False, sort_keys=False)
    b = json.dumps(core.compile_forest(steps), ensure_ascii=False, sort_keys=False)
    assert a == b


def test_the_group_on_a_step_is_a_copy(tmp_path: Path):
    """step["fork"] 和 branch_groups 里那一份共享对象的话，谁改了一处两处都变。"""
    mk(tmp_path, "011", "decision: 走哪条？\n")
    mk(tmp_path, "012", "parent: 011\nbranch: alternative\n")
    steps = mk(tmp_path, "012b", "parent: 011\nbranch: alternative\n")
    f = core.compile_forest(steps)
    by = {s["id"]: s for s in f["steps"]}
    by["011"]["fork"]["options"].append("012c")
    assert f["branch_groups"][0]["options"] == ["012", "012b"]


def test_merges_across_two_deep_branches_stay_cheap(tmp_path: Path):
    """汇回判据要爬到 LCA，而这里两条支各 500 层深、LCA 在最顶上——
    每条 input 边都是最长的那种爬法。钉的是「没有人把它写成对每条边重算一遍全树」。

    形状是刻意挑的：纯链上所有 input 都落在祖先链上，一条汇回都产不出来，
    也就测不到爬 LCA 的那段代码（等于没测）。
    """
    import time

    mk(tmp_path, "001")
    n = 500
    for i in range(2, n + 2):                       # A 线：002 … 501
        mk(tmp_path, f"{i:03d}", f"parent: {i - 1:03d}\n" if i > 2 else "parent: 001\n")
    for i in range(2, n + 2):                       # B 线：002b … 501b
        parent = f"{i - 1:03d}b" if i > 2 else "001"
        fm = f"parent: {parent}\n"
        if i % 5 == 0:                              # 每 5 步往 A 线汇回一次
            fm += f"input: {i:03d} | x.csv\n"
        mk(tmp_path, f"{i:03d}b", fm)
    steps = tmp_path / "steps"

    t0 = time.perf_counter()
    f = core.compile_forest(steps, with_files=False)
    dt = time.perf_counter() - t0
    assert len(f["merges"]) == n // 5
    assert all(m["at"] == "001" for m in f["merges"])
    assert dt < 8.0, f"1001 步 + {n // 5} 条汇回编译用了 {dt:.2f}s"
