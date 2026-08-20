"""不配备份就不让启动。

这不是洁癖：默认什么都不配就能跑起来，等于让每个部署都默默停在
「唯一副本在一块盘上」这个状态，而这件事通常要到盘坏了才被发现。
所以把它变成一个**必须做出的选择** —— 配一个仓库，或者明确说不要。

强制发生在 CLI 那一层，不在 create_app 里：库的调用方和测试不该被这条规矩绑住。
"""
import pytest

pytest.importorskip("fastapi")

from research_trace.server import create_app, main


def test_no_backup_destination_refuses_to_start(tmp_path, capsys):
    code = main(["--data-dir", str(tmp_path)])
    assert code == 2
    message = capsys.readouterr().err
    assert "refusing to start without a backup destination" in message
    # 报错必须把两条出路都给出来，否则人只会去搜怎么关掉它
    assert "--backup-repo" in message
    assert "--no-backup" in message
    assert "PRIVATE" in message, "备份里有原始 transcript，这一点不能只写在文档里"


def test_the_environment_variable_counts_as_configured(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRACE_BACKUP_REPO", str(tmp_path / "repo"))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path)]) == 0


def test_opting_out_is_allowed_but_loud(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path), "--no-backup"]) == 0
    message = capsys.readouterr().err
    assert "ONLY copy" in message


def test_trace_no_backup_env_also_opts_out(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_NO_BACKUP", "true")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path)]) == 0


def test_create_app_itself_stays_unconstrained(tmp_path):
    """强制只在运维那一层。库的调用方（含这套测试自己）不该被绑住。"""
    assert create_app(tmp_path, token="secret") is not None
