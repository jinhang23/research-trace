"""Lifecycle-only intervals do not spend an independent Recorder turn."""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_trace_hook import H, PROTOCOL, bind, event, session_root


def batches(data: Path) -> list[Path]:
    """所有开过的批次，含已归档的：Recorder 收工时会把 manifest 挪进 batches/done/，
    只数还开着的那些会把「派过一次」误读成「没派过」。"""
    root = session_root(data) / "batches"
    return sorted(root.glob("*.json")) + sorted((root / "done").glob("*.json"))


def test_a_turn_with_only_lifecycle_events_does_not_create_another_batch(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"

    H.handle(event("UserPromptSubmit", cwd, prompt="做点事"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    assert len(batches(data)) == 1
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    assert len(batches(data)) == 1, "不该凭空多出一个必然 0 产出的批次"


def test_a_real_turn_still_dispatches_after_a_skipped_one(tmp_path: Path):
    """跳过不能变成「从此不再记录」。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("UserPromptSubmit", cwd, prompt="第一件事"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None

    H.handle(event("UserPromptSubmit", cwd, prompt="第二件事"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    assert len(batches(data)) == 2


def test_the_skipped_events_are_not_lost_but_ride_the_next_batch(tmp_path: Path):
    """跳过时不推进游标：那些事件留到下一批一起带上，一条都不丢。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)   # 只有生命周期
    assert not batches(data)

    H.handle(event("UserPromptSubmit", cwd, prompt="现在有事了"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    manifest = json.loads(batches(data)[0].read_text(encoding="utf-8"))
    # 先前那个被跳过的 Stop 也在这一批里
    assert manifest["event_count"] == 3, manifest["events"]


def test_an_unreadable_event_counts_as_material(tmp_path: Path):
    """读不出来的事件当作有内容：宁可多派一次，也不要静默漏记。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    broken = session_root(data) / "pending" / "9999999999999999999_claude-broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")

    assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    assert len(batches(data)) == 1, "分不清的东西必须入批，漏记比多一次后台整理更贵"
