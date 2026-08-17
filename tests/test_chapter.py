"""章节：给项目内部那几条独立的探索路径起名字（主实验 / 消融实验 / …）。

森林（多个根）早就给了「独立的路径」，这一轮加的只是**一个名字**——声明写在开启
那条线的步骤上，**沿树继承**，其余全部派生：有哪些章节、各自有谁、各自的定稿流程、
各自的可溯源等级、跨章节的边。

这个文件里的断言分两半：一半钉「派生的东西绝不存储」（继承、移动之后自己跟着变），
另一半钉「没写 `chapter:` 的项目完全无感」——现存项目全是那个状态。
"""

import json
import time
from pathlib import Path

import trace_core as core

FULL = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
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
    (root / "project.md").write_text(f"---\nname: p\n{front}---\n\n",
                                     encoding="utf-8", newline="\n")


def S(sid: str, parent=None, chapter: str = "", **kw) -> core.Step:
    """手工造一步，用来对着纯函数写断言（不碰文件系统）。"""
    return core.Step(id=sid, parent=parent, chapter=chapter, dirname=f"{sid}_x", **kw)


def by(steps) -> dict[str, core.Step]:
    return {s.id: s for s in steps}


def chapters_of(f) -> list[str]:
    return [c["name"] for c in f["chapters"]["chapters"]]


def codes(ws) -> list[str]:
    return [w["code"] for w in ws]


# ------------------------------------------------------ ① 没写 chapter = 完全无感


def test_a_project_that_never_declared_a_chapter_gets_no_new_field_no_new_warning(tmp_path: Path):
    """现存项目一个 `chapter:` 都没有，它们的 forest 必须逐字节和从前一样。

    防的是「算一份『未分章』挂上去」：那会给每个项目每个步骤各加一个字段值、
    给顶栏加一条「你还没分章节」。功能没上线就先让所有人多看一条诊断，
    人从此略过诊断栏——真正的缺口（dead 没写结论）反而更难被发现。
    """
    steps = mk(tmp_path, "001", GOOD)
    project(tmp_path)
    f = core.compile_forest(steps)
    assert "chapters" not in f, "没分章节就不该有这个键"
    assert "chapter" not in f["steps"][0], "步骤上也不许多一个字段"
    assert f["warnings"] == [], "一条警告都不许多"


def test_declaring_a_chapter_adds_exactly_one_key_and_nothing_else(tmp_path: Path):
    """森林和步骤各只多一个键，别的一个值都不许动。

    写成「差集恰好是这一个」而不是逐字列出全部键：别人往 forest 里加键时这条
    不该假报警，但「章节顺手改了别的字段」必须当场炸。
    """
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    before = core.compile_forest(steps)
    mk(tmp_path, "002", "parent: 001\nchapter: 消融实验\n" + GOOD)
    after = core.compile_forest(steps)

    assert set(after) - set(before) == {"chapters"} and set(before) - set(after) == set()
    for a, b in zip(after["steps"], before["steps"]):
        assert set(a) - set(b) == {"chapter"} and set(b) - set(a) == set()
        # digest 是 note.md 原始字节的指纹，而声明章节**就是**往那个文件里写一行，
        # 所以只有被改的那一步的 digest 该变（乐观并发控制正是靠它发现「盘上那份
        # 已经不是你读到的那份」）。没被碰过的 001 连它都不许动。
        assert {k: v for k, v in a.items() if k not in ("chapter", "digest")} == \
            {k: v for k, v in b.items() if k != "digest"}, "别的字段一个值都不许变"
    assert after["steps"][0]["digest"] == before["steps"][0]["digest"], \
        "没被编辑的步骤连指纹都不许变"


def test_a_flow_without_chapters_keeps_its_pipeline_byte_for_byte(tmp_path: Path):
    """定稿流程那份派生也不许多一个键——它是 Methods 导出的输入，多一个键就是
    多一次「这是哪来的」。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    project(tmp_path, "result: 002 | r\n")
    before = core.compile_forest(steps)["pipeline"]
    assert "chapters" not in before

    mk(tmp_path, "002", "parent: 001\nchapter: 主实验\n" + GOOD)
    after = core.compile_forest(steps)["pipeline"]
    assert set(after) - set(before) == {"chapters"}
    assert {k: v for k, v in after.items() if k != "chapters"} == before


def test_the_layout_is_not_moved_by_a_chapter_name(tmp_path: Path):
    """布局（order / lanes / tree）是几何，章节是语义。起一个名字不许让图上任何一个
    坐标动一下——否则「分了章节之后图变了」会被当成布局 bug 查半天。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    mk(tmp_path, "002b", "parent: 001\n" + DEAD)
    steps = mk(tmp_path, "003", "parent: 002\ninput: 002b | x.csv\n" + GOOD)
    before = core.compile_forest(steps)
    mk(tmp_path, "002b", "parent: 001\nchapter: 消融实验 | 拿掉模块\n" + DEAD)
    after = core.compile_forest(steps)

    for key in ("order", "lanes", "lane_count", "tree", "branch_groups", "merges",
                "row_h", "lane_w"):
        assert json.dumps(before[key], ensure_ascii=False) == \
            json.dumps(after[key], ensure_ascii=False), f"{key} 不许因为章节而变"
    assert [(s["lane"], s["row"]) for s in before["steps"]] == \
        [(s["lane"], s["row"]) for s in after["steps"]]
    assert before["warnings"] == after["warnings"], "诊断走 chapters.diagnostics，不进顶栏"


def test_chapters_are_byte_deterministic(tmp_path: Path):
    mk(tmp_path, "001", "chapter: 主实验 | 正式那条\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    mk(tmp_path, "003", "chapter: 消融实验 | 逐个拿掉模块\ninput: 002 | main.csv\n" + GOOD)
    steps = mk(tmp_path, "004", "parent: 003\n" + DEAD)
    project(tmp_path, "result: 002 | 主结果\nresult: 003 | 消融\n")
    assert json.dumps(core.compile_forest(steps), ensure_ascii=False) == \
        json.dumps(core.compile_forest(steps), ensure_ascii=False)


# ------------------------------------------------------ ② 一行声明，其余全部派生


def test_a_chapter_line_is_a_name_and_an_optional_description_of_the_chapter():
    """竖线右边是**这个章节**的说明，不是这一步的（这一步的说明是 title 和正文）。"""
    assert core.parse_chapter("消融实验 | 逐个拿掉模块，对着主实验的 023 比") == \
        {"name": "消融实验", "note": "逐个拿掉模块，对着主实验的 023 比"}
    assert core.parse_chapter("主实验") == {"name": "主实验", "note": ""}
    assert core.parse_chapter("") == {"name": "", "note": ""}


def test_a_chapter_name_is_kept_verbatim_so_grep_still_finds_it():
    """`branch:` / `pipeline:` 的取值转小写（词表，机器读），章节名**不转**：
    `grep -r "chapter: Ablation"` 要原样捞到写下去的那几个字（G4）。"""
    assert core.parse_chapter("Ablation Study | x")["name"] == "Ablation Study"
    assert core.parse_chapter("  主实验/数据准备  ")["name"] == "主实验/数据准备"


def test_chapter_lines_round_trip():
    for raw in ("消融实验", "消融实验 | 逐个拿掉模块", "主实验/数据准备 | 只做一次"):
        assert core.format_chapter(core.parse_chapter(raw)) == raw.strip()


def test_a_description_without_a_name_is_reported_instead_of_swallowed(tmp_path: Path):
    """`chapter: | 逐个拿掉模块` 看着像声明了章节，实际一个字都不生效，
    而这一步会静静继承 parent 的章节——人以为开了一条新线，页面上它还在主实验里。"""
    step, ws = core.build_step("002_x", {"id": "002", "chapter": "| 逐个拿掉模块"}, "")
    assert step.chapter == "" and step.chapter_note == "逐个拿掉模块"
    (w,) = ws
    assert w["code"] == "bad_chapter" and w["level"] == "warn"
    assert w["vars"]["note"] == "逐个拿掉模块", "人写的那半句话原样留着，不许悄悄抹掉"


def test_an_empty_chapter_line_is_simply_not_a_declaration():
    step, ws = core.build_step("002_x", {"id": "002", "chapter": "   "}, "")
    assert step.chapter == "" and ws == []


# ------------------------------------------------------ ③ 沿树继承


def test_one_declaration_carries_the_whole_subtree(tmp_path: Path):
    """开启消融那一步声明一次，整条子树都属于它——不用给 20 步各写一遍。
    各写一遍就是 20 份会漂移的拷贝，移动一步之后它们集体过期且没人发现。"""
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\nchapter: 消融实验 | 逐个拿掉模块\n" + GOOD)
    mk(tmp_path, "003", "parent: 002\n" + GOOD)
    steps = mk(tmp_path, "004", "parent: 003\n" + GOOD)
    f = core.compile_forest(steps)
    assert {s["id"]: s["chapter"]["name"] for s in f["steps"]} == \
        {"001": "", "002": "消融实验", "003": "消融实验", "004": "消融实验"}
    assert [s["id"] for s in f["steps"] if s["chapter"]["declared"]] == ["002"], \
        "只有 002 自己写了那一行，其余三步是继承来的"
    raw = (tmp_path / "steps" / "004_x" / "note.md").read_text(encoding="utf-8")
    assert "chapter" not in raw, "继承出来的归属绝不许落盘"


def test_a_step_can_break_away_by_declaring_its_own(tmp_path: Path):
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "003", "parent: 002\nchapter: 消融实验\n" + GOOD)
    got = {s["id"]: s["chapter"]["name"] for s in core.compile_forest(steps)["steps"]}
    assert got == {"001": "主实验", "002": "主实验", "003": "消融实验"}


def test_no_declaration_all_the_way_to_the_root_is_unassigned_not_an_error(tmp_path: Path):
    """「未分章」是绝大多数项目的状态，不是缺陷：一条诊断都不该为它响。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "002", "parent: 001\nchapter: 消融实验\n" + GOOD)
    f = core.compile_forest(steps)
    assert f["chapters"]["unassigned"] == ["001"]
    assert chapters_of(f) == ["消融实验"], "未分章不是一个章节，不进清单"
    assert codes(f["chapters"]["diagnostics"]) == []


def test_moving_a_step_moves_its_chapter_with_it(tmp_path: Path):
    """把一条线从主实验挪进消融，改的是 parent 一个字。一份落盘的归属会在这里
    过期而没人发现——这正是继承（而不是每步各写一份）的意义。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "010", "chapter: 消融实验\nparent: 001\n" + GOOD)
    steps = mk(tmp_path, "011", "parent: 001\n" + GOOD)
    assert {s["id"]: s["chapter"]["name"] for s in core.compile_forest(steps)["steps"]}["011"] \
        == "主实验"

    # 移动 011 到消融那条线下面，并按只追加的规矩留一条审计。
    mk(tmp_path, "011", "parent: 010\n"
                        "moved: 2026-08-17 | 001 | 010 | 我 | 这一支其实是消融\n" + GOOD)
    f = core.compile_forest(steps)
    assert {s["id"]: s["chapter"]["name"] for s in f["steps"]}["011"] == "消融实验", \
        "章节跟着 parent 走，是继承的正确后果"
    assert [c["steps"] for c in f["chapters"]["chapters"] if c["name"] == "消融实验"] \
        == [["010", "011"]]


def test_inheritance_neither_hangs_nor_gives_up_on_a_cycle():
    """十年后的日志一定是残缺的。环上算不出答案（哪一步的声明也够不到）就当未分章，
    但**绝不死循环、绝不中断**——纯函数会被直接拿去算手工造的 by_id。"""
    got = core.resolve_chapters(by([S("001", parent="002"), S("002", parent="001"),
                                    S("003", parent="001", chapter="消融实验")]))
    assert got == {"003": "消融实验"}, "环上的两步是未分章，003 自己声明的照样算数"


def test_inheritance_stops_at_a_dangling_parent():
    got = core.resolve_chapters(by([S("002", parent="999"), S("003", parent="002")]))
    assert got == {}, "链断了就是未分章，不许去猜"


def test_inheritance_is_memoised_and_does_not_rescan_the_ancestor_chain():
    """1000 步的项目不能每步都重爬一遍祖先链（那是 n²/2 次跳跃）。
    这条钉的是「深链上仍然是线性」，不是某个具体的秒数。"""
    n = 4000
    steps = [S("00001", chapter="主实验")]
    steps += [S(f"{i:05d}", parent=f"{i-1:05d}") for i in range(2, n + 1)]
    m = by(steps)
    t0 = time.perf_counter()
    got = core.resolve_chapters(m)
    elapsed = time.perf_counter() - t0
    assert len(got) == n and got[f"{n:05d}"] == "主实验"
    assert elapsed < 1.0, f"{n} 步的继承用了 {elapsed:.2f}s，八成是每步重爬了一遍祖先链"


def test_a_project_with_no_chapter_costs_nothing_to_resolve():
    """一个 `chapter:` 都没写时连爬都不用爬——「完全无感」不只是输出上的。"""
    assert core.resolve_chapters(by([S("001"), S("002", parent="001")])) == {}


# ------------------------------------------------------ ④ 章节清单


def test_a_chapter_knows_its_steps_roots_size_and_status_mix(tmp_path: Path):
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "010", "parent: 001\nchapter: 消融实验 | 逐个拿掉模块\n" + GOOD)
    mk(tmp_path, "011", "parent: 010\n" + DEAD)
    steps = mk(tmp_path, "012", "parent: 010\nstatus: wip\n" + LOC)
    (c,) = core.compile_forest(steps)["chapters"]["chapters"]
    assert c["name"] == "消融实验" and c["note"] == "逐个拿掉模块"
    assert c["steps"] == ["010", "011", "012"] and c["n"] == 3
    assert c["roots"] == ["010"], "章节的入口 = parent 不在同一章节里的成员"
    assert c["status"] == {"wip": 1, "done": 1, "dead": 1}
    assert c["declared_at"] == ["010"]


def test_a_chapter_can_span_several_trees(tmp_path: Path):
    """消融可能是好几条独立的根。章节是**一组步骤**，不必是一棵子树——
    所以它不能用「子树 = 章节」来实现。"""
    mk(tmp_path, "001", "chapter: 消融实验\n" + GOOD)            # 根 A
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "003", "chapter: 消融实验\n" + GOOD)     # 根 B，同一个章节
    (c,) = core.compile_forest(steps)["chapters"]["chapters"]
    assert c["steps"] == ["001", "002", "003"]
    assert c["roots"] == ["001", "003"], "横跨两棵树就有两个入口"


def test_chapters_are_listed_in_the_order_they_were_opened(tmp_path: Path):
    """顺序按「章节最早那一步的 id」——id 是分配顺序，所以这就是章节被开启的先后，
    和步骤列表对得上。这条同时钉着「不按名字排」：那个顺序对读的人不对应任何东西，
    而且下一个人一旦把它「修好」成按语言的排序规则，同一份文件在两台机器上就能
    排出两种顺序（P3 禁止）。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "002", "chapter: 消融实验\n" + GOOD)
    steps = mk(tmp_path, "003", "chapter: Ablation-2\n" + GOOD)
    assert chapters_of(core.compile_forest(steps)) == ["主实验", "消融实验", "Ablation-2"]


def test_the_step_list_of_a_chapter_follows_the_tree_not_the_id(tmp_path: Path):
    """按 order（前序 DFS）排，界面上章节里的步骤和主列表的先后就是一致的。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "005", "parent: 001\n" + GOOD)
    mk(tmp_path, "006", "parent: 005\n" + GOOD)
    steps = mk(tmp_path, "007", "parent: 001\n" + GOOD)
    (c,) = core.compile_forest(steps)["chapters"]["chapters"]
    assert c["steps"] == ["001", "005", "006", "007"] == \
        core.compile_forest(steps)["order"]


def test_a_slashed_name_is_split_for_display_but_stays_one_flat_chapter(tmp_path: Path):
    """名字里可以写 `主实验/数据准备`，显示时按 `/` 分组，但语义上仍然是一层：
    真正的树形章节要么变成第二棵树（和步骤树打架），要么逼出一份父子关系表。"""
    mk(tmp_path, "001", "chapter: 主实验/数据准备\n" + GOOD)
    steps = mk(tmp_path, "002", "chapter: 主实验/训练\n" + GOOD)
    cs = core.compile_forest(steps)["chapters"]["chapters"]
    assert [c["parts"] for c in cs] == [["主实验", "数据准备"], ["主实验", "训练"]]
    assert [c["steps"] for c in cs] == [["001"], ["002"]], "两个平级的章节，不是父子"


# ------------------------------------------------------ ⑤ 章节说明归谁


def test_the_earliest_declaration_owns_the_description(tmp_path: Path):
    mk(tmp_path, "010", "chapter: 消融实验 | 逐个拿掉模块\n" + GOOD)
    steps = mk(tmp_path, "020", "chapter: 消融实验 | 另一句\n" + GOOD)
    (c,) = core.compile_forest(steps)["chapters"]["chapters"]
    assert c["note"] == "逐个拿掉模块" and c["declared_at"] == ["010", "020"]


def test_two_different_descriptions_for_one_chapter_are_named(tmp_path: Path):
    """多半是笔误，或者两个人各写各的。程序不替人合并——它只说清哪一句生效了、
    另一句在界面上一个字都不会出现。"""
    mk(tmp_path, "010", "chapter: 消融实验 | 逐个拿掉模块\n" + GOOD)
    steps = mk(tmp_path, "020", "chapter: 消融实验 | 换数据集重跑\n" + GOOD)
    d = core.compile_forest(steps)["chapters"]["diagnostics"]
    (x,) = [w for w in d if w["code"] == "chapter_note_conflict"]
    assert x["level"] == "warn"
    assert x["vars"] == {"name": "消融实验", "ids": "010 / 020", "id": "010", "n": "2"}


def test_declaring_the_same_chapter_again_without_a_description_is_not_a_conflict(tmp_path: Path):
    """在第二处开同一个章节时不重复说明是正常写法（说明只该写一遍）。
    为它报一条诊断，等于逼人把同一句话抄两遍——那才是双真相源。"""
    mk(tmp_path, "010", "chapter: 消融实验 | 逐个拿掉模块\n" + GOOD)
    steps = mk(tmp_path, "020", "chapter: 消融实验\n" + GOOD)
    f = core.compile_forest(steps)
    assert f["chapters"]["chapters"][0]["note"] == "逐个拿掉模块"
    assert codes(f["chapters"]["diagnostics"]) == []


def test_the_only_description_survives_even_when_written_later(tmp_path: Path):
    """「最早的**带说明的**声明」而不是「最早的声明」：按后者算会把唯一的那句
    说明扔掉，而那不是任何人的本意。"""
    mk(tmp_path, "010", "chapter: 消融实验\n" + GOOD)
    steps = mk(tmp_path, "020", "chapter: 消融实验 | 逐个拿掉模块\n" + GOOD)
    f = core.compile_forest(steps)
    assert f["chapters"]["chapters"][0]["note"] == "逐个拿掉模块"
    assert codes(f["chapters"]["diagnostics"]) == []


# ------------------------------------------------------ ⑥ 每个章节各有自己的定稿流程


def test_each_chapter_gets_its_own_finalised_flow(tmp_path: Path):
    """`result:` 指的是某一步，那一步的章节就决定了这条流程属于哪个章节。
    论文里主实验一段 Methods、消融一段，本来就是两段。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    mk(tmp_path, "010", "chapter: 消融实验\ninput: 002 | main.csv\n" + GOOD)
    steps = mk(tmp_path, "011", "parent: 010\n" + GOOD)
    project(tmp_path, "result: 002 | 主结果\nresult: 011 | 图 4 的消融\n")
    cs = core.compile_forest(steps)["pipeline"]["chapters"]
    assert [c["name"] for c in cs] == ["主实验", "消融实验"]
    assert cs[0]["results"] == ["002"] and cs[0]["order"] == ["001", "002"]
    assert cs[1]["results"] == ["011"] and cs[1]["order"] == ["001", "002", "010", "011"]


def test_the_upstream_a_chapter_borrows_from_another_is_marked(tmp_path: Path):
    """消融当然要吃主实验的产物，那几步当然要出现在消融的 Methods 里（一个输入
    不在流程里的成员，写进 Methods 就是一句断了的话）。但它们是**借来的**，
    导出时该标出来——那正是「消融是对着主结果测的」在图上的样子。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\ninput: 002 | main.csv\n" + GOOD)
    project(tmp_path, "result: 010 | 消融\n")
    (c,) = core.compile_forest(steps)["pipeline"]["chapters"]
    assert c["order"] == ["001", "002", "010"] and c["external"] == ["001", "002"]


def test_one_dag_is_sliced_not_recomputed_per_chapter(tmp_path: Path):
    """两个章节的闭包必然相交（同一份清洗好的数据集喂了主结果和消融）。
    切分同一张图，一步在两份里的位置就一定一致；各算一遍就会漂移。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\ninput: 001 | raw.csv\n" + GOOD)
    project(tmp_path, "result: 002 | 主结果\nresult: 010 | 消融\n")
    p = core.compile_forest(steps)["pipeline"]
    pos = {sid: i for i, sid in enumerate(p["order"])}
    for c in p["chapters"]:
        assert c["order"] == sorted(c["order"], key=lambda s: pos[s]), \
            "各章节的顺序是全局那张图的子序列"
    assert p["order"] == ["001", "002", "010"]


def test_chapter_flows_follow_the_declaration_order_in_project_md(tmp_path: Path):
    """那是作者自己排的论文段落顺序，比任何一种 id 序都更接近他想要的 Methods。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\n" + GOOD)
    project(tmp_path, "result: 010 | 消融\nresult: 001 | 主结果\n")
    assert [c["name"] for c in core.compile_forest(steps)["pipeline"]["chapters"]] == \
        ["消融实验", "主实验"]


def test_an_unassigned_result_still_gets_its_own_group(tmp_path: Path):
    """主实验没起名字、消融起了名字是很常见的写法。未分章的那条流程照样成组
    （名字是空串），否则它会从按章节导出的清单里整条消失。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\ninput: 001 | main.csv\n" + GOOD)
    project(tmp_path, "result: 001 | 主结果\nresult: 010 | 消融\n")
    cs = core.compile_forest(steps)["pipeline"]["chapters"]
    assert [c["name"] for c in cs] == ["", "消融实验"]


def test_each_chapter_flow_reports_its_own_level_and_weak_steps(tmp_path: Path):
    """「消融这部分别人能不能重做」是单独的一个问题——主实验全 L2 也答不了它。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "010", "chapter: 消融实验\ninput: 001 | main.csv\nstatus: done\n", FULL)
    steps = mk(tmp_path, "011", "parent: 010\n" + GOOD)
    project(tmp_path, "result: 001 | 主结果\nresult: 011 | 消融\n")
    cs = {c["name"]: c for c in core.compile_forest(steps)["pipeline"]["chapters"]}
    assert cs["主实验"]["level"] == "L2" and cs["主实验"]["weak"] == []
    assert cs["消融实验"]["level"] == "L1" and cs["消融实验"]["weakest"] == "010"


def test_a_dead_step_inside_one_chapter_flow_is_named_there(tmp_path: Path):
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "010", "chapter: 消融实验\ninput: 001 | main.csv\npipeline: include | 要它\n" + DEAD)
    steps = mk(tmp_path, "011", "parent: 010\ninput: 010 | x.csv\n" + GOOD)
    project(tmp_path, "result: 011 | 消融\n")
    (c,) = core.compile_forest(steps)["pipeline"]["chapters"]
    assert c["dead"] == ["010"] and c["name"] == "消融实验"


# ------------------------------------------------------ ⑦ 每个章节各有自己的等级


def test_a_chapter_level_is_its_weakest_member_and_names_it(tmp_path: Path):
    """判据复用第 10 节那套（traceability），不另起一条。"""
    mk(tmp_path, "010", "chapter: 消融实验\n" + GOOD)             # L2
    steps = mk(tmp_path, "011", "parent: 010\nstatus: done\n", "")  # L0：正文空着
    (c,) = core.compile_forest(steps)["chapters"]["chapters"]
    assert c["level"] == "L0" and c["weakest"] == "011"
    assert c["level"] == core.traceability(
        core.validate(core.scan(steps)[0])[0]["011"])["level"], "复用同一套判据"


def test_a_weak_step_in_another_chapter_does_not_drag_this_one_down(tmp_path: Path):
    """取的是**成员自己**的等级而不是整链等级：整链会把别的章节的祖先算进来，
    而「消融这部分能不能重做」问的正是消融这几步自己。"""
    mk(tmp_path, "001", "chapter: 主实验\nstatus: done\n", "")     # L0，在别的章节
    steps = mk(tmp_path, "010", "parent: 001\nchapter: 消融实验\n" + GOOD)
    f = core.compile_forest(steps)
    cs = {c["name"]: c for c in f["chapters"]["chapters"]}
    assert cs["消融实验"]["level"] == "L2" and cs["主实验"]["level"] == "L0"
    assert {s["id"]: s["trace"]["chain"] for s in f["steps"]}["010"] == "L0", \
        "开发路径那边照旧：整链等级仍然被 001 压住"


def test_no_chapter_diagnostic_can_change_a_traceability_level(tmp_path: Path):
    """这几条问的是「这个项目分得清不清楚」，不是「这个结果追不追得到」。"""
    mk(tmp_path, "010", "chapter: 消融实验 | 一句\n" + GOOD)
    steps = mk(tmp_path, "020", "chapter: 消融实验 | 另一句\n" + GOOD)
    f = core.compile_forest(steps)
    assert codes(f["chapters"]["diagnostics"]) == ["chapter_note_conflict"]
    assert {s["id"]: s["trace"]["self"] for s in f["steps"]} == {"010": "L2", "020": "L2"}


# ------------------------------------------------------ ⑧ 跨章节的边


def test_a_parent_edge_crossing_chapters_is_where_the_line_split_off(tmp_path: Path):
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    steps = mk(tmp_path, "010", "parent: 001\nchapter: 消融实验\n" + GOOD)
    assert core.compile_forest(steps)["chapters"]["crossings"] == [
        {"from": "001", "to": "010", "kind": "parent",
         "from_chapter": "主实验", "to_chapter": "消融实验", "note": ""}]


def test_an_input_edge_crossing_chapters_says_what_it_is_measured_against(tmp_path: Path):
    """`input: 002` 从消融指回主实验，说的正是「消融是对着主结果测的」——
    值得画出来，不该藏起来。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\ninput: 002 | main_auc.csv\n" + GOOD)
    (e,) = core.compile_forest(steps)["chapters"]["crossings"]
    assert e == {"from": "002", "to": "010", "kind": "input",
                 "from_chapter": "主实验", "to_chapter": "消融实验", "note": "main_auc.csv"}


def test_an_edge_from_an_unassigned_step_into_a_chapter_still_crosses(tmp_path: Path):
    """主实验没起名字是很常见的写法，此时那条边仍然是「消融接在某个东西上」
    ——当成同一章内部的边就等于把唯一的连接抹掉。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "010", "parent: 001\nchapter: 消融实验\n" + GOOD)
    (e,) = core.compile_forest(steps)["chapters"]["crossings"]
    assert e["from_chapter"] == "" and e["to_chapter"] == "消融实验"


def test_edges_inside_one_chapter_and_between_two_unassigned_steps_do_not_cross(tmp_path: Path):
    mk(tmp_path, "001", GOOD)
    mk(tmp_path, "002", "parent: 001\ninput: 001 | a.csv\n" + GOOD)
    mk(tmp_path, "010", "chapter: 消融实验\n" + GOOD)
    steps = mk(tmp_path, "011", "parent: 010\ninput: 010 | b.csv\n" + GOOD)
    assert core.compile_forest(steps)["chapters"]["crossings"] == []


# ------------------------------------------------------ ⑨ 诊断


def test_a_chapter_without_a_result_is_told_how_not_blamed(tmp_path: Path):
    """论文里主实验和消融本来就是两段 Methods。只声明了一段，另一段推不出来。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\n" + GOOD)
    project(tmp_path, "result: 001 | 主结果\n")
    d = core.compile_forest(steps)["chapters"]["diagnostics"]
    (x,) = [w for w in d if w["code"] == "chapter_no_result"]
    assert x["level"] == "info" and x["vars"]["name"] == "消融实验"
    assert "result:" in x["message"] and "project.md" in x["message"]
    for blame in ("应该", "必须写", "错误"):
        assert blame not in x["message"], f"这条不许带责备的语气（出现了「{blame}」）"


def test_a_project_that_declared_no_result_at_all_hears_it_once_not_once_per_chapter(tmp_path: Path):
    """一个 result 都没有时，该说的话 pipeline_no_result 已经说过一遍了。
    在这里按章节再说 N 遍，人学会的是略过整个诊断栏。"""
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验\n" + GOOD)
    project(tmp_path)
    assert codes(core.compile_forest(steps)["chapters"]["diagnostics"]) == []


def test_two_chapter_names_that_differ_only_in_case_or_spacing_are_flagged(tmp_path: Path):
    """两半各自算一份成员、各自导出一段 Methods，而两边看着都像对的。"""
    mk(tmp_path, "001", "chapter: Ablation\n" + GOOD)
    steps = mk(tmp_path, "002", "chapter: ablation \n" + GOOD)
    d = core.compile_forest(steps)["chapters"]["diagnostics"]
    (x,) = [w for w in d if w["code"] == "chapter_near_duplicate"]
    assert x["level"] == "warn" and x["vars"] == {"names": "Ablation / ablation", "n": "2"}
    assert chapters_of(core.compile_forest(steps)) == ["Ablation", "ablation"], \
        "报一声就够了，程序不替人合并——名字一个字符不同就是两个章节"


def test_names_that_merely_look_similar_are_not_guessed_at(tmp_path: Path):
    """`消融实验` vs `消融試驗` 只有靠猜才认得出来，而猜错一次就是对着人指认一个
    不存在的笔误。评级一旦会撒谎人就不再看它，诊断也一样。"""
    mk(tmp_path, "001", "chapter: 消融实验\n" + GOOD)
    steps = mk(tmp_path, "002", "chapter: 消融試驗\n" + GOOD)
    assert codes(core.compile_forest(steps)["chapters"]["diagnostics"]) == []


def test_a_chapter_with_a_single_step_says_nothing(tmp_path: Path):
    """**刻意不做**「只有一个步骤 → 也许是笔误」：一个章节被开启的那一刻必然只有
    一步（就是声明它的那一步），这条会在每一次正确使用时当场炸一声。合法的单步
    章节也确实存在（一步就说完的「数据准备」）。会在正确用法上响的诊断，
    人学会的是忽略整个诊断栏。"""
    mk(tmp_path, "001", GOOD)
    steps = mk(tmp_path, "010", "parent: 001\nchapter: 消融实验 | 刚开个头\n" + GOOD)
    f = core.compile_forest(steps)
    assert f["chapters"]["chapters"][0]["n"] == 1
    assert codes(f["chapters"]["diagnostics"]) == []


def test_a_tidy_two_chapter_project_says_nothing_at_all(tmp_path: Path):
    """没事也念叨两句的诊断，人会连有事那次一起略过。"""
    mk(tmp_path, "001", "chapter: 主实验 | 正式那条\n" + GOOD)
    mk(tmp_path, "002", "parent: 001\n" + GOOD)
    steps = mk(tmp_path, "010", "chapter: 消融实验 | 逐个拿掉模块\ninput: 002 | m.csv\n" + GOOD)
    project(tmp_path, "result: 002 | 主结果\nresult: 010 | 消融\n")
    f = core.compile_forest(steps)
    assert f["chapters"]["diagnostics"] == [] and f["pipeline"]["diagnostics"] == []
    assert f["warnings"] == []


# ------------------------------------------------------ ⑩ 双语与性能


def test_chapter_is_a_structural_key(tmp_path: Path):
    """章节沿树继承，所以译文里多写一行不是「这一步换了个章节」，
    是**整条子树**在英文页面上换了归属——同一个项目按两种分法各导出一份 Methods。"""
    assert "chapter" in core.TR_STRUCT_KEYS


def test_a_translation_cannot_move_a_step_into_another_chapter(tmp_path: Path):
    mk(tmp_path, "001", "chapter: 主实验\n" + GOOD)
    steps = mk(tmp_path, "002", "parent: 001\n" + GOOD)
    (steps / "002_x" / "note.en.md").write_text(
        "---\ntitle: T\nchapter: Ablation\n---\n\n## Why\na\n",
        encoding="utf-8", newline="\n")
    f = core.compile_forest(steps)
    assert chapters_of(f) == ["主实验"], "译文里的 chapter: 一个字节都不许生效"
    assert {s["id"]: s["chapter"]["name"] for s in f["steps"]}["002"] == "主实验"
    hit = [w for w in f["warnings"] if w["code"] == "translation_structural_key"]
    assert hit, "而且要说出来：译文里写了结构键"


def _thousand(d: Path, chapters: bool) -> None:
    """1000 步的一条深链。`chapters=True` 时每 100 步开一个章节，其余 99 步靠继承。"""
    for i in range(1, 1001):
        sd = d / f"{i:03d}_x"
        sd.mkdir(exist_ok=True)
        ch = f"chapter: 章节{i // 100}\n" if (chapters and i % 100 == 1) else ""
        (sd / "note.md").write_text(
            f"---\nid: {i:03d}\nparent: {'' if i == 1 else f'{i-1:03d}'}\nstatus: done\n"
            f"title: t\n{ch}commit: abc\npath: /blue/a | output | 数据\n"
            f"input: {max(1, i - 2):03d} | x.csv\n---\n{FULL}",
            encoding="utf-8", newline="\n")


def test_deriving_chapters_does_not_slow_the_compile_down(tmp_path: Path):
    """继承 + 清单都是线性的，不许把 compile_forest 拖回二次复杂度。

    量的是**同一批文件加不加章节的比值**，不是一个绝对秒数：绝对值在跑满测试的
    Windows 上会被杀毒软件和磁盘拖到三倍，于是这条会在和它想防的东西完全无关的
    地方随机变红——一条会随机红的性能测试，人学会的是重跑一遍而不是看它。
    编译的绝对速度另有一条钉着（test_pipeline 那条）。
    """
    d = tmp_path / "steps"
    d.mkdir()
    _thousand(d, chapters=False)
    project(tmp_path, "result: 1000 | r\n")
    core.compile_forest(d)                          # 预热：量算法，不量冷文件系统
    t0 = time.perf_counter()
    plain = core.compile_forest(d)
    base = time.perf_counter() - t0
    assert "chapters" not in plain

    _thousand(d, chapters=True)
    core.compile_forest(d)
    t0 = time.perf_counter()
    f = core.compile_forest(d)
    elapsed = time.perf_counter() - t0

    assert len(f["chapters"]["chapters"]) == 10
    assert sum(c["n"] for c in f["chapters"]["chapters"]) == 1000
    assert [c["n"] for c in f["chapters"]["chapters"]] == [100] * 10, "99 步全靠继承"
    assert elapsed < base * 1.6 + 0.2, \
        f"加了章节之后编译从 {base:.2f}s 涨到 {elapsed:.2f}s"
