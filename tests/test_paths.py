"""外部产物路径的断言。

GB 级的东西（数据集、checkpoint）不进仓库，只在 note 里记"它在哪"——
所以这些路径本身就是溯源链的一部分，必须能写、能读、能 grep。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_mcp as M
import trace_write as W


# ------------------------------------------------------------ 类型识别


@pytest.mark.parametrize(
    "loc,kind",
    [
        ("/blue/yanjun.li/jinhang.wei/exp/agnews", "hpc"),      # HiperGator
        ("/orange/group/user/ckpt", "hpc"),
        ("/red/group/scratch", "hpc"),
        ("/scratch/local/tmp", "hpc"),
        ("https://github.com/jinhang23/research-trace/tree/9b7d112", "github"),
        ("git@github.com:me/repo.git", "github"),
        ("https://gitlab.com/x/y", "git"),
        ("https://www.dropbox.com/s/abc/data.zip", "dropbox"),
        ("~/Dropbox/实验/原始数据", "dropbox"),
        ("https://drive.google.com/drive/folders/xyz", "drive"),
        ("s3://my-bucket/ckpt/run042.pt", "object"),
        ("gs://bucket/data", "object"),
        ("https://zenodo.org/record/1234567", "archive"),
        ("https://huggingface.co/datasets/ag_news", "mlhub"),
        ("https://example.com/whatever", "url"),
        ("C:/Users/me/data", "local"),
        ("D:\\实验\\raw", "local"),
        ("./relative/thing", "path"),
    ],
)
def test_kind_is_detected_from_the_location(loc, kind):
    assert core.path_kind(loc) == kind


# ------------------------------------------------------------ 解析与回写


def test_repeated_path_lines_are_all_kept():
    """front-matter 里其它键重复是后写覆盖先写，只有 path 累积。"""
    meta, _body, ws = core.parse_note(
        "---\nid: 001\ntitle: x\n"
        "path: /blue/g/u/data | 训练集，12 GB\n"
        "path: https://github.com/a/b/tree/9b7d112 | 代码\n"
        "---\n"
    )
    assert meta["path"].count("\n") == 1
    assert ws == []
    step, _ = core.build_step("001_x", meta, "")
    assert [p["location"] for p in step.paths] == ["/blue/g/u/data", "https://github.com/a/b/tree/9b7d112"]
    assert [p["kind"] for p in step.paths] == ["hpc", "github"]
    assert step.paths[0]["note"] == "训练集，12 GB"


def test_other_repeated_keys_still_last_wins():
    meta, _b, _w = core.parse_note("---\nid: 001\ntitle: 先\ntitle: 后\n---\n")
    assert meta["title"] == "后"


def legacy(p: dict) -> dict:
    """只看老三样。结构化字段是**新增**的，老断言不该因为多了几个键就得改写——
    网页和 CLI 读的也正是这三个键。"""
    return {k: p[k] for k in ("location", "note", "kind")}


def test_path_without_a_note_is_fine():
    assert [legacy(p) for p in core.parse_paths("/blue/g/u/data")] == [
        {"location": "/blue/g/u/data", "note": "", "kind": "hpc"}
    ]


def test_windows_paths_survive_the_pipe_split():
    """位置里可能有冒号和反斜杠，分隔符是 | 所以不受影响。"""
    got = core.parse_paths(r"D:\实验\raw | 外置硬盘上的原始数据")
    assert got[0]["location"] == r"D:\实验\raw" and got[0]["note"] == "外置硬盘上的原始数据"


def test_blank_lines_are_skipped():
    assert [legacy(p) for p in core.parse_paths("\n  \n/blue/a\n\n")] == [
        {"location": "/blue/a", "note": "", "kind": "hpc"}
    ]


def test_paths_round_trip_through_the_note_file(tmp_path: Path):
    d = tmp_path / "steps"
    d.mkdir()
    s, _ = W.create_step(
        d, title="有外部产物的一步",
        paths=["/blue/g/u/agnews-clean | 去重后的训练集，12 GB",
               "https://github.com/a/b/tree/9b7d112 | 跑这一步的代码"],
    )
    text = (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "path: /blue/g/u/agnews-clean | 去重后的训练集，12 GB" in text
    back = W.load(d)[s.id]
    assert [p["location"] for p in back.paths] == [p["location"] for p in s.paths]
    assert back.paths[1]["kind"] == "github"


def test_paths_are_greppable_in_the_raw_file(tmp_path: Path):
    """删掉全部程序之后，grep '^path:' 仍然要能列出所有产物在哪。"""
    d = tmp_path / "steps"
    d.mkdir()
    s, _ = W.create_step(d, title="x", paths=["/blue/g/u/data | 12 GB"])
    lines = (d / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8").splitlines()
    assert [l for l in lines if l.startswith("path:")] == ["path: /blue/g/u/data | 12 GB"]


# ------------------------------------------------------------ 结构化的 path
#
# 需求来历：用户这一次核对 164 条路径时发现三个目录已经被删掉了（其中一个 57 GB），
# 而这本该被自动发现。于是位置行要能带上 role / size / n / 校验和 / 最后一次确认，
# 且**现存的 164 条 `位置 | 说明` 一个字都不用改**。


def one(line: str) -> dict:
    got = core.parse_paths(line)
    assert len(got) == 1
    return got[0]


def test_an_existing_location_and_note_line_parses_exactly_as_before():
    """向后兼容是硬要求：现存记录里的中文说明既不是 role 也不是纯 k=v，
    它必须原样落进 note，一个字不多一个字不少。"""
    p = one("/blue/g/u/data | 去重后的训练集，12 GB")
    assert legacy(p) == {"location": "/blue/g/u/data", "note": "去重后的训练集，12 GB",
                         "kind": "hpc"}
    assert p["role"] == "" and p["attrs"] == {} and p["size"] is None and p["state"] == ""


def test_a_segment_that_is_exactly_a_role_word_becomes_the_role():
    p = one("/orange/lab/pockets | output | 纯 RNA 口袋 | n=4554 size=620756992 md5=7d4e1a9c")
    assert p["role"] == "output"
    assert p["note"] == "纯 RNA 口袋", "role 和属性都不许混进说明"
    assert p["n"] == 4554
    assert p["size"] == 620756992
    assert p["checksum"] == "md5:7d4e1a9c"


def test_a_description_that_merely_contains_an_equals_sign_stays_a_description():
    """判据是「**全部** token 都形如 k=v」，刻意选的：顺手在说明里写个等号
    （`lr=3e-4 的那次运行`）不该被当成机器字段吃掉。"""
    p = one("/blue/a | lr=3e-4 的那次运行")
    assert p["note"] == "lr=3e-4 的那次运行" and p["attrs"] == {}


def test_a_chinese_key_with_an_equals_sign_is_not_a_machine_field():
    p = one("/blue/a | 位置=这里")
    assert p["note"] == "位置=这里" and p["attrs"] == {}


def test_unknown_attributes_are_kept_not_eaten():
    """半年后有人写了 `nodes=…`，系统不该把它吃掉——认不认得出来是程序的事，
    写下来的东西是人的事。"""
    p = one("/blue/a | nodes=48 walltime=36h")
    assert p["attrs"] == {"nodes": "48", "walltime": "36h"}
    assert p["note"] == ""


def test_size_is_stored_in_bytes_and_only_formatted_for_display():
    p = one("/blue/a | size=620756992")
    assert p["size"] == 620756992, "存的必须是字节数，格式化是显示层的事"
    assert core.fmt_size(p["size"]) == "592 MB"
    assert core.fmt_size(one("/blue/a | size=abc")["attrs"]["size"]) == "", "非整数不当数字用"
    assert one("/blue/a | size=abc")["size"] is None
    assert one("/blue/a | size=abc")["attrs"] == {"size": "abc"}, "看不懂也要留着"


def test_a_missing_path_is_a_stored_fact_and_the_later_date_wins():
    """`checked=` / `missing=` 是**存储的事实**（某人某天真去看过一眼），
    「现在还在不在」才是派生的。"""
    assert one("/blue/a | checked=2026-08-09")["state"] == "present"
    assert one("/blue/cif | missing=2026-08-09")["state"] == "missing"
    assert one("/blue/a | checked=2026-08-01 missing=2026-08-09")["state"] == "missing"
    assert one("/blue/a | checked=2026-08-09 missing=2026-08-01")["state"] == "present"
    assert one("/blue/a | checked=2026-08-09 missing=2026-08-09")["state"] == "missing", \
        "同一天判 missing：漏报一次丢失（57 GB 那个目录）比多报一次警报贵得多"


def test_pipes_inside_a_description_are_rejoined_verbatim():
    """说明里本来就允许有竖线（老格式是 partition 一刀切，右边整段都是说明）。"""
    assert one("/blue/a | 说明一 | 说明二")["note"] == "说明一 | 说明二"


def test_format_path_round_trips_so_an_unrelated_edit_cannot_drop_the_attributes():
    """**这条钉的是数据丢失**：写入侧回写 front-matter 时如果只拼 `位置 | 说明`，
    用户在网页上改一下标题，刚核对完的 164 条校验和就没了。"""
    line = "/orange/lab/pockets | output | 纯 RNA 口袋 | n=4554 size=620756992 md5=7d4e1a9c"
    p = one(line)
    again = core.format_path(p)
    assert core.parse_paths(again) == [p], "解析 → 还原 → 再解析必须是同一个东西"
    for token in ("output", "纯 RNA 口袋", "n=4554", "size=620756992", "md5=7d4e1a9c"):
        assert token in again
    plain = "/blue/g/u/data | 去重后的训练集，12 GB"
    assert core.format_path(one(plain)) == plain, "老格式还原之后要逐字不变"


def test_structured_paths_are_still_one_grep_able_line(tmp_path: Path):
    """G4：删掉全部程序之后，`grep -rn '^path:'` 仍然要能列出所有产物在哪、还在不在。"""
    d = tmp_path / "steps" / "001_x"
    d.mkdir(parents=True)
    (d / "note.md").write_text(
        "---\nid: 001\ntitle: t\n"
        "path: /blue/lab/cif_files | input | 原始 CIF | size=61203283968 missing=2026-08-09\n"
        "---\n", encoding="utf-8")
    s = core.compile_forest(tmp_path / "steps")["steps"][0]
    assert s["paths"][0]["state"] == "missing"
    raw = (d / "note.md").read_text(encoding="utf-8")
    assert "missing=2026-08-09" in [l for l in raw.splitlines() if l.startswith("path:")][0]


# ------------------------------------------------------------ 写入接口


def test_norm_paths_accepts_strings_dicts_and_a_bare_value():
    assert W.norm_paths("/blue/a | 说明")[0]["note"] == "说明"
    assert W.norm_paths({"location": "/blue/a", "note": "说明"})[0]["location"] == "/blue/a"
    assert W.norm_paths(["/blue/a", {"location": "s3://b"}])[1]["kind"] == "object"
    assert W.norm_paths(None) == [] and W.norm_paths([""]) == []


def test_kind_cannot_be_forged_from_outside():
    """kind 是从位置推出来的派生字段，外面传什么都不算数。"""
    assert W.norm_paths([{"location": "/blue/a", "kind": "github"}])[0]["kind"] == "hpc"


def test_update_replaces_or_appends(tmp_path: Path):
    d = tmp_path / "steps"
    d.mkdir()
    s, _ = W.create_step(d, title="x", paths=["/blue/a | 一"])
    W.update_step(d, s.id, {"add_paths": ["s3://b | 二", "/blue/a | 重复的，不该再加一条"]})
    got = W.load(d)[s.id].paths
    assert [p["location"] for p in got] == ["/blue/a", "s3://b"], "追加要按位置去重"
    assert got[0]["note"] == "一", "已有条目不被追加覆盖"

    W.update_step(d, s.id, {"paths": ["https://zenodo.org/record/1 | 只剩这条"]})
    assert [p["location"] for p in W.load(d)[s.id].paths] == ["https://zenodo.org/record/1"]


def test_paths_can_be_cleared(tmp_path: Path):
    d = tmp_path / "steps"
    d.mkdir()
    s, _ = W.create_step(d, title="x", paths=["/blue/a"])
    W.update_step(d, s.id, {"paths": []})
    assert W.load(d)[s.id].paths == []


# ------------------------------------------------------------ MCP


@pytest.fixture()
def be(tmp_path: Path):
    backend = M.LocalBackend(tmp_path)
    W.create_project(tmp_path, "alpha")
    return backend


def test_mcp_can_write_and_read_paths(be):
    M.dispatch(be, "trace_new_step", {
        "project": "alpha", "title": "跑第一版",
        "body": "## 为什么\n先建立基线。",
        "paths": ["/blue/g/u/agnews | 训练集，12 GB",
                  "https://github.com/a/b/tree/9b7d112 | 代码"],
    })
    out = M.dispatch(be, "trace_read", {"project": "alpha", "step": "001"})
    assert "外部产物" in out
    assert "[超算] /blue/g/u/agnews  — 训练集，12 GB" in out
    assert "[GitHub] https://github.com/a/b/tree/9b7d112" in out


def test_mcp_tree_shows_a_path_count(be):
    M.dispatch(be, "trace_new_step", {"project": "alpha", "title": "x", "paths": ["/blue/a", "s3://b"]})
    assert "2 路径" in M.dispatch(be, "trace_read", {"project": "alpha"})


def test_mcp_add_paths_appends(be):
    M.dispatch(be, "trace_new_step", {"project": "alpha", "title": "x", "paths": ["/blue/a"]})
    M.dispatch(be, "trace_update_step", {"project": "alpha", "step": "001", "add_paths": ["s3://b | ckpt"]})
    locs = [p["location"] for p in be.step("alpha", "001")["paths"]]
    assert locs == ["/blue/a", "s3://b"]


def test_paths_survive_the_http_json_shape(be):
    """服务端返回的 dict 里 paths 必须是结构化的，不是一坨字符串。

    结构化那一轮把 role / attrs / size / checksum / checked / missing / state 加了进来。
    断言写成「老三样一个都不许少」而不是「恰好这几个键」：新增字段是加成，
    改名或删键才是破坏——网页和 CLI 读的就是 location / note / kind。
    """
    M.dispatch(be, "trace_new_step", {"project": "alpha", "title": "x", "paths": ["/blue/a | 说明"]})
    p = be.step("alpha", "001")["paths"][0]
    assert {"location", "note", "kind"} <= set(p)
    assert p["note"] == "说明" and p["kind"] == "hpc"


# ------------------------------------------------------------ 可溯源性评级
# 派生字段：从记了什么算出来，绝不存储。


def S(**kw):
    d = dict(id="001", status="done", title="t", body="", commit="", paths=[], repro=[])
    d.update(kw)
    return core.Step(**d)


FULL = "## 为什么\n因为要验证 X。\n\n## 做了什么\n跑了 train.py\n\n## 结论\n成立。"


def test_L0_when_the_why_is_missing():
    assert core.traceability(S(body="## 做了什么\n跑了脚本\n\n## 结论\nok"))["level"] == "L0"


def test_L0_when_the_body_is_still_the_template():
    """模板里的占位括号不算写了。"""
    tpl = "## 为什么\n（承接上一步的什么发现，想验证什么假设）\n\n## 做了什么\n\n## 结论\n"
    assert core.traceability(S(body=tpl))["level"] == "L0"


def test_L0_when_a_figure_has_no_caption():
    body = FULL + "\n\n![](loss.png)\n"
    t = core.traceability(S(body=body, commit="abc", paths=[{"location": "/blue/a", "note": "", "kind": "hpc"}]))
    assert t["level"] == "L0"
    assert any("图注" in m for m in t["missing"])


def test_wip_without_a_conclusion_is_not_penalised():
    """在做的步骤本来就还没有结论。"""
    body = "## 为什么\n想试 X。\n\n## 做了什么\n跑着"
    assert core.traceability(S(status="wip", body=body))["level"] == "L1"
    assert core.traceability(S(status="done", body=body))["level"] == "L0"


def test_L1_then_L2_as_commit_and_paths_are_added():
    assert core.traceability(S(body=FULL))["level"] == "L1"
    assert core.traceability(S(body=FULL, commit="abc"))["level"] == "L1"
    t = core.traceability(S(body=FULL, commit="abc",
                            paths=[{"location": "/blue/a", "note": "", "kind": "hpc"}]))
    assert t["level"] == "L2" and t["missing"] == []


def _l2(**kw):
    return S(body=FULL, commit="abc", paths=[{"location": "/blue/a", "note": "", "kind": "hpc"}], **kw)


def test_repro_records_lift_to_L3_and_L4():
    assert core.traceability(_l2(repro=[{"state": "runnable", "date": "", "by": "", "note": ""}]))["level"] == "L3"
    assert core.traceability(_l2(repro=[{"state": "verified", "date": "", "by": "", "note": ""}]))["level"] == "L4"


def test_a_failed_repro_does_not_lower_the_level_but_is_surfaced():
    """"试过，跑不起来"不改变记录本身的完整度，但它是最该被看见的一条。"""
    t = core.traceability(_l2(repro=[{"state": "failed", "date": "2026-07-02", "by": "human",
                                      "note": "checkpoint 被清了"}]))
    assert t["level"] == "L2"
    assert t["repro"]["state"] == "failed" and "checkpoint" in t["repro"]["note"]


def test_runnable_cannot_skip_L2():
    """自己都没记 commit / 路径，声称"可重跑"是不成立的。"""
    assert core.traceability(S(body=FULL, repro=[{"state": "runnable", "date": "", "by": "", "note": ""}]))["level"] == "L1"


def test_the_chain_is_capped_by_its_weakest_ancestor(tmp_path: Path):
    """001 没记数据在哪，004 就算自己写得再全，整条链也追不到底。"""
    d = tmp_path / "steps"
    d.mkdir()
    a, _ = W.create_step(d, title="根", body=FULL, commit="aaa")          # L1：没有 path
    b, _ = W.create_step(d, parent=a.id, title="子", body=FULL, commit="bbb",
                         paths=["/blue/x | 数据"])                        # 自己是 L2
    by_id = W.load(d)
    t = core.chain_traceability(by_id, b.id)
    assert t["self"] == "L2"
    assert t["chain"] == "L1" and t["weakest"] == a.id
    assert [x["level"] for x in t["lineage"]] == ["L1", "L2"]


def test_repro_lines_round_trip_and_only_append(tmp_path: Path):
    d = tmp_path / "steps"
    d.mkdir()
    s, _ = W.create_step(d, title="x", body=FULL)
    W.update_step(d, s.id, {"add_repro": ["failed | 2026-07-02 | human | checkpoint 被清了"]})
    W.update_step(d, s.id, {"add_repro": ["verified | 2026-08-08 | agent:claude | 重跑一致"]})
    back = W.load(d)[s.id]
    assert [r["state"] for r in back.repro] == ["failed", "verified"], "去年失败不该被今年成功抹掉"
    assert back.repro[0]["note"] == "checkpoint 被清了"
    text = (d / back.dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert text.count("\nrepro: ") == 2


def test_an_unknown_repro_state_is_refused(tmp_path: Path):
    d = tmp_path / "steps"
    d.mkdir()
    s, _ = W.create_step(d, title="x")
    with pytest.raises(W.WriteError, match="runnable"):
        W.update_step(d, s.id, {"add_repro": ["成功了 | 2026-08-08 | 我 | 说明"]})
