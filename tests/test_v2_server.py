from __future__ import annotations

import shutil
import subprocess

import pytest
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
            json={"name": "主实验"},
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


def test_v2_web_ui_is_accessible_and_content_first(tmp_path):
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        page = client.get("/").text

    assert "@media (prefers-reduced-motion: reduce)" in page
    assert '<a class="skip-link" href="#main">' in page
    assert '<label class="sr-only" for="search">' in page
    assert 'aria-controls="searchResults"' in page
    assert '<dialog id="modal" aria-labelledby="modalTitle">' in page
    assert "min-height: 44px" in page
    assert "workspace_key" in page
    assert "Quiet reading layout" in page
    assert "projectHeaderHtml()" in page
    assert "projectMetricsHtml()" not in page
    assert 'class="comment-compose"' in page


def test_v2_web_ui_keeps_structure_and_record_detail_together(tmp_path):
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        page = client.get("/").text

    assert 'class="workspace-body"' in page
    assert 'class="structure-pane"' in page
    assert 'class="record-pane"' in page
    assert 'data-work-view="graph"' in page
    assert 'data-work-view="list"' in page
    assert 'id="fieldChapter"' in page
    assert 'id="fieldReview"' in page
    assert '新的起点（无 parent）' in page
    assert "function layoutGraphNodes" in page
    assert 'data-select-node="' in page
    assert "连线仅表示明确的 parent 关系" in page
    assert "node.parent_id && byId.has(node.parent_id)" in page


def test_v2_graph_layout_is_deterministic_and_respects_parent_depth(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        page = client.get("/").text

    start = page.index("function nodeOrder")
    end = page.index("\nfunction graphSectionHtml", start)
    functions = page[start:end]
    check = r"""
const input = [
  {id: 'a', parent_id: null, occurred_at: '2026-01-01'},
  {id: 'b', parent_id: 'a', occurred_at: '2026-01-02'},
  {id: 'c', parent_id: 'a', occurred_at: '2026-01-03'},
  {id: 'd', parent_id: 'b', occurred_at: '2026-01-04'},
  {id: 'orphan', parent_id: 'missing', occurred_at: '2026-01-05'}
];
const first = layoutGraphNodes(input);
const second = layoutGraphNodes([...input].reverse());
if (JSON.stringify(first.positions) !== JSON.stringify(second.positions)) throw Error('layout changed with input order');
if (!(first.positions.a.depth < first.positions.b.depth && first.positions.b.depth < first.positions.d.depth)) throw Error('parent depth is wrong');
if (!(first.positions.b.column < first.positions.c.column)) throw Error('siblings are not ordered');
if (first.positions.orphan.depth !== 0) throw Error('missing parent must create a root');
"""
    result = subprocess.run(
        [node, "-"], input=functions + check, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
