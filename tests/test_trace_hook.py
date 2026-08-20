"""Contract tests for the Claude Code capture hooks."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path


os.environ.setdefault("TRACE_HOOK_NO_SPAWN", "1")  # 测试里不真的拉起投递进程

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("research_trace_hook", ROOT / "scripts" / "trace_hook.py")
assert SPEC and SPEC.loader
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PROTOCOL = ROOT / "hooks" / "RECORDER_PROTOCOL.md"


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


def test_plugin_hooks_cover_the_loss_boundaries_and_reuse_configured_python():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    required = {
        "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
        "PostToolUseFailure", "SubagentStart", "SubagentStop", "Stop",
        "StopFailure", "PreCompact", "PostCompact", "SessionEnd",
    }
    assert required <= set(config["hooks"])
    for groups in config["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"] == "${user_config.python}"
                assert "${CLAUDE_PLUGIN_DATA}" in hook["args"]
                assert "${CLAUDE_PLUGIN_ROOT}/scripts/trace_hook.py" in hook["args"]
                assert "${user_config.capture}" in hook["args"]
                assert "${user_config.url}" in hook["args"]


def test_prompt_tool_and_stop_are_staged_before_the_recorder_runs(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    assert H.handle(event("UserPromptSubmit", cwd, prompt="test hypothesis A"), data, PROTOCOL) is None
    assert H.handle(event(
        "PreToolUse", cwd, tool_name="Bash", tool_use_id="tool-1",
        tool_input={"command": "python train.py --lr 1e-4"},
    ), data, PROTOCOL) is None
    assert H.handle(event(
        "PostToolUse", cwd, tool_name="Bash", tool_use_id="tool-1",
        tool_input={"command": "python train.py --lr 1e-4"},
        tool_response={"stdout": "auc=0.91", "exit_code": 0},
    ), data, PROTOCOL) is None

    output = H.handle(event(
        "Stop", cwd, stop_hook_active=False, last_assistant_message="AUC is 0.91",
        background_tasks=[],
    ), data, PROTOCOL)
    assert output and output["decision"] == "block"
    guidance = output["reason"]
    assert "subagent_type='fork'" in guidance
    assert "complete current context" in guidance
    assert len(pending(data)) == 4

    manifests = list((session_root(data) / "batches").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["event_count"] == 4
    assert manifest["workspace_keys"] == ["rt-ws-project-a"]


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


def test_stop_hook_continues_only_once_per_turn(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    first = H.handle(event(
        "Stop", cwd, stop_hook_active=False, last_assistant_message="done", background_tasks=[]
    ), data, PROTOCOL)
    assert first
    before = len(pending(data))
    second = H.handle(event(
        "Stop", cwd, stop_hook_active=True, last_assistant_message="done", background_tasks=[]
    ), data, PROTOCOL)
    assert second is None
    assert len(pending(data)) == before, "the hook's own continuation must not create a loop"


def test_recorder_fork_is_remembered_resumed_and_closes_its_batch(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    output = H.handle(event(
        "Stop", cwd, stop_hook_active=False, last_assistant_message="done", background_tasks=[]
    ), data, PROTOCOL)
    assert output
    manifest_path = next((session_root(data) / "batches").glob("*.json"))
    batch_id = json.loads(manifest_path.read_text(encoding="utf-8"))["batch_id"]

    spawn_prompt = f"{H.RECORDER_MARKER} {H.BATCH_MARKER}{batch_id}] process"
    assert H.handle(event(
        "PreToolUse", cwd, tool_name="Agent", tool_use_id="spawn-1",
        tool_input={"subagent_type": "fork", "prompt": spawn_prompt},
    ), data, PROTOCOL) is None
    assert H.handle(event(
        "SubagentStart", cwd, agent_id="agent-recorder", agent_type="fork"
    ), data, PROTOCOL) is None

    state = json.loads((session_root(data) / "state.json").read_text(encoding="utf-8"))
    assert state["recorder_agent_id"] == "agent-recorder"

    resume = H.handle(event(
        "Stop", cwd, stop_hook_active=False, last_assistant_message="next", background_tasks=[]
    ), data, PROTOCOL)
    assert resume
    assert "SendMessage" in resume["reason"]
    assert "agent-recorder" in resume["reason"]

    # 语义 batch 由 Recorder 的 SubagentStop 关闭；原始文件的去向与它无关。
    assert H.handle(event(
        "SubagentStop", cwd, agent_id="agent-recorder", agent_type="fork",
        last_assistant_message="recorded batch: 0 nodes",
    ), data, PROTOCOL) is None
    root = session_root(data)
    assert not (root / "batches" / f"{batch_id}.json").exists()
    assert (root / "batches" / "done" / f"{batch_id}.json").exists()
    assert len(list((root / "pending").glob("*.json"))) == 2, "raw events stay pending until delivered"
    assert not (root / "awaiting_upload").exists(), "awaiting_upload/ is gone for good"


def test_a_subagent_that_claims_a_receipt_is_not_promoted_to_recorder(tmp_path: Path):
    """①：回执机制取消后，任何自称都不能让普通子 agent 变成 Recorder 并被封杀工具。"""
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    output = H.handle(event(
        "Stop", cwd, stop_hook_active=False, last_assistant_message="done", background_tasks=[]
    ), data, PROTOCOL)
    batch_id = json.loads(next((session_root(data) / "batches").glob("*.json")).read_text("utf-8"))["batch_id"]
    assert output

    claim = 'TRACE_RECEIPT ' + json.dumps({"batch_id": batch_id, "status": "stored", "project": None})
    H.handle(event(
        "SubagentStop", cwd, agent_id="innocent-worker", agent_type="Explore",
        last_assistant_message=claim,
    ), data, PROTOCOL)

    state = json.loads((session_root(data) / "state.json").read_text(encoding="utf-8"))
    assert "recorder_agent_id" not in state
    allowed = H.handle(event(
        "PreToolUse", cwd, agent_id="innocent-worker", tool_name="Edit",
        tool_input={"file_path": "/work/main.py"},
    ), data, PROTOCOL)
    assert allowed is None, "the main task must not be blocked by a forged receipt"
    assert (session_root(data) / "batches" / f"{batch_id}.json").exists()


def test_recorder_internal_tools_are_not_recorded_but_other_subagents_are(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event(
        "PreToolUse", cwd, tool_name="Agent", tool_use_id="spawn-1",
        tool_input={"subagent_type": "fork", "prompt": H.RECORDER_MARKER},
    ), data, PROTOCOL)
    H.handle(event("SubagentStart", cwd, agent_id="rec-1", agent_type="fork"), data, PROTOCOL)
    before = len(pending(data))

    H.handle(event(
        "PostToolUse", cwd, agent_id="rec-1", agent_type="fork", tool_name="Read",
        tool_use_id="inside-recorder", tool_input={"file_path": "/tmp/batch.json"},
        tool_response={"ok": True},
    ), data, PROTOCOL)
    assert len(pending(data)) == before

    denied = H.handle(event(
        "PreToolUse", cwd, agent_id="rec-1", agent_type="fork", tool_name="Bash",
        tool_use_id="recorder-shell", tool_input={"command": "git status"},
    ), data, PROTOCOL)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "read-only" in denied["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(pending(data)) == before

    assert H.handle(event(
        "PreToolUse", cwd, agent_id="rec-1", agent_type="fork",
        tool_name="mcp__plugin_research-trace_trace__trace_record",
        tool_use_id="recorder-trace", tool_input={"title": "safe"},
    ), data, PROTOCOL) is None
    assert len(pending(data)) == before

    H.handle(event(
        "PostToolUse", cwd, agent_id="worker-2", agent_type="Explore", tool_name="Read",
        tool_use_id="inside-worker", tool_input={"file_path": "/work/data.csv"},
        tool_response={"ok": True},
    ), data, PROTOCOL)
    assert len(pending(data)) == before + 1


def test_clear_forgets_an_unaddressable_old_recorder(tmp_path: Path):
    cwd = bind(tmp_path)
    data = tmp_path / "plugin-data"
    H.handle(event(
        "PreToolUse", cwd, tool_name="Agent", tool_input={"prompt": H.RECORDER_MARKER}
    ), data, PROTOCOL)
    H.handle(event("SubagentStart", cwd, agent_id="old-recorder", agent_type="fork"), data, PROTOCOL)
    H.handle(event("SessionStart", cwd, source="clear"), data, PROTOCOL)
    state = json.loads((session_root(data) / "state.json").read_text(encoding="utf-8"))
    assert "recorder_agent_id" not in state


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
            "Stop", cwd, transcript_path=str(transcript), stop_hook_active=False,
            last_assistant_message="second", background_tasks=[],
        ),
        tmp_path / "plugin-data",
        PROTOCOL,
    )
    assert output and output["decision"] == "block"
    root = session_root(tmp_path / "plugin-data")
    manifest_path = next((root / "batches").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["transcript_chunk_count"] == 2
    text = "".join(
        (root / item["path"]).read_text(encoding="utf-8")
        for item in manifest["transcript_chunks"]
    )
    assert '"message":"first"' in text
    assert '"message":"second"' in text


def test_transcript_chunks_never_split_a_utf8_jsonl_record(tmp_path: Path):
    transcript = tmp_path / "unicode.jsonl"
    transcript.write_text(
        '{"message":"批次效应"}\n{"message":"修正方案"}\n', encoding="utf-8"
    )
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
    assert [
        (outbox / item["path"]).read_text(encoding="utf-8") for item in chunks
    ] == ['{"message":"批次效应"}\n', '{"message":"修正方案"}\n']


def test_hidden_reasoning_never_reaches_the_outbox(tmp_path: Path):
    """③：thinking / redacted_thinking 在落盘前就被剥掉（§6）。"""
    transcript = tmp_path / "thinking.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"content": "run it"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "HIDDEN COT SECRET", "signature": "sig-abc"},
            {"type": "text", "text": "visible answer"},
        ]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "redacted_thinking", "data": "OPAQUE"},
        ]}}),
        json.dumps({"type": "thinking", "thinking": "WHOLE LINE SECRET"}),
        json.dumps({"type": "user", "message": "I am thinking about batch effects"}),
        '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"TRUNCATED',
    ]
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outbox = tmp_path / "outbox"
    chunks = H._capture_transcripts(
        outbox, event("Stop", tmp_path, transcript_path=str(transcript)), {}
    )
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
    chunks = H._capture_transcripts(
        outbox, event("Stop", tmp_path, transcript_path=str(transcript)), state
    )
    assert (outbox / chunks[0]["path"]).read_text(encoding="utf-8") == body
    # cursor 按源文件字节推进，与剥离后的落盘长度无关
    assert list(state["transcript_offsets"].values()) == [len(body.encode("utf-8"))]

    # 追加中的半行留到下一次，绝不切开一条 JSON 记录
    with transcript.open("ab") as stream:
        stream.write(b'{"type":"assistant","message":"half')
    more = H._capture_transcripts(
        outbox, event("Stop", tmp_path, transcript_path=str(transcript)), state
    )
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
    assert H._capture_transcripts(
        outbox, event("Stop", tmp_path, transcript_path=str(transcript)), state
    ) == []
    monkeypatch.undo()
    assert "transcript capture failed" in capsys.readouterr().err
    assert list((outbox / "transcripts" / "pending").glob(".*.tmp")) == []
    assert state["transcript_offsets"] == {} or all(
        value == 0 for value in state["transcript_offsets"].values()
    )


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
    assert modes[str(event_file)] == 0o600
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
    assert H.main(argv + ["--capture-enabled", "off"]) == 0


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
