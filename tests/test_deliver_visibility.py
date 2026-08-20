"""投递失败必须留下痕迹。

hook 拉起的投递进程 stdout/stderr 都是 DEVNULL，还额外带 --quiet。所以本机唯一
能回答「上一轮到底怎么了」的地方就是 outbox 根上那个 delivery-status.json。
它不写，`--status` 里「从来没启动过」和「一启动就死」就长得一模一样（都是 null）。
"""
import json
from pathlib import Path

import pytest

from research_trace.deliver import deliver_once, main


def outbox(tmp_path: Path) -> Path:
    target = tmp_path / "outbox"
    (target / "ws" / "session" / "pending").mkdir(parents=True)
    return target


def status_file(tmp_path: Path) -> dict:
    path = tmp_path / "outbox" / "delivery-status.json"
    assert path.is_file(), "投递结束了却没留下 delivery-status.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_crashing_run_still_writes_a_status_file(tmp_path, monkeypatch, capsys):
    outbox(tmp_path)
    monkeypatch.setattr("research_trace.deliver.deliver_once",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("DNS 挂了")))
    code = main(["--data-dir", str(tmp_path), "--url", "https://example.org", "--quiet"])
    assert code == 1
    value = status_file(tmp_path)
    assert value["ok"] is False
    assert "DNS 挂了" in value["last_error"]
    assert value["finished_at"]


def test_status_surfaces_the_failure_instead_of_a_bare_null(tmp_path, monkeypatch, capsys):
    outbox(tmp_path)
    monkeypatch.setattr("research_trace.deliver.deliver_once",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 401")))
    main(["--data-dir", str(tmp_path), "--url", "https://example.org", "--quiet"])
    capsys.readouterr()
    main(["--data-dir", str(tmp_path), "--status"])
    reported = json.loads(capsys.readouterr().out)
    assert reported["last_delivery"] is not None, "排查时最难受的就是这里是 null"
    assert reported["last_delivery"]["ok"] is False
    assert "HTTP 401" in reported["last_delivery"]["last_error"]


def test_a_clean_run_reports_ok(tmp_path):
    outbox(tmp_path)
    report = deliver_once(tmp_path, "https://example.org")
    assert report["ok"] is True


def test_losing_the_lock_is_recorded_too(tmp_path):
    """抢不到锁原本只往 stderr 说一句，而那条路上 stderr 是 DEVNULL。"""
    target = outbox(tmp_path)
    from research_trace.deliver import _DeliverLock
    with _DeliverLock(target) as acquired:
        assert acquired
        report = deliver_once(tmp_path, "https://example.org")
    assert report["skipped"] is True
    assert report["ok"] is False
    assert status_file(tmp_path)["skipped"] is True
