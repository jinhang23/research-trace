"""Contract tests for the Claude Code capture hooks."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TRACE_HOOK_NO_SPAWN", "1")  # 测试里不真的拉起投递进程

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("research_trace_hook", ROOT / "scripts" / "trace_hook.py")
assert SPEC and SPEC.loader
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PROTOCOL = ROOT / "hooks" / "RECORDER_PROTOCOL.md"


def test_entire_failed_export_does_not_publish_partial_chunks_or_advance_cursor(tmp_path, monkeypatch):
    import pytest

    from research_trace.entire_evidence import EntireRepository, EvidenceError

    @contextmanager
    def failing_export(self, session_id):
        yield io.BytesIO(b'{"type":"user","message":"visible new idea"}\n')
        raise EvidenceError('upstream export failed')

    monkeypatch.setattr(EntireRepository, 'session_stream', failing_export)
    state = {}
    payload = {'session_id': 'test-session'}
    config = {'entire_executable': 'unused'}
    binding = {'project_dir': str(tmp_path)}
    with pytest.raises(EvidenceError):
        H._capture_entire_transcript(tmp_path, payload, state, binding, config)
    assert not state.get('entire_offsets')
    assert not list((tmp_path / 'transcripts/pending').glob('*'))
    assert not list((tmp_path / 'transcripts/meta').glob('*'))
    assert not list((tmp_path / 'transcripts/staging').glob('*'))


def bind(tmp_path: Path, name: str = "project-a", **marker) -> Path:
    """采集是 opt-in 的：测试里的每个项目目录都要先显式绑定。"""
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    value = {"schema": H.MARKER_NAME, "workspace_key": f"rt-ws-{name}"}
    value.update(marker)
    (project / H.MARKER_NAME).write_text(json.dumps(value), encoding="utf-8")
    return project


def event(name: str, cwd: Path | str, **extra):
    value = {
        "session_id": "session-123",
        "transcript_path": "/tmp/session-123.jsonl",
        "cwd": str(cwd),
        "hook_event_name": name,
    }
    value.update(extra)
    return value


def session_root(data: Path) -> Path:
    matches = list((data / "outbox").glob("*/session-123"))
    assert len(matches) == 1
    return matches[0]


def pending(data: Path) -> list[Path]:
    return sorted((session_root(data) / "pending").glob("*.json"))


def test_plugin_manifest_does_not_redeclare_the_standard_hooks_file():
    """manifest.hooks 只用来指向**额外的** hook 文件。

    hooks/hooks.json 是标准路径，Claude Code 会自动加载；在 manifest 里再声明一次
    等于同一个文件加载两次，插件会以 "Duplicate hooks file detected" 整体加载失败 ——
    hook 不注册，MCP server 也起不来。这是加载期行为，manifest 的 schema 校验查不出来，
    所以在这里守着。
    """
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    declared = manifest.get("hooks")
    entries = [declared] if isinstance(declared, str) else list(declared or [])
    standard = {"hooks/hooks.json", "./hooks/hooks.json"}
    assert not (standard & {str(entry).strip() for entry in entries}), (
        "plugin.json 不要声明 hooks/hooks.json —— 它是自动加载的"
    )


def test_delivery_is_kicked_off_at_every_turn_boundary(tmp_path: Path, monkeypatch):
    """只挂在 SessionStart/SessionEnd 上的话，长会话可以攒到几百个批次一个都不投。

    HPC 上的会话经常一开就是几小时、而且从不正常结束（被 kill、掉线、超时），
    SessionEnd 根本不会来。Stop 是一轮对话刚结束、batch 正好写完的时刻。
    """
    project = bind(tmp_path, "project-a")
    data = tmp_path / "data"
    calls = []
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: calls.append(a[0]) or True)

    for name in ("SessionStart", "Stop", "SessionEnd"):
        H.handle(event(name, project), data, PROTOCOL, "https://example.org")
    assert len(calls) == 3, f"每个回合边界都该拉一次投递，实际 {len(calls)} 次"


def test_plugin_hooks_cover_the_loss_boundaries_and_reuse_configured_python():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    required = {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "StopFailure",
        "PreCompact",
        "PostCompact",
        "SessionEnd",
    }
    assert required <= set(config["hooks"])
    for groups in config["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"] == "${user_config.python}"
                assert "${CLAUDE_PLUGIN_DATA}" in hook["args"]
                assert "${CLAUDE_PLUGIN_ROOT}/scripts/trace_hook.py" in hook["args"]
                # 全局暂停走 TRACE_CAPTURE 环境变量：capture 不再是插件选项，未设置就会让 hook 失败
                assert "${user_config.capture}" not in hook["args"]
                assert "${user_config.url}" in hook["args"]


def test_prompt_tool_and_stop_are_staged_without_blocking_the_main_agent(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    assert H.handle(event("UserPromptSubmit", cwd, prompt="test hypothesis A"), data, PROTOCOL) is None
    assert (
        H.handle(
            event(
                "PreToolUse",
                cwd,
                tool_name="Bash",
                tool_use_id="tool-1",
                tool_input={"command": "python train.py --lr 1e-4"},
            ),
            data,
            PROTOCOL,
        )
        is None
    )
    assert (
        H.handle(
            event(
                "PostToolUse",
                cwd,
                tool_name="Bash",
                tool_use_id="tool-1",
                tool_input={"command": "python train.py --lr 1e-4"},
                tool_response={"stdout": "auc=0.91", "exit_code": 0},
            ),
            data,
            PROTOCOL,
        )
        is None
    )

    output = H.handle(
        event(
            "Stop",
            cwd,
            stop_hook_active=False,
            last_assistant_message="AUC is 0.91",
            background_tasks=[],
        ),
        data,
        PROTOCOL,
    )
    assert output is None
    assert len(pending(data)) == 4

    manifests = list((session_root(data) / "batches").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["event_count"] == 4
    assert manifest["workspace_keys"] == ["rt-ws-project-a"]
    assert "recorder_agent_id" not in json.loads((session_root(data) / "state.json").read_text(encoding="utf-8"))


def test_an_unbound_directory_is_never_touched(tmp_path: Path):
    """②：没有 marker 就一个字节都不写，连 outbox 目录都不建。"""
    plain = tmp_path / "not-bound"
    plain.mkdir()
    data = tmp_path / "plugin-data"
    assert H.handle(event("UserPromptSubmit", plain, prompt="secret"), data, PROTOCOL) is None
    assert H.handle(event("Stop", plain, stop_hook_active=False, background_tasks=[]), data, PROTOCOL) is None
    assert not data.exists(), "an opt-out project must not create even the outbox directory"

    excluded = bind(tmp_path, "excluded", capture=False)
    assert H.handle(event("UserPromptSubmit", excluded, prompt="secret"), data, PROTOCOL) is None
    assert not data.exists(), '"capture": false must exclude the project (§13)'

    bound = bind(tmp_path)
    assert H.handle(event("UserPromptSubmit", bound, prompt="ok"), data, PROTOCOL) is None
    assert len(pending(data)) == 1


def test_the_opt_in_gate_survives_an_unimportable_package(tmp_path: Path, monkeypatch):
    """插件被裁剪、research_trace 导不进来时，opt-in 这道闸门仍然必须生效。"""
    monkeypatch.setattr(H, "_package_binding", None)
    data = tmp_path / "plugin-data"
    plain = tmp_path / "loose"
    plain.mkdir()
    assert H.handle(event("UserPromptSubmit", plain, prompt="secret"), data, PROTOCOL) is None
    assert not data.exists()
    cwd = bind(tmp_path, "fallback", project_id="prj_fb")
    assert H.handle(event("UserPromptSubmit", cwd, prompt="ok"), data, PROTOCOL) is None
    record = json.loads(pending(data)[0].read_text(encoding="utf-8"))
    assert record["project_id"] == "prj_fb"
    assert record["workspace_keys"] == ["rt-ws-fallback"]


def test_marker_project_identity_travels_with_the_directory(tmp_path: Path):
    """§7：不同绝对路径、同一个 workspace key → 同一个 outbox 分支。"""
    data = tmp_path / "plugin-data"
    first = bind(tmp_path, "checkout-a", workspace_key="rt-ws-shared", project_id="prj_1")
    second = bind(tmp_path, "checkout-b", workspace_key="rt-ws-shared", project_id="prj_1")
    H.handle(event("UserPromptSubmit", first, prompt="a"), data, PROTOCOL)
    H.handle(event("UserPromptSubmit", second, prompt="b"), data, PROTOCOL)
    assert len(list((data / "outbox").glob("*"))) == 1
    record = json.loads(pending(data)[0].read_text(encoding="utf-8"))
    assert record["project_id"] == "prj_1"
    assert record["workspace_keys"] == ["rt-ws-shared"]


def test_stop_is_always_non_blocking_and_does_not_batch_lifecycle_only(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("UserPromptSubmit", cwd, prompt="做点事"), data, PROTOCOL)
    assert (
        H.handle(
            event("Stop", cwd, stop_hook_active=False, last_assistant_message="done", background_tasks=[]),
            data,
            PROTOCOL,
        )
        is None
    )
    root = session_root(data)
    assert len(list((root / "batches").glob("*.json"))) == 1
    before = len(pending(data))
    assert (
        H.handle(
            event("Stop", cwd, stop_hook_active=True, last_assistant_message="done", background_tasks=[]),
            data,
            PROTOCOL,
        )
        is None
    )
    assert len(pending(data)) == before
    assert len(list((root / "batches").glob("*.json"))) == 1


def test_enabled_recorder_only_seals_a_batch_and_never_starts_model_process(tmp_path: Path, monkeypatch):
    cwd = bind(
        tmp_path,
        recorder={
            "enabled": True,
            "mode": "independent",
            "model": "sonnet",
            "extra_usage_disabled": True,
        },
    )
    data = tmp_path / "plugin-data"
    calls = []
    monkeypatch.setattr(H.subprocess, "Popen", lambda command, **options: calls.append((command, options)))
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)
    H.handle(event("UserPromptSubmit", cwd, prompt="做点事"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd), data, PROTOCOL, "http://trace") is None
    assert calls == []
    assert list((session_root(data) / "batches").glob("*.json"))


def test_recorder_without_extra_usage_confirmation_only_queues(tmp_path: Path, monkeypatch):
    cwd = bind(tmp_path, recorder={"enabled": True, "model": "sonnet"})
    data = tmp_path / "plugin-data"
    calls = []
    monkeypatch.setattr(H.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)
    H.handle(event("UserPromptSubmit", cwd, prompt="hypothesis"), data, PROTOCOL)
    assert H.handle(event("Stop", cwd), data, PROTOCOL) is None
    assert calls == []
    assert list((session_root(data) / "batches").glob("*.json")), "material stays queued"


def test_trace_mcp_calls_do_not_become_research_material(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    for name in ("PreToolUse", "PostToolUse"):
        H.handle(
            event(
                name,
                cwd,
                tool_name="mcp__plugin_research-trace_trace__trace_record",
                tool_input={"title": "plumbing"},
            ),
            data,
            PROTOCOL,
        )
    assert not list((data / "outbox").glob("*/*/pending/*.json"))


@pytest.mark.parametrize("diagnostic", ["TranscriptCaptureError", "CodeCaptureError"])
def test_capture_diagnostics_alone_do_not_start_a_recorder(tmp_path, diagnostic):
    cwd, data = bind(tmp_path), tmp_path / "plugin-data"
    H.handle(event(diagnostic, cwd, error="capture temporarily unavailable"), data, PROTOCOL)
    for _ in range(4):
        assert H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL) is None
    records = [json.loads(p.read_text(encoding="utf-8")) for p in pending(data)]
    assert records[0]["hook_event"] == diagnostic
    assert not list((session_root(data) / "batches").glob("*.json"))


def test_session_start_launches_the_deliverer_without_waiting(tmp_path: Path, monkeypatch):
    """①(b)：hook 只负责分离启动一次投递器，绝不等它、绝不因它失败而失败。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    monkeypatch.delenv("TRACE_HOOK_NO_SPAWN", raising=False)
    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, command, **options):
            calls.append(command)
            assert "cwd" in options and "env" in options

        def wait(self, *_a, **_k):  # pragma: no cover - 调用它就说明 hook 在等
            raise AssertionError("the hook must never wait for the deliverer")

    monkeypatch.setattr(H.subprocess, "Popen", FakePopen)
    H.handle(event("SessionStart", cwd, source="startup"), data, PROTOCOL, "http://central:8765")
    assert calls and calls[0][1:3] == ["-m", "research_trace.deliver"]
    assert "--url" in calls[0] and "http://central:8765" in calls[0]

    H.handle(event("UserPromptSubmit", cwd, prompt="x"), data, PROTOCOL, "http://central:8765")
    assert len(calls) == 1, "only session boundaries spawn the deliverer"

    def explode(*_a, **_k):
        raise OSError("no exec for you")

    monkeypatch.setattr(H.subprocess, "Popen", explode)
    monkeypatch.setattr(H, "DELIVER_SPAWN_INTERVAL", 0.0)
    assert H.handle(event("SessionEnd", cwd, reason="exit"), data, PROTOCOL) is None
    assert len(pending(data)) == 3, "a failed spawn must not lose the event"


def test_transcript_content_is_copied_incrementally_into_the_batch(tmp_path: Path):
    cwd = bind(tmp_path)
    transcript = tmp_path / "claude-session.jsonl"
    transcript.write_text('{"type":"user","message":"first"}\n', encoding="utf-8")
    payload = event("UserPromptSubmit", cwd, transcript_path=str(transcript), prompt="first")
    H.handle(payload, tmp_path / "plugin-data", PROTOCOL)
    transcript.write_text(
        transcript.read_text(encoding="utf-8") + '{"type":"assistant","message":"second"}\n',
        encoding="utf-8",
    )
    output = H.handle(
        event(
            "Stop",
            cwd,
            transcript_path=str(transcript),
            stop_hook_active=False,
            last_assistant_message="second",
            background_tasks=[],
        ),
        tmp_path / "plugin-data",
        PROTOCOL,
    )
    assert output is None
    root = session_root(tmp_path / "plugin-data")
    manifest_path = next((root / "batches").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["transcript_chunk_count"] == 2
    text = "".join(H._long(root / item["path"]).read_text(encoding="utf-8") for item in manifest["transcript_chunks"])
    assert '"message":"first"' in text
    assert '"message":"second"' in text


def test_transcript_chunks_never_split_a_utf8_jsonl_record(tmp_path: Path):
    transcript = tmp_path / "unicode.jsonl"
    transcript.write_text('{"message":"批次效应"}\n{"message":"修正方案"}\n', encoding="utf-8")
    outbox = tmp_path / "outbox"
    (outbox / "transcripts" / "pending").mkdir(parents=True)
    (outbox / "transcripts" / "meta").mkdir(parents=True)
    state = {}
    chunks = H._capture_transcripts(
        outbox,
        event("Stop", tmp_path, transcript_path=str(transcript)),
        state,
        chunk_size=10,
    )
    assert len(chunks) == 2
    assert [(outbox / item["path"]).read_text(encoding="utf-8") for item in chunks] == [
        '{"message":"批次效应"}\n',
        '{"message":"修正方案"}\n',
    ]


def test_hidden_reasoning_never_reaches_the_outbox(tmp_path: Path):
    """③：thinking / redacted_thinking 在落盘前就被剥掉（§6）。"""
    transcript = tmp_path / "thinking.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"content": "run it"}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "HIDDEN COT SECRET", "signature": "sig-abc"},
                        {"type": "text", "text": "visible answer"},
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "redacted_thinking", "data": "OPAQUE"},
                    ]
                },
            }
        ),
        json.dumps({"type": "thinking", "thinking": "WHOLE LINE SECRET"}),
        json.dumps({"type": "user", "message": "I am thinking about batch effects"}),
        '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"TRUNCATED',
    ]
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outbox = tmp_path / "outbox"
    chunks = H._capture_transcripts(outbox, event("Stop", tmp_path, transcript_path=str(transcript)), {})
    text = "".join((outbox / item["path"]).read_text(encoding="utf-8") for item in chunks)
    for secret in ("HIDDEN COT SECRET", "WHOLE LINE SECRET", "sig-abc", "OPAQUE", "TRUNCATED"):
        assert secret not in text
    assert "visible answer" in text
    assert '"run it"' in text
    # 只是碰巧出现 thinking 这个词的普通内容必须原样保留
    assert "I am thinking about batch effects" in text
    # 解析不了又带 thinking 字样的行不静默消失：留一条只有长度和 hash 的缺口记录。
    assert "research-trace.redacted" in text
    assert '"type":"thinking"' not in text


def test_untouched_lines_are_copied_verbatim_and_cursor_tracks_source_bytes(tmp_path: Path):
    transcript = tmp_path / "plain.jsonl"
    body = '{"type":"user","message":"a"}\n{"type":"assistant","message":"b"}\n'
    transcript.write_bytes(body.encode("utf-8"))  # 不能让 Windows 换行翻译改变字节数
    outbox = tmp_path / "outbox"
    state: dict = {}
    chunks = H._capture_transcripts(outbox, event("Stop", tmp_path, transcript_path=str(transcript)), state)
    assert (outbox / chunks[0]["path"]).read_text(encoding="utf-8") == body
    # cursor 按源文件字节推进，与剥离后的落盘长度无关
    assert list(state["transcript_offsets"].values()) == [len(body.encode("utf-8"))]

    # 追加中的半行留到下一次，绝不切开一条 JSON 记录
    with transcript.open("ab") as stream:
        stream.write(b'{"type":"assistant","message":"half')
    more = H._capture_transcripts(outbox, event("Stop", tmp_path, transcript_path=str(transcript)), state)
    assert more == []
    assert list(state["transcript_offsets"].values()) == [len(body.encode("utf-8"))]


def test_transcript_io_failure_is_reported_and_leaves_no_tmp_garbage(tmp_path: Path, capsys, monkeypatch):
    transcript = tmp_path / "broken.jsonl"
    transcript.write_text('{"type":"user","message":"a"}\n', encoding="utf-8")
    outbox = tmp_path / "outbox"
    (outbox / "transcripts" / "pending").mkdir(parents=True)

    real_write = Path.write_bytes

    def failing(self, value):
        if self.name.endswith(".tmp"):
            real_write(self, value)  # 先真的落一个 .tmp，再让 replace 失败
            raise OSError("simulated ENOSPC")
        return real_write(self, value)

    monkeypatch.setattr(Path, "write_bytes", failing)
    state: dict = {}
    assert H._capture_transcripts(outbox, event("Stop", tmp_path, transcript_path=str(transcript)), state) == []
    monkeypatch.undo()
    assert "transcript capture failed" in capsys.readouterr().err
    assert list((outbox / "transcripts" / "pending").glob(".*.tmp")) == []
    assert state["transcript_offsets"] == {} or all(value == 0 for value in state["transcript_offsets"].values())


def test_outbox_files_and_directories_are_private(tmp_path: Path, monkeypatch):
    """outbox 里是完整对话和带令牌的命令原文；多用户节点上不能同机可读。"""
    modes: dict[str, int] = {}
    real_chmod = os.chmod

    def record(path, mode, *args, **kwargs):
        modes[str(path)] = mode
        try:
            real_chmod(path, mode, *args, **kwargs)
        except OSError:
            pass

    monkeypatch.setattr(os, "chmod", record)
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("UserPromptSubmit", cwd, prompt="export TOKEN=sk-secret"), data, PROTOCOL)
    root = session_root(data)
    assert modes[str(root / "pending")] == 0o700
    assert modes[str(data / "outbox")] == 0o700
    event_file = pending(data)[0]
    assert modes[str(H._long(event_file))] == 0o600
    assert modes[str(root / "state.json")] == 0o600


def test_a_dead_state_lock_does_not_tax_every_later_event(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event("UserPromptSubmit", cwd, prompt="warm up"), data, PROTOCOL)
    lock = session_root(data) / ".state-lock"
    lock.mkdir()
    (lock / "owner").write_text("999999999", encoding="utf-8")
    os.utime(lock, (time.time() - 3600, time.time() - 3600))

    started = time.monotonic()
    H.handle(event("UserPromptSubmit", cwd, prompt="after a killed hook"), data, PROTOCOL)
    assert time.monotonic() - started < 0.5
    assert len(pending(data)) == 2

    # 系统时钟回拨会让 age 变成负数；旧实现因此永远拆不掉这把锁。
    lock.mkdir(exist_ok=True)
    os.utime(lock, (time.time() + 7200, time.time() + 7200))
    started = time.monotonic()
    H.handle(event("UserPromptSubmit", cwd, prompt="after an ntp step back"), data, PROTOCOL)
    assert time.monotonic() - started < 0.5
    assert len(pending(data)) == 3


def _grow(transcript: Path, line: str) -> None:
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "user", "message": line}) + "\n")


def _chunks(data: Path) -> str:
    root = session_root(data)
    return "".join(
        path.read_text(encoding="utf-8")
        for directory in ("pending", "sent")
        for path in sorted((root / "transcripts" / directory).glob("*.jsonl"))
    )


def test_a_global_pause_does_not_backfill_what_was_written_while_paused(tmp_path: Path, monkeypatch):
    """plugin.json 的承诺：capture=off「暂停期间不会补采，适合临时处理令牌」。

    以前 off 只是让 hook 提前 return，transcript 游标一动不动，重新开启后暂停期间写进
    transcript 的内容会被完整补采上传——而这个开关的文档用途正是最不该发生这件事的场景。
    """
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    transcript = tmp_path / "session-123.jsonl"
    _grow(transcript, "before")
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)

    H.handle(event("UserPromptSubmit", cwd, prompt="x", transcript_path=str(transcript)), data, PROTOCOL)
    _grow(transcript, "DURING-PAUSE-SECRET")
    assert (
        H.handle(
            event("PreToolUse", cwd, tool_name="Bash", transcript_path=str(transcript)),
            data,
            PROTOCOL,
            paused=True,
        )
        is None
    )
    _grow(transcript, "after")
    H.handle(event("UserPromptSubmit", cwd, prompt="y", transcript_path=str(transcript)), data, PROTOCOL)

    chunks = _chunks(data)
    assert "before" in chunks and "after" in chunks
    assert "DURING-PAUSE-SECRET" not in chunks
    hook_events = [json.loads(p.read_text(encoding="utf-8"))["hook_event"] for p in pending(data)]
    assert hook_events == ["UserPromptSubmit", "UserPromptSubmit"], "no event is written while paused"
    state = json.loads((session_root(data) / "state.json").read_text(encoding="utf-8"))
    assert state["paused_through"]


def test_a_disabled_marker_pauses_the_same_way_without_creating_anything(tmp_path: Path, monkeypatch):
    """`trace-project disable` 写的 capture:false 和全局开关走同一条暂停路径。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    transcript = tmp_path / "session-123.jsonl"
    _grow(transcript, "before")
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)
    H.handle(event("UserPromptSubmit", cwd, prompt="x", transcript_path=str(transcript)), data, PROTOCOL)

    marker = cwd / H.MARKER_NAME
    value = json.loads(marker.read_text(encoding="utf-8"))
    marker.write_text(json.dumps({**value, "capture": False}), encoding="utf-8")
    _grow(transcript, "DURING-DISABLE-SECRET")
    assert H.handle(event("UserPromptSubmit", cwd, prompt="z", transcript_path=str(transcript)), data, PROTOCOL) is None
    marker.write_text(json.dumps({**value, "capture": True}), encoding="utf-8")
    _grow(transcript, "after")
    H.handle(event("UserPromptSubmit", cwd, prompt="y", transcript_path=str(transcript)), data, PROTOCOL)

    chunks = _chunks(data)
    assert "before" in chunks and "after" in chunks and "DURING-DISABLE-SECRET" not in chunks
    assert len(pending(data)) == 2

    # 暂停中的 hook 从不为任何项目建目录：没绑定的目录、没见过的 session 都一样
    elsewhere = tmp_path / "nowhere"
    elsewhere.mkdir()
    assert H.handle(event("Stop", elsewhere, session_id="never-seen"), data, PROTOCOL, paused=True) is None
    assert not list((data / "outbox").glob("*/never-seen"))
    assert H.handle(event("Stop", cwd, session_id="never-seen"), data, PROTOCOL, paused=True) is None
    assert not list((data / "outbox").glob("*/never-seen"))


def test_bad_stdin_never_blocks_the_main_task(tmp_path: Path, monkeypatch, capsys):
    class FakeStdin:
        def __init__(self, value: str):
            self.value = value

        def read(self) -> str:
            return self.value

    data = tmp_path / "plugin-data"
    argv = ["--data-dir", str(data), "--protocol", str(PROTOCOL)]
    for raw in ("", "not json", "[1,2,3]"):
        monkeypatch.setattr(H.sys, "stdin", FakeStdin(raw))
        assert H.main(argv) == 0
    capsys.readouterr()
    assert not data.exists()
    monkeypatch.setattr(H.sys, "stdin", FakeStdin("{}"))
    assert H.main([*argv, "--capture-enabled", "off"]) == 0
    monkeypatch.setenv("TRACE_CAPTURE", "off")
    monkeypatch.setattr(H.sys, "stdin", FakeStdin("{}"))
    assert H.main(argv) == 0
    assert not data.exists()


def test_a_long_windows_outbox_path_does_not_silently_swallow_events(tmp_path: Path):
    """Windows MAX_PATH（260）会让 os.replace 以 WinError 3 失败。

    hook 是 fail-open 的，所以那次失败只在没人看的 stderr 上留一行、退出码仍是 0——
    实测在一个稍深的 data-dir 下，这台机器上每一条事件都被静默丢掉。
    outbox 路径的长度我们只控制得了一半（宿主的 CLAUDE_PLUGIN_DATA + 36 字符的
    session UUID + 64 字符的事件文件名），所以必须显式挡住。
    """
    deep = tmp_path
    while len(str(deep)) < 200:
        deep = deep / "nested-directory-segment"
    deep.mkdir(parents=True, exist_ok=True)
    project = bind(tmp_path, "long")
    data = deep / "plugin-data"

    from research_trace.deliver import long_path

    assert H.handle(event("UserPromptSubmit", project, prompt="hi"), data, PROTOCOL) is None
    # 列目录也要走长路径：短根 + 超长子路径时 is_file() 本身就会失败，
    # 于是"文件不存在"和"看不见文件"长得一模一样。
    files = [path for path in long_path(data / "outbox").rglob("*.json") if path.is_file()]
    assert any("claude-" in path.name for path in files), files


def test_the_deliverer_can_see_what_the_hook_wrote_at_the_same_depth(tmp_path: Path):
    """两端必须用同一套长路径处理：hook 写得进去而投递器看不见，
    结果和写不进去完全一样——那段历史永远上不去，而且没有任何报错。"""
    from research_trace import deliver as D

    deep = tmp_path
    while len(str(deep)) < 200:
        deep = deep / "nested-directory-segment"
    deep.mkdir(parents=True, exist_ok=True)
    project = bind(tmp_path, "long2")
    data = deep / "plugin-data"
    H.handle(event("UserPromptSubmit", project, prompt="hi"), data, PROTOCOL)

    sent: list[dict] = []

    def accept(url, path, value, token, timeout):
        if path == "/api/ingest":
            sent.append(value)
        return 200, {"ok": True}

    import pytest as _pytest

    monkey = _pytest.MonkeyPatch()
    monkey.setattr(D, "_post_json", accept)
    try:
        report = D.deliver_once(data, "http://127.0.0.1:8765", token="t")
    finally:
        monkey.undo()
    assert report["delivered_events"] == 1, report
    assert sent and sent[0]["events"][0]["hook_event"] == "UserPromptSubmit"


def test_stop_and_session_end_deliver_even_inside_the_spawn_throttle(tmp_path: Path, monkeypatch):
    """UF 第 5 轮：`claude -p` 一分钟内跑完，SessionStart 那次投递跑在事件之前，Stop/SessionEnd 的
    被 60 秒节流吞掉，这一轮内容一直躺在 pending/ 里，--status 全程 idle。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    monkeypatch.delenv("TRACE_HOOK_NO_SPAWN", raising=False)
    monkeypatch.setattr(H, "DELIVER_SPAWN_INTERVAL", 3600.0)
    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, command, **options):
            calls.append(command)

    monkeypatch.setattr(H.subprocess, "Popen", FakePopen)
    H.handle(event("SessionStart", cwd, source="startup"), data, PROTOCOL, "http://central:8765")
    H.handle(event("SessionStart", cwd, source="resume"), data, PROTOCOL, "http://central:8765")
    assert len(calls) == 1, "SessionStart is throttled"
    H.handle(event("UserPromptSubmit", cwd, prompt="x"), data, PROTOCOL, "http://central:8765")
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL, "http://central:8765")
    H.handle(event("SessionEnd", cwd, reason="exit"), data, PROTOCOL, "http://central:8765")
    assert len(calls) == 3, "Stop and SessionEnd must launch the deliverer regardless of the throttle"


def test_batches_seal_by_accumulated_material_not_per_turn(tmp_path: Path, monkeypatch):
    """一轮一批 = 一轮一次 Recorder 模型调用。a39：攒够 batch_min_chars 或最老材料超龄或会话结束才封。"""
    cwd = bind(tmp_path, recorder={"enabled": True, "batch_min_chars": 3000, "batch_max_age_minutes": 60})
    data = tmp_path / "plugin-data"
    monkeypatch.delenv("TRACE_BATCH_MIN_CHARS", raising=False)
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)
    batches = lambda: sorted((session_root(data) / "batches").glob("*.json"))  # noqa: E731

    for i in range(2):
        H.handle(event("UserPromptSubmit", cwd, prompt=f"short {i}"), data, PROTOCOL)
        H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert batches() == [], "two short turns are below the threshold: no batch, no model call"
    state = json.loads((session_root(data) / "state.json").read_text(encoding="utf-8"))
    assert state.get("unsealed_chars", 0) > 0 and "batched_through" not in state

    H.handle(event("UserPromptSubmit", cwd, prompt="x" * 4000), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert len(batches()) == 1, "the turn that crosses the threshold seals everything accumulated"
    manifest = json.loads(batches()[0].read_text(encoding="utf-8"))
    assert manifest["event_count"] >= 5, "all three turns' events are in the one batch"

    H.handle(event("UserPromptSubmit", cwd, prompt="tail"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert len(batches()) == 1
    H.handle(event("SessionEnd", cwd, reason="exit"), data, PROTOCOL)
    assert len(batches()) == 2, "SessionEnd seals whatever is left regardless of size"


def test_old_unsealed_material_is_sealed_by_age(tmp_path: Path, monkeypatch):
    cwd = bind(tmp_path, recorder={"enabled": True, "batch_min_chars": 10**6, "batch_max_age_minutes": 30})
    data = tmp_path / "plugin-data"
    monkeypatch.delenv("TRACE_BATCH_MIN_CHARS", raising=False)
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)
    H.handle(event("UserPromptSubmit", cwd, prompt="early"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert not list((session_root(data) / "batches").glob("*.json"))
    old = time.time() - 31 * 60
    for path in (session_root(data) / "pending").glob("*.json"):
        os.utime(path, (old, old))
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert len(list((session_root(data) / "batches").glob("*.json"))) == 1


def test_env_zero_restores_one_batch_per_turn(tmp_path: Path, monkeypatch):
    cwd = bind(tmp_path, recorder={"enabled": True, "batch_min_chars": 10**6})
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("TRACE_BATCH_MIN_CHARS", "0")
    monkeypatch.setattr(H, "_spawn_deliver", lambda *a, **k: False)
    H.handle(event("UserPromptSubmit", cwd, prompt="one"), data, PROTOCOL)
    H.handle(event("Stop", cwd, stop_hook_active=False), data, PROTOCOL)
    assert len(list((session_root(data) / "batches").glob("*.json"))) == 1
