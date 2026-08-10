"""数据流、代码位置、移动审计、洞察 id —— 这一轮新加的四组 front-matter 记录。

四条都来自真实用的那条 RNA-蛋白口袋流水线（14 步、164 条外部路径），
每一条都对应当时卡住人的一件具体的事，所以断言尽量照着那个场景写。
"""

from pathlib import Path

import trace_core as core


def codes(ws):
    return sorted(w["code"] for w in ws)


def mk(root: Path, sid: str, fm: str = "", body: str = "") -> Path:
    d = root / "steps" / f"{sid}_x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "note.md").write_text(f"---\nid: {sid}\ntitle: t{sid}\n{fm}---\n{body}",
                               encoding="utf-8", newline="\n")
    return root / "steps"


def S(**kw):
    d = dict(id="001", status="done", title="t", body="", dirname="001_x")
    d.update(kw)
    return core.Step(**d)


# ------------------------------------------------------------ ② inputs


def test_a_step_can_declare_inputs_from_more_than_one_step():
    """森林是单父树，数据流是 DAG：016 的输入同时来自 013 和 014，树上只能挂一个。
    以前只能在正文里写一句「本步的输入其实来自 X」，读的人得自己拼。"""
    meta, _b, ws = core.parse_note(
        "---\nid: 016\nparent: 013b\ntitle: 口袋-配体配对\n"
        "input: 013 | pocket_composition.csv\n"
        "input: 014 | rmscore_pairs.csv\n---\n")
    assert ws == []
    step, _ = core.build_step("016_x", meta, "")
    assert step.inputs == [{"step": "013", "note": "pocket_composition.csv"},
                           {"step": "014", "note": "rmscore_pairs.csv"}]
    assert step.parent == "013b", "parent 仍然表示记录的派生关系，两件事互不干扰"


def test_consumers_are_derived_never_stored(tmp_path: Path):
    """正向的 `input:` 写在消费者身上；「013 的产物被谁用了」是扫出来的。
    存第二份就是双真相源——上一代系统正是这么死的。"""
    mk(tmp_path, "013")
    mk(tmp_path, "014")
    steps = mk(tmp_path, "016", "parent: 013\ninput: 013 | pocket_composition.csv\n"
                                "input: 014 | rmscore_pairs.csv\n")
    by = {s["id"]: s for s in core.compile_forest(steps)["steps"]}
    assert by["013"]["consumers"] == ["016"]
    assert by["014"]["consumers"] == ["016"]
    assert by["016"]["consumers"] == []
    assert "consumers" not in (tmp_path / "steps" / "013_x" / "note.md").read_text(encoding="utf-8")


def test_classifying_an_input_as_a_merge_changes_nothing_about_the_data_flow(tmp_path: Path):
    """汇回（`merges`）是**同一批 input 边的一个子集**，不是新的一张图。

    防的是：既然树上要把汇回单独画出来，顺手就把它从数据流图里挪走 / 双算一遍。
    数据流问的是「这些字节从哪来」，那个答案和这条边在树上长什么样毫无关系——
    consumers、dep_edges、inputs 三处都必须原样。
    """
    mk(tmp_path, "011")
    mk(tmp_path, "012", "parent: 011\n")
    mk(tmp_path, "012b", "parent: 011\n")
    mk(tmp_path, "013", "parent: 012b\n")
    steps = mk(tmp_path, "014", "parent: 012\ninput: 013 | scores.csv\n")
    f = core.compile_forest(steps)
    by = {s["id"]: s for s in f["steps"]}

    assert [m["from"] for m in f["merges"]] == ["013"], "先确认这确实被判成了汇回"
    assert by["013"]["consumers"] == ["014"]
    assert by["014"]["inputs"] == [{"step": "013", "note": "scores.csv"}]
    by_id, _ = core.validate(core.scan(steps)[0])
    assert core.dep_edges(by_id, "014") == ["012", "013"]


def test_a_dangling_or_self_input_is_reported_but_never_stops_the_build(tmp_path: Path):
    """和 dangling_parent 同一个处理方式：说出来，然后继续画图。
    十年后的日志一定是残缺的。"""
    steps = mk(tmp_path, "016", "input: 999 | 早就删掉的那一步\ninput: 016 | 手滑写了自己\n")
    f = core.compile_forest(steps)
    assert codes(f["warnings"]) == ["dangling_input", "self_input"]
    assert [s["id"] for s in f["steps"]] == ["016"], "构建照常"
    assert len(f["steps"][0]["inputs"]) == 2, "那两行文本一个字都不许丢"


def test_a_cycle_in_the_data_flow_is_reported_with_the_nodes_on_it(tmp_path: Path):
    mk(tmp_path, "013", "input: 014 | b.csv\n")
    steps = mk(tmp_path, "014", "input: 013 | a.csv\n")
    f = core.compile_forest(steps)
    cyc = [w for w in f["warnings"] if w["code"] == "input_cycle"]
    assert len(cyc) == 1
    assert "013" in cyc[0]["message"] and "014" in cyc[0]["message"]
    assert len(f["steps"]) == 2, "有环也要出图"
    for s in f["steps"]:
        assert s["trace"]["chain"] in core.LEVELS, "环上的节点照样要算得出等级"


def test_the_weakest_link_follows_the_data_and_not_only_the_tree(tmp_path: Path):
    """**这条是 inputs 参与评级的理由本身。**

    「这一步的输入来自哪一步」正是溯源在问的那件事：016 的口袋组成来自 013，
    013 连产物在哪都没记，那么 016 这个结论就是追不到底的——哪怕它在树上挂着的
    是写得很全的 013b。
    """
    full = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
    good = f"status: done\ncommit: abc\npath: /blue/x | 数据\n"
    mk(tmp_path, "013", "status: done\n", "")                        # L0：正文空着
    mk(tmp_path, "013b", good, full)                                 # L2
    steps = mk(tmp_path, "016", "parent: 013b\ninput: 013 | pocket_composition.csv\n" + good, full)
    t = {s["id"]: s["trace"] for s in core.compile_forest(steps)["steps"]}["016"]
    assert t["self"] == "L2"
    assert t["weakest"] == "013" and t["chain"] == "L0"
    assert t["via"] == "input", "要说清最弱一环是从数据依赖那条边找到的"
    assert [e["id"] for e in t["lineage"]] == ["013b", "016"], \
        "lineage 仍然是记录派生那条路（面包屑）—— DAG 摊不成一条链"


def test_the_batch_and_single_step_versions_agree_once_inputs_join(tmp_path: Path):
    """compute_traces 是 chain_traceability 的批量版本，两者必须逐字段相等。
    加了数据依赖之后递推变成了 DAG 上的 DFS，这条断言是它唯一的保险。"""
    full = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
    mk(tmp_path, "001", "status: done\ncommit: a\npath: /blue/a | d\n", full)
    mk(tmp_path, "002", "status: done\n", full)
    mk(tmp_path, "003", "parent: 001\ninput: 002 | x.csv\nstatus: done\n", full)
    steps = mk(tmp_path, "004", "parent: 003\ninput: 001 | y.csv\nstatus: done\n", full)
    raw, _files, _w = core.scan(steps)
    by_id, _ = core.validate(raw)
    order = core.compute_order(by_id, core.build_children(by_id))
    assert core.compute_traces(by_id, order) == {
        sid: core.chain_traceability(by_id, sid) for sid in order}


def test_inputs_survive_a_cycle_without_hanging(tmp_path: Path):
    """脏边不许让整棵树算不出等级——DFS 踩到正在栈上的节点就跳过它的贡献。"""
    mk(tmp_path, "001", "input: 002 | a\nstatus: done\n")
    steps = mk(tmp_path, "002", "input: 001 | b\nstatus: done\n")
    f = core.compile_forest(steps)
    assert {s["trace"]["chain"] for s in f["steps"]} == {"L0"}


def test_data_dependencies_do_not_bring_back_quadratic_compile(tmp_path: Path):
    """最弱一环沿着 parent ∪ inputs 走，是一张 DAG 上的记忆化 DFS：每条边只走一次。

    写成断言是因为这里最容易退化——「问一个节点就把它的闭包重算一遍」在深链上
    立刻回到 n²（改之前 1000 步是 17 秒）。顺便钉住 P3：两次编译逐字节一致。
    """
    import json
    import time

    body = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
    d = tmp_path / "steps"
    d.mkdir()
    for i in range(1, 801):
        sd = d / f"{i:03d}_x"
        sd.mkdir()
        (sd / "note.md").write_text(
            f"---\nid: {i:03d}\nparent: {'' if i == 1 else f'{i-1:03d}'}\nstatus: done\n"
            f"title: t\ncommit: abc\npath: /blue/a | output | 数据 | size=620756992\n"
            f"input: {max(1, i - 2):03d} | x.csv\n---\n{body}",
            encoding="utf-8", newline="\n")
    core.compile_forest(d)                       # 预热：量算法，不量冷文件系统
    t0 = time.perf_counter()
    f = core.compile_forest(d)
    elapsed = time.perf_counter() - t0
    assert len(f["steps"]) == 800
    assert elapsed < 3.0, f"800 步（带数据依赖）编译用了 {elapsed:.2f}s —— 二次复杂度回来了"
    assert json.dumps(f, ensure_ascii=False) == \
        json.dumps(core.compile_forest(d), ensure_ascii=False)


# ------------------------------------------------------------ ⑤ code


def test_commit_is_still_accepted_and_shows_up_as_a_derived_git_record():
    """`commit: c1d2e3f` 等价于一条 `code: git`，但文件里仍然只有 commit 一份——
    往文件里写第二份就是双真相源。"""
    step = S(commit="c1d2e3f")
    recs = core.code_records(step)
    assert recs == [{"kind": "git", "location": "", "attrs": {"commit": "c1d2e3f"},
                     "note": "", "from": "commit"}]
    assert step.to_dict()["code"] == recs
    assert core.code_located(step)


def test_a_snapshot_with_a_manifest_reaches_L2_just_like_a_commit():
    """用户原话：「代码在这里、逐文件校验和在这里」在可溯源性上不比 commit 差。
    卡着不给 L2 只会让这类记录永远显示成追不到底。"""
    full = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
    code = core.parse_code("snapshot | /orange/lab/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43")
    step = S(body=full, code=code, paths=[{"location": "/blue/a", "note": "", "kind": "hpc"}])
    assert code[0]["kind"] == "snapshot"
    assert code[0]["location"] == "/orange/lab/run_snapshots/20260809"
    assert code[0]["attrs"] == {"manifest": "MANIFEST.md5", "n": "43"}
    t = core.traceability(step)
    assert t["level"] == "L2" and t["missing"] == []


def test_a_snapshot_without_a_location_does_not_count():
    """快照要算数，得说清它在哪——「有个快照」和「快照在这里」是两回事。"""
    full = "## 为什么\nx\n\n## 做了什么\ny\n\n## 结论\nz\n"
    step = S(body=full, code=core.parse_code("snapshot"),
             paths=[{"location": "/blue/a", "note": "", "kind": "hpc"}])
    assert core.traceability(step)["level"] == "L1"
    assert any("commit" in m for m in core.traceability(step)["missing"]), \
        "文案里要留着 commit 这个词：网页按它把中文 missing 认回 i18n 的 key"


def test_a_container_digest_counts_and_an_unknown_kind_is_not_thrown_away():
    assert core.code_located(S(code=core.parse_code("container | ghcr.io/lab/pocket:2026-08 | digest=sha256:ab")))
    assert core.code_located(S(code=core.parse_code("nix | /nix/store/xxx"))), \
        "十年后多一种形态是正常的，认不出不等于它没定位到东西"


def test_code_lines_round_trip_and_the_derived_one_is_marked(tmp_path: Path):
    line = "snapshot | /orange/lab/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43"
    rec = core.parse_code(line)[0]
    assert core.format_code(rec) == line
    assert rec["from"] == "code", "写入侧靠 from 分辨哪些是文件里真有的"
    assert core.code_records(S(commit="abc", code=[rec]))[-1]["from"] == "commit"


def test_a_git_record_that_repeats_the_commit_field_is_not_shown_twice():
    step = S(commit="c1d2e3f", code=core.parse_code("git | https://github.com/a/b | commit=c1d2e3f"))
    assert [r["from"] for r in core.code_records(step)] == ["code"]


# ------------------------------------------------------------ ① moved


def test_moved_records_are_history_and_only_append(tmp_path: Path):
    """P2 的地基是「不丢历史」，不是「不能改结构」——记下来就不丢。
    parent 于是可以改，但必须留下审计；id 仍然不许改（`[[003b]]` 要永远有效）。"""
    steps = mk(tmp_path, "016",
               "parent: 013b\n"
               "moved: 2026-08-09 | 014 | 013b | human | 补原子的产物从未进过下游计算，树形是错的\n"
               "moved: 2026-08-10 | 013b | 013 | agent:claude | 再挪一次\n")
    s = core.compile_forest(steps)["steps"][0]
    assert [m["date"] for m in s["moved"]] == ["2026-08-09", "2026-08-10"], "顺序即历史"
    assert s["moved"][0] == {"date": "2026-08-09", "from": "014", "to": "013b",
                             "by": "human",
                             "reason": "补原子的产物从未进过下游计算，树形是错的"}
    assert core.format_moved(s["moved"][0]) in \
        (tmp_path / "steps" / "016_x" / "note.md").read_text(encoding="utf-8")


def test_a_reason_containing_a_pipe_stays_in_the_reason():
    m = core.parse_moved("2026-08-09 | 014 | 013b | human | a | b")[0]
    assert m["reason"] == "a | b"


# ------------------------------------------------------------ ④ 洞察的 id 与取代


INSIGHTS = ("## 坑\n"
            "- `p3` PDBFixer 误杀 944 个带修饰残基，见 [[013b]] · 取代 p1\n"
            "- `p1` PDBFixer 误杀 1,099 个\n"
            "\n## 有效\n"
            "- 训练集去重之后所有模型都涨 0.4 个点\n")


def test_insight_ids_and_supersedes_are_read_back():
    got = core.parse_insights(INSIGHTS)
    pit = got["pitfall"]
    assert [i["id"] for i in pit] == ["p3", "p1"]
    assert pit[0]["supersedes"] == ["p1"]
    assert pit[0]["text"] == "PDBFixer 误杀 944 个带修饰残基，见 [[013b]]"


def test_being_superseded_is_derived_and_written_nowhere():
    """`· 取代 p1` 只写在**取代者**身上。「p1 已被取代」写第二份就又是双真相源，
    而且一定先漂移——人只会去改自己正在写的那一行。"""
    got = core.parse_insights(INSIGHTS)
    assert got["pitfall"][1]["superseded_by"] == ["p3"]
    assert "被取代" not in INSIGHTS


def test_english_insights_use_the_same_closed_vocabulary():
    got = core.parse_insights("## Pitfalls\n- `p3` PDBFixer kills modified residues · supersedes p1\n"
                              "- `p1` older count\n")
    assert got["pitfall"][0]["supersedes"] == ["p1"]
    assert got["pitfall"][1]["superseded_by"] == ["p3"]


def test_old_insight_lines_without_an_id_keep_working():
    """现存数据一条 id 都没有——它们必须照常读出来，id 为空即可。"""
    got = core.parse_insights("## 无效\n- 回译在这个数据集上一直没用 —— [[004]]\n")
    assert got["fails"][0]["id"] == ""
    assert got["fails"][0]["text"] == "回译在这个数据集上一直没用 —— [[004]]"


def test_finding_and_allocating_insight_ids():
    assert core.find_insight(INSIGHTS, "p1")["kind"] == "pitfall"
    assert core.find_insight(INSIGHTS, "p9") is None
    assert core.next_insight_id(INSIGHTS) == "p4", "取 max+1：被取代的 id 也不许被重用"
    assert core.next_insight_id("") == "p1"


def test_format_insight_is_what_parse_insights_reads_back():
    line = core.format_insight("PDBFixer 误杀 944 个", "p3", ["p1"])
    assert line == "`p3` PDBFixer 误杀 944 个 · 取代 p1"
    got = core.parse_insights("## 坑\n- " + line + "\n")["pitfall"][0]
    assert (got["id"], got["text"], got["supersedes"]) == ("p3", "PDBFixer 误杀 944 个", ["p1"])


def test_insights_stay_one_greppable_line():
    """洞察要能被 `grep` 一行捞出来（G4）。"""
    assert "\n" not in core.format_insight("换\n行\n了", "p2")
