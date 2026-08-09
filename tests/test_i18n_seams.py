"""双语接缝上的断言。

这一批缺口的共同点还是：**没有一个模块拥有它们**。六个 agent 在各自的文件里
把双语做对了，然后在报告里互相点名请对方开口子——core 请 write 开 lang、
web 请 core 加 digest、server 请 write 加 drop_project_translation——
谁也不能改谁的文件，于是口子一个都没开。

按接缝组织，不按模块。
"""

from pathlib import Path

import pytest

import trace_core as core
import trace_mcp as M
import trace_write as W

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import trace_server as S  # noqa: E402

TOKEN = "i18n-seam-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture()
def proj(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


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


# ───────────────────────── 主语言必须能被声明，不能只能被猜

def test_the_primary_language_can_actually_be_declared(proj):
    """没有这个参数时，`lang:` 只能手写进文件——网页和 agent 都设不了。

    后果不是少一个字段：读的一侧只剩下从小节名倒推这一条路，而对**还没翻译**
    的记录，界面只能说「这是原文」，说不出是哪种语言的原文。声明是唯一的真凭据。
    """
    _root, d = proj
    s, _ = W.create_step(d, title="Baseline", lang="en", body="## Why\nfloor\n")
    assert s.lang == "en"
    assert "lang: en" in (d / s.dirname / "note.md").read_text(encoding="utf-8")
    assert core.compile_forest(d)["steps"][0]["lang"] == "en"


def test_a_declaration_can_be_corrected_and_withdrawn(proj):
    """写错了要能改回来，否则一个手滑的 `lang: ja` 会永久留在那儿。"""
    _root, d = proj
    s, _ = W.create_step(d, title="t", lang="ja")
    W.update_step(d, s.id, {"lang": "en"})
    assert core.compile_forest(d)["steps"][0]["lang"] == "en"
    W.update_step(d, s.id, {"lang": ""})
    text = (d / s.dirname / "note.md").read_text(encoding="utf-8")
    assert "lang:" not in text, "空串是撤回声明，不该留下一行空的 lang:"


def test_a_bogus_language_is_refused_at_declaration_time(proj):
    """它会被 render_note 原样写进 front-matter，所以校验必须在写之前。"""
    _root, d = proj
    for bad in ("../evil", "zh CN", "en/us", "1en"):
        with pytest.raises(W.WriteError):
            W.create_step(d, title=f"t-{bad}", lang=bad)


def test_the_web_reports_the_language_the_author_picked(client):
    """编辑器里那个下拉框是**用户看得见、能当场改**的，所以它是声明不是猜。

    以前它只决定插入哪套模板，选完就丢——转头再打开这一步，语言又不知道了。
    """
    r = client.post("/api/p/课题/steps", headers=AUTH,
                    json={"title": "Baseline", "lang": "en", "body": "## Why\nfloor\n"})
    assert r.status_code == 201
    assert client.get(f"/api/p/课题/steps/{r.json()['id']}").json()["lang"] == "en"


def test_the_web_actually_sends_it():
    """接口开了但前端没接，等于没开——这条盯的就是那根线。"""
    app_js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")
    assert 'lang: $("#nf-lang").value' in app_js


@pytest.mark.parametrize("tool", ["trace_new_step", "trace_update_step"])
def test_agents_can_declare_it_too(tool, proj):
    root, d = proj
    be = M.LocalBackend(root)
    assert "lang" in next(t for t in M.TOOLS if t["name"] == tool)["inputSchema"]["properties"]
    if tool == "trace_new_step":
        M.dispatch(be, tool, {"project": "课题", "title": "Baseline", "lang": "en"})
    else:
        M.dispatch(be, "trace_new_step", {"project": "课题", "title": "Baseline"})
        M.dispatch(be, tool, {"project": "课题", "step": "001", "lang": "en"})
    assert core.compile_forest(d)["steps"][0]["lang"] == "en"


# ───────────────────────── 译文也要有 expect，否则「谁最后按保存谁赢」

def test_a_translation_carries_its_own_digest(proj):
    """网页首屏就要拿得到它，否则第一次编辑某份译文时 expect 是空的。

    这个 digest **不**回答「译文是不是过时了」——那要存 note.md 当时的指纹，
    是把派生关系变成存储字段（P1 禁止）。它只回答 expect 那一个问题：
    我读到的这一份，和我要覆盖的那一份，是同一份吗。
    """
    _root, d = proj
    s, _ = W.create_step(d, title="t")
    out = W.write_translation(d, s.id, "en", title="T", body="## Why\nx")
    tr = core.compile_forest(d)["steps"][0]["tr"]["en"]
    assert tr["digest"] == out["digest"], "首屏拿到的和写入返回的必须是同一个值"


def test_that_digest_is_the_one_expect_checks(proj):
    _root, d = proj
    s, _ = W.create_step(d, title="t")
    W.write_translation(d, s.id, "en", title="T", body="## Why\nfirst")
    dig = core.compile_forest(d)["steps"][0]["tr"]["en"]["digest"]
    W.write_translation(d, s.id, "en", title="T", body="## Why\nsecond", expect=dig)
    with pytest.raises(W.Conflict):
        W.write_translation(d, s.id, "en", title="T", body="## Why\nthird", expect=dig)


def test_changing_the_original_does_not_invalidate_a_translation_expect(proj):
    """两条链各管各的。否则每改一次正文，所有语言的译文都得先重读一遍才写得进去。"""
    _root, d = proj
    s, _ = W.create_step(d, title="t", body="## 为什么\n甲")
    dig = W.write_translation(d, s.id, "en", title="T", body="## Why\nA")["digest"]
    W.update_step(d, s.id, {"body": "## 为什么\n乙"})
    W.write_translation(d, s.id, "en", title="T", body="## Why\nB", expect=dig)


# ───────────────────────── 项目译文也要能通过唯一写入路径删掉

def test_a_project_translation_can_be_dropped_through_the_one_write_path(client):
    """没有它，服务端要么让人手工删文件（绕过唯一写入路径），要么自己 unlink 一把
    ——后者就是第二条写入路径，正是这个仓库用结构杜绝的东西。"""
    client.put("/api/p/课题/tr/en", headers=AUTH, json={"name": "Topic"})
    assert (core.project_dir(client.root, "课题") / "project.en.md").is_file()
    r = client.request("DELETE", "/api/p/课题/tr/en", headers=AUTH)
    assert r.status_code == 200 and r.json()["removed"] is True
    assert not (core.project_dir(client.root, "课题") / "project.en.md").exists()
    assert (core.project_dir(client.root, "课题") / core.PROJECT_NOTE).is_file(), "原文不受影响"


def test_dropping_a_translation_that_is_not_there_is_a_404(client):
    assert client.request("DELETE", "/api/p/课题/tr/en", headers=AUTH).status_code == 404


# ───────────────────────── 文件名匹配的锚点

def test_a_trailing_newline_cannot_smuggle_a_file_in_as_a_translation():
    r"""Python 的 `$` 在结尾换行之前也匹配，所以 `note.en.md\n` 会被当成 en 译文。

    Linux 上那是一个能造出来的真文件名。被认成译文的后果是它绕过附件清单、
    直接以译文身份出现在界面上。锚点必须是 \Z。
    """
    assert core.TR_RE.match("note.en.md\n") is None
    assert core.PROJECT_TR_RE.match("project.en.md\n") is None
    assert core.TR_RE.match("note.en.md") is not None
