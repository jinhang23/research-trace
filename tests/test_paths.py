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


def test_path_without_a_note_is_fine():
    assert core.parse_paths("/blue/g/u/data") == [
        {"location": "/blue/g/u/data", "note": "", "kind": "hpc"}
    ]


def test_windows_paths_survive_the_pipe_split():
    """位置里可能有冒号和反斜杠，分隔符是 | 所以不受影响。"""
    got = core.parse_paths(r"D:\实验\raw | 外置硬盘上的原始数据")
    assert got[0]["location"] == r"D:\实验\raw" and got[0]["note"] == "外置硬盘上的原始数据"


def test_blank_lines_are_skipped():
    assert core.parse_paths("\n  \n/blue/a\n\n") == [
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
    """服务端返回的 dict 里 paths 必须是结构化的，不是一坨字符串。"""
    M.dispatch(be, "trace_new_step", {"project": "alpha", "title": "x", "paths": ["/blue/a | 说明"]})
    p = be.step("alpha", "001")["paths"][0]
    assert set(p) == {"location", "note", "kind"}
