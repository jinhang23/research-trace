from __future__ import annotations

import json

from research_trace_v2.mcp import TOOLS, _manifest_payload


def test_v2_mcp_exposes_six_research_tools_plus_device_login():
    assert [tool["name"] for tool in TOOLS] == [
        "trace_context", "trace_ingest", "trace_record", "trace_curate",
        "trace_attach", "trace_search", "trace_login",
    ]


def test_manifest_loader_reads_raw_files_without_model_transcription(tmp_path):
    root = tmp_path / "session"
    (root / "batches").mkdir(parents=True)
    (root / "pending").mkdir()
    (root / "transcripts" / "pending").mkdir(parents=True)
    event = {
        "event_id": "event-1", "session_id": "session-1", "agent_id": "agent-1",
        "agent_type": "fork", "hook_event": "PostToolUse", "payload": {"ok": True},
    }
    (root / "pending" / "event.json").write_text(json.dumps(event), encoding="utf-8")
    (root / "transcripts" / "pending" / "chunk.jsonl").write_text(
        '{"message":"verbatim"}\n', encoding="utf-8"
    )
    manifest = {
        "batch_id": "batch-1",
        "session_id": "session-1",
        "project_dir": "/work/project",
        "events": ["pending/event.json"],
        "transcript_chunks": [{
            "path": "transcripts/pending/chunk.jsonl", "chunk_id": "chunk-1",
            "session_id": "session-1", "agent_id": "agent-1",
        }],
    }
    path = root / "batches" / "batch-1.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    value = _manifest_payload(str(path), "project-1")
    assert value["events"][0]["event_id"] == "event-1"
    assert value["transcript_chunks"][0]["content"].endswith("verbatim\"}\n")
    assert value["agents"][0]["id"] == "agent-1"
