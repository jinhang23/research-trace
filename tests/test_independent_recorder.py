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


def test_cli_is_isolated_and_reuses_only_its_own_session(monkeypatch, tmp_path):
    _clear_paid_env(monkeypatch)
    calls = []

    def run(command, **options):
        calls.append((command, options))
        if command[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({
                    "loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                }),
                stderr="",
            )
        return subprocess.CompletedProcess(
            command, 0, stdout=_result({"status": "skip", "records": []}), stderr=""
        )

    monkeypatch.setattr(R.subprocess, "run", run)
    worker = R.RecorderWorker(tmp_path / "data", "http://trace")
    assert worker._invoke("first evidence", {"model": "sonnet"}, "project-1")["status"] == "skip"
    assert worker._invoke("second evidence", {"model": "sonnet"}, "project-1")["status"] == "skip"

    first, second = calls[1][0], calls[2][0]
    assert first[first.index("--tools") + 1] == ""
    assert first[first.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in first and "--permission-mode" in first
    assert "--session-id" in first and "--resume" not in first
    assert "--resume" in second and "--session-id" not in second
    assert "--no-session-persistence" not in first
    assert calls[1][1]["input"] == "first evidence"
    assert calls[1][1]["cwd"].endswith("recorder-workspace")
    assert all(not calls[1][1]["env"].get(key) for key in R.PAID_CREDENTIAL_ENV)


def test_classifier_distinguishes_skip_malformed_quota_and_overage():
    kind, value, _ = R.classify_cli_output(
        _result({"status": "skip", "records": []}), "", 0
    )
    assert (kind, value["status"]) == ("success", "skip")
    assert R.classify_cli_output("", "", 0)[0] == "empty"
    assert R.classify_cli_output("", "You have hit your usage limit", 1)[0] == "quota"
    event = json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {"isUsingOverage": True, "resetsAt": 2_000_000_000},
    })
    kind, _, reset = R.classify_cli_output(event, "", 0)
    assert kind == "overage" and reset == 2_000_000_000

    allowed = "\n".join([
        json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "isUsingOverage": False},
        }),
        _result({"status": "skip", "records": []}).strip(),
    ])
    assert R.classify_cli_output(allowed, "", 0)[0] == "success"


def test_usage_counters_preserve_cache_observations():
    output = json.dumps({
        "type": "result", "usage": {
            "input_tokens": 10, "output_tokens": 4,
            "cache_read_input_tokens": 800, "cache_creation_input_tokens": 20,
        },
    })
    assert R.extract_usage(output) == {
        "input_tokens": 10, "output_tokens": 4,
        "cache_read_input_tokens": 800, "cache_creation_input_tokens": 20,
    }


def test_plan_rejects_sources_and_structure_not_present_in_packet():
    packet = {
        "new_evidence": {"events": [{"event_id": "e1"}]},
        "existing_memory": {
            "chapters": [{"id": "c1"}], "recent_nodes": [],
            "related_old_records": [], "recent_runs": [],
        },
    }
    good = {
        "status": "record",
        "records": [{"title": "Direction", "body": "Untested and worth checking.",
                     "source_event_ids": ["e1"], "chapter_id": "c1"}],
    }
    assert R.validate_plan(good, packet)[0]["chapter_id"] == "c1"
    good["records"][0]["source_event_ids"] = ["some-old-event"]
    with pytest.raises(R.RecorderError, match="outside this batch"):
        R.validate_plan(good, packet)


def _staged_batch(tmp_path: Path, *, records_enabled=True):
    project = tmp_path / "project"
    project.mkdir()
    write_marker(
        project, workspace_key="rt-ws-one", project_id="project-1", capture=True,
        recorder={
            "enabled": records_enabled, "mode": "independent", "model": "sonnet",
            "extra_usage_disabled": True,
        },
    )
    data = tmp_path / "data"
    root = data / "outbox" / "workspace" / "session"
    (root / "pending").mkdir(parents=True)
    (root / "batches").mkdir()
    event = {
        "event_id": "event-1", "captured_at": "2026-09-04T12:00:00Z",
        "session_id": "session", "hook_event": "UserPromptSubmit",
        "payload": {"prompt": "探索 pocket cutoff，但还没有运行"},
    }
    (root / "pending" / "event.json").write_text(json.dumps(event), encoding="utf-8")
    manifest = {
        "schema": "research-trace.batch.v1", "batch_id": "batch-1",
        "created_at": "2026-09-04T12:00:00Z", "session_id": "session",
        "project_dir": str(project), "project_id": "project-1",
        "workspace_keys": ["rt-ws-one"], "events": ["pending/event.json"],
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
                    "id": "project-1", "name": "Affinity", "overview": "predict affinity",
                    "chapters": [{"id": "chapter-1", "name": "main", "summary": ""}],
                    "recent_nodes": [], "unresolved_corrections": [],
                },
                "recent_runs": [],
            }
        if path.startswith("/api/search"):
            return {"items": []}
        if path == "/api/record":
            self.records += 1
            if self.fail_record_number == self.records:
                raise RuntimeError("central unavailable")
            return {"id": f"node-{self.records}"}
        raise AssertionError(path)


def test_batch_is_archived_only_after_idempotent_node_write(monkeypatch, tmp_path):
    data, root, path = _staged_batch(tmp_path)
    worker = R.RecorderWorker(data, "http://trace")
    remote = FakeRemote()
    worker.remote = remote
    monkeypatch.setattr(worker, "_invoke", lambda prompt, config, project_id: {
        "status": "record",
        "records": [{
            "title": "Pocket cutoff is an untested direction",
            "body": "The proposed comparison has not been run; the expected effect remains unknown.",
            "chapter_id": "chapter-1", "source_event_ids": ["event-1"],
        }],
    })
    result = worker.process(path)
    assert result == {
        "batch_id": "batch-1", "status": "complete", "records": 1, "curations": 0,
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
    monkeypatch.setattr(worker, "_invoke", lambda *args: {
        "status": "record", "records": [],
        "curations": [{
            "target_type": "overview", "target_id": None,
            "body": "Affinity project; pocket cutoff remains an untested direction.",
            "expect_version": 0, "source_event_ids": ["event-1"],
        }],
    })

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
        return {"status": "record", "records": [
            {"title": "A", "body": "First durable finding", "source_event_ids": ["event-1"]},
            {"title": "B", "body": "Second durable finding", "source_event_ids": ["event-1"]},
        ]}

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
        worker, "_invoke",
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


def test_disabling_a_project_retains_its_batch_and_reports_a_clean_pause(tmp_path):
    data, _root, path = _staged_batch(tmp_path, records_enabled=False)
    worker = R.RecorderWorker(data, "http://trace")
    result = worker.run_once()
    assert result["counts"] == {"disabled": 1}
    assert result["pending_batches"] == 1
    assert worker.state["status"] == "disabled"
    assert path.exists()
