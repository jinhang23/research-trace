from __future__ import annotations

import base64
import json

import pytest

from research_trace.storage import Conflict, Store, ValidationError


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
    # 机器承认「我读过并已并入」，但了结这条纠正只能是人的动作：
    # 让 Recorder 自己写 resolved_at，等于一次 curate 就把人的纠正从界面
    # 和后续 Recorder 上下文里抹掉（§3.4 / §4）。
    assert detail["comments"][0]["acknowledged_at"]
    assert detail["comments"][0]["acknowledged_by"] == "recorder"
    assert detail["comments"][0]["resolved_at"] is None
    assert store.context(project_id=project["id"])["project"]["unresolved_corrections"]


def test_a_recorder_acknowledgement_does_not_close_a_human_correction(tmp_path):
    """闸门不能靠回声绕过：Recorder 把 id 抄回来就能继续 curate（那是它必须
    声明「我读过了」的方式），但那条纠正对人仍然是未处理的，只有人能了结它。"""
    store = Store(tmp_path)
    project = store.create_project("P", overview="v1")
    correction = store.add_comment(
        project["id"], target_type="overview", target_id=None, kind="correction",
        body="这里错了", author_id="jinhang",
    )
    store.curate(project["id"], target_type="overview", body="v2", expect_version=1,
                 actor_type="recorder", resolve_comment_ids=[correction["id"]])
    # 已 acknowledge 过，第二轮不再被同一条挡住（否则 Recorder 会永久卡死）
    store.curate(project["id"], target_type="overview", body="v3", expect_version=2,
                 actor_type="recorder")
    assert store.get_project(project["id"])["comments"][0]["resolved_at"] is None
    store.resolve_comment(correction["id"], "jinhang")
    assert store.get_project(project["id"])["comments"][0]["resolved_at"]
    assert store.context(project_id=project["id"])["project"]["unresolved_corrections"] == []


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

def _store_with_noise(tmp_path):
    """一条 2020 年的语义 Node，加 80 条 2026 年的原始 event，都命中同一个词。"""
    store = Store(tmp_path)
    project = store.create_project("Project")
    store.record_node(
        project["id"], idempotency_key="conclusion",
        title="Batch effect conclusion",
        body="平台与年份混杂，结论是 batch effect 不可分离。",
        occurred_at="2020-01-01T00:00:00.000+00:00",
    )
    store.ingest(
        batch_id="noise",
        project_id=project["id"],
        session={"id": "s1", "source": "claude-code"},
        agents=[],
        events=[
            {
                "event_id": f"e{index}",
                "event_type": "PostToolUse",
                "captured_at": f"2026-08-18T12:{index // 60:02d}:{index % 60:02d}.000+00:00",
                "payload": {"command": f"grep batch effect {index}"},
            }
            for index in range(80)
        ],
    )
    return store, project


def test_semantic_records_are_not_drowned_by_raw_events(tmp_path):
    store, project = _store_with_noise(tmp_path)
    hits = store.search("batch effect", project_id=project["id"], limit=50)

    assert len(hits) == 50
    assert [hit["scope"] for hit in hits].count("node") == 1, "语义 Node 必须留在结果里"
    assert hits.totals == {"node": 1, "comment": 0, "overview": 0, "event": 80, "transcript": 0}
    assert hits.truncated is True
    assert hits.omitted["event"] == 31
    assert hits.as_dict()["returned"] == {"node": 1, "comment": 0, "overview": 0,
                                          "event": 49, "transcript": 0}


def test_search_gives_unused_quota_back_and_reports_no_truncation(tmp_path):
    store, project = _store_with_noise(tmp_path)
    raw_only = store.search("grep", project_id=project["id"], limit=10)
    assert {hit["scope"] for hit in raw_only} == {"event"}
    assert len(raw_only) == 10, "语义层没有命中时，名额应全部让给原始层"
    assert raw_only.truncated is True

    semantic_only = store.search("不可分离", project_id=project["id"], limit=30)
    assert [hit["scope"] for hit in semantic_only] == ["node"]
    assert semantic_only.truncated is False
    assert semantic_only.total == 1


def test_search_scope_semantic_never_returns_raw_history(tmp_path):
    store, project = _store_with_noise(tmp_path)
    hits = store.search("batch effect", project_id=project["id"], scope="semantic", limit=50)
    assert [hit["scope"] for hit in hits] == ["node"]
    assert "event" not in hits.totals


def test_like_wildcards_in_the_query_are_matched_literally(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    store.record_node(project["id"], idempotency_key="n1", title="100% coverage reached")
    store.record_node(project["id"], idempotency_key="n2", title="100 tests reached")
    store.record_node(project["id"], idempotency_key="n3", title="a_c literal underscore")
    store.record_node(project["id"], idempotency_key="n4", title="abc other")

    percent = store.search("100%", project_id=project["id"])
    assert [hit["title"] for hit in percent] == ["100% coverage reached"]
    underscore = store.search("a_c", project_id=project["id"])
    assert [hit["title"] for hit in underscore] == ["a_c literal underscore"]


def test_reused_event_id_with_different_content_is_reported_not_silently_dropped(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    store.ingest(
        batch_id="b1", project_id=project["id"], session={"id": "s1"}, agents=[],
        events=[{"event_id": "e1", "event_type": "Stop", "payload": {"text": "first"}}],
        transcript_chunks=[{"chunk_id": "c1", "content": "first transcript"}],
    )
    result = store.ingest(
        batch_id="b2", project_id=project["id"], session={"id": "s1"}, agents=[],
        events=[
            {"event_id": "e1", "event_type": "Stop", "payload": {"text": "REWRITTEN"}},
            {"event_id": "e2", "event_type": "Stop", "payload": {"text": "new"}},
        ],
        transcript_chunks=[{"chunk_id": "c1", "content": "REWRITTEN transcript"}],
    )

    assert result["event_count"] == 1
    assert result["duplicate_event_count"] == 1
    assert result["conflicting_event_ids"] == ["e1"]
    assert result["conflicting_transcript_chunk_ids"] == ["c1"]
    assert store.search("REWRITTEN", project_id=project["id"]) == [], "已存内容不得被覆盖"


def test_admin_purge_removes_content_objects_and_leaves_a_contentless_audit(tmp_path):
    secret = "sk-live-DO-NOT-KEEP-THIS"
    store = Store(tmp_path)
    project = store.create_project("Project", overview=f"token {secret}")
    node = store.record_node(
        project["id"], idempotency_key="n1", title="Leak", body=f"printed {secret}"
    )
    attachment = store.attach(
        project["id"], target_type="node", target_id=node["id"], name="log.txt",
        data_base64=base64.b64encode(secret.encode()).decode("ascii"),
    )
    store.ingest(
        batch_id="b1", project_id=project["id"], session={"id": "s1"}, agents=[],
        events=[{"event_id": "e1", "event_type": "Bash", "payload": {"command": secret}}],
        transcript_chunks=[{"chunk_id": "c1", "content": secret}],
    )
    object_path, _mime, _name = store.attachment_content(attachment["id"])
    assert object_path.is_file()

    result = store.purge(
        actor_id="admin-jinhang", reason="令牌泄漏，紧急清除", project_ids=[project["id"]]
    )

    assert result["purge_generation"] == 1
    assert result["objects_removed"] == 1
    assert not object_path.exists()
    counts = store.health()["counts"]
    assert counts["projects"] == 0 and counts["nodes"] == 0
    assert counts["events"] == 0 and counts["transcript_chunks"] == 0
    assert counts["attachments"] == 0
    assert store.search(secret) == []

    entry = store.purge_log()[0]
    assert entry["actor_id"] == "admin-jinhang"
    assert entry["selector"]["project_ids"] == [project["id"]]
    assert entry["removed"]["events"] == 1
    assert entry["removed"]["sessions"] == 1 and entry["removed"]["nodes"] == 1
    assert secret not in json.dumps(entry, ensure_ascii=False), "审计记录不得含被删原文"
    assert store.health()["purge_generation"] == 1


def test_purge_can_target_one_session_and_refuses_unbounded_or_unexplained_calls(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Project")
    store.record_node(project["id"], idempotency_key="keep", title="Keep this conclusion")
    for name in ("s1", "s2"):
        store.ingest(
            batch_id=f"b-{name}", project_id=project["id"], session={"id": name}, agents=[],
            events=[{"event_id": f"e-{name}", "event_type": "Bash",
                     "payload": {"command": f"secret in {name}"}}],
            transcript_chunks=[{"chunk_id": f"c-{name}", "content": f"secret in {name}"}],
        )

    store.purge(actor_id="admin", reason="只清一个会话", session_ids=["s1"])
    counts = store.health()["counts"]
    assert counts["events"] == 1 and counts["transcript_chunks"] == 1
    assert counts["nodes"] == 1, "别的会话与语义记录不受影响"
    assert [hit["id"] for hit in store.search("secret in", project_id=project["id"])] == ["e-s2"] or \
           {hit["id"] for hit in store.search("secret in", project_id=project["id"])} == {"e-s2", "c-s2"}

    with pytest.raises(ValidationError, match="selector"):
        store.purge(actor_id="admin", reason="没有选择器")
    with pytest.raises(ValidationError, match="reason"):
        store.purge(actor_id="admin", reason="", project_ids=[project["id"]])
    with pytest.raises(ValidationError, match="actor_id"):
        store.purge(actor_id="", reason="缺少操作者", project_ids=[project["id"]])


def test_new_columns_are_added_to_a_database_created_before_this_round(tmp_path):
    """CREATE TABLE IF NOT EXISTS 对已存在的表什么也不做：老库必须被真正补列，
    否则升级后第一次写就是 sqlite3.OperationalError。"""
    import sqlite3

    store = Store(tmp_path)
    store.close()
    database = tmp_path / "trace.sqlite3"
    raw = sqlite3.connect(database)
    for table, column in (
        ("comments", "acknowledged_at"), ("comments", "acknowledged_by"),
        ("device_credentials", "expires_at"), ("ingest_batches", "delivered_by"),
    ):
        raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    raw.commit()
    raw.close()

    store = Store(tmp_path)
    for table, column in (
        ("comments", "acknowledged_at"), ("comments", "acknowledged_by"),
        ("device_credentials", "expires_at"), ("ingest_batches", "delivered_by"),
    ):
        names = {row["name"] for row in store._db.execute(f"PRAGMA table_info({table})")}
        assert column in names, f"{table}.{column} was not migrated"
    project = store.create_project("P")
    store.ingest(batch_id="b", project_id=project["id"], session=None, agents=[],
                 events=[{"event_id": "e", "event_type": "Stop", "payload": {}}],
                 delivered_by="alice@node")
    store.close()


def test_revoking_a_user_clears_sessions_and_devices_without_disabling_them(tmp_path):
    """移出白名单的人在请求时已经被挡住，但数据库里的行会躺到自然过期。
    update_auth_user(disabled=True) 是唯一的全撤手段，它还会对最后一个管理员报错。"""
    store = Store(tmp_path)
    user = store.upsert_github_user({"id": 1, "login": "alice"}, default_role="admin")
    store.create_web_session(user["id"], "raw-session-secret-" + "z" * 40, "2099-01-01T00:00:00.000+00:00")
    started = store.start_device_authorization("laptop")
    store.approve_device_authorization(started["user_code"], user["id"])
    issued = store.exchange_device_authorization(started["device_code"])
    assert store.device_credential_identity(issued["credential"])

    result = store.revoke_user_credentials(user["id"])
    assert result["sessions_removed"] == 1 and result["devices_revoked"] == 1
    assert store.device_credential_identity(issued["credential"]) is None
    assert store.web_session_user("raw-session-secret-" + "z" * 40) is None
    # 撤凭证不等于禁用账号：这个人重新被加回白名单后照常能登录
    assert store.list_auth_users()[0]["disabled"] is False
    store.close()


def test_a_machine_cannot_patch_over_a_human_edit_even_with_the_right_version(tmp_path):
    """record_node 早有这道闸，update_node 没有——版本号只防"并发丢更新"，
    防不住"机器有权改人的定稿"（§15）。"""
    store = Store(tmp_path)
    project = store.create_project("P")
    node = store.record_node(project["id"], idempotency_key="k", title="draft", body="recorder")
    edited = store.update_node(
        node["id"], {"body": "人写的结论"}, expect_version=node["version"],
        actor_type="human", actor_id="jinhang",
    )
    with pytest.raises(Conflict, match="human revision"):
        store.update_node(
            node["id"], {"body": "机器覆盖"}, expect_version=edited["version"],
            actor_type="recorder", actor_id="alice@hpg",
        )
    current = store.get_project(project["id"])["nodes"][0]
    assert current["body"] == "人写的结论"
    # 人自己继续改当然可以
    assert store.update_node(
        node["id"], {"body": "再改一次"}, expect_version=edited["version"],
        actor_type="human", actor_id="jinhang",
    )["body"] == "再改一次"
