from __future__ import annotations

import base64

import pytest

from research_trace_v2.backup import export_backup, restore_backup, verify_backup
from research_trace_v2.storage import Store, ValidationError


def populated_store(path):
    store = Store(path)
    project = store.create_project("RNA project", overview="Current understanding")
    chapter = store.create_chapter(project["id"], "Data understanding")
    first = store.record_node(
        project["id"], idempotency_key="n1", chapter_id=chapter["id"],
        title="Inspect counts", body="Found a batch-shaped pattern.",
    )
    store.record_node(
        project["id"], idempotency_key="n2", chapter_id=chapter["id"],
        parent_id=first["id"], title="Check confounding",
    )
    store.attach(
        project["id"], target_type="node", target_id=first["id"], name="pca.png",
        data_base64=base64.b64encode(b"image bytes").decode("ascii"), mime_type="image/png",
    )
    store.ingest(
        batch_id="b1", project_id=project["id"],
        session={"id": "s1", "source": "claude-code"}, agents=[],
        events=[{"event_id": "e1", "event_type": "Stop", "payload": {"message": "done"}}],
        transcript_chunks=[{"chunk_id": "t1", "content": '{"message":"raw history"}\n'}],
    )
    return store, project


def test_backup_is_deterministic_verified_and_restores_an_empty_store(tmp_path):
    source, project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    first_manifest = export_backup(source, target)
    first_bytes = {
        str(path.relative_to(target)): path.read_bytes()
        for path in target.rglob("*") if path.is_file()
    }
    second_manifest = export_backup(source, target)
    second_bytes = {
        str(path.relative_to(target)): path.read_bytes()
        for path in target.rglob("*") if path.is_file()
    }
    assert first_manifest == second_manifest
    assert first_bytes == second_bytes
    verify_backup(target)

    restored = Store(tmp_path / "restored")
    result = restore_backup(target, restored)
    assert result["restored"] is True
    assert restored.health()["counts"] == source.health()["counts"]
    detail = restored.get_project(project["id"])
    assert [node["title"] for node in detail["nodes"]] == ["Inspect counts", "Check confounding"]
    assert detail["nodes"][1]["parent_id"] == detail["nodes"][0]["id"]
    assert restored.search("raw history", project_id=project["id"])[0]["scope"] == "transcript"


def test_backup_verification_rejects_tampering_and_restore_rejects_nonempty_store(tmp_path):
    source, _project = populated_store(tmp_path / "source")
    target = tmp_path / "backup"
    export_backup(source, target)
    projects = target / "tables" / "projects.jsonl"
    projects.write_bytes(projects.read_bytes() + b"tamper")
    with pytest.raises(ValidationError, match="checksum"):
        verify_backup(target)

    export_backup(source, target)
    occupied = Store(tmp_path / "occupied")
    occupied.create_project("Already here")
    with pytest.raises(ValidationError, match="empty"):
        restore_backup(target, occupied)
