from __future__ import annotations

import shutil
import subprocess
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from research_trace.server import TeamProjectMap, create_app
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
if (!(first.positions.b.left < first.positions.c.left)) throw Error('siblings are not ordered');
if (first.positions.orphan.depth !== 0) throw Error('missing parent must create a root');
// Reingold-Tilford 的两条定义性质，缺一张图就画错。
if (first.positions.a.left !== (first.positions.b.left + first.positions.c.left) / 2) throw Error('a parent must sit centred over its children');
if (first.positions.c.left - first.positions.b.left < first.cardWidth) throw Error('siblings overlap');
// 每一层各自紧排：b 有孩子、c 没有，两者仍在同一层且只隔一个间距。
if (first.positions.b.top !== first.positions.c.top) throw Error('siblings must share a row');
// 孤儿是另一棵树，必须整个躲开第一棵，而不是压在它上面。
const firstTreeRight = Math.max(first.positions.a.left, first.positions.c.left) + first.cardWidth;
if (first.positions.orphan.left < firstTreeRight) throw Error('a second tree must clear the first');
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

def _js_slice(start_marker: str, end_marker: str) -> str:
    start = INDEX_HTML.index(start_marker)
    return INDEX_HTML[start:INDEX_HTML.index(end_marker, start)]


def _run_js(source: str, check: str) -> None:
    """把页面里的真函数原样跑一遍。断言渲染结果，而不是断言源码里有没有某个字符串。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "-"], input=source + check, text=True, encoding="utf-8",
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def _dataflow_js(*extra: str) -> str:
    """esc / fmt 那一段 + 数据流的渲染路径。S 由每个用例自己摆好。"""
    source = "var S = {project: {chapters: []}, dataflow: null, workView: 'graph', selectedNodeId: null};\n"
    source += _js_slice("const esc = value =>", "function file64")
    for name in ("nodeOrder", "dataflowAvailable", "effectiveWorkView",
                 "dataflowKeyLabel", "layoutDataflowNodes", "dataflowSectionHtml", *extra):
        source += _js_function(INDEX_HTML, name)
    return source


def test_web_dataflow_block_is_absent_until_edges_exist():
    """§10：数据流只在存在明确 artifact 关系时显示。一个恒空的面板是纯噪声，
    所以「登记了产物但没给可比对的键」也一样不画图。"""
    # 视图按钮本身必须挂在同一个判据上，否则会出现一个点进去是空白的按钮
    assert "${dataflowAvailable() ? `<button type=\"button\" data-work-view=\"dataflow\"" in INDEX_HTML
    _run_js(_dataflow_js(), r"""
if (dataflowAvailable()) throw Error('no payload must not count as a data flow');
if (dataflowSectionHtml() !== '') throw Error('an always-empty panel is pure noise');
S.workView = 'dataflow';
if (effectiveWorkView() !== 'graph') throw Error('a stored dataflow view must fall back');

S.dataflow = {nodes: [], edges: [], unkeyed: [], stats: {edges: 0, unkeyed: 3, truncated: false}};
if (dataflowAvailable()) throw Error('registered-but-unkeyed artifacts are still zero edges');
if (dataflowSectionHtml() !== '') throw Error('unkeyed artifacts must not produce an empty graph');

S.project = {chapters: [{id: 'c1', name: '主实验'}]};
S.dataflow = {
  nodes: [
    {id: 'n1', title: '训练', chapter_id: 'c1', occurred_at: '2026-01-01T00:00:00Z'},
    {id: 'n2', title: '评估', chapter_id: 'c1', occurred_at: '2026-01-02T00:00:00Z'}
  ],
  edges: [{from_node_id: 'n1', to_node_id: 'n2', key: 'x'.repeat(64), key_kind: 'sha256', name: 'm.ckpt'}],
  unkeyed: [], stats: {edges: 1, unkeyed: 0, truncated: false}
};
if (!dataflowAvailable()) throw Error('one registered relation is enough to show the view');
if (effectiveWorkView() !== 'dataflow') throw Error('the stored view must come back once edges exist');
if (dataflowSectionHtml() === '') throw Error('edges exist but nothing was drawn');
""")


def test_web_says_when_artifacts_were_left_at_the_default_reference_direction():
    """空图有两种可修的原因，界面必须分别说出来。

    `direction` 默认是 `reference`，reference 两边都不参与 join，所以「键给得完美、
    只是没人改方向」画出来的图和「这个项目没有产物」完全一样。这一条比缺键更容易
    发生（键要主动写错，方向只要不写就错），不说出来就没人查得到。
    """
    # 结构面板的提示：两种缺口各一句，都只在真的没有边时出现
    hint = _js_slice("const gaps = view !== 'dataflow'", "const chapterOptions")
    assert "flowStats.unkeyed" in hint and "flowStats.unlabeled_direction" in hint
    assert "!dataflowAvailable()" in hint  # 有边时一个字都不该出现

    _run_js(_dataflow_js(), r"""
S.project = {chapters: [{id: 'c1', name: '主实验'}]};
S.dataflow = {
  nodes: [
    {id: 'n1', title: '训练', chapter_id: 'c1', occurred_at: '2026-01-01T00:00:00Z'},
    {id: 'n2', title: '评估', chapter_id: 'c1', occurred_at: '2026-01-02T00:00:00Z'}
  ],
  edges: [{from_node_id: 'n1', to_node_id: 'n2', key: 'x'.repeat(64), key_kind: 'sha256', name: 'm.ckpt'}],
  unkeyed: [], stats: {edges: 1, unkeyed: 0, unlabeled_direction: 4, truncated: false}
};
const withGap = dataflowSectionHtml();
if (!/reference/.test(withGap)) throw Error('the panel must name the reference-direction gap');
if (!/\b4\b/.test(withGap)) throw Error('the panel must say how many');

S.dataflow.stats.unlabeled_direction = 0;
if (/reference/.test(dataflowSectionHtml())) throw Error('nothing to report must print nothing');
""")


def test_web_dataflow_edge_says_what_it_joined_on_and_crosses_chapters():
    """§8 的全部立场是「只画登记过的、不猜」，所以每条边都要能说出凭哪个键连的。
    边可以跨 Chapter（消融吃主实验的产物），但 Chapter 之间仍然互不相连——
    这个视图因此不画任何 Chapter 容器，Chapter 只是节点卡片上的一行标签。"""
    _run_js(_dataflow_js(), r"""
S.project = {chapters: [{id: 'c1', name: '主实验'}, {id: 'c2', name: '消融实验'}]};
S.dataflow = {
  nodes: [
    {id: 'n1', title: '训练主模型', chapter_id: 'c1', occurred_at: '2026-01-01T00:00:00Z'},
    {id: 'n2', title: '消融：去掉注意力', chapter_id: 'c2', occurred_at: '2026-01-02T00:00:00Z'},
    {id: 'n3', title: '再训一次覆盖 latest', chapter_id: 'c2', occurred_at: '2026-01-03T00:00:00Z'}
  ],
  edges: [
    {from_node_id: 'n1', to_node_id: 'n2', key: 'a'.repeat(64), key_kind: 'sha256', name: 'model.ckpt'},
    {from_node_id: 'n1', to_node_id: 'n3', key: 's3://lab/latest.ckpt', key_kind: 'uri', name: 'latest.ckpt'}
  ],
  unkeyed: [], stats: {edges: 2, unkeyed: 0, truncated: false}
};
const html = dataflowSectionHtml();
if (!html.includes('a'.repeat(64))) throw Error('the sha256 it joined on must be visible');
if (!html.includes('s3://lab/latest.ckpt')) throw Error('the uri it joined on must be visible');
if (!html.includes('同一份内容')) throw Error('sha256 edges must read as same bytes');
if (!html.includes('同一个位置')) throw Error('uri edges must read as same location');
if (!html.includes('覆盖')) throw Error('a location key can be a later run overwriting the file; say so');
if (!html.includes('主实验') || !html.includes('消融实验')) throw Error('cross-chapter membership must be readable');
if (html.includes('chapter-map-title')) throw Error('the data flow must not draw chapters as containers');
if (!html.includes('model.ckpt')) throw Error('the artifact name is part of the evidence');
if ((html.match(/class="flow-edge /g) || []).length !== 2) throw Error('one edge per registered relation');
""")


def test_web_dataflow_layout_survives_a_cycle_and_ignores_dangling_edges():
    """存储层只做一次键 join、不按时间过滤方向，所以 A→B→A 是可能出现的。
    布局里的深度计算必须自带环保护，否则一条环就能让页面转不出来。"""
    _run_js(_dataflow_js(), r"""
const nodes = [
  {id: 'a', title: 'A', chapter_id: 'c1', occurred_at: '2026-01-01'},
  {id: 'b', title: 'B', chapter_id: 'c1', occurred_at: '2026-01-02'},
  {id: 'c', title: 'C', chapter_id: 'c1', occurred_at: '2026-01-03'}
];
const edges = [
  {from_node_id: 'a', to_node_id: 'b', key: 'k1', key_kind: 'sha256'},
  {from_node_id: 'b', to_node_id: 'c', key: 'k2', key_kind: 'sha256'},
  {from_node_id: 'c', to_node_id: 'a', key: 'k3', key_kind: 'sha256'},
  {from_node_id: 'a', to_node_id: 'a', key: 'k4', key_kind: 'sha256'},
  {from_node_id: 'ghost', to_node_id: 'b', key: 'k5', key_kind: 'sha256'}
];
const first = layoutDataflowNodes(nodes, edges);
const second = layoutDataflowNodes([...nodes].reverse(), [...edges].reverse());
if (JSON.stringify(first.positions) !== JSON.stringify(second.positions)) throw Error('layout changed with input order');
['a', 'b', 'c'].forEach(id => {
  if (!Number.isFinite(first.positions[id].depth)) throw Error('the cycle swallowed ' + id);
});
if (first.width <= 0 || first.height <= 0) throw Error('empty canvas');

/* 无环时才谈得上「生产者在上、消费者在下」；环里没有拓扑序，上面那半只要求
   算得出来、算得稳。 */
const chain = layoutDataflowNodes(nodes, edges.slice(0, 2));
if (!(chain.positions.a.depth < chain.positions.b.depth)) throw Error('a producer must sit above its consumer');
if (!(chain.positions.b.depth < chain.positions.c.depth)) throw Error('depth must follow the chain');
const only = layoutDataflowNodes([nodes[0]], []);
if (only.positions.a.depth !== 0) throw Error('an isolated node is a root');
""")


def test_web_dataflow_long_edges_do_not_run_through_the_cards_between_them():
    """跨层的边如果按「直上直下」画，在同一列上就是一条从中间那些节点身上碾过去的
    竖线；两条边的中点还会重合，标签叠成一团。图读不懂就等于没画。"""
    _run_js(_dataflow_js(), r"""
S.project = {chapters: [{id: 'c1', name: '主实验'}]};
const at = day => `2026-01-0${day}T00:00:00Z`;
S.dataflow = {
  nodes: [1, 2, 3, 4].map(i => ({id: 'n' + i, title: 'N' + i, chapter_id: 'c1', occurred_at: at(i)})),
  edges: [
    {from_node_id: 'n1', to_node_id: 'n2', key: 'k1', key_kind: 'sha256', name: 'a'},
    {from_node_id: 'n2', to_node_id: 'n3', key: 'k2', key_kind: 'sha256', name: 'b'},
    {from_node_id: 'n3', to_node_id: 'n4', key: 'k3', key_kind: 'sha256', name: 'c'},
    {from_node_id: 'n1', to_node_id: 'n4', key: 'k4', key_kind: 'sha256', name: 'd'}
  ],
  unkeyed: [], stats: {edges: 4, unkeyed: 0, truncated: false}
};
const html = dataflowSectionHtml();
const paths = [...html.matchAll(/<path class="flow-edge[^"]*" d="([^"]+)"/g)].map(m => m[1]);
if (paths.length !== 4) throw Error('expected one path per edge, got ' + paths.length);
const spanning = paths.filter(d => d.split(' H ').length > 2);
if (spanning.length !== 1) throw Error('the n1->n4 edge must be routed around, not straight down a column');
const lane = Number(spanning[0].split(' H ')[1].split(' ')[0]);
const canvas = Number(html.match(/width:(\d+(?:\.\d+)?)px;height/)[1]);
if (!(lane > 20 + 184)) throw Error('the detour lane sits inside the card column');
if (!(canvas > lane)) throw Error('the canvas must be wide enough to show the detour');

const labels = [...html.matchAll(/<text class="flow-edge-label" x="([^"]+)" y="([^"]+)"/g)].map(m => m[1] + ',' + m[2]);
if (new Set(labels).size !== labels.length) throw Error('two edge labels landed on the same point');
""")


def test_web_dataflow_escapes_hostile_artifact_metadata():
    """artifact 的 name / uri / 机器路径是任何持凭证的机器能写的自由文本，
    整块图都是拼出来直插 innerHTML 的。fmt 那次存储型 XSS 不许在这里重犯。"""
    _run_js(_dataflow_js(), r"""
const attack = '<img src=x onerror="fetch(\'/api/search\')">';
S.project = {chapters: [{id: 'c1', name: attack}]};
S.dataflow = {
  nodes: [
    {id: 'n1', title: attack, chapter_id: 'c1', occurred_at: attack},
    {id: 'n2', title: attack, chapter_id: 'nope', occurred_at: attack}
  ],
  edges: [{from_node_id: 'n1', to_node_id: 'n2', key: attack, key_kind: attack, name: attack}],
  unkeyed: [], stats: {edges: 1, unkeyed: 0, truncated: false}
};
const html = dataflowSectionHtml();
if (html.includes('<img')) throw Error('hostile artifact metadata reached innerHTML unescaped');
if (!html.includes('&lt;img')) throw Error('the raw value must still be readable, escaped');
if (dataflowKeyLabel(attack).includes('<')) throw Error('an unknown key_kind is returned verbatim');
""")


def test_web_backup_capacity_alarm_and_missing_objects_are_visible():
    """§13「Git 接近容量阈值时告警」的落点。sync_git_backup 每轮都算 capacity，
    界面不渲染的话那次计算等于没做。"""
    source = "var S = {};\n"
    source += _js_slice("const esc = value =>", "function file64")
    source += _js_slice("const HEALTH_STATE_PILL", "function healthCardHtml")
    for name in ("healthCardHtml", "bytesLabel", "backupCapacityLines", "backupHealthHtml"):
        source += _js_function(INDEX_HTML, name)
    _run_js(source, r"""
const ok = backupHealthHtml({backup: {enabled: true, last_success_at: '2026-08-18T00:00:00Z',
  unpushed_commits: 0, capacity: {level: 'ok', export_bytes: 1024, volumes: 2, warnings: []},
  missing_objects: []}});
if (!ok.includes('正常')) throw Error('a healthy backup must not be painted as a problem');
if (!ok.includes('2 个分卷')) throw Error('volume count is the shape of the new backup tree');

const bad = backupHealthHtml({backup: {enabled: true, last_success_at: '2026-08-18T00:00:00Z',
  unpushed_commits: 0,
  capacity: {level: 'critical', export_bytes: 1073741824, repository_bytes: 5368709120, volumes: 4,
    largest_file: 'volumes/2026/tables/events.0001.jsonl', largest_file_bytes: 99614720,
    limits: {}, warnings: ['largest backup file <img src=x> is 99614720 bytes']},
  missing_objects: ['ab/cd/ef']}});
if (!bad.includes('严重')) throw Error('a critical capacity level must escalate the card');
if (!bad.includes('95.0 MiB')) throw Error('the largest file size must be readable');
if (!bad.includes('5.0 GiB')) throw Error('repository size must be shown');
if (!bad.includes('4 个分卷')) throw Error('volume count missing');
if (!bad.includes('99614720 bytes')) throw Error('the warning text itself must be shown');
if (bad.includes('<img')) throw Error('a backup warning reached innerHTML unescaped');
if (!bad.includes('附件对象在导出时已不存在')) throw Error('missing attachment objects must be surfaced');

const warn = backupHealthHtml({backup: {enabled: true, last_success_at: '2026-08-18T00:00:00Z',
  unpushed_commits: 0, capacity: {level: 'warn', export_bytes: 0, volumes: 1, warnings: ['close']},
  missing_objects: []}});
if (!warn.includes('需要注意')) throw Error('a warn level must not read as healthy');
if (bytesLabel('nonsense') !== '—') throw Error('an unparsable size must not be echoed back');
""")


def test_dataflow_endpoint_feeds_the_web_view(tmp_path):
    """页面的取数入口。两个 Node 登记同一个 sha256（一个 output、一个 input）
    就该连出一条边；只登记 reference 的项目仍然是空图而不是错误（§8）。"""
    app = create_app(tmp_path, token="secret")
    headers = {"Authorization": "Bearer secret", "X-Trace-Actor": "tester"}
    digest = "b" * 64
    with TestClient(app) as client:
        project = client.post("/api/projects", headers=headers, json={"name": "flow"}).json()
        chapters = [
            client.post(f"/api/projects/{project['id']}/chapters", headers=headers,
                        json={"name": name}).json()
            for name in ("主实验", "消融实验")
        ]
        nodes = [
            client.post("/api/record", headers=headers, json={
                "project_id": project["id"], "chapter_id": chapter["id"],
                "idempotency_key": f"flow-{index}", "title": title, "body": "b",
            }).json()
            for index, (chapter, title) in enumerate(zip(chapters, ("训练", "消融")))
        ]
        for node, direction in zip(nodes, ("output", "input")):
            attached = client.post("/api/attach", headers=headers, json={
                "project_id": project["id"], "target_type": "node", "target_id": node["id"],
                "name": "model.ckpt", "direction": direction, "sha256": digest,
            })
            assert attached.status_code == 200, attached.text
        flow = client.get(f"/api/projects/{project['id']}/dataflow").json()

    assert [edge["from_node_id"] for edge in flow["edges"]] == [nodes[0]["id"]]
    edge = flow["edges"][0]
    assert edge["to_node_id"] == nodes[1]["id"]
    assert (edge["key"], edge["key_kind"]) == (digest, "sha256")
    # 跨 Chapter 是数据流的常态，视图靠 chapter_id 把这件事说清楚
    assert {node["chapter_id"] for node in flow["nodes"]} == {chapters[0]["id"], chapters[1]["id"]}
    assert flow["stats"]["unkeyed"] == 0


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


# --------------------------------------------------------------------------------------
# §7 第三种 workspace key：团队配置映射
# --------------------------------------------------------------------------------------

# 另一台机器上同一个仓库的绝对 cwd。§7 的第一句就是它不能当项目身份。
WINDOWS_CWD_KEY = r"C:\Users\bob\rna"


class _FakeGitHub:
    """只够走完一次 OAuth 回调：团队映射的写入口是管理员端点，必须有浏览器会话。"""

    def __init__(self):
        self.profile = {"id": 101, "login": "alice", "name": "Alice", "avatar_url": ""}

    def authorize_url(self, *, state, challenge):
        return f"https://github.test/login?state={state}&code_challenge={challenge}"

    def exchange_code(self, *, code, verifier):
        return "access-token"

    def fetch_user(self, access_token):
        return dict(self.profile)

    def active_org_member(self, access_token, organization):
        return False


def _admin_client(tmp_path):
    app = create_app(
        tmp_path, token="t", github_client_id="cid", github_client_secret="sec",
        public_url="https://trace.example", session_secret="s" * 48,
        github_admins="alice", oauth_client=_FakeGitHub(),
    )
    client = TestClient(app, base_url="https://trace.example")
    start = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get(
        "/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    return app, client, {"X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"]}


def test_a_team_mapping_rule_lands_a_fresh_clone_on_the_same_central_project(tmp_path):
    """§7 的第三种发现方式：新机器上 clone 同一个仓库，marker key 是全新的随机串，
    Git remote 也还没在中央登记过——只有团队映射能把它接住，否则就是又一个重复项目。"""
    app = create_app(tmp_path, token="t")
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        project = client.post("/api/projects", headers=headers, json={
            "name": "RNA", "workspace_keys": ["rt-ws-original"],
        }).json()
        app.state.team_map.add(
            pattern="https://github.com/lab/rna*", project_id=project["id"],
            note="lab monorepo", actor="alice",
        )
        value = client.post("/api/context", headers=headers, json={
            "workspace_keys": ["rt-ws-fresh-clone", "https://github.com/lab/rna"],
        }).json()

    assert value["matched"] is True
    assert value["project"]["id"] == project["id"]
    assert value["resolved_by"] == "team_mapping"
    assert value["matched_rules"][0]["created_by"] == "alice"
    # 命中后把 key 登记上去，下一次直接走第一/第二种发现方式，不必再过映射
    assert {k["workspace_key"] for k in value["project"]["workspace_keys"]} == {
        "rt-ws-original", "rt-ws-fresh-clone", "https://github.com/lab/rna",
    }


def test_an_ambiguous_team_mapping_refuses_to_create_even_when_asked_to(tmp_path):
    """§7 硬要求：不确定时进入待确认状态。create_if_missing 也不能把它推过去。"""
    app = create_app(tmp_path, token="t")
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        alpha = client.post("/api/projects", headers=headers, json={"name": "Alpha"}).json()
        beta = client.post("/api/projects", headers=headers, json={"name": "Beta"}).json()
        app.state.team_map.add(pattern="https://github.com/lab/*", project_id=alpha["id"],
                               note="", actor="alice")
        app.state.team_map.add(pattern="https://github.com/*/shared", project_id=beta["id"],
                               note="", actor="bob")
        value = client.post("/api/context", headers=headers, json={
            "workspace_keys": ["https://github.com/lab/shared"],
            "create_if_missing": True, "project_name": "Would be a duplicate",
        }).json()
        names = {p["name"] for p in client.get("/api/projects", headers=headers).json()["projects"]}

    assert value["matched"] is False
    assert value["pending_confirmation"] is True
    assert {c["project_id"] for c in value["candidates"]} == {alpha["id"], beta["id"]}
    assert {c["project_name"] for c in value["candidates"]} == {"Alpha", "Beta"}
    assert names == {"Alpha", "Beta"}, "待确认状态下一个新项目都不许出现"


def test_team_mapping_rules_are_admin_only_and_record_who_added_them_and_when(tmp_path):
    """§7：映射本身要能被审计。规则带 created_by/created_at，增删都进 history，
    而 created_by 来自凭证不是请求体。"""
    app, client, headers = _admin_client(tmp_path)
    with client:
        project = client.post("/api/projects", headers=headers, json={"name": "RNA"}).json()
        anonymous = client.post("/api/team/mapping", json={
            "pattern": "https://github.com/lab/rna*", "project_id": project["id"],
            "created_by": "mallory",
        })
        assert anonymous.status_code == 403, "没有 CSRF 的写入必须被挡住"

        rule = client.post("/api/team/mapping", headers=headers, json={
            "pattern": "https://github.com/lab/rna*", "project_id": project["id"],
            "note": "lab monorepo", "created_by": "mallory",
        }).json()
        assert rule["created_by"] == "alice" and rule["created_at"]

        listing = client.get("/api/team/mapping").json()
        assert [item["id"] for item in listing["rules"]] == [rule["id"]]
        assert listing["history"][0]["action"] == "add"
        assert listing["history"][0]["actor"] == "alice"

        assert client.delete(f"/api/team/mapping/{rule['id']}", headers=headers).json()["removed"]
        assert client.get("/api/team/mapping").json()["rules"] == []

    # 删除也留痕，而且落在磁盘上：重启后审计记录还在
    reloaded = TeamProjectMap(tmp_path / "team-project-map.json")
    assert [item["action"] for item in reloaded.history()] == ["remove", "add"]
    assert reloaded.rules() == []


def test_a_wildcard_only_rule_is_refused(tmp_path):
    """一条 `*` 就能把每个工作区映射到同一个项目上——那是「静默落进错误项目」，
    和「静默创建重复项目」一样是 §7 要防的东西。"""
    app, client, headers = _admin_client(tmp_path)
    with client:
        project = client.post("/api/projects", headers=headers, json={"name": "RNA"}).json()
        for pattern in ("*", "**", "/srv/*"):
            denied = client.post("/api/team/mapping", headers=headers, json={
                "pattern": pattern, "project_id": project["id"],
            })
            assert denied.status_code == 400, pattern


def test_the_backup_capacity_alert_reaches_health_and_the_service_log(tmp_path, monkeypatch, capsys):
    """§13：「Git 接近容量阈值时告警。」备份那一层每轮都算了这个数，服务端不把它
    带进 /api/health 的话，那次计算谁也看不见——健康卡片依旧一片正常，直到某天
    push 被拒。"""
    from research_trace import server as S

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    capacity = {"level": "critical", "warnings": ["export is 5 GiB, above the repository limit"],
                "export_bytes": 5 * 1024 ** 3}

    def fake_sync(store, backup_repo, **_kwargs):
        return {"changed": True, "pushed": True, "unpushed_commits": 0,
                "capacity": capacity, "missing_objects": ["sha256:deadbeef"]}

    monkeypatch.setattr(S, "sync_git_backup", fake_sync)
    app = create_app(tmp_path / "data", token="t", backup_repo=repo, backup_interval_hours=24)
    with TestClient(app) as client:
        # 备份循环是后台任务，第一轮要经过一次线程跳转；轮询而不是赌它已经跑完。
        for _ in range(100):
            backup = client.get("/api/health", headers={"Authorization": "Bearer t"}).json()["backup"]
            if backup.get("capacity"):
                break
            time.sleep(0.02)

    assert backup["capacity"]["level"] == "critical"
    assert backup["capacity"]["warnings"] == capacity["warnings"]
    assert backup["missing_objects"] == ["sha256:deadbeef"]
    assert "backup capacity critical" in capsys.readouterr().err, "无人值守的部署也要看得见"


def test_the_dataflow_view_is_reachable_over_http_and_stays_opt_in(tmp_path):
    """§8 的派生视图只有一条 join 查询和一个界面；服务端不转发 include_dataflow 的话，
    MCP 侧的开关会被静默吞掉，客户端只会看到「这台服务器不会算数据流」。"""
    app = create_app(tmp_path, token="t")
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        project = client.post("/api/projects", headers=headers, json={
            "name": "RNA", "workspace_keys": ["rt-ws-flow"],
        }).json()
        quiet = client.post("/api/context", headers=headers, json={
            "workspace_keys": ["rt-ws-flow"],
        }).json()
        assert "dataflow" not in quiet["project"], "热路径默认不算这张图"

        asked = client.post("/api/context", headers=headers, json={
            "workspace_keys": ["rt-ws-flow"], "include_dataflow": True,
        }).json()
        assert asked["project"]["dataflow"]["edges"] == []

        # 网页不必为了一张图重拉整个 context
        standalone = client.get(f"/api/projects/{project['id']}/dataflow", headers=headers).json()
    assert standalone["edges"] == []
    assert "stats" in standalone, "空图也要能分辨「没产物」和「登记时忘了给键」"


def test_an_absolute_cwd_is_refused_as_a_project_identity(tmp_path):
    """审计复核：workspace key 以前没有任何形态校验，绝对 cwd 可以直接当项目身份。"""
    app = create_app(tmp_path, token="t")
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        denied = client.post("/api/context", headers=headers, json={
            "workspace_keys": ["/home/alice/rna"],
            "create_if_missing": True, "project_name": "RNA",
        })
        assert denied.status_code == 400
        assert client.get("/api/projects", headers=headers).json()["projects"] == []

        # 混着来时只丢掉路径那一个并回报，剩下的身份仍然成立
        value = client.post("/api/context", headers=headers, json={
            "workspace_keys": [WINDOWS_CWD_KEY, "rt-ws-shared"],
            "create_if_missing": True, "project_name": "RNA",
        }).json()
        assert value["matched"] is True
        assert value["rejected_workspace_keys"][0]["workspace_key"] == WINDOWS_CWD_KEY
        assert [k["workspace_key"] for k in value["project"]["workspace_keys"]] == ["rt-ws-shared"]


def test_web_graph_cards_carry_their_own_size():
    """两张图的卡片都必须把宽高**写在自己身上**，不能指望 CSS 里有个尺寸等着它。

    真出过事：一次改动把 `.graph-node` 的基础样式块（含 position/width/height）
    整个删掉了，结构图因为改成了内联尺寸没事，数据流却还在等 CSS 给它宽高 ——
    结果是一堆按内容自适应的卡片全挤在左上角，而边还画在原来的坐标上。
    全套测试当时一条都没红：它们只比对 HTML 字符串，看不见样式表少了什么。

    所以这条从两个方向钉死：卡片自带尺寸，且那个尺寸来自布局函数本人。
    """
    _run_js(_dataflow_js(), r"""
S.project = {chapters: [{id: 'c1', name: '主实验'}]};
S.dataflow = {
  nodes: [
    {id: 'n1', title: '预处理', chapter_id: 'c1'},
    {id: 'n2', title: '训练', chapter_id: 'c1'}
  ],
  edges: [{from_node_id: 'n1', to_node_id: 'n2', key: 'sha256:abc', key_kind: 'sha256'}],
  unkeyed: [],
  stats: {edges: 1, unkeyed: 0, truncated: false}
};
const layout = layoutDataflowNodes(S.dataflow.nodes, S.dataflow.edges);
const html = dataflowSectionHtml();
const size = new RegExp('width:' + layout.cardWidth + 'px;height:' + layout.cardHeight + 'px');
const cards = html.split('class="graph-node').length - 1;
if (cards !== 2) throw Error('expected one card per node, got ' + cards);
if ((html.match(new RegExp(size.source, 'g')) || []).length !== cards)
  throw Error('every data flow card must carry the size its layout computed');
""")

    # 结构图同样：布局算出来的那个尺寸必须原样出现在卡片上。
    _run_js(
        "var S = {project: {chapters: []}, selectedNodeId: null};\n"
        + _js_slice("const esc = value =>", "function file64")
        + _js_function(INDEX_HTML, "nodeOrder")
        + _js_function(INDEX_HTML, "nodeReview")
        + _js_slice("const TREE_NODE_W", "\n/* 缩放。"),
        r"""
const nodes = [
  {id: 'a', parent_id: null, title: '起点', occurred_at: '2026-01-01', review_state: 'unreviewed', comments: []},
  {id: 'b', parent_id: 'a', title: '接着', occurred_at: '2026-01-02', review_state: 'unreviewed', comments: []}
];
const layout = layoutGraphNodes(nodes);
const html = graphSectionHtml({id: 'c1', name: '主实验'}, nodes);
const size = 'width:' + layout.cardWidth + 'px;height:' + layout.cardHeight + 'px';
if ((html.split(size).length - 1) !== 2)
  throw Error('every structure card must carry the size its layout computed');
""")
