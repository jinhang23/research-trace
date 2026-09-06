"""`trace-project status` 要能回答「采集还活着吗」。

UF 联调里四次静默故障（插件选项缺失、settings.json 被别的进程整体覆盖、投递节流、日志缓冲）
的共同点：hook 根本没跑时 `claude -p` 照常返回，deliver/recorder 的 --status 全绿。
这里守的是那两个能戳穿它的信号：插件在 settings.json 里还配着吗；最近一次采到事件是什么时候。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from research_trace import deliver as D


def _settings(tmp_path: Path, body: dict) -> Path:
    root = tmp_path / "claude"
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.json").write_text(json.dumps(body), encoding="utf-8")
    return root


def _outbox_with_event(tmp_path: Path, age_seconds: float) -> Path:
    data = tmp_path / "plugin-data"
    session = data / "outbox" / "ws1" / "session-1" / "sent"
    session.mkdir(parents=True)
    path = session / "1-event.json"
    path.write_text("{}", encoding="utf-8")
    stamp = time.time() - age_seconds
    import os

    os.utime(path, (stamp, stamp))
    return data


def test_a_healthy_install_reports_the_last_event_and_no_problems(tmp_path, monkeypatch):
    root = _settings(
        tmp_path,
        {
            "enabledPlugins": {"research-trace@research-trace": True},
            "pluginConfigs": {
                "research-trace@research-trace": {"options": {"python": "/x/python", "url": "https://c"}}
            },
        },
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    data = _outbox_with_event(tmp_path, 120)
    health = D.capture_health([data])
    assert health["problems"] == []
    assert health["plugin"]["python"] == "/x/python"
    assert 100 <= health["outbox"]["last_event_age_seconds"] <= 200


def test_an_overwritten_settings_file_is_called_out(tmp_path, monkeypatch):
    """UF 第 6 轮：另一个进程用旧视图覆盖了 settings.json，三个插件段全没了，三轮一个事件都没采到。"""
    root = _settings(tmp_path, {"model": "opus"})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    health = D.capture_health([_outbox_with_event(tmp_path, 3600)])
    assert health["plugin"]["enabled"] is False
    assert any("plugin not enabled" in p for p in health["problems"])


def test_a_missing_plugin_option_is_called_out(tmp_path, monkeypatch):
    root = _settings(
        tmp_path,
        {
            "enabledPlugins": {"research-trace@research-trace": True},
            "pluginConfigs": {"research-trace@research-trace": {"options": {"python": "/x/python"}}},
        },
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    health = D.capture_health([_outbox_with_event(tmp_path, 10)])
    assert any('option "url" is not set' in p for p in health["problems"])


def test_status_exits_nonzero_when_capture_cannot_be_alive(tmp_path, monkeypatch, capsys):
    root = _settings(tmp_path, {})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    project = tmp_path / "proj"
    project.mkdir()
    D.write_marker(project, project_id="prj_x", project_name="x")
    data = tmp_path / "empty-data"
    assert D.project_main(["status", str(project), "--data-dir", str(data), "--url", "http://127.0.0.1:1"]) == 1
    out = capsys.readouterr().out
    assert "!!! plugin not enabled" in out and "capture: no event captured yet" in out
