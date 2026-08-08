"""部署形态的断言：代码仓公开、数据仓私有。

这是实际上线要用的形态，而绝大多数开发都发生在 data_dir="." 下，
所以它特别容易在不知不觉中被改坏——尤其是「同步到底打在哪个仓」这一条：
搞反了就等于把科研笔记推到公开的代码仓上，而且不会有任何报错。
"""

import json
from pathlib import Path

import pytest

import trace_cli as cli
import trace_core as core
import trace_server as srv


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch):
    """把 trace_cli 的"代码仓根"指到临时目录，避免动到真的 config.json。"""
    code = tmp_path / "code"
    code.mkdir()
    monkeypatch.setattr(cli, "ROOT", code)
    monkeypatch.setattr(cli, "CONFIG_PATH", code / "config.json")
    monkeypatch.setattr(srv, "ROOT", code)
    monkeypatch.setattr(srv, "CONFIG_PATH", code / "config.json")
    return code


def init(sandbox, **kw):
    args = type("A", (), {"title": "t", "project": "第一个课题", "data_dir": ".",
                          "force": False, "no_git": True, **kw})()
    assert cli.cmd_init(args) == 0
    return json.loads((sandbox / "config.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ init --data-dir


def test_init_creates_the_first_project_in_the_data_repo(sandbox, tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    cfg = init(sandbox, data_dir=str(data))
    assert cfg["data_dir"] == str(data)
    assert [p.name for p in (data / "projects").iterdir()] == ["第一个课题"]
    assert not (sandbox / "projects").exists(), "代码仓里不该出现 projects/"


def test_init_without_data_dir_keeps_everything_together(sandbox):
    init(sandbox)
    assert (sandbox / "projects" / "第一个课题").is_dir()


def test_init_generates_a_token_so_nobody_has_to_invent_one(sandbox):
    cfg = init(sandbox)
    assert len(cfg["token"]) >= 32 and len(cfg["space"]) >= 16
    assert cfg["token"] != init(sandbox, force=True)["token"], "每次都要是新的随机值"


def test_init_refuses_to_clobber_an_existing_config(sandbox):
    init(sandbox)
    args = type("A", (), {"title": "t", "project": "p", "data_dir": ".",
                          "force": False, "no_git": True})()
    assert cli.cmd_init(args) == 1


# ------------------------------------------------------------ 同步打在哪个仓


def test_git_sync_targets_the_data_repo_not_the_code_repo(sandbox, tmp_path: Path, monkeypatch):
    """搞反了就是把科研笔记推到公开的代码仓上，而且悄无声息。"""
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    (sandbox / "config.json").write_text(json.dumps({
        "space": "s", "token": "t", "data_dir": str(data),
        "git": {"enabled": True, "remote": "origin", "branch": "main", "debounce": 45},
    }), encoding="utf-8")
    (sandbox / "web").mkdir(exist_ok=True)

    seen = {}
    real = srv.GitSync

    def spy(root, **kw):
        seen["root"] = Path(root)
        return real(root, **kw)

    monkeypatch.setattr(srv, "GitSync", spy)
    srv.create_app(srv.load_config(sandbox / "config.json"))
    assert seen["root"] == data.resolve(), f"同步目标应当是数据仓，实际是 {seen['root']}"


def test_data_dir_is_resolved_relative_to_the_code_repo(sandbox, tmp_path: Path):
    """deploy 文档里写的是 ../trace-data 这种相对路径。"""
    sibling = sandbox.parent / "trace-data"
    sibling.mkdir()
    cfg = init(sandbox, data_dir="../trace-data")
    assert (sibling / "projects" / "第一个课题").is_dir()
    assert cli.data_root(cfg) == sibling.resolve()


# ------------------------------------------------------------ 隔离


def test_the_code_repo_stays_free_of_step_files(sandbox, tmp_path: Path):
    import trace_write as W

    data = tmp_path / "data"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    W.create_step(core.steps_dir_of(data, "第一个课题"), title="一步")
    assert list(sandbox.rglob("note.md")) == []
    assert len(list(data.rglob("note.md"))) == 1


def test_config_with_a_secret_lives_in_the_code_repo_and_is_ignored(sandbox, tmp_path: Path):
    """令牌在 config.json 里，而 config.json 在 .gitignore 里 —— 数据仓里不该有它。"""
    data = tmp_path / "data"
    data.mkdir()
    init(sandbox, data_dir=str(data))
    assert (sandbox / "config.json").is_file()
    assert not (data / "config.json").exists()
    ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "config.json" in ignored
