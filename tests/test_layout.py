"""布局算法的断言。order / lanes 是纯函数，这里完全不碰 IO、不碰 HTTP。"""

from trace_core import Step, build_children, compute_lanes, compute_order


def mk(*pairs):
    """pairs: (id, parent)。返回 (by_id, children, order, lane, end)。"""
    by_id = {sid: Step(id=sid, parent=parent, title=sid) for sid, parent in pairs}
    children = build_children(by_id)
    order = compute_order(by_id, children)
    lane, end = compute_lanes(by_id, children, order)
    return by_id, children, order, lane, end


def test_linear_chain_stays_on_lane_zero():
    _, _, order, lane, _ = mk(("001", None), ("002", "001"), ("003", "002"))
    assert order == ["001", "002", "003"]
    assert lane == {"001": 0, "002": 0, "003": 0}


def test_id_sorting_is_numeric_not_lexical():
    """字符串序下 "9" > "10"，必须按 int 比。"""
    _, _, order, _, _ = mk(("009", None), ("010", "009"), ("002", None))
    assert order == ["002", "009", "010"]


def test_heir_is_chosen_by_depth_not_by_first_child():
    """最早尝试的那条支往往是后来废掉的；主干应该留给实际走下去的那条线。"""
    _, _, order, lane, _ = mk(("001", None), ("002", "001"), ("003", "001"), ("004", "003"))
    assert order == ["001", "002", "003", "004"]
    # 002 是 id 更小的第一个子节点，但它是叶子；主线应该是 003 → 004
    assert lane == {"001": 0, "002": 1, "003": 0, "004": 0}


def test_lane_is_reclaimed_after_a_branch_ends():
    _, _, order, lane, _ = mk(
        ("001", None), ("002", "001"), ("003", "001"), ("004", "003"), ("005", "003")
    )
    assert order == ["001", "002", "003", "004", "005"]
    assert lane["002"] == 1
    assert lane["005"] == 1, "002 那条支结束后，它的轨道应当被 005 复用"
    assert max(lane.values()) == 1, "只需要两条轨道"


def test_lane_count_does_not_grow_with_step_count():
    pairs = [("001", None)] + [(f"{i:03d}", f"{i - 1:03d}") for i in range(2, 51)]
    _, _, order, lane, _ = mk(*pairs)
    assert len(order) == 50
    assert max(lane.values()) == 0, "轨道总数只取决于最大同时活跃分支数，与 step 总数无关"


def test_single_root_mainline_is_always_lane_zero():
    pairs = [
        ("001", None), ("002", "001"), ("002b", "001"), ("002c", "001"),
        ("003", "002"), ("004", "003"), ("005", "002b"),
    ]
    _, _, _, lane, _ = mk(*pairs)
    assert lane["001"] == 0
    assert lane["003"] == 0 and lane["004"] == 0


def test_multiple_roots_are_legal():
    _, _, order, lane, _ = mk(("001", None), ("002", "001"), ("003", None))
    assert order == ["001", "002", "003"]
    assert lane["003"] == 0, "001 那棵树在 003 出现时已经结束，轨道 0 可以复用"


def test_O1_parent_row_strictly_less_than_children():
    by_id, children, order, _, _ = mk(*_big_tree())
    row = {sid: i for i, sid in enumerate(order)}
    for sid, s in by_id.items():
        if s.parent:
            assert row[s.parent] < row[sid], f"{s.parent} 必须排在 {sid} 前面"


def test_O2_subtree_occupies_a_contiguous_row_range():
    """子树连续，将来加"折叠分支"就退化成"隐藏一段连续区间"，几乎零成本。"""
    by_id, children, order, _, end = mk(*_big_tree())
    row = {sid: i for i, sid in enumerate(order)}

    def subtree(sid):
        out = [sid]
        for c in children[sid]:
            out += subtree(c)
        return out

    for sid in by_id:
        rows = sorted(row[x] for x in subtree(sid))
        assert rows == list(range(row[sid], end[sid] + 1)), f"{sid} 的子树行区间不连续"


def test_lanes_never_collide():
    """同一条轨道上，两个节点的活跃区间 [row, end] 不能重叠。

    注意占用范围的定义：一条跨轨道的边是沿**父的轨道**向下、到子那一行才拐进
    子的轨道（规格 6.2），所以它并不占用子轨道的整段，只占子那一个行位。
    因此轨道上真正被占的是每个节点自己的 [row(n), end(n)]。
    唯一允许相接的是主线继承——heir 继承父的轨道，那是同一条线的延续。
    """
    by_id, _, order, lane, end = mk(*_big_tree())
    row = {sid: i for i, sid in enumerate(order)}

    by_lane: dict[int, list[str]] = {}
    for sid in order:
        by_lane.setdefault(lane[sid], []).append(sid)

    for l, nodes in by_lane.items():
        nodes.sort(key=lambda n: row[n])
        for prev, cur in zip(nodes, nodes[1:]):
            if by_id[cur].parent == prev:
                continue  # heir 继承，同一条线往下走
            assert row[cur] > end[prev], (
                f"轨道 {l} 上 {prev}[{row[prev]}..{end[prev]}] 与 {cur}[{row[cur]}..] 重叠"
            )


def _big_tree():
    return [
        ("001", None),
        ("002", "001"),
        ("002b", "001"),
        ("002c", "001"),
        ("003", "002"),
        ("004", "003"),
        ("005", "004"),
        ("005b", "004"),
        ("006", "002b"),
        ("007", "005"),
        ("008", "002c"),
        ("009", None),
        ("010", "009"),
    ]
