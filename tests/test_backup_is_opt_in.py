"""备份默认关闭，要开得自己说。

早先这条是反的：不配备份就拒绝启动。当时的理由现在依然成立——唯一副本在一块盘上，
而这件事通常要到盘坏了才被发现。但那不足以让它**默认开**：备份是把原始 transcript
推进一个 git remote，那是一件外向的、推上去就不完全在本地掌控之内的事，
必须有人明确要求，不能因为「对你好」就自己跑起来。

所以现在：不配 = 不备份、正常启动、提醒一句；`--backup-repo` = 开；
`--no-backup` 含义从「豁免那道关卡」变成「我知道，别再提醒」。
"""
import pytest

pytest.importorskip("fastapi")

from research_trace.server import create_app, main


def test_it_starts_without_any_backup_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACE_BACKUP_REPO", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path)]) == 0, "什么都不配就该能跑起来"


def test_it_says_once_what_you_are_giving_up(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRACE_BACKUP_REPO", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    main(["--data-dir", str(tmp_path)])
    message = capsys.readouterr().err
    assert "only copy" in message, "得说清没有备份意味着什么，而不是默默跑起来"
    # 说了代价就得给出路，否则人只会去搜怎么关掉这条提示
    assert "--backup-repo" in message
    assert "PRIVATE" in message, "导出里有原始 transcript，这一点不能只写在文档里"
    assert "--no-backup" in message, "得告诉人怎么把这条提示关掉"


def test_nothing_refuses_to_start_any_more(tmp_path, monkeypatch, capsys):
    """这条盯的是那个已经被撤掉的关卡，别再回来。"""
    monkeypatch.delenv("TRACE_BACKUP_REPO", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path)]) != 2
    assert "refusing to start" not in capsys.readouterr().err


def test_no_backup_silences_the_note(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRACE_BACKUP_REPO", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path), "--no-backup"]) == 0
    # stderr 上还会有别的告警（比如 OAuth 未配置），所以只盯备份这一句
    assert "no backup destination" not in capsys.readouterr().err, "明确说了不要备份，就别再提醒"


def test_env_var_also_silences_it(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TRACE_BACKUP_REPO", raising=False)
    monkeypatch.setenv("TRACE_NO_BACKUP", "true")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path)]) == 0
    assert "no backup destination" not in capsys.readouterr().err


def test_configuring_a_repo_turns_it_on_and_stays_quiet(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRACE_BACKUP_REPO", str(tmp_path / "repo"))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    assert main(["--data-dir", str(tmp_path)]) == 0
    assert "only copy" not in capsys.readouterr().err


def test_the_app_reports_backup_as_off_by_default(tmp_path):
    """默认关不能只是「CLI 不传参数」，create_app 自己也得是关的。"""
    app = create_app(tmp_path, token="secret")
    assert app is not None
    assert app.state.backup_status["enabled"] is False
