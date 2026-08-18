from __future__ import annotations

from fastapi.testclient import TestClient

from research_trace_v2.server import create_app


def test_v2_http_flow_and_write_auth(tmp_path):
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        assert client.get("/api/v2/health").json()["write_protected"] is True
        denied = client.post("/api/v2/projects", json={"name": "RNA"})
        assert denied.status_code == 401
        headers = {"Authorization": "Bearer secret", "X-Trace-Actor": "tester"}
        project = client.post(
            "/api/v2/projects",
            headers=headers,
            json={"name": "RNA", "workspace_keys": ["https://github.com/lab/rna"]},
        ).json()
        chapter = client.post(
            f"/api/v2/projects/{project['id']}/chapters",
            headers=headers,
            json={"name": "数据理解"},
        ).json()
        node = client.post(
            "/api/v2/record",
            headers=headers,
            json={
                "project_id": project["id"],
                "chapter_id": chapter["id"],
                "idempotency_key": "http-1",
                "title": "检查 batch effect",
                "body": "PCA completed",
            },
        ).json()
        assert node["chapter_id"] == chapter["id"]
        invalid = client.patch(
            f"/api/v2/nodes/{node['id']}", headers=headers,
            json={"patch": {"body": "missing version"}},
        )
        assert invalid.status_code == 400
        comment = client.post(
            "/api/v2/comments",
            headers=headers,
            json={
                "project_id": project["id"],
                "target_type": "node",
                "target_id": node["id"],
                "kind": "correction",
                "body": "还不能归因于平台",
            },
        ).json()
        assert comment["author_id"] == "tester"
        detail = client.get(f"/api/v2/projects/{project['id']}").json()
        assert detail["nodes"][0]["review_state"] == "corrected"
        client.post(
            "/api/v2/ingest", headers=headers,
            json={
                "batch_id": "http-batch", "project_id": project["id"],
                "session": {"id": "http-session", "source": "claude-code"},
                "agents": [],
                "events": [{"event_id": "http-event", "event_type": "Stop", "payload": {"ok": True}}],
            },
        ).raise_for_status()
        raw = client.get(f"/api/v2/projects/{project['id']}/raw").json()
        assert raw["items"][0]["id"] == "http-event"
        page = client.get("/").text
        assert "Research Trace" in page
        assert "原始 Session / Agent 历史" in page
        assert "data-edit-node" in page
