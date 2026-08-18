#!/usr/bin/env python3
"""Claude Code hooks for durable Research Trace event capture.

The hook never writes research records itself.  It stages immutable raw events in
CLAUDE_PLUGIN_DATA and, once per main turn, asks Claude to hand one batch to a
forked recorder.  All failures are fail-open: research work must not be blocked
because the recorder or the central service is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = "research-trace.event.v1"
RECORDER_MARKER = "[research-trace-recorder]"
BATCH_MARKER = "[research-trace-batch:"
FINAL_RECEIPT_STATUSES = {"stored", "local", "ignored"}
RECEIPT_RE = re.compile(r"^TRACE_RECEIPT\s+(\{.*\})\s*$", re.MULTILINE)
RECORDER_READ_TOOLS = {"Read", "Grep", "Glob"}
RECORDER_TRACE_TOOLS = {
    "trace_context", "trace_ingest", "trace_record", "trace_curate", "trace_attach", "trace_search",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return (text or fallback)[:120]


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{uuid.uuid4().hex}.tmp"
    temp.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{uuid.uuid4().hex}.tmp"
    temp.write_bytes(value)
    os.replace(temp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _session_root(data_dir: Path, payload: dict[str, Any]) -> Path:
    cwd = str(payload.get("cwd") or os.getcwd())
    project = hashlib.sha256(os.path.normcase(cwd).encode("utf-8")).hexdigest()[:16]
    session = _safe(payload.get("session_id"), "unknown-session")
    root = data_dir / "outbox" / project / session
    for name in (
        "pending", "awaiting_upload", "sent", "batches", "batches/done",
        "transcripts/pending", "transcripts/awaiting_upload", "transcripts/sent",
        "transcripts/meta",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def _state_lock(root: Path, timeout: float = 2.0) -> Iterator[bool]:
    """A tiny cross-platform lock based on atomic directory creation."""
    lock = root / ".state-lock"
    deadline = time.monotonic() + timeout
    acquired = False
    while time.monotonic() < deadline:
        try:
            lock.mkdir()
            acquired = True
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.rmdir()
                    continue
            except OSError:
                pass
            time.sleep(0.025)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.rmdir()
            except OSError:
                pass


def _write_event(root: Path, payload: dict[str, Any]) -> Path:
    event_id = f"claude-{uuid.uuid4().hex}"
    record = {
        "schema": SCHEMA,
        "event_id": event_id,
        "captured_at": _now(),
        "source": "claude-code",
        "session_id": payload.get("session_id"),
        "project_dir": payload.get("cwd"),
        "hook_event": payload.get("hook_event_name"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "payload": payload,
    }
    stamp = time.time_ns()
    path = root / "pending" / f"{stamp}_{event_id}.json"
    _atomic_json(path, record)
    return path


def _capture_transcripts(
    root: Path, payload: dict[str, Any], state: dict[str, Any], chunk_size: int = 512 * 1024
) -> list[dict[str, Any]]:
    """Copy newly appended transcript JSONL records into immutable outbox chunks.

    Hook payloads contain transcript paths, not durable transcript content.  A cursor per
    physical transcript keeps this cheap while ensuring `/clear`, process exit, or later
    host cleanup cannot remove the only copy before central ingest.
    """
    candidates: list[tuple[str, str | None]] = []
    main_path = payload.get("transcript_path")
    if isinstance(main_path, str) and main_path.strip():
        candidates.append((main_path, None))
    agent_path = payload.get("agent_transcript_path")
    if isinstance(agent_path, str) and agent_path.strip():
        candidates.append((agent_path, str(payload.get("agent_id") or "") or None))

    cursors = state.setdefault("transcript_offsets", {})
    if not isinstance(cursors, dict):
        cursors = {}
        state["transcript_offsets"] = cursors
    captured: list[dict[str, Any]] = []
    for source_text, agent_id in candidates:
        source = Path(source_text).expanduser()
        key = hashlib.sha256(os.path.normcase(str(source)).encode("utf-8")).hexdigest()[:20]
        try:
            size = source.stat().st_size
        except OSError:
            continue
        start = int(cursors.get(key, 0) or 0)
        if start < 0 or start > size:
            start = 0
        if start == size:
            continue
        try:
            with source.open("rb") as stream:
                stream.seek(start)
                offset = start
                while offset < size:
                    parts: list[bytes] = []
                    part_size = 0
                    while offset + part_size < size and part_size < chunk_size:
                        line = stream.readline()
                        if not line:
                            break
                        parts.append(line)
                        part_size += len(line)
                    raw = b"".join(parts)
                    if not raw:
                        break
                    end = offset + len(raw)
                    digest = hashlib.sha256(raw).hexdigest()
                    chunk_id = f"claude-transcript-{digest}"
                    filename = f"{key}_{offset:016d}_{end:016d}_{digest[:16]}.jsonl"
                    destination = root / "transcripts" / "pending" / filename
                    if not destination.exists():
                        _atomic_bytes(destination, raw)
                    metadata = {
                        "path": f"transcripts/pending/{filename}",
                        "chunk_id": chunk_id,
                        "session_id": payload.get("session_id"),
                        "agent_id": agent_id,
                        "source_path": source_text,
                        "start_offset": offset,
                        "end_offset": end,
                        "sha256": digest,
                    }
                    _atomic_json(root / "transcripts" / "meta" / f"{filename}.json", metadata)
                    captured.append(metadata)
                    offset = end
                cursors[key] = offset
        except OSError:
            continue
    return captured


def _extract_agent_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("agent_id", "agentId"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        for child in value.values():
            found = _extract_agent_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _extract_agent_id(child)
            if found:
                return found
    elif isinstance(value, str):
        match = re.search(r"\bagent[-_ ]?id\b[^A-Za-z0-9_-]*([A-Za-z0-9_-]{6,})", value, re.I)
        if match:
            return match.group(1)
    return None


def _receipt_from_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, str):
        return None
    matches = list(RECEIPT_RE.finditer(message))
    if not matches:
        return None
    try:
        value = json.loads(matches[-1].group(1))
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    batch_id = _safe(value.get("batch_id"), "")
    status = str(value.get("status") or "").lower()
    if not batch_id or status not in FINAL_RECEIPT_STATUSES | {"retry"}:
        return None
    value["batch_id"] = batch_id
    value["status"] = status
    value["received_at"] = _now()
    return value


def _reconcile_receipts(root: Path) -> None:
    done = root / "batches" / "done"
    for manifest_path in sorted((root / "batches").glob("*.json")):
        if manifest_path.name.endswith(".receipt.json"):
            continue
        receipt_path = manifest_path.with_name(manifest_path.stem + ".receipt.json")
        if not receipt_path.is_file():
            continue
        receipt = _read_json(receipt_path, {})
        if receipt.get("status") not in FINAL_RECEIPT_STATUSES:
            continue
        manifest = _read_json(manifest_path, {})
        archive_dir = "sent" if receipt.get("status") == "stored" else "awaiting_upload"
        archived_events: list[str] = []
        for rel in manifest.get("events", []):
            candidate = Path(str(rel))
            if candidate.is_absolute() or ".." in candidate.parts or candidate.parts[:1] != ("pending",):
                continue
            source = root / candidate
            if source.is_file():
                destination = root / archive_dir / source.name
                os.replace(source, destination)
                archived_events.append(f"{archive_dir}/{source.name}")
        archived_chunks: list[dict[str, Any]] = []
        for item in manifest.get("transcript_chunks", []):
            if not isinstance(item, dict):
                continue
            candidate = Path(str(item.get("path") or ""))
            if (
                candidate.is_absolute() or ".." in candidate.parts
                or candidate.parts[:2] != ("transcripts", "pending")
            ):
                continue
            source = root / candidate
            if source.is_file():
                destination = root / "transcripts" / archive_dir / source.name
                os.replace(source, destination)
                archived = dict(item)
                archived["path"] = f"transcripts/{archive_dir}/{source.name}"
                archived_chunks.append(archived)
                meta = root / "transcripts" / "meta" / f"{source.name}.json"
                try:
                    meta.unlink()
                except OSError:
                    pass
        manifest["receipt_status"] = receipt.get("status")
        manifest["archived_events"] = archived_events
        manifest["archived_transcript_chunks"] = archived_chunks
        _atomic_json(done / manifest_path.name, manifest)
        manifest_path.unlink()
        os.replace(receipt_path, done / receipt_path.name)


def _open_manifests(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((root / "batches").glob("*.json")):
        if path.name.endswith(".receipt.json"):
            continue
        value = _read_json(path, {})
        if isinstance(value, dict) and value.get("batch_id"):
            out.append((path, value))
    return out


def _ensure_batch(root: Path, payload: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    open_batches = _open_manifests(root)
    already_batched = {
        str(rel)
        for _, manifest in open_batches
        for rel in manifest.get("events", [])
    }
    waiting = [
        path for path in sorted((root / "pending").glob("*.json"))
        if f"pending/{path.name}" not in already_batched
    ]
    already_batched_chunks = {
        str(item.get("path"))
        for _, manifest in open_batches
        for item in manifest.get("transcript_chunks", [])
        if isinstance(item, dict)
    }
    waiting_chunks: list[dict[str, Any]] = []
    for path in sorted((root / "transcripts" / "pending").glob("*.jsonl")):
        rel = f"transcripts/pending/{path.name}"
        if rel in already_batched_chunks:
            continue
        metadata = _read_json(root / "transcripts" / "meta" / f"{path.name}.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["path"] = rel
        waiting_chunks.append(metadata)
    if waiting or waiting_chunks:
        batch_id = f"{int(time.time())}-{uuid.uuid4().hex[:10]}"
        manifest = {
            "schema": "research-trace.batch.v1",
            "batch_id": batch_id,
            "created_at": _now(),
            "session_id": payload.get("session_id"),
            "project_dir": payload.get("cwd"),
            "event_count": len(waiting),
            "events": [f"pending/{path.name}" for path in waiting],
            "transcript_chunk_count": len(waiting_chunks),
            "transcript_chunks": waiting_chunks,
        }
        manifest_path = root / "batches" / f"{batch_id}.json"
        _atomic_json(manifest_path, manifest)
        open_batches.append((manifest_path, manifest))
    return open_batches[0] if open_batches else None


def _is_trace_orchestration(
    root: Path, payload: dict[str, Any], state: dict[str, Any]
) -> bool:
    event = str(payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")
    tool_blob = _json_text(payload.get("tool_input"))

    if event == "SessionStart" and payload.get("source") == "clear":
        state.pop("recorder_agent_id", None)
        state.pop("pending_recorder_spawn", None)

    if event == "PreToolUse" and tool == "Agent" and RECORDER_MARKER in tool_blob:
        state["pending_recorder_spawn"] = _now()
        return True

    if event == "SubagentStart" and state.get("pending_recorder_spawn"):
        agent_id = payload.get("agent_id")
        if agent_id:
            state["recorder_agent_id"] = str(agent_id)
            state.pop("pending_recorder_spawn", None)
            return True

    if event == "PostToolUse" and tool == "Agent" and RECORDER_MARKER in tool_blob:
        agent_id = _extract_agent_id(payload.get("tool_response"))
        if agent_id:
            state["recorder_agent_id"] = agent_id
        state.pop("pending_recorder_spawn", None)
        return True

    if tool == "SendMessage" and BATCH_MARKER in tool_blob:
        if event == "PostToolUseFailure":
            state.pop("recorder_agent_id", None)
        return True

    receipt = _receipt_from_message(payload.get("last_assistant_message"))
    if event == "SubagentStop" and receipt:
        batch_id = receipt["batch_id"]
        manifest = root / "batches" / f"{batch_id}.json"
        if manifest.is_file():
            receipt_path = manifest.with_name(manifest.stem + ".receipt.json")
            receipt["agent_id"] = payload.get("agent_id")
            _atomic_json(receipt_path, receipt)
            if payload.get("agent_id"):
                state["recorder_agent_id"] = str(payload["agent_id"])
        return True

    recorder_id = state.get("recorder_agent_id")
    if recorder_id and str(payload.get("agent_id") or "") == str(recorder_id):
        return True
    if event == "Stop" and payload.get("stop_hook_active"):
        return True
    return False


def _recorder_running(payload: dict[str, Any], recorder_id: str | None) -> bool:
    if not recorder_id:
        return False
    for task in payload.get("background_tasks") or []:
        if isinstance(task, dict) and str(task.get("id") or "") == recorder_id:
            return str(task.get("status") or "").lower() in {"running", "pending"}
    return False


def _recorder_tool_guard(
    payload: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any] | None:
    """Deny mutation and unrelated tools inside the full-context Recorder fork.

    A fork intentionally inherits the main agent's tools for context/cache parity. The hook is
    therefore the enforcement boundary: the Recorder may inspect existing material and write only
    through the Research Trace MCP tools.
    """
    if payload.get("hook_event_name") != "PreToolUse":
        return None
    recorder_id = str(state.get("recorder_agent_id") or "")
    if not recorder_id or str(payload.get("agent_id") or "") != recorder_id:
        return None
    tool = str(payload.get("tool_name") or "")
    tool_basename = tool.rsplit("__", 1)[-1]
    if tool in RECORDER_READ_TOOLS or tool_basename in RECORDER_TRACE_TOOLS:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Research Trace Recorder is read-only outside the trace MCP; "
                f"tool {tool or '<unknown>'} is not allowed"
            ),
        }
    }


def _nudge(
    manifest_path: Path,
    manifest: dict[str, Any],
    recorder_id: str | None,
    protocol_path: Path,
) -> dict[str, Any]:
    batch_id = manifest["batch_id"]
    batch_message = (
        f"{BATCH_MARKER}{batch_id}] Read the batch manifest at {manifest_path}. "
        f"Follow the recorder protocol at {protocol_path}. Process this batch without asking "
        "the user, then end with the required one-line TRACE_RECEIPT JSON."
    )
    if recorder_id:
        action = (
            f"Use SendMessage once with to={recorder_id!r} and this message:\n{batch_message}"
        )
    else:
        action = (
            "Spawn one background Agent with subagent_type='fork'. Its task prompt must begin "
            f"with {RECORDER_MARKER!r} and then contain:\n{batch_message}\n"
            "A fork is required because it receives the main agent's complete current context."
        )
    guidance = (
        "Research Trace has durably queued a recorder batch. Do not summarize or interpret the "
        "batch in the main context. " + action + " After dispatching it once, stop without adding "
        "a user-facing trace message and do not wait for the background recorder. If fork or "
        "SendMessage is unavailable, do not retry this turn; the batch remains safely queued."
    )
    # Stop hooks do not support hookSpecificOutput.additionalContext.  The documented
    # continuation mechanism is a top-level block decision whose reason becomes the
    # agent's next instruction.  stop_hook_active prevents a second continuation.
    return {"decision": "block", "reason": guidance}


def handle(payload: dict[str, Any], data_dir: Path, protocol_path: Path) -> dict[str, Any] | None:
    """Handle one hook input. Exposed separately for deterministic tests."""
    root = _session_root(data_dir, payload)
    state_path = root / "state.json"
    with _state_lock(root) as acquired:
        if not acquired:
            # Unique event files remain safe without the state lock. Avoid nudging because
            # batching while another process owns state could duplicate a batch.
            _write_event(root, payload)
            return None
        state = _read_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        _capture_transcripts(root, payload, state)
        internal = _is_trace_orchestration(root, payload, state)
        if not internal:
            _write_event(root, payload)
        _reconcile_receipts(root)

        result = _recorder_tool_guard(payload, state)
        if result is None and payload.get("hook_event_name") == "Stop" and not payload.get("stop_hook_active"):
            selected = _ensure_batch(root, payload)
            recorder_id = state.get("recorder_agent_id")
            if selected and not _recorder_running(payload, recorder_id):
                result = _nudge(selected[0], selected[1], recorder_id, protocol_path)
        state["updated_at"] = _now()
        _atomic_json(state_path, state)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--capture-enabled", default="on")
    args = parser.parse_args(argv)
    if str(args.capture_enabled).strip().lower() in {"0", "false", "off", "no"}:
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        output = handle(payload, Path(args.data_dir), Path(args.protocol))
        if output:
            print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:  # fail-open by design; stderr is debug-only on exit 0
        print(f"research-trace hook capture failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
