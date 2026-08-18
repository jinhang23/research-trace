from __future__ import annotations

import base64

import pytest

from research_trace_v2.storage import Conflict, Store, ValidationError


def test_project_chapters_general_nodes_and_idempotency(tmp_path):
    store = Store(tmp_path)
    project = store.create_project(
        "RNA processing",
        workspace_keys=["git@github.com:lab/rna-pipeline.git"],
        overview="Current understanding",
    )
    assert project["chapters"][0]["name"] == "Inbox"

    context = store.context(workspace_keys=["https://github.com/lab/rna-pipeline"])
    assert context["matched"] is True
    assert context["project"]["id"] == project["id"]

    chapter = store.create_chapter(project["id"], "主实验")
    evidence = [{
        "file_path": "src/qc.py",
        "symbol": "detect_batch",
        "snippet": "def detect_batch(x): ...",
        "annotation": "关键 QC 入口",
        "attribution": "ambiguous",
        "contributor_agent_ids": ["agent-b", "agent-a"],
    }]
    node = store.record_node(
        project["id"],
        idempotency_key="semantic:batch-1:0",
        chapter_id=chapter["id"],
        title="发现 batch effect",
        body="PCA 按批次分离；来源仍是假设。",
        labels=["data", "hypothesis"],
        source_event_ids=["e2", "e1"],
        code_evidence=evidence,
    )
    assert node["version"] == 1
    assert node["code_evidence"][0]["attribution"] == "ambiguous"

    retry = store.record_node(
        project["id"],
        idempotency_key="semantic:batch-1:0",
        chapter_id=chapter["id"],
        title="发现 batch effect",
        body="PCA 按批次分离；来源仍是假设。",
        labels=["hypothesis", "data"],
        occurred_at=node["occurred_at"],
        source_event_ids=["e1", "e2"],
        code_evidence=evidence,
    )
    assert retry["id"] == node["id"]
    assert retry["version"] == 1, "an at-least-once retry must not create a revision"

    changed = store.record_node(
        project["id"],
        idempotency_key="semantic:batch-1:0",
        chapter_id=chapter["id"],
        title="发现 batch effect",
        body="PCA 按批次分离；测序平台与年份混杂。",
        labels=["data", "hypothesis"],
        occurred_at=node["occurred_at"],
        source_event_ids=["e1", "e2"],
        code_evidence=evidence,
    )
    assert changed["version"] == 2
    assert len(store.revisions("node", node["id"])) == 2


def test_human_correction_blocks_recorder_overview_overwrite(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project", overview="假设：差异来自平台。")
    correction = store.add_comment(
        project["id"],
        target_type="overview",
        target_id=None,
        kind="correction",
        body="年份与平台完全混杂，不能下这个结论。",
        author_id="jinhang",
    )
    with pytest.raises(Conflict, match="corrections"):
        store.curate(
            project["id"],
            target_type="overview",
            body="差异确定来自平台。",
            expect_version=1,
            actor_type="recorder",
        )

    curated = store.curate(
        project["id"],
        target_type="overview",
        body="存在 batch effect；来源尚未区分。",
        expect_version=1,
        actor_type="recorder",
        resolve_comment_ids=[correction["id"]],
    )
    assert curated["version"] == 2
    detail = store.get_project(project["id"])
    assert detail["overview"] == "存在 batch effect；来源尚未区分。"
    assert detail["comments"][0]["resolved_at"]


def test_version_conflict_prevents_silent_human_overwrite(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    node = store.record_node(
        project["id"], idempotency_key="n1", title="Initial", body="one"
    )
    updated = store.update_node(node["id"], {"body": "two"}, expect_version=1)
    assert updated["version"] == 2
    with pytest.raises(Conflict):
        store.update_node(node["id"], {"body": "stale"}, expect_version=1)


def test_recorder_uses_only_human_created_chapters_and_cannot_self_confirm(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    main = store.create_chapter(project["id"], "主实验")

    with pytest.raises(ValidationError, match="human-created"):
        store.record_node(
            project["id"], idempotency_key="n-unknown", title="Ablation result",
            chapter_name="AI 自己发明的章节",
        )

    node = store.record_node(
        project["id"], idempotency_key="n-main", title="Primary result",
        chapter_id=main["id"], review_state="confirmed", created_by="recorder",
    )
    assert node["chapter_id"] == main["id"]
    assert node["review_state"] == "unreviewed"


def test_recorder_retry_cannot_undo_a_human_chapter_move_or_confirmation(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    main = store.create_chapter(project["id"], "主实验")
    ablation = store.create_chapter(project["id"], "消融实验")
    node = store.record_node(
        project["id"], idempotency_key="semantic:batch:0", title="Remove correction module",
        chapter_id=main["id"], body="Initial recorder placement.",
    )
    moved = store.update_node(
        node["id"], {"chapter_id": ablation["id"], "review_state": "confirmed"},
        expect_version=node["version"], actor_type="human", actor_id="researcher",
    )
    assert moved["chapter_id"] == ablation["id"]
    assert moved["review_state"] == "confirmed"

    with pytest.raises(Conflict, match="human revision"):
        store.record_node(
            project["id"], idempotency_key="semantic:batch:0", title="Remove correction module",
            chapter_id=main["id"], body="Initial recorder placement.",
            occurred_at=node["occurred_at"], created_by="recorder",
        )

    current = next(item for item in store.get_project(project["id"])["nodes"] if item["id"] == node["id"])
    assert current["chapter_id"] == ablation["id"]
    assert current["review_state"] == "confirmed"


def test_parent_relationship_cannot_form_a_cycle(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    first = store.record_node(project["id"], idempotency_key="n1", title="First")
    second = store.record_node(
        project["id"], idempotency_key="n2", title="Second", parent_id=first["id"]
    )
    with pytest.raises(ValidationError, match="cycle"):
        store.update_node(first["id"], {"parent_id": second["id"]}, expect_version=1)


def test_node_correction_is_itself_a_versioned_semantic_change(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    node = store.record_node(project["id"], idempotency_key="n1", title="Claim")
    store.add_comment(
        project["id"], target_type="node", target_id=node["id"], body="This is a hypothesis.",
        kind="correction", author_id="human",
    )
    changed = next(item for item in store.get_project(project["id"])["nodes"] if item["id"] == node["id"])
    assert changed["review_state"] == "corrected"
    assert changed["version"] == 2


def test_node_confirmation_is_a_human_revision_and_blocks_recorder_retry(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    main = store.create_chapter(project["id"], "主实验")
    node = store.record_node(
        project["id"], idempotency_key="semantic:confirm:0", chapter_id=main["id"],
        title="Primary result", body="AUC = 0.91",
    )
    store.add_comment(
        project["id"], target_type="node", target_id=node["id"], body="结果与原始输出一致。",
        kind="confirmation", author_id="human",
    )
    confirmed = next(item for item in store.get_project(project["id"])["nodes"] if item["id"] == node["id"])
    assert confirmed["review_state"] == "confirmed"
    assert confirmed["version"] == 2
    with pytest.raises(Conflict, match="human revision"):
        store.record_node(
            project["id"], idempotency_key="semantic:confirm:0", chapter_id=main["id"],
            title="Primary result", body="AUC = 0.91", occurred_at=node["occurred_at"],
        )
    assert len(store.revisions("node", node["id"])) == 2


def test_raw_ingest_is_append_only_searchable_and_batch_idempotent(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    value = store.ingest(
        batch_id="batch-1",
        project_id=project["id"],
        session={"id": "session-1", "source": "claude-code", "cwd": "/work/p"},
        agents=[{"id": "agent-1", "session_id": "session-1", "agent_type": "fork"}],
        events=[{
            "event_id": "event-1",
            "session_id": "session-1",
            "agent_id": "agent-1",
            "hook_event": "PostToolUse",
            "captured_at": "2026-08-18T12:00:00Z",
            "payload": {"command": "python qc.py", "result": "batch effect detected"},
        }],
        transcript_chunks=[{
            "chunk_id": "chunk-1",
            "session_id": "session-1",
            "agent_id": "agent-1",
            "content": '{"message":"read RNA counts"}\n',
        }],
    )
    assert value["event_count"] == 1
    assert value["transcript_chunk_count"] == 1
    duplicate = store.ingest(
        batch_id="batch-1", project_id=project["id"], session=None, agents=[], events=[]
    )
    assert duplicate["duplicate"] is True
    assert store.health()["counts"]["events"] == 1
    assert {hit["scope"] for hit in store.search("batch effect", project_id=project["id"])} == {"event"}
    assert {hit["scope"] for hit in store.search("RNA counts", project_id=project["id"])} == {"transcript"}
    timeline = store.raw_timeline(project["id"])
    assert {item["kind"] for item in timeline} == {"event", "transcript"}
    assert next(item for item in timeline if item["kind"] == "event")["agent_id"] == "agent-1"
    assert "RNA counts" in next(item for item in timeline if item["kind"] == "transcript")["preview"]


def test_content_addressed_attachment_and_external_artifact(tmp_path):
    store = Store(tmp_path, attachment_limit=100)
    project = store.create_project("Project")
    node = store.record_node(project["id"], idempotency_key="n1", title="Result")
    raw = b"small image bytes"
    attachment = store.attach(
        project["id"],
        target_type="node",
        target_id=node["id"],
        name="result.png",
        mime_type="image/png",
        data_base64=base64.b64encode(raw).decode("ascii"),
    )
    path, mime, name = store.attachment_content(attachment["id"])
    assert path.read_bytes() == raw
    assert (mime, name) == ("image/png", "result.png")
    external = store.attach(
        project["id"],
        target_type="node",
        target_id=node["id"],
        name="checkpoint",
        direction="output",
        machine="hpg-b200",
        external_path="/blue/lab/model.ckpt",
        size=123,
        sha256="abc",
    )
    assert external["object_path"] is None
    with pytest.raises(ValidationError):
        store.attach(
            project["id"], target_type="node", target_id=node["id"], name="bad"
        )
