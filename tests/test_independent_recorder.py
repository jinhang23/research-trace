from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from research_trace import recorder as R
from research_trace.deliver import write_marker


def _result(output):
    return json.dumps({"type": "result", "structured_output": output}) + "\n"


def _clear_paid_env(monkeypatch):
    for key in R.PAID_CREDENTIAL_ENV:
        monkeypatch.delenv(key, raising=False)


def test_paid_credentials_stop_before_any_cli_call(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    called = []
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: called.append((a, k)))
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    with pytest.raises(R.RecorderError, match="ANTHROPIC_API_KEY") as error:
        worker._invoke("evidence", {"model": "sonnet"}, "project-1")
    assert error.value.kind == "paid_credentials"
    assert called == []


def test_cli_is_isolated_and_stateless(monkeypatch, tmp_path):
    _clear_paid_env(monkeypatch)
    calls = []

    def run(command, **options):
        calls.append((command, options))
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "subscriptionType": "max",
                        "apiProvider": "firstParty",
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout=_result({"status": "skip", "records": []}), stderr="")

    monkeypatch.setattr(R.subprocess, "run", run)
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    assert worker._invoke("first evidence", {"model": "sonnet"}, "project-1")["status"] == "skip"
    assert worker._invoke("second evidence", {"model": "sonnet"}, "project-1")["status"] == "skip"

    assert len(calls) == 3, "auth is checked once, then one CLI call per batch"
    first, second = calls[1][0], calls[2][0]
    assert first[first.index("--tools") + 1] == ""
    assert first[first.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in first and "--permission-mode" in first
    assert "--no-session-persistence" in first
    # 无状态：两次调用命令逐字相同，固定前缀才是缓存命中的条件；不再 --resume 累积上下文
    assert first == second
    for dead in ("--resume", "--session-id", "--exclude-dynamic-system-prompt-sections"):
        assert dead not in first
    assert calls[1][1]["input"] == "first evidence"
    assert calls[1][1]["cwd"].endswith("recorder-workspace")
    assert all(not calls[1][1]["env"].get(key) for key in R.PAID_CREDENTIAL_ENV)
    assert worker.state["projects"] and next(iter(worker.state["projects"].values()))["calls"] == 2


def test_nested_claude_session_identity_is_stripped_but_the_oauth_token_survives():
    env = R.subscription_environment(
        {
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_PID": "42",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token",
            "CLAUDE_CONFIG_DIR": "/cfg",
            "GIT_DIR": "/g",
            "PATH": "/bin",
        }
    )
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"
    assert env["CLAUDE_CONFIG_DIR"] == "/cfg" and env["PATH"] == "/bin"
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID", "GIT_DIR"):
        assert key not in env
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_a_custom_base_url_is_refused_like_an_api_key():
    with pytest.raises(R.RecorderError) as error:
        R.subscription_environment({"ANTHROPIC_BASE_URL": "https://gateway.example"})
    assert error.value.kind == "paid_credentials"


def test_auth_preflight_tolerates_a_cli_without_the_auth_subcommand(monkeypatch, tmp_path):
    """`claude auth status` 在 2.1.30 不存在、2.1.261 存在。UF 上的版本未知，预检不能把
    「子命令不存在」判成「没登录」然后 300 秒退避无限重试。"""
    monkeypatch.setattr(
        R.subprocess,
        "run",
        lambda command, **options: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="error: unknown command 'auth'",
        ),
    )
    value = R.verify_subscription_auth("claude", {}, tmp_path)
    assert value["logged_in"] is None and value["auth_method"] == "unverified"


def test_auth_preflight_still_rejects_a_reported_non_subscription_login(monkeypatch, tmp_path):
    def answer(**fields):
        return lambda command, **options: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(fields),
            stderr="",
        )

    monkeypatch.setattr(R.subprocess, "run", answer(loggedIn=True, authMethod="api-key"))
    with pytest.raises(R.RecorderError) as error:
        R.verify_subscription_auth("claude", {}, tmp_path)
    assert error.value.kind == "auth"
    monkeypatch.setattr(R.subprocess, "run", answer(loggedIn=True, authMethod="claude.ai", apiProvider="bedrock"))
    with pytest.raises(R.RecorderError, match="bedrock"):
        R.verify_subscription_auth("claude", {}, tmp_path)
    monkeypatch.setattr(R.subprocess, "run", answer(loggedIn=False))
    with pytest.raises(R.RecorderError, match="not logged in"):
        R.verify_subscription_auth("claude", {}, tmp_path)


def test_related_records_come_from_search_hits_and_only_node_scope():
    """`/api/search` 返回的键是 `hits`。方案 3 的第三个输入「相关旧记录」此前读的是
    `items`，所以从来没进过模型。"""
    result = {
        "hits": [
            {"id": "node-9", "scope": "node", "title": "old", "chapter_id": "chapter-1"},
            {"id": "cmt-1", "scope": "comment", "body": "human correction"},
            {"id": "project-1", "scope": "overview", "overview": "..."},
        ],
        "totals": {"node": 1, "comment": 1, "overview": 1},
    }
    assert [x["id"] for x in R.related_nodes(result)] == ["node-9"]
    assert R.related_nodes({"items": [{"id": "n", "title": "t"}]}) == [{"id": "n", "title": "t"}]
    assert R.related_nodes(None) == [] and R.related_nodes({"hits": "junk"}) == []


def test_search_terms_are_identifiers_and_repeated_nouns_not_whole_sentences():
    """服务端是单个 `%query%` 子串匹配：整句 prompt 永远搜不到旧 Node。"""
    events = [
        {
            "hook_event": "UserPromptSubmit",
            "payload": {
                "prompt": "按之前说的做 10Å 截断的消融，ESM-2 的 warmup 和主实验一致，先看 models/esm2_gnn 的配置。"
            },
        },
        {
            "hook_event": "PostToolUse",
            "payload": {"tool_input": {"command": "ls models"}, "tool_response": {"stdout": "noise noise"}},
        },
        {
            "hook_event": "Stop",
            "payload": {"last_assistant_message": "消融的 10Å 截断配置已经改好，warmup 保持 500 步。"},
        },
    ]
    terms = R._search_terms(events)
    # original spelling is sent: the server folds ASCII only, like SQLite's lower()
    assert "ESM-2" in terms and "10Å" in terms and "warmup" in terms
    assert "消融" in terms and "截断" in terms
    assert "noise" not in terms and not any(len(t) > 40 for t in terms)
    assert R._search_terms([{"hook_event": "PostToolUse", "payload": {}}]) == []


def test_status_is_advisory_and_the_outcome_comes_from_the_arrays():
    """模拟里 T7：模型对「只更新 Overview、零记录」答了 status=skip，旧规则把它判成
    format 失败并重试。status 只是模型的自述，写什么由数组决定。"""
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "project": {"overview": "old", "overview_version": 2},
            "chapters": [],
            "recent_nodes": [],
            "related_old_records": [],
            "recent_runs": [],
            "unresolved_human_corrections": [],
        },
    }
    output = {
        "status": "skip",
        "records": [],
        "curations": [
            {
                "target_type": "overview",
                "body": "new",
                "expect_version": 2,
                "source_event_ids": ["e1"],
                "reason": "correction_absorbed",
            }
        ],
    }
    assert R.validate_plan(output, packet) == []
    assert R.validate_curations(output, packet)[0]["reason"] == "correction_absorbed"
    output = {"status": "record", "records": [], "curations": []}
    assert R.validate_plan(output, packet) == [] and R.validate_curations(output, packet) == []
    with pytest.raises(R.RecorderError, match=r"status=record\|skip"):
        R.validate_plan({"status": "maybe", "records": []}, packet)


def test_progress_only_summaries_are_discarded_before_any_write():
    """用户反馈：摘要不用每轮都改。摘要是研究线当前的答案，不是步骤日志；模型必须说明
    为什么改，progress_only 和「已有摘要的章再来一次 first_summary」直接丢掉。"""
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "project": {"overview": "", "overview_version": 0},
            "chapters": [
                {"id": "c-empty", "summary": "", "summary_version": 0},
                {"id": "c-has", "summary": "主实验目前 CI 0.889", "summary_version": 3},
            ],
            "unresolved_human_corrections": [],
        },
    }

    def curation(target, reason, **extra):
        value = {
            "target_type": "chapter",
            "target_id": target,
            "body": "新摘要",
            "source_event_ids": ["e1"],
            "expect_version": 0 if target == "c-empty" else 3,
            "reason": reason,
        }
        value.update(extra)
        return value

    output = {
        "status": "record",
        "records": [],
        "curations": [
            curation("c-has", "progress_only"),
            curation("c-empty", "first_summary"),
        ],
    }
    kept = R.validate_curations(output, packet)
    assert [(c["target_id"], c["reason"]) for c in kept] == [("c-empty", "first_summary")]
    output["curations"] = [curation("c-has", "first_summary")]
    assert R.validate_curations(output, packet) == [], "a Chapter that has a summary cannot get a 'first' one"
    output["curations"] = [curation("c-has", "result_changed")]
    assert R.validate_curations(output, packet)[0]["reason"] == "result_changed"
    output["curations"] = [curation("c-has", "because")]
    with pytest.raises(R.RecorderError, match="unknown reason"):
        R.validate_curations(output, packet)


def test_a_node_correction_listed_on_a_summary_curation_is_dropped_not_fatal():
    """三天模拟里 T6/T7 就卡在这：模型把 Node 上的人工纠正 id 放进了章摘要的
    resolve_comment_ids。服务端的 409 闸门按目标算，列上它改变不了任何事，所以丢掉；
    不存在的 id 仍然是硬错误（那是编出来的）。"""
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "project": {"overview_version": 3},
            "chapters": [{"id": "c1", "summary_version": 2}],
            "unresolved_human_corrections": [
                {"id": "cmt-node", "target_type": "node", "target_id": "n1", "body": "用 0.874"},
                {"id": "cmt-c1", "target_type": "chapter", "target_id": "c1", "body": "口径"},
                {"id": "cmt-ov", "target_type": "overview", "target_id": None, "body": "总览"},
            ],
        },
    }
    output = {
        "status": "record",
        "records": [],
        "curations": [
            {
                "target_type": "chapter",
                "target_id": "c1",
                "body": "摘要",
                "expect_version": 2,
                "source_event_ids": ["e1"],
                "resolve_comment_ids": ["cmt-node", "cmt-c1", "cmt-ov"],
            }
        ],
    }
    assert R.validate_curations(output, packet)[0]["resolve_comment_ids"] == ["cmt-c1"]
    output["curations"][0]["resolve_comment_ids"] = ["cmt-made-up"]
    with pytest.raises(R.RecorderError, match="unknown correction"):
        R.validate_curations(output, packet)


def test_related_recall_merges_terms_and_skips_nodes_already_in_recent(tmp_path):
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    queries = []

    class Remote:
        def request(self, method, path, value=None):
            if path == "/api/context":
                return {"matched": True, "project": {"id": "p", "recent_nodes": [{"id": "n-recent"}]}}
            queries.append(path)
            if "q=esm-2" in path:
                return {"hits": [{"id": "n-recent", "scope": "node"}, {"id": "n-old-1", "scope": "node"}]}
            if "q=warmup" in path:
                return {
                    "hits": [
                        {"id": "n-old-1", "scope": "node"},
                        {"id": "cmt", "scope": "comment"},
                        {"id": "n-old-2", "scope": "node"},
                    ]
                }
            return {"hits": []}

    worker.remote = Remote()
    manifest = {"project_id": "p", "workspace_keys": []}
    material = {"events": [{"hook_event": "UserPromptSubmit", "payload": {"prompt": "ESM-2 warmup 消融"}}]}
    _context, related = worker._context(manifest, material)
    assert [x["id"] for x in related] == ["n-old-1", "n-old-2"]
    assert len(queries) >= 2 and all("scope=semantic" in q for q in queries)


def test_plan_treats_only_node_hits_as_possible_parents():
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "chapters": [{"id": "c1"}],
            "recent_nodes": [],
            "related_old_records": [
                {"id": "n-old", "scope": "node", "chapter_id": "c1"},
                {"id": "cmt-1", "scope": "comment"},
            ],
            "recent_runs": [],
        },
    }
    good = {
        "status": "record",
        "records": [
            {
                "title": "T",
                "body": "B",
                "source_event_ids": ["e1"],
                "chapter_id": "c1",
                "parent_id": "n-old",
            }
        ],
    }
    assert R.validate_plan(good, packet)[0]["parent_id"] == "n-old"
    good["records"][0]["parent_id"] = "cmt-1"
    with pytest.raises(R.RecorderError, match="unknown parent_id"):
        R.validate_plan(good, packet)


def test_an_artifact_uri_absent_from_the_evidence_is_rejected():
    packet = {
        "new_evidence": {
            "events": [
                {
                    "event_id": "e1",
                    "payload_json": json.dumps({"prompt": "看 https://wandb.ai/lab/aff/runs/1 的曲线"}),
                }
            ]
        },
        "existing_memory": {"chapters": [], "recent_nodes": [], "related_old_records": [], "recent_runs": []},
    }
    plan = {
        "status": "record",
        "records": [
            {
                "title": "T",
                "body": "B",
                "source_event_ids": ["e1"],
                "artifact_refs": [{"name": "W&B run 1", "uri": "https://wandb.ai/lab/aff/runs/1"}],
            }
        ],
    }
    assert R.validate_plan(plan, packet)[0]["artifact_refs"] == [
        {"name": "W&B run 1", "uri": "https://wandb.ai/lab/aff/runs/1", "direction": "reference"},
    ]
    plan["records"][0]["artifact_refs"][0]["uri"] = "https://wandb.ai/lab/aff/runs/2"
    with pytest.raises(R.RecorderError, match="not present in this batch"):
        R.validate_plan(plan, packet)
    plan["records"][0]["artifact_refs"][0]["uri"] = "runs/1"
    with pytest.raises(R.RecorderError, match="absolute uri"):
        R.validate_plan(plan, packet)


def test_classifier_distinguishes_skip_malformed_quota_and_overage():
    kind, value, _ = R.classify_cli_output(_result({"status": "skip", "records": []}), "", 0)
    assert (kind, value["status"]) == ("success", "skip")
    assert R.classify_cli_output("", "", 0)[0] == "empty"
    assert R.classify_cli_output("", "You have hit your usage limit", 1)[0] == "quota"
    event = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"isUsingOverage": True, "resetsAt": 2_000_000_000},
        }
    )
    kind, _, reset = R.classify_cli_output(event, "", 0)
    assert kind == "overage" and reset == 2_000_000_000

    allowed = "\n".join(
        [
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed", "isUsingOverage": False},
                }
            ),
            _result({"status": "skip", "records": []}).strip(),
        ]
    )
    assert R.classify_cli_output(allowed, "", 0)[0] == "success"


def test_usage_counters_preserve_cache_observations():
    output = json.dumps(
        {
            "type": "result",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 20,
            },
        }
    )
    assert R.extract_usage(output) == {
        "input_tokens": 10,
        "output_tokens": 4,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 20,
    }


def test_plan_rejects_sources_and_structure_not_present_in_packet():
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "chapters": [{"id": "c1"}],
            "recent_nodes": [],
            "related_old_records": [],
            "recent_runs": [],
        },
    }
    good = {
        "status": "record",
        "records": [
            {
                "title": "Direction",
                "body": "Untested and worth checking.",
                "source_event_ids": ["e1"],
                "chapter_id": "c1",
            }
        ],
    }
    assert R.validate_plan(good, packet)[0]["chapter_id"] == "c1"
    good["records"][0]["source_event_ids"] = ["some-old-event"]
    with pytest.raises(R.RecorderError, match="outside this batch"):
        R.validate_plan(good, packet)


def _staged_batch(tmp_path: Path, *, records_enabled=True, prompt="探索 pocket cutoff，但还没有运行"):
    project = tmp_path / "project"
    project.mkdir()
    write_marker(
        project,
        workspace_key="rt-ws-one",
        project_id="project-1",
        capture=True,
        recorder={
            "enabled": records_enabled,
            "mode": "independent",
            "model": "sonnet",
            "extra_usage_disabled": True,
        },
    )
    data = tmp_path / "data"
    root = data / "outbox" / "workspace" / "session"
    (root / "pending").mkdir(parents=True)
    (root / "batches").mkdir()
    event = {
        "event_id": "event-1",
        "captured_at": "2026-09-04T12:00:00Z",
        "session_id": "session",
        "hook_event": "UserPromptSubmit",
        "payload": {"prompt": prompt},
    }
    (root / "pending" / "event.json").write_text(json.dumps(event), encoding="utf-8")
    manifest = {
        "schema": "research-trace.batch.v1",
        "batch_id": "batch-1",
        "created_at": "2026-09-04T12:00:00Z",
        "session_id": "session",
        "project_dir": str(project),
        "project_id": "project-1",
        "workspace_keys": ["rt-ws-one"],
        "events": ["pending/event.json"],
        "transcript_chunks": [],
    }
    path = root / "batches" / "batch-1.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return data, root, path


class FakeRemote:
    def __init__(self, fail_record_number=0):
        self.calls = []
        self.records = 0
        self.fail_record_number = fail_record_number

    def request(self, method, path, value=None):
        self.calls.append((method, path, value))
        if path == "/api/context":
            return {
                "matched": True,
                "project": {
                    "id": "project-1",
                    "name": "Affinity",
                    "overview": "predict affinity",
                    "chapters": [{"id": "chapter-1", "name": "main", "summary": ""}],
                    "recent_nodes": [],
                    "unresolved_corrections": [],
                },
                "recent_runs": [],
            }
        if path.startswith("/api/search"):
            return {"hits": [], "totals": {}}  # the real server's shape
        if path == "/api/record":
            self.records += 1
            if self.fail_record_number == self.records:
                raise RuntimeError("central unavailable")
            return {"id": f"node-{self.records}"}
        if path == "/api/attach":
            return {"id": f"att-{len(self.calls)}"}
        raise AssertionError(path)


def test_batch_is_archived_only_after_idempotent_node_write(monkeypatch, tmp_path):
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")
    remote = FakeRemote()
    worker.remote = remote
    monkeypatch.setattr(
        worker,
        "_invoke",
        lambda prompt, config, project_id: {
            "status": "record",
            "records": [
                {
                    "title": "Pocket cutoff is an untested direction",
                    "body": "The proposed comparison has not been run; the expected effect remains unknown.",
                    "chapter_id": "chapter-1",
                    "source_event_ids": ["event-1"],
                }
            ],
        },
    )
    result = worker.process(path)
    assert result == {
        "batch_id": "batch-1",
        "status": "complete",
        "records": 1,
        "curations": 0,
    }
    request = next(value for method, route, value in remote.calls if route == "/api/record")
    assert request["idempotency_key"] == "semantic:batch-1:0"
    assert request["source_event_ids"] == ["event-1"]
    assert not path.exists()
    assert (root / "batches" / "done" / "batch-1.json").exists()


def test_summary_curation_uses_current_version_and_survives_lost_local_receipt(monkeypatch, tmp_path):
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")

    class CurateRemote(FakeRemote):
        def __init__(self):
            super().__init__()
            self.overview = "predict affinity"
            self.version = 0
            self.lose_first_response = True

        def request(self, method, route, value=None):
            if route == "/api/context":
                result = super().request(method, route, value)
                result["project"]["overview"] = self.overview
                result["project"]["overview_version"] = self.version
                return result
            if route == "/api/curate":
                self.calls.append((method, route, value))
                assert value["expect_version"] == 0
                self.overview = value["body"]
                self.version = 1
                if self.lose_first_response:
                    self.lose_first_response = False
                    raise RuntimeError("connection lost after central commit")
                return {"version": 1}
            return super().request(method, route, value)

    remote = CurateRemote()
    worker.remote = remote
    monkeypatch.setattr(
        worker,
        "_invoke",
        lambda *args: {
            "status": "record",
            "records": [],
            "curations": [
                {
                    "target_type": "overview",
                    "target_id": None,
                    "body": "Affinity project; pocket cutoff remains an untested direction.",
                    "expect_version": 0,
                    "source_event_ids": ["event-1"],
                }
            ],
        },
    )

    # The central write succeeds but its response is lost, so no local receipt exists.
    assert worker.process(path)["status"] == "storage_or_network_error"
    assert remote.version == 1
    state_path = root / "batches" / "state" / "batch-1.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["retry_at"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert worker.process(path)["status"] == "complete"
    assert len([call for call in remote.calls if call[1] == "/api/curate"]) == 1


def test_partial_write_reuses_durable_plan_without_second_model_call(monkeypatch, tmp_path):
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")
    remote = FakeRemote(fail_record_number=2)
    worker.remote = remote
    invocations = []

    def invoke(*args):
        invocations.append(args)
        return {
            "status": "record",
            "records": [
                {"title": "A", "body": "First durable finding", "source_event_ids": ["event-1"]},
                {"title": "B", "body": "Second durable finding", "source_event_ids": ["event-1"]},
            ],
        }

    monkeypatch.setattr(worker, "_invoke", invoke)
    assert worker.process(path)["status"] == "storage_or_network_error"
    state_path = root / "batches" / "state" / "batch-1.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["completed_records"] == [0] and len(state["plan"]) == 2
    state["retry_at"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")
    remote.fail_record_number = 0
    assert worker.process(path)["status"] == "complete"
    assert len(invocations) == 1


def test_quota_pauses_without_archiving_or_retrying_in_same_pass(monkeypatch, tmp_path):
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")
    worker.remote = FakeRemote()
    monkeypatch.setattr(
        worker,
        "_invoke",
        lambda *args: (_ for _ in ()).throw(
            R.RecorderError("subscription quota exhausted", kind="quota", retry_at=time.time() + 600)
        ),
    )
    result = worker.run_once()
    assert result["counts"] == {"quota": 1}
    assert path.exists()
    state = json.loads((root / "batches" / "state" / "batch-1.json").read_text(encoding="utf-8"))
    assert state["status"] == "quota" and state["retry_at"] > time.time()
    assert not list((root / "batches" / "done").glob("batch-1.json"))


def test_overage_is_a_hard_block_until_explicit_retry(monkeypatch, tmp_path):
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")
    worker.remote = FakeRemote()
    calls = []

    def overage(*args):
        calls.append(args)
        raise R.RecorderError("overage reported", kind="overage")

    monkeypatch.setattr(worker, "_invoke", overage)
    assert worker.process(path)["status"] == "overage"
    assert worker.process(path)["status"] == "overage"
    assert len(calls) == 1
    assert R._clear_blocked(data / "outbox") == 1
    state = json.loads((root / "batches" / "state" / "batch-1.json").read_text(encoding="utf-8"))
    assert state["retry_at"] == 0 and "status" not in state


def test_format_failures_stop_after_the_attempt_cap_and_wait_for_the_operator(monkeypatch, tmp_path):
    """指数退避封的是间隔不是次数：每次 format/malformed 重试都是一次真实模型调用。"""
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")
    worker.remote = FakeRemote()
    calls = []

    def malformed(*args):
        calls.append(args)
        raise R.RecorderError("Recorder output is not a JSON object", kind="malformed")

    monkeypatch.setattr(worker, "_invoke", malformed)
    state_path = root / "batches" / "state" / "batch-1.json"
    statuses = []
    for _ in range(R.MAX_MODEL_ATTEMPTS + 2):
        statuses.append(worker.process(path)["status"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("retry_at"):
            state["retry_at"] = 0
            state_path.write_text(json.dumps(state), encoding="utf-8")
    assert len(calls) == R.MAX_MODEL_ATTEMPTS
    assert statuses[: R.MAX_MODEL_ATTEMPTS - 1] == ["malformed"] * (R.MAX_MODEL_ATTEMPTS - 1)
    assert statuses[R.MAX_MODEL_ATTEMPTS - 1 :] == ["attempts_exhausted"] * 3
    assert path.exists(), "material is retained"
    # it is a per-batch block: run_once keeps going to the next batch
    assert worker.run_once()["counts"] == {"attempts_exhausted": 1}
    assert R._clear_blocked(data / "outbox") == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["attempts"] == 0


def test_artifact_refs_are_attached_to_the_written_node_idempotently(monkeypatch, tmp_path):
    uri = "https://wandb.ai/lab/affinity/runs/abc123"
    data, root, path = _staged_batch(tmp_path, prompt=f"看 W&B {uri} 的曲线")
    worker = R.RecorderWorker(data, "http://trace")
    remote = FakeRemote()
    worker.remote = remote
    monkeypatch.setattr(
        worker,
        "_invoke",
        lambda *args: {
            "status": "record",
            "records": [
                {
                    "title": "lr 3e-5 的 W&B 曲线",
                    "body": "loss 下降；下游收益待验证。",
                    "chapter_id": "chapter-1",
                    "source_event_ids": ["event-1"],
                    "artifact_refs": [{"name": "W&B run abc123", "uri": uri, "direction": "output"}],
                }
            ],
        },
    )
    assert worker.process(path)["status"] == "complete"
    record = next(value for _m, route, value in remote.calls if route == "/api/record")
    assert "artifact_refs" not in record, "the Node API does not take artifact refs"
    attach = [value for _m, route, value in remote.calls if route == "/api/attach"]
    assert len(attach) == 1
    assert attach[0]["target_type"] == "node" and attach[0]["target_id"] == "node-1"
    assert attach[0]["uri"] == uri and attach[0]["direction"] == "output"
    assert attach[0]["metadata"]["capture_key"] == "semantic:batch-1:0:artifact:0"
    done = json.loads((root / "batches" / "done" / "batch-1.state.json").read_text(encoding="utf-8"))
    assert done["completed_artifacts"] == ["0:0"] and done["node_ids"] == {"0": "node-1"}


def test_disabling_a_project_retains_its_batch_and_reports_a_clean_pause(tmp_path):
    data, _root, path = _staged_batch(tmp_path, records_enabled=False)
    worker = R.RecorderWorker(data, "http://trace")
    result = worker.run_once()
    assert result["counts"] == {"disabled": 1}
    assert result["pending_batches"] == 1
    assert worker.state["status"] == "disabled"
    assert path.exists()


def test_watch_is_a_separate_long_lived_consumer_even_when_queue_is_empty(monkeypatch, tmp_path):
    sleeps = []

    def stop_after_first_idle_wait(delay):
        sleeps.append(delay)
        raise KeyboardInterrupt

    monkeypatch.setattr(R.time, "sleep", stop_after_first_idle_wait)
    result = R.main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--watch",
            "--interval",
            "7",
            "--quiet",
        ]
    )
    assert result == 130
    assert sleeps == [7.0]


def test_watch_logs_one_line_per_batch_and_nothing_when_idle():
    """UF 首次联调：`--watch >> recorder.log` 跑了三个 batch，日志还是空文件——每轮的 JSON 报告
    被整块缓冲，而且空转也打印整份报告。现在一批一行、空转不写。"""
    assert R.watch_lines({"pending_batches": 0, "counts": {}, "results": []}) == []
    lines = R.watch_lines(
        {
            "pending_batches": 1,
            "counts": {"complete": 1, "quota": 1},
            "results": [
                {"batch_id": "b1", "status": "complete", "records": 2, "curations": 0},
                {"batch_id": "b2", "status": "quota", "error": "rate limited", "retry_at": 1234.0},
            ],
        }
    )
    assert len(lines) == 3
    assert "batch=b1 status=complete records=2 curations=0" in lines[0]
    assert "batch=b2 status=quota" in lines[1] and "error='rate limited'" in lines[1] and "retry_at=1234.0" in lines[1]
    assert lines[2].endswith("pending=1")


def test_a_parent_in_inbox_makes_the_record_follow_it_instead_of_failing_the_batch():
    """UF 第 4 批：模型按提示词把 chapter_id 留空，parent 却是 Inbox 里的 Node（带 Inbox 的真实
    chapter_id），以前按 format 整批失败并烧掉 4 次模型调用。现在跟着 parent 走；显式选了别的
    Chapter 时改为丢掉 parent。"""
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "chapters": [{"id": "ch-inbox", "name": "Inbox"}, {"id": "ch-main", "name": "主实验"}],
            "recent_nodes": [{"id": "n1", "scope": "node", "chapter_id": "ch-inbox"}],
            "related_old_records": [],
            "recent_runs": [],
        },
    }
    record = {"title": "T", "body": "B", "source_event_ids": ["e1"], "chapter_id": None, "parent_id": "n1"}
    plan = R.validate_plan({"status": "record", "records": [record]}, packet)
    assert plan[0]["chapter_id"] == "ch-inbox" and plan[0]["parent_id"] == "n1"

    record["chapter_id"] = "ch-main"
    plan = R.validate_plan({"status": "record", "records": [record]}, packet)
    assert plan[0]["chapter_id"] == "ch-main" and plan[0]["parent_id"] is None
