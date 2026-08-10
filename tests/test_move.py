"""move_step：parent 可以改，但必须留下审计记录。

这是 P2 在这一版里的重新定义——**只追加的地基是「不丢历史」，不是「不能改结构」**。
所以这个文件里的每一条断言，防的都是「移动之后有东西说不清楚了」：

  * 没写原因就能移动 → 半年后看到一棵和创建顺序对不上的树，没人解释得了；
  * 审计被覆盖       → 移动过两次的节点只剩最后一次，中间那次凭空消失；
  * 能挂到自己的后代下 → 整棵子树从森林上掉下来，页面上直接看不见；
  * id 跟着变         → 笔记里的 [[003b]] 和论文脚注同时失效。

真实来历：一条 `014 → 015 → 016` 挂在「补原子」那一步下面，而补原子的产物从未
进过任何下游计算。没有这个函数的时候，用户是把两个节点的**正文对调**才让网站显示
正确的——于是 013b 的创建日期和它现在装的内容对不上号，而那次修改一条记录都没留下。
"""

import re
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


def moved_lines(d: Path, sid: str) -> list[str]:
    return [l for l in note(d, sid).split("\n") if l.startswith("moved:")]


@pytest.fixture()
def chain(steps: Path) -> Path:
    """001 ─ 002(补原子) ─ 003 ─ 004，外加另一条支 002b。"""
    W.create_step(steps, title="口袋筛选")
    W.create_step(steps, parent="001", title="补原子")
    W.create_step(steps, parent="002", title="配对")
    W.create_step(steps, parent="003", title="打分")
    W.create_step(steps, parent="001", title="另一条支")
    return steps


# ------------------------------------------------------------ 原因必填


def test_moving_without_a_reason_is_refused(chain: Path):
    """原因是这条审计里**唯一无法自动生成**的部分。日期、谁、从哪到哪都能自动填，
    只有「为什么这棵树和创建顺序对不上」必须人写。"""
    for empty in ("", "   ", "\n"):
        with pytest.raises(W.WriteError, match="原因"):
            W.move_step(chain, "003", "001", empty)
    assert W.load(chain)["003"].parent == "002", "被拒绝时一个字节都没写"
    assert moved_lines(chain, "003") == []


def test_the_reason_lands_in_the_file_and_is_greppable(chain: Path):
    """G4：删掉全部程序之后，`grep -rn '^moved:'` 还要能回答「这棵树为什么是这样」。"""
    W.move_step(chain, "003", "001", "补原子的产物从未进过下游计算，树形是错的",
                by="human", date="2026-08-09")
    line = moved_lines(chain, "003")[0]
    assert line == "moved: 2026-08-09 | 002 | 001 | human | 补原子的产物从未进过下游计算，树形是错的"
    assert "补原子的产物从未进过下游计算" in note(chain, "003")


def test_a_reason_with_pipes_is_kept_whole(chain: Path):
    """原因是最后一段，里面再有竖线也并进原因（和 repro 同一个规矩）。"""
    W.move_step(chain, "003", "001", "见 [[013]] | 那条线才是真的输入")
    got = core.parse_moved(moved_lines(chain, "003")[0][len("moved: "):])
    assert got[0]["reason"] == "见 [[013]] | 那条线才是真的输入"


# ------------------------------------------------------------ 只追加


def test_a_second_move_appends_and_never_overwrites_the_first(chain: Path):
    """一个节点可以被移动多次，那是**一段历史**，不是一个当前值。
    覆盖掉上一条等于宣称「它一直挂在这儿」——正是只追加要防的事。"""
    W.move_step(chain, "003", "001", "第一次：挂错了父节点", date="2026-08-09")
    W.move_step(chain, "003", "002b", "第二次：其实来自另一条支", date="2026-08-10")
    lines = moved_lines(chain, "003")
    assert len(lines) == 2
    assert lines[0].endswith("第一次：挂错了父节点") and " | 002 | 001 | " in lines[0]
    assert lines[1].endswith("第二次：其实来自另一条支") and " | 001 | 002b | " in lines[1]
    assert W.load(chain)["003"].parent == "002b"


def test_an_ordinary_edit_afterwards_keeps_the_audit(chain: Path):
    """render_note 是**全量重写** front-matter 的。审计只要有一次被漏掉，
    以后任何一次改状态都会把它删掉——而删掉的东西没人会发现。"""
    W.move_step(chain, "003", "001", "挂错了")
    W.update_step(chain, "003", {"status": "done", "title": "换个标题"})
    W.update_step(chain, "003", {"body": "## 结论\n成立。"})
    assert len(moved_lines(chain, "003")) == 1
    assert W.load(chain)["003"].moved[0]["reason"] == "挂错了"


def test_the_id_and_the_directory_never_change(chain: Path):
    """移动改的是父子关系，不是身份。目录名一改，已经发出去的相对链接就失效了。"""
    before = W.load(chain)["003"].dirname
    W.move_step(chain, "003", "001", "挂错了")
    after = W.load(chain)["003"]
    assert after.id == "003" and after.dirname == before
    assert (chain / before / core.NOTE_NAME).is_file()


def test_promoting_to_root_is_recorded_too(chain: Path):
    """提为根也是一次移动。不记的话，「它本来挂在 002 下面」就再也问不出来了。"""
    out = W.move_step(chain, "003", None, "这一支其实是独立的另一个问题")
    assert out["new_parent"] is None and out["old_parent"] == "002"
    assert W.load(chain)["003"].parent is None
    assert "parent:" not in note(chain, "003").split("---")[1]
    got = core.parse_moved(moved_lines(chain, "003")[0][len("moved: "):])[0]
    assert (got["from"], got["to"]) == ("002", "")


@pytest.mark.parametrize("root_word", ["", "-", "none", "NULL"])
def test_the_several_ways_to_say_root_all_mean_root(chain: Path, root_word: str):
    """`parent:` 那一侧认 空/none/-/null 四种写法，move 这一侧不能只认 None，
    否则 REST 传上来的 `{"parent": "-"}` 会去找一个叫 `-` 的步骤。"""
    W.move_step(chain, "003", root_word, "提为根")
    assert W.load(chain)["003"].parent is None


# ------------------------------------------------------------ 校验


def test_moving_to_the_same_parent_is_refused(chain: Path):
    """空操作不该在历史里留下一条什么都没改的审计——那种行会让整段历史贬值。"""
    with pytest.raises(W.WriteError, match="已经是"):
        W.move_step(chain, "003", "002", "再挂一次")
    assert moved_lines(chain, "003") == []


def test_promoting_a_root_to_root_is_refused_too(chain: Path):
    with pytest.raises(W.WriteError, match="已经是"):
        W.move_step(chain, "001", None, "提为根")


def test_a_step_cannot_become_its_own_parent(chain: Path):
    with pytest.raises(W.WriteError):
        W.move_step(chain, "003", "003", "挂到自己身上")


def test_a_step_cannot_be_moved_under_its_own_descendant(chain: Path):
    """004 是 003 的后代。挂过去会成环，整棵子树从森林上掉下来——
    页面不会崩（validate 会断开一条边），但那一支在图上直接消失。"""
    with pytest.raises(W.Conflict, match="成环"):
        W.move_step(chain, "003", "004", "挂到自己的孩子下面")
    assert W.load(chain)["003"].parent == "002"
    assert moved_lines(chain, "003") == []


def test_a_deeper_descendant_is_caught_too(steps: Path):
    """不能只查直接子节点：001 → 002 → 003 → 004，把 001 挂到 004 下面同样是环。"""
    W.create_step(steps, title="根")
    for parent, title in [("001", "二"), ("002", "三"), ("003", "四")]:
        W.create_step(steps, parent=parent, title=title)
    with pytest.raises(W.Conflict, match="成环"):
        W.move_step(steps, "001", "004", "挂到孙子的孩子下面")


def test_an_unknown_target_is_a_404(chain: Path):
    """父子关系只在同一个项目内成立。别的项目里的 007 在这里就是「不存在」。"""
    with pytest.raises(W.NotFound, match="007"):
        W.move_step(chain, "003", "007", "挂到另一个项目的那一步下面")


def test_moving_an_unknown_step_is_a_404(chain: Path):
    with pytest.raises(W.NotFound):
        W.move_step(chain, "099", "001", "x")


def test_a_bad_date_is_refused(chain: Path):
    """日期是机器字段。写歪了，「这棵树什么时候变成这样的」就排不了序。"""
    with pytest.raises(W.WriteError, match="YYYY-MM-DD"):
        W.move_step(chain, "003", "001", "挂错了", date="去年夏天")


def test_a_duplicate_id_label_cannot_be_moved(steps: Path):
    """`001~dup2` 是 validate 贴的**临时显示标签**，纯派生。
    照着它写回 note.md 会把派生信息变成存储信息（违反 P1）。"""
    import shutil

    s, _ = W.create_step(steps, title="原始")
    dup = steps / "001_从备份恢复的"
    dup.mkdir()
    shutil.copyfile(steps / s.dirname / core.NOTE_NAME, dup / core.NOTE_NAME)
    W.create_step(steps, title="另一步")
    with pytest.raises(W.Conflict, match="临时标签"):
        W.move_step(steps, "001~dup2", "002", "挪走重复的那个")


# ------------------------------------------------------------ 并发


def test_expect_guards_the_move_the_same_way_it_guards_an_edit(chain: Path):
    """移动和改正文一视同仁：拿着过期的快照就别动这一步。
    移动的代价是整棵子树，比覆盖一段正文更贵。"""
    stale = W.load(chain)["003"].digest
    W.update_step(chain, "003", {"status": "done"})       # 别人先改了一手
    with pytest.raises(W.Conflict, match="被改过"):
        W.move_step(chain, "003", "001", "挂错了", expect=stale)
    assert W.load(chain)["003"].parent == "002"
    assert moved_lines(chain, "003") == []

    fresh = W.load(chain)["003"].digest
    out = W.move_step(chain, "003", "001", "挂错了", expect=fresh)
    assert out["digest"] and out["digest"] != fresh


# ------------------------------------------------------------ 残缺数据


def test_a_dangling_parent_is_reported_as_the_old_parent_not_as_root(steps: Path):
    """014 被删了，003 的 parent 悬空。读侧会把它降级为根**用于渲染**——
    但审计里必须写真正的旧值，否则「它本来挂在哪」被这次移动永久抹掉。"""
    W.create_step(steps, title="根")
    s, _ = W.create_step(steps, parent="001", title="孤儿")
    p = steps / s.dirname / core.NOTE_NAME
    p.write_text(p.read_text(encoding="utf-8").replace("parent: 001", "parent: 014"),
                 encoding="utf-8")
    assert W.load(steps)[s.id].parent is None, "前提：读侧确实降级为根"

    out = W.move_step(steps, s.id, "001", "014 早就删了，这一支其实接在 001 后面")
    assert out["old_parent"] == "014"
    assert " | 014 | 001 | " in moved_lines(steps, s.id)[0]


def test_an_unrelated_edit_does_not_silently_erase_a_dangling_parent(steps: Path):
    """同一条理由的另一面：改个状态不该顺手把「它本来挂在 014 上」删掉。
    残缺输入产出部分结果（读侧）和残缺输入不被悄悄改写（写侧）是同一条原则。"""
    W.create_step(steps, title="根")
    s, _ = W.create_step(steps, parent="001", title="孤儿")
    p = steps / s.dirname / core.NOTE_NAME
    p.write_text(p.read_text(encoding="utf-8").replace("parent: 001", "parent: 014"),
                 encoding="utf-8")
    W.update_step(steps, s.id, {"status": "done"})
    assert "parent: 014" in p.read_text(encoding="utf-8")


def test_a_hand_made_cycle_does_not_hang_the_move(steps: Path):
    """两个人分别手改了 note.md，磁盘上真的成了环。写入侧的后代检测必须转得出来
    ——死循环不会报错，它只是让请求永远不返回。"""
    W.create_step(steps, title="甲")
    W.create_step(steps, parent="001", title="乙")
    W.create_step(steps, title="丙")
    a = steps / W.load(steps)["001"].dirname / core.NOTE_NAME
    a.write_text(a.read_text(encoding="utf-8").replace("status:", "parent: 002\nstatus:", 1),
                 encoding="utf-8")
    # 环上的一条边会被 validate 断开，所以这次移动本身是合法的；断言的是它**返回了**。
    out = W.move_step(steps, "002", "003", "从环里拆出来")
    assert out["new_parent"] == "003"


# ------------------------------------------------------------ 子树


def test_the_whole_subtree_comes_along_and_is_reported(chain: Path):
    """移的是一步还是一步加它下面的九步，是两个不同的决定。调用方要能当场说给人听。"""
    out = W.move_step(chain, "003", "001", "整条线挂错了")
    assert out["subtree"] == ["004"]
    assert W.load(chain)["004"].parent == "003", "子节点自己的 parent 不动"
    assert moved_lines(chain, "004") == [], "被顺带带走的节点不写审计——它没有被移动"


# ------------------------------------------------------------ 和 update_step 的分工


def test_update_step_still_refuses_parent_but_points_at_move_step(chain: Path):
    """PATCH 那条路收不到原因，所以它必须继续拒绝——但要说清楚该走哪条路，
    否则调用方只会得到一句「不可修改」，然后回去对调两个节点的正文。"""
    with pytest.raises(W.Conflict) as e:
        W.update_step(chain, "003", {"parent": "001"})
    assert "move_step" in str(e.value) and "原因" in str(e.value)


def test_update_step_still_refuses_id(chain: Path):
    """id 是真的不可改：这一轮放宽的是 parent，不是 id。"""
    with pytest.raises(W.Conflict, match="id"):
        W.update_step(chain, "003", {"id": "999"})


def test_moved_is_not_a_writable_field_on_the_patch_path(chain: Path):
    """审计只能由 move_step 自己写。让调用方直接塞一行 moved:，
    就等于让「这一步被移动过」变成一句可以随手编的话。"""
    with pytest.raises(W.WriteError, match="不支持的字段"):
        W.update_step(chain, "003", {"moved": "2026-08-09 | 002 | 001 | 我 | 编的"})


def test_the_forest_reflects_the_move(chain: Path):
    """写完之后读侧看到的必须是新树——审计写对了但树没动是最坏的一种。"""
    W.move_step(chain, "003", "001", "挂错了")
    f = core.compile_forest(chain)
    got = {s["id"]: s["parent"] for s in f["steps"]}
    assert got["003"] == "001" and got["004"] == "003"
    assert not [w for w in f["warnings"] if w["level"] == "error"]
    assert re.search(r"^moved:", note(chain, "003"), re.M)


# ------------------------------------------------------------ 移动和互斥候选组


@pytest.fixture()
def rivals(steps: Path) -> Path:
    """001 底下两个互斥候选 002 / 002b，外加一个空的落脚点 003。"""
    W.create_step(steps, title="类别不平衡")
    W.create_step(steps, parent="001", title="重采样")
    W.create_step(steps, parent="001", title="focal loss")
    W.create_step(steps, title="另一条线")
    W.mark_alternatives(steps, ["002", "002b"], decision="类别不平衡怎么处理？")
    return steps


def test_a_moved_candidate_keeps_its_branch_line_untouched(rivals: Path):
    """候选组是**派生**的：磁盘上只有孩子自己那一行 `branch:`，父节点上没有清单。

    收益正好在这里兑现——把 002 挪到 003 底下，写入侧一个字都不用改，
    它的含义自动从「001 那个岔路口的候选」变成「003 那个岔路口的候选」。
    要是父节点上存了一份孩子清单，这一步就必须同时改两个文件，漏一个就是双真相源。
    """
    W.move_step(rivals, "002", "003", "它其实是在回答另一个问题")
    assert "branch: alternative" in note(rivals, "002")
    assert W.load(rivals)["002"].branch == "alternative"

    by_id = W.load(rivals)
    at = {g["at"]: g["options"]
          for g in core.compute_branch_groups(by_id, core.build_children(by_id))}
    assert at["003"] == ["002"] and at["001"] == ["002b"], "组的归属跟着 parent 自动变"


def test_a_move_reports_both_forks_it_touched(rivals: Path):
    """理由和 subtree 那条一模一样：这是移动的**直接后果**，事后只能靠重新拉一遍
    森林才看得见，而移动的人这会儿正好在做决定。「011 那组现在只剩 012b 一个」
    是他现在就该听到的一句话。"""
    out = W.move_step(rivals, "002", "003", "它其实是在回答另一个问题")
    assert out["alternatives"]["left"]["options"] == ["002b"]
    assert out["alternatives"]["joined"]["options"] == ["002"]
    assert out["alternatives"]["left"]["decision"] == "类别不平衡怎么处理？"


def test_moving_something_that_is_no_candidate_reports_no_fork(chain: Path):
    """没牵扯到任何岔路口的移动就该明说没有，而不是回一个空壳让调用方去猜。"""
    out = W.move_step(chain, "003", "001", "挂错了")
    assert out["alternatives"] == {"left": None, "joined": None}


def test_a_move_that_leaves_a_lone_candidate_is_never_refused(rivals: Path):
    """只剩一个候选不是错误——完全可能正是本意（「B 不再是候选了，它是独立的
    一条线」），而一组候选做完决定之后本来就只剩一个不是 dead 的。P4：结论不是错误。

    拒绝移动只会逼人先把 `branch:` 那一行删掉再移，留下的历史更差。
    """
    out = W.move_step(rivals, "002", "003", "它其实是在回答另一个问题")
    assert out["new_parent"] == "003"
    assert out["alternatives"]["left"]["state"] == "decided", "core 说这叫「定了」，不是错"
    assert W.load(rivals)["001"].decision == "类别不平衡怎么处理？", "父节点上那句话不动"
