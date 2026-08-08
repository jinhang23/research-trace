"""HTTP 层的断言。

内核和写入路径各自有测试，但它们之间还隔着一层：鉴权、状态码、错误体。
这一层出问题的后果和别处不一样——**写入端点漏掉令牌检查，就是公网上的任意写**。
读是公开的（用户的明确选择），所以"哪些端点必须要令牌"是条硬边界，值得机械地全测一遍。
"""

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import trace_core as core  # noqa: E402
import trace_server as S  # noqa: E402
import trace_write as W  # noqa: E402

TOKEN = "test-token-not-a-real-one"  # HTTP header 只能是 ASCII
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": TOKEN, "git": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    with TestClient(app) as c:
        c.root = tmp_path
        c.sd = core.steps_dir_of(tmp_path, "课题")
        yield c


def mkstep(c, **kw):
    s, _ = W.create_step(c.sd, **kw)
    return s


# ------------------------------------------------------- 令牌边界


WRITES = [
    ("POST", "/api/projects", {"name": "另一个"}),
    ("POST", "/api/p/课题/steps", {"title": "x"}),
    ("PATCH", "/api/p/课题/steps/001", {"status": "done"}),
    ("DELETE", "/api/p/课题/steps/001", {"reason": "误建"}),
]


@pytest.mark.parametrize(("method", "path", "payload"), WRITES)
def test_every_write_endpoint_requires_the_token(client, method, path, payload):
    mkstep(client, title="在那儿")
    r = client.request(method, path, json=payload)
    assert r.status_code == 401, f"{method} {path} 没要令牌 —— 这是公网上的任意写"


@pytest.mark.parametrize(("method", "path", "payload"), WRITES)
def test_a_wrong_token_is_not_good_enough(client, method, path, payload):
    mkstep(client, title="在那儿")
    r = client.request(method, path, json=payload, headers={"Authorization": "Bearer wrong-guess"})
    assert r.status_code == 401


def test_reads_stay_public(client):
    """用户的明确选择：读公开，只有写要令牌。"""
    mkstep(client, title="公开可读")
    for path in ("/api/projects", "/api/p/课题/forest", "/api/p/课题/steps/001"):
        assert client.get(path).status_code == 200, path


# ------------------------------------------------------- DELETE


def test_delete_removes_the_step_and_reports_the_damage(client):
    a = mkstep(client, title="根")
    b = mkstep(client, parent=a.id, title="要删的")
    mkstep(client, parent=b.id, title="孩子", body=f"## 为什么\n见 [[{b.id}]]")

    r = client.request("DELETE", f"/api/p/课题/steps/{b.id}",
                       json={"reason": "误建的测试步骤", "by": "human"}, headers=AUTH)
    assert r.status_code == 200
    info = r.json()
    assert info["orphaned"] == ["003"] and info["dangling_refs"] == ["003"]

    assert not (client.sd / b.dirname).exists()
    assert client.get(f"/api/p/课题/steps/{b.id}").status_code == 404
    forest = client.get("/api/p/课题/forest").json()
    assert [s["id"] for s in forest["steps"]] == ["001", "003"]
    assert any(w["code"] == "dangling_parent" for w in forest["warnings"])


def test_delete_without_a_reason_is_a_400_not_a_500(client):
    mkstep(client, title="x")
    for payload in ({}, {"reason": "   "}):
        r = client.request("DELETE", "/api/p/课题/steps/001", json=payload, headers=AUTH)
        assert r.status_code == 400, "缺原因是用户的错，不是服务器的错"
        assert "原因" in r.json()["error"]
    assert (client.sd / "001_x").exists() or list(client.sd.glob("001_*")), "报错之后不该已经删了"


def test_deleting_a_missing_step_is_a_404(client):
    r = client.request("DELETE", "/api/p/课题/steps/999", json={"reason": "随便"}, headers=AUTH)
    assert r.status_code == 404


def test_delete_bumps_the_version_so_open_pages_refresh(client):
    """页面靠 SSE 推的 version 变化来决定重编译。删除不 bump，别人的页面就一直显示已经没了的步骤。"""
    mkstep(client, title="x")
    before = client.get("/api/p/课题/forest").json()["version"]
    client.request("DELETE", "/api/p/课题/steps/001", json={"reason": "误建"}, headers=AUTH)
    assert client.get("/api/p/课题/forest").json()["version"] != before


def test_the_reason_lands_in_project_md(client):
    mkstep(client, title="误建的")
    client.request("DELETE", "/api/p/课题/steps/001",
                   json={"reason": "手滑建的，不是真实验", "by": "human", "date": "2026-08-08"},
                   headers=AUTH)
    text = (core.project_dir(client.root, "课题") / core.PROJECT_NOTE).read_text(encoding="utf-8")
    assert "## 已删除" in text and "手滑建的" in text


def test_a_malformed_body_is_a_400_not_a_traceback(client):
    mkstep(client, title="x")
    r = client.request("DELETE", "/api/p/课题/steps/001",
                       content="{ 这不是 json".encode("utf-8"),
                       headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 400
