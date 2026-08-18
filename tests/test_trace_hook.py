"""Contract tests for the Claude Code capture hooks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("research_trace_hook", ROOT / "scripts" / "trace_hook.py")
assert SPEC and SPEC.loader
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)
PROTOCOL = ROOT / "hooks" / "RECORDER_PROTOCOL.md"


def event(name: str, **extra):
    value = {
        "session_id": "session-123",
        "transcript_path": "/tmp/session-123.jsonl",
        "cwd": "/work/project-a",
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


def test_prompt_tool_and_stop_are_staged_before_the_recorder_runs(tmp_path: Path):
    assert H.handle(event("UserPromptSubmit", prompt="test hypothesis A"), tmp_path, PROTOCOL) is None
    assert H.handle(event(
        "PreToolUse", tool_name="Bash", tool_use_id="tool-1",
        tool_input={"command": "python train.py --lr 1e-4"},
    ), tmp_path, PROTOCOL) is None
    assert H.handle(event(
        "PostToolUse", tool_name="Bash", tool_use_id="tool-1",
        tool_input={"command": "python train.py --lr 1e-4"},
        tool_response={"stdout": "auc=0.91", "exit_code": 0},
    ), tmp_path, PROTOCOL) is None

    output = H.handle(event(
        "Stop", stop_hook_active=False, last_assistant_message="AUC is 0.91",
        background_tasks=[],
    ), tmp_path, PROTOCOL)
    assert output and output["decision"] == "block"
    guidance = output["reason"]
    assert "subagent_type='fork'" in guidance
    assert "complete current context" in guidance
    assert len(pending(tmp_path)) == 4

    manifests = list((session_root(tmp_path) / "batches").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["event_count"] == 4


def test_stop_hook_continues_only_once_per_turn(tmp_path: Path):
    first = H.handle(event(
        "Stop", stop_hook_active=False, last_assistant_message="done", background_tasks=[]
    ), tmp_path, PROTOCOL)
    assert first
    before = len(pending(tmp_path))
    second = H.handle(event(
        "Stop", stop_hook_active=True, last_assistant_message="done", background_tasks=[]
    ), tmp_path, PROTOCOL)
    assert second is None
    assert len(pending(tmp_path)) == before, "the hook's own continuation must not create a loop"


def test_recorder_fork_is_remembered_resumed_and_acks_the_batch(tmp_path: Path):
    output = H.handle(event(
        "Stop", stop_hook_active=False, last_assistant_message="done", background_tasks=[]
    ), tmp_path, PROTOCOL)
    assert output
    manifest_path = next((session_root(tmp_path) / "batches").glob("*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch_id = manifest["batch_id"]

    spawn_prompt = f"{H.RECORDER_MARKER} {H.BATCH_MARKER}{batch_id}] process"
    assert H.handle(event(
        "PreToolUse", tool_name="Agent", tool_use_id="spawn-1",
        tool_input={"subagent_type": "fork", "prompt": spawn_prompt},
    ), tmp_path, PROTOCOL) is None
    assert H.handle(event(
        "SubagentStart", agent_id="agent-recorder", agent_type="fork"
    ), tmp_path, PROTOCOL) is None

    state = json.loads((session_root(tmp_path) / "state.json").read_text(encoding="utf-8"))
    assert state["recorder_agent_id"] == "agent-recorder"

    resume = H.handle(event(
        "Stop", stop_hook_active=False, last_assistant_message="next", background_tasks=[]
    ), tmp_path, PROTOCOL)
    assert resume
    assert "SendMessage" in resume["reason"]
    assert "agent-recorder" in resume["reason"]

    receipt_line = "TRACE_RECEIPT " + json.dumps({
        "batch_id": batch_id,
        "status": "local",
        "project": None,
        "experiment_ids": [],
        "note": "v1 has no raw ingest tool",
    })
    assert H.handle(event(
        "SubagentStop", agent_id="agent-recorder", agent_type="fork",
        last_assistant_message=receipt_line,
    ), tmp_path, PROTOCOL) is None

    root = session_root(tmp_path)
    assert not (root / "batches" / f"{batch_id}.json").exists()
    assert (root / "batches" / "done" / f"{batch_id}.json").exists()
    assert list((root / "awaiting_upload").glob("*.json"))
    assert not list((root / "sent").glob("*.json"))


def test_only_centrally_stored_batches_enter_sent(tmp_path: Path):
    assert H.handle(event(
        "Stop", stop_hook_active=False, last_assistant_message="done", background_tasks=[]
    ), tmp_path, PROTOCOL)
    root = session_root(tmp_path)
    manifest_path = next((root / "batches").glob("*.json"))
    batch_id = json.loads(manifest_path.read_text(encoding="utf-8"))["batch_id"]
    receipt_line = "TRACE_RECEIPT " + json.dumps({
        "batch_id": batch_id,
        "status": "stored",
        "project": "project-a",
        "experiment_ids": [],
        "note": "central trace_ingest acknowledged every event",
    })
    H.handle(event(
        "SubagentStop", agent_id="agent-recorder", agent_type="fork",
        last_assistant_message=receipt_line,
    ), tmp_path, PROTOCOL)

    assert list((root / "sent").glob("*.json"))
    assert not list((root / "awaiting_upload").glob("*.json"))


def test_recorder_internal_tools_are_not_recorded_but_other_subagents_are(tmp_path: Path):
    H.handle(event(
        "PreToolUse", tool_name="Agent", tool_use_id="spawn-1",
        tool_input={"subagent_type": "fork", "prompt": H.RECORDER_MARKER},
    ), tmp_path, PROTOCOL)
    H.handle(event("SubagentStart", agent_id="rec-1", agent_type="fork"), tmp_path, PROTOCOL)
    before = len(pending(tmp_path))

    H.handle(event(
        "PostToolUse", agent_id="rec-1", agent_type="fork", tool_name="Read",
        tool_use_id="inside-recorder", tool_input={"file_path": "/tmp/batch.json"},
        tool_response={"ok": True},
    ), tmp_path, PROTOCOL)
    assert len(pending(tmp_path)) == before

    denied = H.handle(event(
        "PreToolUse", agent_id="rec-1", agent_type="fork", tool_name="Bash",
        tool_use_id="recorder-shell", tool_input={"command": "git status"},
    ), tmp_path, PROTOCOL)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "read-only" in denied["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(pending(tmp_path)) == before

    assert H.handle(event(
        "PreToolUse", agent_id="rec-1", agent_type="fork",
        tool_name="mcp__plugin_research-trace_trace__trace_record",
        tool_use_id="recorder-trace", tool_input={"title": "safe"},
    ), tmp_path, PROTOCOL) is None
    assert len(pending(tmp_path)) == before

    H.handle(event(
        "PostToolUse", agent_id="worker-2", agent_type="Explore", tool_name="Read",
        tool_use_id="inside-worker", tool_input={"file_path": "/work/data.csv"},
        tool_response={"ok": True},
    ), tmp_path, PROTOCOL)
    assert len(pending(tmp_path)) == before + 1


def test_clear_forgets_an_unaddressable_old_recorder(tmp_path: Path):
    H.handle(event(
        "PreToolUse", tool_name="Agent", tool_input={"prompt": H.RECORDER_MARKER}
    ), tmp_path, PROTOCOL)
    H.handle(event("SubagentStart", agent_id="old-recorder", agent_type="fork"), tmp_path, PROTOCOL)
    H.handle(event("SessionStart", source="clear"), tmp_path, PROTOCOL)
    state = json.loads((session_root(tmp_path) / "state.json").read_text(encoding="utf-8"))
    assert "recorder_agent_id" not in state


def test_transcript_content_is_copied_incrementally_into_the_batch(tmp_path: Path):
    transcript = tmp_path / "claude-session.jsonl"
    transcript.write_text('{"type":"user","message":"first"}\n', encoding="utf-8")
    payload = event("UserPromptSubmit", transcript_path=str(transcript), prompt="first")
    H.handle(payload, tmp_path / "plugin-data", PROTOCOL)
    transcript.write_text(
        transcript.read_text(encoding="utf-8") + '{"type":"assistant","message":"second"}\n',
        encoding="utf-8",
    )
    output = H.handle(
        event(
            "Stop", transcript_path=str(transcript), stop_hook_active=False,
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
        event("Stop", transcript_path=str(transcript)),
        state,
        chunk_size=10,
    )
    assert len(chunks) == 2
    assert [
        (outbox / item["path"]).read_text(encoding="utf-8") for item in chunks
    ] == ['{"message":"批次效应"}\n', '{"message":"修正方案"}\n']
