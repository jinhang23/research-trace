"""Contract tests for the independent outbox deliverer and project binding."""

from __future__ import annotations

import json
from pathlib import Path

from research_trace import deliver as D


def make_session(data: Path, workspace: str, session: str, events: int = 1, chunks: int = 0) -> Path:
    root = data / "outbox" / workspace / session
    for name in ("pending", "transcripts/pending", "transcripts/meta"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for index in range(events):
        (root / "pending" / f"{index:019d}_claude-{session}-{index}.json").write_text(
            json.dumps({
                "event_id": f"claude-{session}-{index}", "session_id": session,
                "project_dir": "/work/x", "project_id": "prj_1", "agent_id": None,
                "hook_event": "UserPromptSubmit", "payload": {"prompt": "hi"},
            }),
            encoding="utf-8",
        )
    for index in range(chunks):
        name = f"{'a' * 20}_{index:016d}_{index + 1:016d}_{'b' * 16}.jsonl"
        (root / "transcripts" / "pending" / name).write_text('{"message":"x"}\n', encoding="utf-8")
    return root


def recorder(status: int = 200):
    """Fake central. `.calls` holds ingest POSTs only.

    投递器每轮还会 POST 一次 /api/telemetry/outbox（纯遥测，不影响任何文件去向），
    把它混进 calls 会让「第几个 batch」这类断言变得看运气。
    """
    calls: list[dict] = []

    def fake(url, path, value, token, timeout):
        if path != "/api/ingest":
            return 200, {"ok": True}
        calls.append({"url": url, "path": path, "value": value, "token": token})
        return status, {"batch_id": value.get("batch_id")}

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def test_only_a_central_2xx_moves_files_into_sent(tmp_path: Path, monkeypatch):
    data = tmp_path / "plugin-data"
    root = make_session(data, "ws-a", "session-1", events=2, chunks=1)

    refuse = recorder(503)
    monkeypatch.setattr(D, "_post_json", refuse)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert report["delivered_events"] == 0 and report["failed_batches"] == 2
    assert len(list((root / "pending").glob("*.json"))) == 2
    assert not list((root / "sent").glob("*.json"))

    accept = recorder(200)
    monkeypatch.setattr(D, "_post_json", accept)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert report["delivered_events"] == 2 and report["delivered_chunks"] == 1
    assert not list((root / "pending").glob("*.json"))
    assert len(list((root / "sent").glob("*.json"))) == 2
    assert len(list((root / "transcripts" / "sent").glob("*.jsonl"))) == 1
    assert accept.calls[0]["token"] == "t"
    assert accept.calls[0]["path"] == "/api/ingest"
    payload = accept.calls[0]["value"]
    assert payload["project_id"] == "prj_1"
    assert payload["session"]["id"] == "session-1"
    assert len(payload["events"]) == 2


def test_the_whole_outbox_is_scanned_not_just_the_live_session(tmp_path: Path, monkeypatch):
    """被 kill 掉 / 正常退出的 session 残留因此终于有重放路径（§15）。"""
    data = tmp_path / "plugin-data"
    make_session(data, "ws-a", "dead-session-1")
    make_session(data, "ws-a", "dead-session-2")
    make_session(data, "ws-b", "live-session")
    accept = recorder(200)
    monkeypatch.setattr(D, "_post_json", accept)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert report["sessions"] == 3
    assert report["delivered_events"] == 3
    assert not list((data / "outbox").rglob("pending/*.json"))


def test_legacy_awaiting_upload_is_recovered_into_pending(tmp_path: Path, monkeypatch):
    data = tmp_path / "plugin-data"
    root = make_session(data, "ws-a", "session-1", events=0)
    (root / "awaiting_upload").mkdir()
    (root / "awaiting_upload" / "0_claude-old.json").write_text(
        json.dumps({"event_id": "claude-old", "session_id": "session-1"}), encoding="utf-8"
    )
    (root / "transcripts" / "awaiting_upload").mkdir(parents=True)
    name = f"{'c' * 20}_{0:016d}_{16:016d}_{'d' * 16}.jsonl"
    (root / "transcripts" / "awaiting_upload" / name).write_text('{"m":"old"}\n', encoding="utf-8")

    accept = recorder(200)
    monkeypatch.setattr(D, "_post_json", accept)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert report["recovered"] == 2
    assert report["delivered_events"] == 1 and report["delivered_chunks"] == 1
    assert not (root / "awaiting_upload").exists()
    assert (root / "sent" / "0_claude-old.json").is_file()
    chunk = accept.calls[-1]["value"]["transcript_chunks"][0]
    assert chunk["content"] == '{"m":"old"}\n'
    assert chunk["chunk_id"].startswith("claude-transcript-")


def test_an_unreachable_central_keeps_everything_pending(tmp_path: Path, monkeypatch):
    data = tmp_path / "plugin-data"
    root = make_session(data, "ws-a", "session-1", events=2)

    def unreachable(*_a, **_k):
        raise D.DeliveryError("Research Trace unavailable at http://127.0.0.1:8765: [Errno -2] dns")

    monkeypatch.setattr(D, "_post_json", unreachable)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert "unavailable" in report["last_error"]
    assert len(list((root / "pending").glob("*.json"))) == 2
    assert not (root / "sent").exists() or not list((root / "sent").glob("*.json"))
    status = json.loads((data / "outbox" / "delivery-status.json").read_text(encoding="utf-8"))
    assert status["pending_events"] == 2


def test_a_stale_project_id_falls_back_to_unassigned_instead_of_blocking_forever(
    tmp_path: Path, monkeypatch
):
    data = tmp_path / "plugin-data"
    root = make_session(data, "ws-a", "session-1", events=1)
    seen: list[str | None] = []

    def picky(url, path, value, token, timeout):
        if path != "/api/ingest":
            return 200, {"ok": True}
        seen.append(value.get("project_id"))
        if value.get("project_id"):
            return 404, {"error": "no such project"}
        return 200, {"ok": True}

    monkeypatch.setattr(D, "_post_json", picky)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert seen == ["prj_1", None]
    assert report["delivered_events"] == 1
    assert list((root / "sent").glob("*.json"))


def test_a_repeated_delivery_reuses_the_same_batch_id(tmp_path: Path, monkeypatch):
    data = tmp_path / "plugin-data"
    make_session(data, "ws-a", "session-1", events=1)
    refuse = recorder(500)
    monkeypatch.setattr(D, "_post_json", refuse)
    D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert refuse.calls[0]["value"]["batch_id"] == refuse.calls[1]["value"]["batch_id"]


def test_transcript_only_batches_still_carry_the_session_and_project(tmp_path: Path, monkeypatch):
    data = tmp_path / "plugin-data"
    make_session(data, "ws-a", "session-1", events=1, chunks=1)
    accept = recorder(200)
    monkeypatch.setattr(D, "_post_json", accept)
    D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    chunk_payload = [c["value"] for c in accept.calls if c["value"]["transcript_chunks"]][0]
    assert chunk_payload["events"] == []
    assert chunk_payload["project_id"] == "prj_1"
    assert chunk_payload["session"]["id"] == "session-1"


def test_real_http_only_archives_on_a_2xx(tmp_path: Path):
    """不 mock `_post_json`：真的走一遍 urllib，钉住「2xx 才搬」这条本身。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = {"code": 500, "bodies": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            state["bodies"].append(json.loads(self.rfile.read(length)))
            self.send_response(state["code"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        data = tmp_path / "plugin-data"
        root = make_session(data, "ws-a", "session-1", events=1)
        report = D.deliver_once(data, url, token="t", timeout=10)
        assert report["failed_batches"] == 1
        assert list((root / "pending").glob("*.json"))

        state["code"] = 201
        report = D.deliver_once(data, url, token="t", timeout=10)
        assert report["delivered_events"] == 1
        assert not list((root / "pending").glob("*.json"))
        assert list((root / "sent").glob("*.json"))
        assert state["bodies"][0]["batch_id"] == state["bodies"][1]["batch_id"]
    finally:
        server.shutdown()
        server.server_close()


def test_marker_lookup_walks_up_and_honours_exclusion(tmp_path: Path):
    project = tmp_path / "repo"
    (project / "src" / "deep").mkdir(parents=True)
    assert D.project_binding(project / "src" / "deep") is None

    D.write_marker(project, workspace_key="rt-ws-1", project_id="prj_9", project_name="Repo")
    binding = D.project_binding(project / "src" / "deep")
    assert binding["workspace_key"] == "rt-ws-1"
    assert binding["project_id"] == "prj_9"
    assert Path(binding["marker_path"]) == project / D.MARKER_NAME

    D.write_marker(project, capture=False)
    assert D.project_binding(project / "src" / "deep") is None
    # 排除是个开关，不是删除：身份字段仍然留在 marker 里
    assert D.read_marker(project / D.MARKER_NAME)["project_id"] == "prj_9"


def test_write_marker_merges_instead_of_clobbering(tmp_path: Path):
    project = tmp_path / "repo"
    project.mkdir()
    D.write_marker(project, workspace_key="rt-ws-1", workspace_keys=["https://github.com/t/r"])
    D.write_marker(project, project_id="prj_9")
    value = D.read_marker(project / D.MARKER_NAME)
    assert value["workspace_key"] == "rt-ws-1"
    assert value["workspace_keys"] == ["https://github.com/t/r"]
    assert value["project_id"] == "prj_9"
    assert D.project_binding(project)["workspace_keys"] == [
        "rt-ws-1", "https://github.com/t/r"
    ]


def test_a_2xx_that_reports_reused_ids_is_not_a_clean_success(tmp_path: Path, monkeypatch):
    """中央按 id 去重，所以「200」不等于「你发的那份存下来了」。

    复用了 event_id 但内容不同时中央保留旧的、报回冲突；投递器必须把它说出来，
    否则发送端永远不知道自己刚丢了一份内容。
    """
    data = tmp_path / "plugin-data"
    make_session(data, "ws-a", "session-1", events=1)

    def conflicting(url, path, value, token, timeout):
        if path != "/api/ingest":
            return 200, {"ok": True}
        return 200, {"batch_id": value["batch_id"], "conflicting_event_ids": ["claude-session-1-0"]}

    monkeypatch.setattr(D, "_post_json", conflicting)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert report["conflicts"] == 1
    assert "reused id" in report["last_error"]


def test_sent_is_reclaimed_by_age_but_pending_is_never_touched(tmp_path: Path, monkeypatch):
    """§12：`sent/` 是唯一允许回收的目录（中央已确认存下），`pending/` 永不删除。"""
    import os
    import time

    data = tmp_path / "plugin-data"
    root = make_session(data, "ws-a", "session-1", events=2, chunks=1)
    accept = recorder(200)
    monkeypatch.setattr(D, "_post_json", accept)
    D.deliver_once(data, "http://127.0.0.1:8765", token="t", retain_sent_days=0)
    assert len(list((root / "sent").glob("*.json"))) == 2

    old = time.time() - 40 * 86400
    for path in list((root / "sent").glob("*")) + list((root / "transcripts" / "sent").glob("*")):
        os.utime(path, (old, old))
    # 同时放一条还没投出去的、同样"很旧"的 pending，确认回收碰不到它
    stale_pending = root / "pending" / "0000000000000000000_claude-never-sent.json"
    stale_pending.write_text('{"event_id":"claude-never-sent"}', encoding="utf-8")
    os.utime(stale_pending, (old, old))

    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t", retain_sent_days=30)
    assert report["reclaimed"] == 3
    # 那条 40 天前才写进 pending/ 的事件这一轮刚投出去，保留期从"中央确认"起算，
    # 所以它不能在同一轮就被回收掉（os.replace 保留 mtime，很容易踩到）。
    assert [p.name for p in (root / "sent").glob("*.json")] == ["0000000000000000000_claude-never-sent.json"]
    assert not stale_pending.exists()


def test_status_answers_without_the_network_and_deliver_reports_health(
    tmp_path: Path, monkeypatch, capsys
):
    """§12 的「卸载前显示未同步数量」与 §10 的 outbox 健康上报。"""
    data = tmp_path / "plugin-data"
    make_session(data, "ws-a", "session-1", events=3)

    # --status 不联网：中央挂着也答得出还有多少没传
    assert D.main(["--data-dir", str(data), "--status"]) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["pending"] == 3 and value["sent"] == 0

    telemetry: list[dict] = []

    def fake(url, path, payload, token, timeout):
        if path == "/api/telemetry/outbox":
            telemetry.append(payload)
        return 200, {"ok": True}

    monkeypatch.setattr(D, "_post_json", fake)
    report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    assert report["reported"] is True
    assert telemetry[0]["pending"] == 0 and telemetry[0]["sent"] == 3
    assert telemetry[0]["machine"]

    assert D.main(["--data-dir", str(data), "--status"]) == 0
    after = json.loads(capsys.readouterr().out)
    assert after["pending"] == 0 and after["last_delivery"]["delivered_events"] == 3


def test_bind_writes_the_resolved_project_id_into_the_marker(tmp_path: Path, monkeypatch):
    """`/api/context` 的成功响应把项目放在 `project` 里，这里以前读的是外层 `body["id"]`，
    永远是 None —— 所以 `trace-project bind` 从来没写进过 project_id，每台机器都停在
    「未归属」，而那正是 §7 要靠 marker 解决的问题本身。"""
    project = tmp_path / "repo"
    project.mkdir()

    def fake(url, path, value, token, timeout):
        assert path == "/api/context"
        return 200, {"matched": True, "project": {"id": "prj_42", "name": "Batch effect"}}

    monkeypatch.setattr(D, "_post_json", fake)
    assert D.project_main(["bind", str(project), "--no-git", "--create"]) == 0
    marker = D.read_marker(project / D.MARKER_NAME)
    assert marker["project_id"] == "prj_42"
    assert D.project_binding(project)["project_id"] == "prj_42"


def test_an_ambiguous_team_mapping_stops_the_bind_instead_of_creating_a_duplicate(
    tmp_path: Path, monkeypatch, capsys
):
    """§7：「映射不确定时进入待确认状态，不能静默创建多个重复项目。」

    --create 也不能把它推过去：候选摊开给人选，marker 一个字都不写。"""
    project = tmp_path / "repo"
    project.mkdir()

    def ambiguous(url, path, value, token, timeout):
        return 200, {
            "matched": False, "pending_confirmation": True, "reason": "team_mapping_ambiguous",
            "candidates": [
                {"project_id": "prj_a", "project_name": "Alpha", "pattern": "https://github.com/lab/*",
                 "created_by": "alice", "created_at": "2026-08-19T00:00:00Z"},
                {"project_id": "prj_b", "project_name": "Beta", "pattern": "https://github.com/*/shared",
                 "created_by": "bob", "created_at": "2026-08-19T01:00:00Z"},
            ],
        }

    monkeypatch.setattr(D, "_post_json", ambiguous)
    assert D.project_main(["bind", str(project), "--no-git", "--create"]) == 2
    err = capsys.readouterr().err
    assert "prj_a" in err and "prj_b" in err
    assert "alice" in err, "映射要能被审计：是谁加的规则把我送到这里的"
    assert not (project / D.MARKER_NAME).exists(), "待确认状态下不许留下任何绑定"


def test_a_filesystem_path_is_never_accepted_as_a_workspace_key(tmp_path: Path, monkeypatch):
    """审计复核：绝对 cwd 以前可以直接当项目身份，于是同一个仓库在两台机器上
    会各自长出一个中央项目（§7 禁止的「静默创建重复项目」）。"""
    for bad in ("/home/alice/rna", "C:\\Users\\bob\\rna", "~/rna", "./rna",
                "\\\\fileserver\\share\\rna", "file:///home/alice/rna"):
        assert D.workspace_key_problem(bad), bad
    for good in ("rt-ws-0123456789ab", "https://github.com/lab/rna",
                 "git@github.com:lab/rna.git", "batch-effect-correction"):
        assert D.workspace_key_problem(good) is None, good

    project = tmp_path / "repo"
    project.mkdir()
    assert D.project_main(
        ["bind", str(project), "--no-git", "--offline", "--workspace-key", str(project)]
    ) == 2
    assert not (project / D.MARKER_NAME).exists()

    # 本机路径的 Git remote 同样不能变成第二个 key
    class Local:
        returncode = 0
        stdout = "/srv/mirrors/rna.git\n"

    class Remote:
        returncode = 0
        stdout = "git@github.com:lab/rna.git\n"

    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: Local())
    assert D.git_remote_key(project) is None
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: Remote())
    assert D.git_remote_key(project) == "https://github.com/lab/rna"


def test_events_staged_before_the_marker_had_a_project_id_still_arrive_assigned(
    tmp_path: Path, monkeypatch
):
    """§7 说 `project_id` 要等中央映射完成后才写进 marker，而 hook 从 marker 存在
    那一刻就开始采集。中间那批 pending 事件带着 None；按快照投出去就永久孤立，
    因为 storage.ingest 只对 sessions 行 COALESCE 回填，events 是 INSERT OR IGNORE。"""
    data = tmp_path / "plugin-data"
    repo = tmp_path / "repo"
    repo.mkdir()
    root = data / "outbox" / "ws-a" / "session-1"
    (root / "pending").mkdir(parents=True)
    (root / "pending" / "0000000000000000000_claude-e1.json").write_text(
        json.dumps({
            "event_id": "claude-e1", "session_id": "session-1", "project_dir": str(repo),
            "project_id": None, "hook_event": "UserPromptSubmit", "payload": {"prompt": "hi"},
        }),
        encoding="utf-8",
    )
    D.write_marker(repo, workspace_key="rt-ws-late", project_id="prj_late", capture=True)

    # 只补空、不改写：这个 session 的事件已经带着归属，marker 后来改绑到别的项目
    # 也不能追溯性地把旧历史搬走。
    other = data / "outbox" / "ws-b" / "session-2"
    (other / "pending").mkdir(parents=True)
    (other / "pending" / "0000000000000000000_claude-e2.json").write_text(
        json.dumps({
            "event_id": "claude-e2", "session_id": "session-2", "project_dir": str(repo),
            "project_id": "prj_original", "hook_event": "Stop", "payload": {},
        }),
        encoding="utf-8",
    )

    accept = recorder(200)
    monkeypatch.setattr(D, "_post_json", accept)
    D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    delivered = {call["value"]["session"]["id"]: call["value"]["project_id"]
                 for call in accept.calls}
    assert delivered == {"session-1": "prj_late", "session-2": "prj_original"}


def test_a_skipped_run_says_so_instead_of_looking_like_an_empty_outbox(tmp_path):
    """被锁挡住时必须**出声**。

    以前这里只往 JSON 里塞一个 `skipped` 就返回：sessions=0、delivered=0、
    last_error=null —— 和「本来就没东西要发」逐字一样。人手动跑一次 trace-deliver
    看到这个输出，会认为数据已经传完了。

    而握着锁的通常正是 SessionStart hook 刚刚分离启动的那一个，所以这不是
    罕见路径，是日常路径：真实的端到端跑里就撞上了。
    """
    from research_trace import deliver

    data = tmp_path / "d"
    (data / "outbox" / ".deliver-lock").mkdir(parents=True)     # 假装别人正握着
    report = deliver.deliver_once(data, "http://127.0.0.1:1", timeout=1)

    assert report["skipped"] is True
    assert report["last_error"], "被跳过时 last_error 不许是 None —— 那和成功长得一样"
    assert "lock" in report["last_error"].lower()
    assert report["finished_at"], "跳过也是一次完成，要有时间戳"
