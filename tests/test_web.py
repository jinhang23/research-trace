"""网页层（web/app.js、web/index.html、web/style.css）的断言。

这一层没有 Python 代码可以直接调，所以测的是三样：
  1) 语法与纯函数：把 node 拉起来跑 `--check` 和 tests/app.test.js；
  2) 结构不变量：某个能力有没有真的接上线（用源码里的锚点断言，不是断言实现细节）；
  3) 自包含：静态导出必须能在 file:// 下断网打开——一条外部资源都不许有。

之所以用「源码里找得到锚点」这种粗断言：这些缺口全都是**整块能力没接线**
（等级算出来了但一个字都不显示、搜索框被 CSS 藏掉、草稿根本不存在），
锚点消失就等于能力又掉了，而这正是要防的那件事。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="这台机器没有 node，跳过 JS 断言")


# ---------------------------------------------------------------- node


@needs_node
def test_app_js_parses():
    """app.js 语法必须过。它没有构建步骤，写坏了就是整个界面白屏。"""
    r = subprocess.run([NODE, "--check", str(WEB / "app.js")], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr


@needs_node
def test_pure_helpers_pass_their_own_suite():
    """tests/app.test.js 里那些「洞察别被覆盖 / 草稿键别撞」的断言也归 pytest 管。"""
    r = subprocess.run([NODE, "--test", str(ROOT / "tests" / "app.test.js")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


@needs_node
def test_requiring_app_js_without_a_dom_does_not_start_the_ui():
    """没有 document 时载入 app.js 只应拿到纯函数——否则上一条测试根本跑不起来。"""
    r = subprocess.run(
        [NODE, "-e", "const U=require('./web/app.js');"
                     "console.log(typeof U.splitInsightBody, typeof U.draftKey)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["function", "function"]


# ---------------------------------------------------------------- 自包含


EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", re.I)
CSS_REMOTE = re.compile(r"""url\(\s*['"]?(?:https?:)?//""", re.I)


def test_no_external_resource_is_referenced_anywhere_in_web():
    """静态导出要能 file:// 断网打开：CDN、字体、图标库一个都不许有。"""
    for name in ("index.html", "app.js", "style.css", "md.js"):
        text = (WEB / name).read_text(encoding="utf-8")
        assert not EXTERNAL.search(text), f"{name} 里有指向外部主机的 src/href"
        assert not CSS_REMOTE.search(text), f"{name} 里有指向外部主机的 url()"
        assert "@import" not in text, f"{name} 里有 @import"


def test_static_export_is_self_contained(tmp_path: Path, monkeypatch):
    """导出的 HTML 只引用同目录下的三个资源文件，而且它们都真的被拷过去了。"""
    import trace_cli  # noqa: PLC0415  —— 只在这条测试里需要

    data = tmp_path / "data"
    out = tmp_path / "site"
    (data / "projects" / "demo" / "steps" / "001_a").mkdir(parents=True)
    (data / "projects" / "demo" / "project.md").write_text(
        "---\nname: demo\n---\n\n## 有效\n- x\n", encoding="utf-8")
    (data / "projects" / "demo" / "steps" / "001_a" / "note.md").write_text(
        "---\nid: '001'\ntitle: 第一步\nstatus: done\n---\n\n## 为什么\n试试\n", encoding="utf-8")

    monkeypatch.setattr(trace_cli, "load_config",
                        lambda *a, **k: {"title": "T", "data_dir": str(data), "space": "", "token": ""})
    monkeypatch.setattr(trace_cli, "ROOT", tmp_path)
    trace_cli.cmd_build(type("A", (), {"out": str(out), "project": None})())

    for name in trace_cli.STATIC_ASSETS:
        assert (out / name).is_file(), f"{name} 没被拷进导出目录"
    page = (out / "p" / "demo" / "index.html").read_text(encoding="utf-8")
    assert not EXTERNAL.search(page)
    assert 'src="../../app.js"' in page and 'href="../../style.css"' in page


# ---------------------------------------------------------------- L0–L4 / repro


def test_the_web_actually_renders_the_traceability_level():
    """FORMAT.md 第 10 节承诺「网页会给出等级」，而以前 web/ 里连 trace 这个词都没有。"""
    assert "function renderTrace" in APP
    assert "renderTrace(s)" in APP, "算出来了却没插进详情面板等于没接线"
    for field in ("t.self", "t.chain", "t.weakest", "t.missing", "t.lineage"):
        assert field in APP, f"trace.{field.split('.')[1]} 没被用到"


def test_the_weakest_link_is_clickable():
    """「补记录要从最弱的一环补起」——不给一个能点过去的入口，这句话落不了地。"""
    m = re.search(r"function renderTrace[\s\S]*?\n  \}\n", APP)
    assert m, "renderTrace 不见了"
    body = m.group(0)
    assert 'data-goto="' in body, "最弱一环 / 祖先链没有做成可跳转的链接"


def test_all_repro_records_are_shown_including_failed():
    """「试过，checkpoint 被清了」本身就是溯源结论，不许只显示成功的那几条。"""
    m = re.search(r"function renderTrace[\s\S]*?\n  \}\n", APP)
    body = m.group(0)
    assert "s.repro" in body and "repro.map(" in body, "repro 记录没有整列出来"
    assert "REPRO_LABEL" in body
    assert '"failed"' not in body.replace("r-failed", ""), "不该在渲染时按状态过滤记录"


def test_graph_marks_do_not_steal_the_existing_visual_channels():
    """规格：线型只归 status，不透明度只归祖先链/搜索命中。新标记只能用字形通道。"""
    m = re.search(r"function traceMarks[\s\S]*?\n  \}\n", APP)
    assert m, "图上的可溯源标记不见了"
    body = m.group(0)
    assert "cmk" in body, "标记应当复用 🖼📎🤖 那一套字形标记"
    for stolen in ("opacity", "faded", "border-style", "stroke-dasharray", "classList.toggle(\"s-"):
        assert stolen not in body, f"标记动了已经被占用的通道：{stolen}"
    # 只标 L0 和 failed 两种；标满五级会退化成噪声
    assert '"L0"' in body and '"failed"' in body


# ---------------------------------------------------------------- 洞察


def test_insight_editor_submits_only_the_insight_sections():
    """整体覆盖 project.md 会连 agent 写的洞察和「为什么删的」一起抹掉。"""
    assert "splitInsightBody" in APP, "编辑框还在拿整段正文预填"
    m = re.search(r"function openInsightEditor[\s\S]*?\n  \}\n", APP)
    assert m and "currentProject()" in m.group(0)
    assert "esc(split.editable)" in m.group(0), "预填的必须是切出来的洞察部分"
    # 保存路径上不能再出现「把整段 body 提交回去」
    save = re.search(r"function saveInsights[\s\S]*?\n  \}\n", APP).group(0)
    assert "ta.value" in save and ".body" not in save


def test_insight_editor_shows_the_untouched_sections_read_only():
    """只把它们排除掉还不够：框里看不到「## 已删除」，用户会以为它已经没了。"""
    m = re.search(r"function openInsightEditor[\s\S]*?\n  \}\n", APP)
    assert "split.others" in m.group(0)
    assert "othersec" in CSS, "只读小节没有样式"


# ---------------------------------------------------------------- 新建项目


def test_new_project_button_lives_in_the_header_not_only_on_the_index_page():
    """只剩一个项目时索引页会 302 弹回项目页，按钮长在那儿就等于永远点不到。"""
    header = HTML.split("</header>")[0]
    assert 'id="btn-newproj"' in header, "＋ 新建项目 必须在顶栏，任何页面都够得着"
    assert "data-newproj" in header
    # 而且没有任何一条 CSS 规则再把它藏起来（旧版就是被 #home[hidden] 连带藏掉的）
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    for sel, decl in re.findall(r"([^{}]+)\{([^}]*)\}", stripped):
        if "#btn-newproj" not in sel:
            continue
        assert "display: none" not in decl and "visibility: hidden" not in decl, \
            f"这条规则又把顶栏的新建项目按钮藏了：{sel.strip()}"


# ---------------------------------------------------------------- 跨项目搜索


def test_the_index_page_search_box_is_not_hidden_anymore():
    """FORMAT.md 第 0 节：人和 LLM 信息对等。agent 能跨项目搜，人也必须能。"""
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    hidden = re.search(r"body\.home-mode[^{]*\{[^}]*\}", stripped)
    assert hidden, "home-mode 的隐藏规则不见了（其余断言失去意义）"
    assert "#top .grow" not in hidden.group(0), "项目索引页又把搜索框整个藏起来了"


def test_cross_project_search_has_a_fallback_when_the_endpoint_is_missing():
    """端点不在（老服务端 / 静态导出）时必须还能搜，否则人只会以为自己记错了。"""
    assert "function searchRemote" in APP and "/api/search?q=" in APP
    assert "function searchLocally" in APP
    run = re.search(r"function runGlobalSearch[\s\S]*?\n  \}\n", APP).group(0)
    assert "searchRemote(q).catch(" in run and "searchLocally(q)" in run


def test_search_scope_can_be_switched_from_inside_a_project():
    assert 'id="btn-scope"' in HTML
    assert "trace.scope" in APP, "范围开关没有被记住"


# ---------------------------------------------------------------- 草稿


def test_the_editor_keeps_a_local_draft():
    """写十分钟的正文，一次误点或一下 Esc 就没了——这条是防那个的。"""
    assert "function saveDraftNow" in APP and "localStorage.setItem(U.draftKey" in APP
    assert "scheduleDraft" in APP


def test_escape_no_longer_throws_the_body_away():
    m = re.search(r'if \(e\.key === "Escape"\) \{\s*\n\s*e\.preventDefault\(\);\s*\n\s*guardLeave', APP)
    assert m, "编辑框里的 Esc 还是直接丢弃"


def test_leaving_with_unsaved_text_is_confirmed_and_offers_three_ways_out():
    assert 'id="dlg-leave"' in HTML
    for how in ("stay", "keep", "discard"):
        assert f'data-leave="{how}"' in HTML, f"离开确认少了 {how} 这个出口"
    assert "function guardLeave" in APP
    assert "beforeunload" in APP, "关标签页/刷新时没有任何提示"


def test_a_draft_is_offered_for_recovery_instead_of_being_applied_silently():
    """自动套上去等于替用户做了选择，而磁盘那份可能才是新的。"""
    m = re.search(r"function draftBanner[\s\S]*?\n  \}\n", APP)
    assert m, "没有草稿提示条"
    assert 'data-draft="restore"' in m.group(0) and 'data-draft="discard"' in m.group(0)
    assert "d.base" in m.group(0), "没有提示「草稿写的时候服务器上是另一版」"


def test_the_new_step_dialog_also_keeps_a_draft():
    """<dialog> 按 Esc 直接就关了，而「为什么」常常是先写十分钟才想好标题。"""
    assert "function saveNewDraft" in APP and "NEW_DRAFT_ID" in APP
    assert 'id="nf-draft"' in HTML


def test_the_back_button_pins_the_draft_instead_of_dropping_it():
    """hashchange 绕过 select() 的确认框；不在这里存一次，后退键就是一次静默丢失。"""
    m = re.search(r'addEventListener\("hashchange", function \(\) \{([\s\S]*?)\n  \}\);', APP)
    assert m, "hashchange 处理器不见了"
    assert "saveDraftNow()" in m.group(1)


def test_a_successful_save_clears_the_draft():
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "dropDraft(s.id)" in save, "保存成功后不清草稿，下次打开会一直问要不要恢复"


# ---------------------------------------------------------------- 冲突


def test_the_editor_sends_expect_so_the_server_can_detect_a_conflict():
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "expect: s.digest" in save, "不带 expect 就是「谁最后按保存谁赢」"


def test_a_409_shows_both_versions_instead_of_silently_choosing_one():
    assert 'id="dlg-conflict"' in HTML
    for how in ("cancel", "theirs", "mine"):
        assert f'data-conflict="{how}"' in HTML
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "e.status === 409" in save
    h = re.search(r"function handleConflict[\s\S]*?\n  \}\n", APP).group(0)
    assert "err.data" in h, "服务端连当前内容一起返回时应当直接用"
    assert "papi(" in h, "没返回就得自己再读一次，不能空着两边让人瞎猜"


def test_keeping_the_server_version_does_not_destroy_the_local_text():
    theirs = re.search(r'if \(how === "theirs"\) \{([\s\S]*?)\n    \}', APP)
    assert theirs, "冲突框里没有「保留服务器版本」这个出口"
    assert "dropDraft" not in theirs.group(1), "选了服务器版本就把自己写的删掉，等于另一种静默丢弃"


def test_overwriting_uses_the_freshly_read_digest_not_an_empty_expect():
    """强制覆盖也要带 expect，否则「用我的覆盖」变成一个永久绕过冲突检测的后门。"""
    r = re.search(r"function resolveConflict[\s\S]*?\n  \}\n", APP).group(0)
    assert "expect: c.server.digest" in r


# ---------------------------------------------------------------- git 同步


def test_git_sync_failure_is_visible_in_the_ui():
    """数据仓是换机器和灾难恢复的全部依据，静默失败几周后才发现代价太大。"""
    assert "/api/status" in APP and "function paintGit" in APP
    assert 'id="gitwarn"' in HTML and 'id="gitdot"' in HTML
    assert "g-error" in CSS, "失败态没有可见样式"


def test_git_status_is_polled_and_can_be_retried():
    assert "setInterval(refreshGit" in APP, "只在版本变化时查，push 失败时版本根本不变"
    assert "function retrySync" in APP and '"/api/sync"' in APP


def test_git_indicator_stays_quiet_when_sync_is_simply_off():
    """没开自动同步、刚起服务还没写过——都不是问题，吵起来会让真失败被当噪声忽略。"""
    m = re.search(r"function paintGit[\s\S]*?\n  \}\n", APP).group(0)
    assert '!== "disabled"' in m and '!== "idle"' in m


def test_git_states_the_client_knows_cover_what_the_syncer_can_report():
    """服务端多一个状态、前端不认识，就会在顶栏显示一个裸英文单词。"""
    import trace_git  # noqa: PLC0415

    states = set(re.findall(r'_record\(\s*"([a-z_]+)"', (ROOT / "trace_git.py").read_text(encoding="utf-8")))
    states |= set(re.findall(r'^\s+"([a-z_]+)",\s*$', (ROOT / "trace_git.py").read_text(encoding="utf-8"), re.M))
    known = set(re.findall(r"(\w+):", re.search(r"var GIT_LABEL = \{([\s\S]*?)\};", APP).group(1)))
    missing = {s for s in states if s in trace_git.OK_STATES or s in
               {"disabled", "misconfigured", "idle", "error", "committed"}} - known
    assert not missing, f"网页不认识这些同步状态：{missing}"


def test_the_export_does_not_offer_server_only_controls():
    """静态导出是断网可读的一堆文件，写入按钮和同步状态在那儿只能是骗人。"""
    assert '$("#btn-newproj").hidden = MODE === "static";' in APP
    ps = re.search(r"function paintScope[\s\S]*?\n  \}\n", APP).group(0)
    assert 'b.hidden = MODE !== "server"' in ps
    rg = re.search(r"function refreshGit[\s\S]*?\n  \}\n", APP).group(0)
    assert 'MODE !== "server"' in rg


# ---------------------------------------------------------------- 窄屏


def test_narrow_screens_default_to_the_list_view():
    """图视图的画布是绝对像素宽，375px 上等于一屏一个节点全靠横向拖。"""
    assert "NARROW" in APP
    m = re.search(r"var view = savedView[\s\S]*?;", APP)
    assert m and "innerWidth" in m.group(0)
    assert 'savedView === "graph"' in m.group(0), "存过偏好就必须听用户的"


def test_the_header_and_dialogs_survive_a_phone_width():
    assert "@media (max-width: 760px)" in CSS
    narrow = CSS.split("@media (max-width: 760px)")[1]
    assert "#top { flex-wrap: wrap" in narrow, "顶栏在窄屏会被挤成一条缝"
    assert ".cfsplit { grid-template-columns: 1fr; }" in narrow, "冲突对比在窄屏必须上下排"


# ---------------------------------------------------------------- 表格底纹


def test_bars_read_the_number_md_js_already_parsed():
    """两套数值判定会分叉：md.js 判定右对齐生效，app.js 判定 NaN 于是整列不画底纹。"""
    m = re.search(r"function numOf[\s\S]*?\n  \}\n", APP).group(0)
    assert "dataset.num" in m
    assert "NUMCELL" not in APP, "app.js 里还留着第二套数值正则"
    bars = re.search(r"function barTables[\s\S]*?\n  \}\n", APP).group(0)
    assert "vals.some(isNaN)" not in bars, "一格占位横线不该让整列的底纹消失"


# ---------------------------------------------------------------- 一致性


def test_insight_headings_match_the_writer_side():
    """前端切错一个字，那一节就会被当成非洞察小节，用户在框里删不掉它。"""
    import trace_write as W  # noqa: PLC0415

    m = re.search(r"var INSIGHT_HEADINGS = \[([^\]]*)\];", APP)
    assert m
    got = [x.strip().strip('"') for x in m.group(1).split(",") if x.strip()]
    assert set(got) == set(W.INSIGHT_SECTIONS.values())


def test_levels_match_the_core_side():
    import trace_core as core  # noqa: PLC0415

    m = re.search(r"var LEVELS = (\[[^\]]*\]);", APP)
    assert m
    assert json.loads(m.group(1)) == list(core.LEVELS)


def test_repro_states_match_the_core_side():
    import trace_core as core  # noqa: PLC0415

    m = re.search(r"var REPRO_LABEL = \{([\s\S]*?)\};", APP)
    assert m
    got = set(re.findall(r"(\w+):", m.group(1)))
    assert set(core.REPRO_STATES) <= got, "core 新增了一个 repro 状态，网页不认识它"
    assert "unknown" in got, "parse_repro 会把认不出来的状态归成 unknown"
