"""图视图的树布局断言。和 order/lanes 一样是纯函数，不碰 IO、不碰渲染。"""

import trace_core as core
from trace_core import Step, build_children, compute_order, compute_tree

STEP = core.NODE_W + core.H_GAP          # 同层相邻节点的中心距
LEVEL = core.NODE_H + core.V_GAP         # 层间距
PAD = core.PAD


def mk(*pairs):
    by_id = {sid: Step(id=sid, parent=parent, title=sid) for sid, parent in pairs}
    children = build_children(by_id)
    order = compute_order(by_id, children)
    return by_id, children, order, compute_tree(by_id, children, order)


def xs(tree):
    return {k: v["x"] for k, v in tree["nodes"].items()}


def ys(tree):
    return {k: v["y"] for k, v in tree["nodes"].items()}


# ------------------------------------------------------------ 基本形状


def test_empty_forest_does_not_blow_up():
    t = compute_tree({}, {}, [])
    assert t["nodes"] == {} and t["w"] > 0 and t["h"] > 0


def test_single_node():
    _, _, _, t = mk(("001", None))
    assert xs(t) == {"001": PAD}
    assert ys(t) == {"001": PAD}


def test_linear_chain_is_a_straight_column():
    _, _, _, t = mk(("001", None), ("002", "001"), ("003", "002"))
    assert xs(t) == {"001": PAD, "002": PAD, "003": PAD}
    assert ys(t) == {"001": PAD, "002": PAD + LEVEL, "003": PAD + 2 * LEVEL}


def test_parent_is_centred_over_its_children():
    _, _, _, t = mk(("001", None), ("002", "001"), ("002b", "001"))
    x = xs(t)
    assert x["002"] == PAD
    assert x["002b"] == PAD + STEP
    assert x["001"] == PAD + STEP / 2, "父必须落在首末子节点的正中间"


def test_three_way_fork_keeps_parent_centred():
    _, _, _, t = mk(("001", None), ("002", "001"), ("002b", "001"), ("002c", "001"))
    x = xs(t)
    assert [x["002"], x["002b"], x["002c"]] == [PAD, PAD + STEP, PAD + 2 * STEP]
    assert x["001"] == PAD + STEP


def test_subtree_is_shifted_as_a_whole_to_avoid_collision():
    """居中后会撞上左边已有节点时，必须移动整棵子树，而不是只挪父节点。

    只挪父节点会让它不再居中于子节点——那正是朴素实现最常见的错误。
    """
    _, _, _, t = mk(("001", None), ("002", "001"), ("002b", "001"), ("003", "002b"))
    x = xs(t)
    assert x["002"] == PAD
    # 002b 想居中于 003（本来在 PAD），但本层已经被 002 占到 PAD+STEP，于是整支右移
    assert x["002b"] == PAD + STEP
    assert x["003"] == PAD + STEP, "子节点跟着父一起移动，父仍然居中"
    assert x["001"] == PAD + STEP / 2


def test_separate_trees_do_not_interleave():
    _, _, _, t = mk(("001", None), ("002", "001"), ("009", None), ("010", "009"))
    x = xs(t)
    assert x["009"] >= x["001"] + core.NODE_W + core.TREE_GAP - core.H_GAP
    assert x["010"] >= x["002"] + core.NODE_W, "第二棵树在所有层上都要躲开第一棵"


# ------------------------------------------------------------ 性质


def _big():
    return [
        ("001", None), ("002", "001"), ("002b", "001"), ("002c", "001"),
        ("003", "002"), ("004", "003"), ("005", "004"), ("005b", "004"),
        ("006", "002b"), ("007", "005"), ("008", "002c"),
        ("009", None), ("010", "009"),
    ]


def test_T1_parent_is_always_above_its_children():
    by_id, _, _, t = mk(*_big())
    y = ys(t)
    for sid, s in by_id.items():
        if s.parent:
            assert y[s.parent] < y[sid], f"{s.parent} 必须画在 {sid} 上方"
            assert y[sid] - y[s.parent] == LEVEL, "父子必须正好相隔一层"


def test_T2_nodes_on_the_same_level_never_overlap():
    _, _, _, t = mk(*_big())
    by_level: dict[int, list[float]] = {}
    for v in t["nodes"].values():
        by_level.setdefault(v["depth"], []).append(v["x"])
    for depth, row in by_level.items():
        row.sort()
        for a, b in zip(row, row[1:]):
            assert b - a >= core.NODE_W, f"第 {depth} 层上 x={a} 与 x={b} 的卡片重叠"


def test_T3_every_parent_stays_centred():
    by_id, children, _, t = mk(*_big())
    x = xs(t)
    for p, kids in children.items():
        if kids:
            assert abs(x[p] - (x[kids[0]] + x[kids[-1]]) / 2) < 0.01, f"{p} 没有居中于子节点"


def test_T4_canvas_covers_every_node():
    _, _, _, t = mk(*_big())
    for sid, v in t["nodes"].items():
        assert v["x"] >= 0 and v["x"] + t["node_w"] <= t["w"], f"{sid} 超出画布宽度"
        assert v["y"] >= 0 and v["y"] + t["node_h"] <= t["h"], f"{sid} 超出画布高度"


def test_deep_chain_does_not_hit_the_recursion_limit():
    """后序遍历是迭代的——一条很深的链不该把布局函数打爆。"""
    pairs = [("001", None)] + [(f"{i:03d}", f"{i - 1:03d}") for i in range(2, 1201)]
    _, _, _, t = mk(*pairs)
    assert len(t["nodes"]) == 1200
    assert t["h"] == PAD * 2 + 1200 * core.NODE_H + 1199 * core.V_GAP
    assert {v["x"] for v in t["nodes"].values()} == {PAD}, "单链应当是一条直柱"


def test_tree_is_deterministic():
    a = mk(*_big())[3]
    b = mk(*_big())[3]
    assert a == b
