from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from research_trace.server import create_app
from research_trace.webapp import INDEX_HTML


def test_http_flow_and_write_auth(tmp_path):
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        assert client.get("/api/health").json()["write_protected"] is True
        denied = client.post("/api/projects", json={"name": "RNA"})
        assert denied.status_code == 401
        headers = {"Authorization": "Bearer secret", "X-Trace-Actor": "tester"}
        project = client.post(
            "/api/projects",
            headers=headers,
            json={"name": "RNA", "workspace_keys": ["https://github.com/lab/rna"]},
        ).json()
        chapter = client.post(
            f"/api/projects/{project['id']}/chapters",
            headers=headers,
            json={"name": "主实验"},
        ).json()
        node = client.post(
            "/api/record",
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
            f"/api/nodes/{node['id']}", headers=headers,
            json={"patch": {"body": "missing version"}},
        )
        assert invalid.status_code == 400
        comment = client.post(
            "/api/comments",
            headers=headers,
            json={
                "project_id": project["id"],
                "target_type": "node",
                "target_id": node["id"],
                "kind": "comment",
                "body": "还不能归因于平台",
            },
        ).json()
        assert comment["author_id"] == "tester"
        # 共享机器 token 背后没有可认证的人，所以只能是 recorder。
        assert comment["author_type"] == "recorder"
        detail = client.get(f"/api/projects/{project['id']}").json()
        assert detail["nodes"][0]["review_state"] == "unreviewed"
        client.post(
            "/api/ingest", headers=headers,
            json={
                "batch_id": "http-batch", "project_id": project["id"],
                "session": {"id": "http-session", "source": "claude-code"},
                "agents": [],
                "events": [{"event_id": "http-event", "event_type": "Stop", "payload": {"ok": True}}],
            },
        ).raise_for_status()
        raw = client.get(f"/api/projects/{project['id']}/raw").json()
        assert raw["items"][0]["id"] == "http-event"
        page = client.get("/").text
        assert "Research Trace" in page
        assert "原始 Session / Agent 历史" in page
        assert "data-edit-node" in page


def test_machine_token_cannot_confirm_or_correct_and_cannot_claim_a_human_identity(tmp_path):
    """共享机器 token 不是"有人在回路里"的证明，不能自我确认。"""
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret", "X-Trace-Actor": "tester"}
        project = client.post("/api/projects", headers=headers, json={"name": "RNA"}).json()
        node = client.post(
            "/api/record", headers=headers,
            json={
                "project_id": project["id"], "idempotency_key": "k1", "title": "t",
                # 请求体自称是人、自称已确认，都必须被忽略。
                "created_by": "human", "review_state": "confirmed",
            },
        ).json()
        assert node["created_by"] == "recorder"
        assert node["review_state"] == "unreviewed"

        for kind in ("confirmation", "correction"):
            denied = client.post(
                "/api/comments", headers=headers,
                json={
                    "project_id": project["id"], "target_type": "node", "target_id": node["id"],
                    "kind": kind, "body": "self service", "author_type": "human",
                    "author_id": "alice",
                },
            )
            assert denied.status_code == 403, kind

        denied_patch = client.patch(
            f"/api/nodes/{node['id']}", headers=headers,
            json={"expect_version": node["version"], "actor_type": "human",
                  "patch": {"review_state": "confirmed"}},
        )
        assert denied_patch.status_code == 403
        detail = client.get(f"/api/projects/{project['id']}").json()
        assert detail["nodes"][0]["review_state"] == "unreviewed"


def test_anonymous_read_is_announced_loudly_when_oauth_is_not_configured(tmp_path, capsys):
    app = create_app(tmp_path, token="secret")
    warning = capsys.readouterr().err
    assert "GitHub OAuth is NOT configured" in warning
    assert "open to" in warning
    with TestClient(app) as client:
        assert client.get("/api/health").json()["anonymous_read"] is True


def test_web_ui_is_accessible_and_content_first(tmp_path):
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


def test_web_ui_keeps_structure_and_record_detail_together(tmp_path):
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


def test_graph_layout_is_deterministic_and_respects_parent_depth(tmp_path):
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


def _js_function(page: str, name: str) -> str:
    """取出一个顶层函数的源码：断言渲染路径时不想被别的函数干扰。"""
    start = page.index(f"function {name}(")
    end = page.index("\n}\n", start) + 3
    return page[start:end]


def test_web_fmt_has_no_unescaped_return_path():
    """存储型 XSS 就长在这里：occurred_at 解析失败时 fmt 把原始字符串原样交回，
    而每个调用点都是未转义地插进 innerHTML。"""
    body = _js_function(INDEX_HTML, "fmt")
    assert "return value;" not in body
    returns = [line.strip() for line in body.splitlines() if line.strip().startswith("return ")]
    assert returns, body
    for statement in returns:
        assert statement.startswith("return esc(") or statement == "return '';", statement


def test_web_fmt_escapes_hostile_timestamps():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    start = INDEX_HTML.index("const esc = value =>")
    source = INDEX_HTML[start:INDEX_HTML.index("function file64", start)]
    check = r"""
const attack = '<img src=x onerror="fetch(\'/api/search\')">';
if (fmt(attack).includes('<')) throw Error('hostile timestamp reached innerHTML unescaped');
if (!fmt(attack).includes('&lt;img')) throw Error('the raw value must still be readable, escaped');
if (fmt('2026-08-18T02:03:04Z').includes('<')) throw Error('a valid date must not produce markup');
if (fmt('') !== '') throw Error('empty stays empty');
if (fmt(null) !== '') throw Error('null stays empty');
"""
    result = subprocess.run(
        [node, "-"], input=source + check, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_web_shows_outbox_recorder_and_backup_health():
    """§10 的健康状态：用户判断"我的东西到底传上去没有"的唯一入口。"""
    assert 'id="healthBtn"' in INDEX_HTML
    assert "$('#healthBtn').onclick" in INDEX_HTML
    assert "async function showHealth()" in INDEX_HTML
    assert "await api('/api/health')" in INDEX_HTML
    for name in ("outboxHealthHtml", "recorderHealthHtml", "backupHealthHtml"):
        assert f"function {name}(" in INDEX_HTML
    # 投递器还没上报时必须说"未上报"，不能画一个绿灯
    assert "还没有投递器上报 outbox 状态。" in INDEX_HTML
    assert "value.outbox" in INDEX_HTML


def test_web_revision_history_is_reachable_from_overview_chapter_and_node():
    """§3.4：被纠正的原文必须保留；/api/revisions 以前从未被调用过。"""
    assert "async function showRevisions(" in INDEX_HTML
    assert "'/api/revisions/' + encodeURIComponent(targetType)" in INDEX_HTML
    for target in ("overview", "chapter", "node"):
        assert f'data-history-type="{target}"' in INDEX_HTML
    assert "document.querySelectorAll('[data-history-type]')" in INDEX_HTML


def test_web_raw_timeline_stays_reachable_and_a_node_can_jump_to_its_sources():
    assert "if (S.chapter) return summaryHtml(S.chapter) + rawHistoryHtml();" in INDEX_HTML
    detail = _js_function(INDEX_HTML, "detailContentHtml")
    assert detail.count("rawHistoryHtml()") == 3, detail
    assert 'data-raw-node="' in INDEX_HTML
    assert "async function showNodeRaw(" in INDEX_HTML
    assert "node.source_event_ids" in INDEX_HTML
    assert "document.querySelectorAll('[data-raw-node]')" in INDEX_HTML


def test_web_search_hits_name_their_project_and_open_it():
    assert "function searchHitHtml(hit)" in INDEX_HTML
    assert 'data-hit-project="${esc(hit.project_id || \'\')}"' in INDEX_HTML
    assert "esc(projectName(hit.project_id))" in INDEX_HTML
    assert "async function openHit(button)" in INDEX_HTML
    assert "S.selectedNodeId = node.id;" in INDEX_HTML
    assert "button.onclick = () => openHit(button)" in INDEX_HTML


def test_search_no_longer_drops_the_truncation_report(tmp_path):
    """存储层算出了「还有多少条没显示」，服务端以前在最后一行把它丢掉，
    于是用户界面永远看不出自己只拿到了一部分（REQUIREMENTS §15）。"""
    app = create_app(tmp_path, token="t")
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        project = client.post("/api/projects", headers=headers, json={"name": "P"}).json()
        client.post("/api/record", headers=headers, json={
            "project_id": project["id"], "idempotency_key": "k", "title": "alpha", "body": "alpha",
        }).raise_for_status()
        for index in range(60):
            client.post("/api/ingest", headers=headers, json={
                "batch_id": f"b{index}", "project_id": project["id"],
                "session": {"id": "s", "source": "claude-code"},
                "events": [{"event_id": f"e{index}", "event_type": "Stop",
                            "payload": {"x": "alpha"}}],
            }).raise_for_status()
        value = client.get("/api/search", params={"q": "alpha", "limit": 10}).json()
    assert value["hits"], "旧结构必须还在，网页和 MCP 都在读 hits"
    assert value["truncated"] is True
    assert value["omitted"]["event"] > 0
    assert value["totals"]["node"] == 1
    assert {hit["scope"] for hit in value["hits"]} >= {"node", "event"}


def test_the_deliverer_can_report_outbox_health_and_health_shows_it(tmp_path):
    """§10 的 outbox 面板此前没有任何客户端上报通道，界面永远显示"未上报"。"""
    app = create_app(tmp_path, token="t")
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        assert "outbox" not in client.get("/api/health", headers=headers).json()
        assert client.post("/api/telemetry/outbox", json={
            "machine": "hpg-node-7", "pending": 3, "sent": 41,
            "oldest_pending_at": "2026-08-19T00:00:00Z", "last_error": None,
        }).status_code == 401, "遥测也要写权限，否则谁都能往健康页上写字"
        client.post("/api/telemetry/outbox", headers=headers, json={
            "machine": "hpg-node-7", "pending": 3, "sent": 41,
            "oldest_pending_at": "2026-08-19T00:00:00Z",
        }).raise_for_status()
        health = client.get("/api/health", headers=headers).json()
    machine = health["outbox"]["machines"][0]
    assert machine["machine"] == "hpg-node-7"
    assert machine["pending"] == 3 and machine["sent"] == 41


def test_a_raw_batch_records_which_credential_delivered_it(tmp_path):
    """投递已经和产生事件的 session 解耦，所以「哪台机器推的」只能来自凭证。"""
    app = create_app(tmp_path, token="t")
    with TestClient(app) as client:
        client.post("/api/ingest", json={
            "batch_id": "b1", "session": {"id": "s", "source": "claude-code"},
            "events": [{"event_id": "e1", "event_type": "Stop", "payload": {}}],
        }, headers={"Authorization": "Bearer t", "X-Trace-Actor": "alice@hpg-node-7"}).raise_for_status()
        row = app.state.store._db.execute(
            "SELECT delivered_by FROM ingest_batches WHERE batch_id='b1'"
        ).fetchone()
    assert row["delivered_by"] == "alice@hpg-node-7"


def test_web_surfaces_truncation_expiry_and_an_unpushed_backup():
    """三件以前只存在于 JSON 里、界面上完全看不出来的事。"""
    assert "function searchTruncationHtml(" in INDEX_HTML
    assert "value.truncated" in INDEX_HTML
    assert "未显示" in INDEX_HTML
    assert "backup.unpushed_commits" in INDEX_HTML
    assert "远端落后" in INDEX_HTML
    assert "function deviceExpiringSoon(" in INDEX_HTML
    assert "trace-login --renew" in INDEX_HTML
    assert "value.anonymous_read" in INDEX_HTML


def test_web_client_never_asserts_its_own_identity():
    """身份只来自凭证。客户端再发 actor_type/created_by 只会让人以为它说了算。"""
    assert "actor_type: 'human'" not in INDEX_HTML
    assert "created_by: 'human'" not in INDEX_HTML


def test_web_gives_a_human_the_only_way_to_close_a_correction():
    """服务端现在对机器凭证的 resolve 返回 403，而界面上以前根本没有这个按钮——
    结果会是纠正永远关不掉。两边必须一起有。"""
    assert 'data-resolve-comment="' in INDEX_HTML
    assert "'/resolve', {method: 'POST'}" in INDEX_HTML
    assert "comment.acknowledged_at" in INDEX_HTML, "Recorder 读过但人还没确认要看得出来"
