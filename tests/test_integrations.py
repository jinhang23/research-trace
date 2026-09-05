from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from research_trace.frameworks import MLflowEvidence, fingerprint, load_config, visible_content
from research_trace.integrations import Integrations
from research_trace.server import create_app
from research_trace.storage import Store, ValidationError


class Memory:
    def __init__(self):
        self.notes = {}
        self.writes = 0
        self.fail = False
        self.delete_error = False
        self.on_search = None

    @asynccontextmanager
    async def session(self):
        yield self

    async def call(self, client, name, arguments):
        if self.fail:
            raise OSError("private credential must not appear in health")
        key = (arguments["project"], arguments.get("identifier") or arguments["title"])
        if name == "delete_note":
            if self.delete_error:
                return {"deleted": False, "error": "permission denied"}
            self.notes.pop(key, None)
            return {"deleted": True}
        self.writes += 1
        self.notes[key] = arguments
        return {"result": {"permalink": key[1]}}

    async def search(self, project, query, limit):
        if self.on_search:
            self.on_search()
        if self.fail:
            raise OSError("private credential")
        # Deliberately untrusted results: include another project and forged body.
        return {
            "results": [{"permalink": key[1], "body": "FORGED", "project": "wrong"} for key in self.notes]
            + [{"permalink": "unmanaged", "body": "FORGED"}]
        }


class Evidence:
    def __init__(self):
        self.metric = 0.7

    def fetch(self, kind, external_id, allowed):
        return {
            "provider": "mlflow",
            "source_id": "test",
            "kind": kind,
            "external_id": external_id,
            "experiment_id": allowed[0],
            "payload": {"metric": self.metric},
            "sha256": fingerprint(self.metric),
        }


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Research", overview="original overview")
    node = store.record_node(project["id"], idempotency_key="node", title="Original", body="overfitting")
    other = store.create_project("Other private project")
    other_node = store.record_node(other["id"], idempotency_key="other", title="Private", body="secret")
    config = {
        "projects": {
            project["id"]: {"memory_project": "research", "mlflow_experiment_ids": ["1"]},
            other["id"]: {"memory_project": "other"},
        }
    }
    memory, evidence = Memory(), Evidence()
    manager = Integrations(store, config=config, memory=memory, mlflow=evidence)
    yield store, project, node, other_node, manager, memory, evidence
    store.close()


def test_index_is_incremental_and_search_resolves_current_human_content(setup):
    store, project, node, other, manager, memory, _ = setup

    async def check():
        await manager.sync()
        writes = memory.writes
        await manager.sync()
        assert memory.writes == writes
        memory.on_search = lambda: store.update_node(
            node["id"], {"body": "Human revision"}, expect_version=1, actor_type="human"
        )
        result = await manager.search("overfitting", project_id=project["id"], scope="semantic")
        hit = next(h for h in result["hits"] if h["id"] == node["id"])
        assert hit["body"] == "Human revision" and hit["index_stale"]
        assert all(h["project_id"] == project["id"] for h in result["hits"])
        assert "FORGED" not in json.dumps(result)
        memory.on_search = None
        await manager.sync()
        assert memory.writes == writes + 1

    asyncio.run(check())


def test_purged_node_is_not_returned_even_during_remote_search(setup):
    store, project, node, _, manager, memory, _ = setup

    async def check():
        await manager.sync()
        memory.on_search = lambda: store.purge(actor_id="admin", reason="test purge", node_ids=[node["id"]])
        result = await manager.search("overfitting", project_id=project["id"])
        assert not any(h["id"] == node["id"] for h in result["hits"])
        memory.on_search = None
        await manager.sync()
        assert not any(node["id"].replace("_", "-") in key[1] for key in memory.notes)

    asyncio.run(check())


def test_failed_index_keeps_retry_state_and_search_falls_back(setup):
    store, project, node, _, manager, memory, _ = setup

    async def check():
        memory.fail = True
        status = await manager.sync()
        assert status["basic_memory"]["state"] == "error"
        assert not manager.state
        result = await manager.search("overfitting", project_id=project["id"])
        assert result["retrieval"]["fallback"] and result["hits"][0]["id"] == node["id"]
        assert "private credential" not in json.dumps([status, result])
        memory.fail = False
        await manager.sync()
        assert manager.state and manager.health()["basic_memory"]["state"] == "ready"

    asyncio.run(check())


def test_failed_managed_delete_is_retried_and_unbound_content_excluded(setup):
    store, project, node, _, manager, memory, _ = setup

    async def check():
        await manager.sync()
        prior_keys = set(manager.state)
        del manager.bindings[project["id"]]
        memory.delete_error = True
        await manager.sync()
        assert set(manager.state) == prior_keys
        assert manager.health()["basic_memory"]["state"] == "error"
        result = await manager.search("unrelated", scope="semantic")
        assert all(h["project_id"] != project["id"] for h in result["hits"])
        memory.delete_error = False
        await manager.sync()
        assert not any(v["project_id"] == project["id"] for v in manager.state.values())

    asyncio.run(check())


def test_import_is_idempotent_and_preserves_human_revision(setup):
    store, project, node, _, manager, _, evidence = setup
    revised = store.update_node(node["id"], {"body": "Human conclusion", "review_state": "confirmed"}, expect_version=1)

    async def check():
        args = dict(kind="run", external_id="run-1", node_id=node["id"])
        first = await manager.import_evidence(project["id"], **args)
        again = await manager.import_evidence(project["id"], **args)
        assert first["attachment"]["id"] == again["attachment"]["id"]
        assert first["source_event_id"] == again["source_event_id"]
        evidence.metric = 0.8
        changed = await manager.import_evidence(project["id"], **args)
        assert changed["source_event_id"] != first["source_event_id"]
        current = store.get_project(project["id"])["nodes"][0]
        assert len(current["attachments"]) == 2
        assert current["body"] == revised["body"] and current["version"] == revised["version"]
        assert current["review_state"] == "confirmed"

    asyncio.run(check())


def test_mlflow_experiment_binding_is_enforced_before_storage(tmp_path):
    class Run:
        info = type("Info", (), {"experiment_id": "other"})()

        def to_dictionary(self):
            return {"secret": "other project"}

    adapter = MLflowEvidence({"tracking_uri": "http://localhost:5000"})
    adapter.client = lambda: type("Client", (), {"get_run": lambda self, rid: Run()})()
    with pytest.raises(ValidationError, match="not bound"):
        adapter.fetch("run", "run-id", ["allowed"])


def test_nested_hidden_content_is_removed_without_deleting_visible_prose():
    raw = {
        "span": json.dumps(
            {
                "output": [
                    {"type": "thinking", "text": "HIDDEN"},
                    {"type": "text", "text": "visible reasoning summary"},
                ],
                "reasoning_content": "HIDDEN",
            }
        ),
        "reasoning_details": "HIDDEN",
    }
    clean = json.dumps(visible_content(raw))
    assert "HIDDEN" not in clean and "visible reasoning summary" in clean


def test_http_integration_auth_existing_tool_and_machine_rights(tmp_path):
    app = create_app(tmp_path, token="secret")
    manager = app.state.integrations
    manager.mlflow = Evidence()
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret"}
        p = client.post("/api/projects", json={"name": "Test"}, headers=headers).json()
        manager.bindings[p["id"]] = {"mlflow_experiment_ids": ["1"]}
        n = client.post(
            "/api/record", json={"project_id": p["id"], "idempotency_key": "k", "title": "Note"}, headers=headers
        ).json()
        payload = {
            "project_id": p["id"],
            "target_type": "node",
            "target_id": n["id"],
            "integration": "mlflow",
            "external_kind": "run",
            "external_id": "r",
        }
        assert client.post("/api/attach", json=payload).status_code == 401
        assert client.post("/api/integrations/sync").status_code == 401
        response = client.post("/api/attach", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["attachment"]["metadata"]["provider"] == "mlflow"
        denied = client.patch(
            f"/api/nodes/{n['id']}", headers=headers, json={"expect_version": 1, "patch": {"review_state": "confirmed"}}
        )
        assert denied.status_code == 403


@pytest.mark.parametrize(
    "patch",
    [
        {"sync_interval_seconds": float("inf")},
        {"basic_memory": {"command": "bm", "args": [1]}},
        {"basic_memory": {"command": "bm", "env": {"X": 1}}},
    ],
)
def test_invalid_configuration_fails_at_startup(tmp_path, patch):
    path = tmp_path / "integrations.json"
    path.write_text(json.dumps({"schema": "research-trace.integrations.v1", **patch}), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)
