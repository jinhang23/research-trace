from __future__ import annotations

import base64
import json

import pytest

from research_trace.storage import Conflict, Store, ValidationError, artifact_keys


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


def test_history_delivered_before_binding_stops_being_orphaned(tmp_path):
    """§7 让 marker 的 project_id 等中央映射完成后才写，而 hook 从 marker 存在那一刻
    就开始采集。中间那批 batch 因此带着 project_id=None 投上来，而 events /
    transcript_chunks 是 INSERT OR IGNORE——写进去是 NULL 就永远是 NULL，
    `raw_timeline(project_id)` 从此看不到它们：历史还在库里，但对人不存在。
    同一个 session 之后拿到归属时必须把它们补上。"""
    store = Store(tmp_path)
    project = store.create_project("Late binding")
    store.ingest(
        batch_id="before-bind", project_id=None,
        session={"id": "session-9", "source": "claude-code"}, agents=[],
        events=[{"event_id": "orphan-1", "event_type": "Stop", "payload": {"note": "unattributed"}}],
        transcript_chunks=[{"chunk_id": "orphan-chunk", "content": '{"m":"unattributed"}\n'}],
    )
    assert store.raw_timeline(project["id"]) == []  # 归属之前确实看不到，这是对的

    store.ingest(
        batch_id="after-bind", project_id=project["id"],
        session={"id": "session-9", "source": "claude-code"}, agents=[],
        events=[{"event_id": "attributed-1", "event_type": "Stop", "payload": {"note": "bound"}}],
    )
    timeline = store.raw_timeline(project["id"])
    assert {item["id"] for item in timeline} == {"orphan-1", "orphan-chunk", "attributed-1"}

    # 只补空，不改写：另一个项目的历史不因为一次新 batch 被搬走
    other = store.create_project("Other")
    store.ingest(
        batch_id="other-project", project_id=other["id"],
        session={"id": "session-9", "source": "claude-code"}, agents=[],
        events=[{"event_id": "other-1", "event_type": "Stop", "payload": {}}],
    )
    assert {item["id"] for item in store.raw_timeline(other["id"])} == {"other-1"}
    assert "orphan-1" in {item["id"] for item in store.raw_timeline(project["id"])}
    store.close()


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


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def test_dataflow_edges_come_only_from_registered_output_and_input_keys(tmp_path):
    """§8：边只来自明确登记的 output/input，且必须共享一个可比对的键。"""
    store = Store(tmp_path)
    project = store.create_project("P")
    prepare = store.record_node(
        project["id"], idempotency_key="n1", title="预处理",
        occurred_at="2026-01-01T00:00:00.000+00:00",
    )
    train = store.record_node(
        project["id"], idempotency_key="n2", title="训练",
        occurred_at="2026-01-02T00:00:00.000+00:00",
    )
    reading = store.record_node(
        project["id"], idempotency_key="n3", title="读论文",
        occurred_at="2026-01-03T00:00:00.000+00:00",
    )
    store.attach(project["id"], target_type="node", target_id=prepare["id"], name="counts.parquet",
                 direction="output", sha256=DIGEST_A, uri="s3://lab/counts.parquet")
    store.attach(project["id"], target_type="node", target_id=train["id"], name="counts.parquet",
                 direction="input", sha256=DIGEST_A)
    # reference 既不是产出也不是消费：登记的人没有声明任何流向，不能凭它连边
    store.attach(project["id"], target_type="node", target_id=reading["id"], name="counts.parquet",
                 direction="reference", sha256=DIGEST_A)
    # 同一个 Node 原地读写同一份产物不是节点之间的流向，不产生自环
    store.attach(project["id"], target_type="node", target_id=train["id"], name="ckpt",
                 direction="output", sha256=DIGEST_B)
    store.attach(project["id"], target_type="node", target_id=train["id"], name="ckpt",
                 direction="input", sha256=DIGEST_B)

    flow = store.dataflow(project["id"])
    assert [(edge["from_node_id"], edge["to_node_id"], edge["key_kind"]) for edge in flow["edges"]] == [
        (prepare["id"], train["id"], "sha256")
    ]
    assert flow["edges"][0]["key"] == DIGEST_A
    assert {node["id"] for node in flow["nodes"]} == {prepare["id"], train["id"]}
    assert flow["stats"]["unkeyed"] == 0
    store.close()


def test_a_sha256_alone_is_a_valid_artifact_registration_but_a_name_alone_is_not(tmp_path):
    """RECORDER_PROTOCOL 让登记者给 sha256 **或** 规范化 uri；旧的校验只认位置，
    等于把最强的那个键拒之门外。只有名字的登记仍然要拒——它永远连不上任何边。"""
    store = Store(tmp_path)
    project = store.create_project("P")
    node = store.record_node(project["id"], idempotency_key="n1", title="产出")
    registered = store.attach(project["id"], target_type="node", target_id=node["id"],
                              name="model.ckpt", direction="output", sha256=DIGEST_A)
    assert registered["object_path"] is None and registered["sha256"] == DIGEST_A
    with pytest.raises(ValidationError, match="joined"):
        store.attach(project["id"], target_type="node", target_id=node["id"],
                     name="model.ckpt", direction="output", sha256="abc123")
    store.close()


def test_artifact_keys_only_merge_what_is_equal_by_definition():
    """规范化过头就是在猜（§8）。这条钉住"该合的合、判不了的不给键"的分界线。"""
    def keys(**fields):
        return dict(artifact_keys(fields))

    # scheme 与 host 按 RFC 3986 大小写不敏感；path 不是
    assert keys(uri="S3://Lab/Counts.parquet")["uri"] == keys(uri="s3://lab/Counts.parquet")["uri"]
    assert keys(uri="s3://lab/counts.parquet")["uri"] != keys(uri="s3://lab/Counts.parquet")["uri"]
    # 对象存储里 k 和 k/ 是两个键，所以 URI 的尾斜杠不能删
    assert keys(uri="s3://lab/out")["uri"] != keys(uri="s3://lab/out/")["uri"]
    # RFC 8089 明写 file://localhost/x 与 file:///x 同义；Windows 盘符与分隔符是语法事实
    assert keys(uri="file://localhost/data/x.csv")["uri"] == keys(uri="file:///data/x.csv")["uri"]
    assert keys(uri="file:///c:/Data/x.csv")["uri"] == keys(uri=r"file:///C:\Data\x.csv")["uri"]

    # machine + 绝对路径成对才算键，主机名大小写不敏感，尾斜杠与重复斜杠在文件系统里无意义
    assert (keys(machine="HPG", external_path=r"C:\data\x.csv")["path"]
            == keys(machine="hpg", external_path="c:/data/x.csv")["path"])
    assert (keys(machine="hpg", external_path="/blue/lab//out/")["path"]
            == keys(machine="hpg", external_path="/blue/lab/out")["path"])
    # 不同机器上的同名路径不是同一份东西
    assert (keys(machine="hpg", external_path="/data/x.csv")["path"]
            != keys(machine="laptop", external_path="/data/x.csv")["path"])

    # 判不了的一律不给键：没有机器、相对路径、~、截断的哈希、裸路径冒充 URI
    assert "path" not in keys(external_path="/data/x.csv")
    assert "path" not in keys(machine="hpg", external_path="out/x.csv")
    assert "path" not in keys(machine="hpg", external_path="~/out/x.csv")
    assert "sha256" not in keys(sha256="abc123")
    assert "uri" not in keys(uri=r"C:\data\x.csv")  # 单字母"scheme"是盘符
    assert "uri" not in keys(uri="/data/x.csv")
    assert artifact_keys({"name": "只有名字"}) == []


def test_dataflow_is_empty_and_quiet_without_keys_but_still_counts_the_gap(tmp_path):
    """没有 artifact 关系的项目仍可完整使用（§8）：空图、不报错、不告警。
    但"没登记产物"和"登记了却没给键"必须分得开，否则空图无法解释。"""
    store = Store(tmp_path)
    project = store.create_project("P")
    empty = store.dataflow(project["id"])
    assert empty["edges"] == [] and empty["nodes"] == []
    assert empty["stats"] == {"artifacts": 0, "keyed": 0, "unkeyed": 0,
                              "unlabeled_direction": 0, "edges": 0, "truncated": False}

    producer = store.record_node(project["id"], idempotency_key="n1", title="跑了个脚本")
    consumer = store.record_node(project["id"], idempotency_key="n2", title="用了那个结果")
    store.attach(project["id"], target_type="node", target_id=producer["id"],
                 name="results.csv", direction="output", external_path="results.csv")
    store.attach(project["id"], target_type="node", target_id=consumer["id"],
                 name="results.csv", direction="input", external_path="results.csv")
    flow = store.dataflow(project["id"])
    assert flow["edges"] == []  # 同名不是键：两条相对路径可能根本不在同一台机器上
    assert flow["stats"]["unkeyed"] == 2
    assert {item["node_id"] for item in flow["unkeyed"]} == {producer["id"], consumer["id"]}
    store.close()


def test_dataflow_counts_artifacts_left_at_the_default_reference_direction(tmp_path):
    """键给得完美、只是没人改 direction —— 这是最容易发生的空图，必须能说出来。

    `direction` 默认就是 `reference`，而 reference 两边都不参与 join。没有这一格
    计数时，「两个 Node 用同一个 sha256 登记了同一份产物」和「这个项目没有任何
    产物」在返回值里一模一样（artifacts / keyed / unkeyed 全是 0），§8 要求能分辨的
    正是这种沉默失败。
    """
    store = Store(tmp_path)
    project = store.create_project("P")
    producer = store.record_node(project["id"], idempotency_key="n1", title="训练")
    consumer = store.record_node(project["id"], idempotency_key="n2", title="评估")
    for node in (producer, consumer):
        store.attach(project["id"], target_type="node", target_id=node["id"],
                     name="model.ckpt", sha256=DIGEST_A)  # direction 用默认值
    flow = store.dataflow(project["id"])
    # reference 依然一条边都不连：登记它的人确实没有声明流向，猜它是猜。
    assert flow["edges"] == [] and flow["nodes"] == []
    assert flow["stats"]["unkeyed"] == 0  # 键没问题，问题在方向
    assert flow["stats"]["unlabeled_direction"] == 2

    # 有方向的登记不会被算进这一格
    fixed = store.record_node(project["id"], idempotency_key="n3", title="产出")
    store.attach(project["id"], target_type="node", target_id=fixed["id"],
                 name="x", direction="output", sha256=DIGEST_B)
    assert store.dataflow(project["id"])["stats"]["unlabeled_direction"] == 2
    store.close()


def test_dataflow_survives_cycles_and_attachments_pointing_at_deleted_nodes(tmp_path):
    """环、自引用和孤儿附件都不能让这条查询崩——它是每次现算的派生视图。"""
    store = Store(tmp_path)
    project = store.create_project("P")
    first = store.record_node(project["id"], idempotency_key="n1", title="第一轮",
                              occurred_at="2026-01-01T00:00:00.000+00:00")
    second = store.record_node(project["id"], idempotency_key="n2", title="第二轮",
                               occurred_at="2026-01-02T00:00:00.000+00:00")
    # 迭代式实验：A 的产物喂给 B，B 的产物又回到 A。时间顺序不能用来砍边（那是猜），
    # 所以图里就是有环。
    store.attach(project["id"], target_type="node", target_id=first["id"], name="a",
                 direction="output", sha256=DIGEST_A)
    store.attach(project["id"], target_type="node", target_id=second["id"], name="a",
                 direction="input", sha256=DIGEST_A)
    store.attach(project["id"], target_type="node", target_id=second["id"], name="b",
                 direction="output", sha256=DIGEST_B)
    store.attach(project["id"], target_type="node", target_id=first["id"], name="b",
                 direction="input", sha256=DIGEST_B)
    with store.transaction() as db:
        # purge 之后可能留下指向已删除 Node 的附件行；它不能变成指向不存在节点的边
        db.execute(
            "INSERT INTO attachments(id,project_id,target_type,target_id,direction,name,sha256,"
            "created_at) VALUES('att_ghost',?,'node','nd_deleted','input','ghost',?,?)",
            (project["id"], DIGEST_A, "2026-01-03T00:00:00.000+00:00"),
        )

    flow = store.dataflow(project["id"])
    pairs = {(edge["from_node_id"], edge["to_node_id"]) for edge in flow["edges"]}
    assert pairs == {(first["id"], second["id"]), (second["id"], first["id"])}
    assert all(edge["to_node_id"] != "nd_deleted" for edge in flow["edges"])
    store.close()


def test_dataflow_bounds_a_hub_artifact_instead_of_pairing_everything(tmp_path):
    """一个被反复覆盖的 latest.ckpt 会让几百个 Node 两两配对（生产者数 × 消费者数）。
    生成量必须有上限，而且截断要说出来，不能装作图就是这么大。"""
    store = Store(tmp_path)
    project = store.create_project("P")
    chapter = store.get_project(project["id"])["chapters"][0]["id"]
    stamp = "2026-01-01T00:00:00.000+00:00"
    nodes = []
    attachments = []
    for side in ("out", "in"):
        for i in range(110):
            node_id = f"nd_{side}_{i:03d}"
            nodes.append((node_id, project["id"], chapter, f"{side} {i}", stamp, f"k_{side}_{i}",
                          stamp, stamp))
            attachments.append((
                f"att_{side}_{i:03d}", project["id"], "node", node_id,
                "output" if side == "out" else "input", "latest.ckpt", DIGEST_A, stamp,
            ))
    with store.transaction() as db:
        db.executemany(
            "INSERT INTO nodes(id,project_id,chapter_id,title,occurred_at,idempotency_key,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", nodes)
        db.executemany(
            "INSERT INTO attachments(id,project_id,target_type,target_id,direction,name,sha256,"
            "created_at) VALUES(?,?,?,?,?,?,?,?)", attachments)

    flow = store.dataflow(project["id"], limit=50)
    assert len(flow["edges"]) == 50
    assert flow["stats"]["truncated"] is True
    assert flow["stats"]["edges"] <= 10000, "两两配对必须在上限处停下，不能算满 12100 条"
    assert all(edge["from_node_id"].startswith("nd_out") for edge in flow["edges"])
    store.close()


def test_context_only_computes_dataflow_when_asked(tmp_path):
    """context 是每个 batch 都要拉的热路径，派生视图必须是可选的（§8）。"""
    store = Store(tmp_path)
    project = store.create_project("P", workspace_keys=["rt-ws-flow"])
    node = store.record_node(project["id"], idempotency_key="n1", title="产出")
    store.attach(project["id"], target_type="node", target_id=node["id"], name="x",
                 direction="output", sha256=DIGEST_A)
    assert "dataflow" not in store.context(workspace_keys=["rt-ws-flow"])["project"]
    detail = store.context(workspace_keys=["rt-ws-flow"], include_dataflow=True)["project"]
    assert detail["dataflow"]["stats"]["keyed"] == 1
    assert detail["dataflow"]["edges"] == []
    store.close()
