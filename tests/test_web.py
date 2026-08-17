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

# 只认汉字和中文标点。全角 ＋ − ↺ 这些是**字形**不是词（i18n 表里两种语言都用
# 同一个 ＋），把它们也算成「没翻译的中文」会让这条断言变成一句狼来了。
CJK = re.compile(r"[　-〿㐀-䶿一-鿿]")


def js_string_literals(src: str) -> list[tuple[int, str]]:
    """扫出 JS 源码里的字符串字面量，跳过注释和正则。返回 [(行号, 内容)]。

    双语这一层唯一能机械查证的事就是「界面上的字有没有真的都走 i18n」——
    只在源码里 grep 中文会把满篇的中文注释一起抓进来（这个仓库的注释是中文的，
    而且按规矩必须是中文），所以得先把注释和正则摘掉，只看字面量。
    """
    out: list[tuple[int, str]] = []
    i, n, line, prev = 0, len(src), 1, ""
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and src[i + 1:i + 2] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and src[i + 1:i + 2] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
            continue
        if c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%~^<>"):
            j, in_class = i + 1, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "\n" or (src[j] == "/" and not in_class):
                    break
                j += 1
            i, prev = j + 1, "/"
            continue
        if c in "\"'`":
            quote, j, buf = c, i + 1, []
            while j < n:
                if src[j] == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if src[j] == quote or (src[j] == "\n" and quote != "`"):
                    break
                if src[j] == "\n":
                    line += 1
                buf.append(src[j])
                j += 1
            out.append((line, "".join(buf)))
            i, prev = j + 1, quote
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


def i18n_keys() -> dict[str, set[str]]:
    """从 i18n.js 里取两种语言各自的 key 集合（用 node 读，不用正则猜）。"""
    r = subprocess.run(
        [NODE, "-e", "const i=require('./web/i18n.js');"
                     "console.log(JSON.stringify({en:Object.keys(i.STRINGS.en),"
                     "zh:Object.keys(i.STRINGS.zh)}))"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    return {k: set(v) for k, v in got.items()}


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
    for name in ("index.html", "app.js", "style.css", "md.js", "i18n.js"):
        text = (WEB / name).read_text(encoding="utf-8")
        assert not EXTERNAL.search(text), f"{name} 里有指向外部主机的 src/href"
        assert not CSS_REMOTE.search(text), f"{name} 里有指向外部主机的 url()"
        assert "@import" not in text, f"{name} 里有 @import"


def test_static_export_is_self_contained(tmp_path: Path, monkeypatch):
    """导出的 HTML 只引用同目录下的那几个资源文件，而且它们都真的被拷过去了。"""
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
    assert "reproName(" in body, "状态标签没走 i18n（trace.repro.*）"
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
    # 清的必须是**这一个语言版本**那份草稿：中英两份各存各的（见 U.draftKey）
    assert "dropDraft(s.id, st.lang)" in save, "保存成功后不清草稿，下次打开会一直问要不要恢复"


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


@needs_node
def test_git_states_the_client_knows_cover_what_the_syncer_can_report():
    """服务端多一个状态、前端不认识，就会在顶栏显示一个裸英文单词。

    双语之后这条更硬：服务端的 summary/hint 是中文（Python 侧不在翻译范围），
    英文界面上优先用本地的 git.state.*，本地缺一个就会当场掉回中文。
    """
    import trace_git  # noqa: PLC0415

    states = set(re.findall(r'_record\(\s*"([a-z_]+)"', (ROOT / "trace_git.py").read_text(encoding="utf-8")))
    states |= set(re.findall(r'^\s+"([a-z_]+)",\s*$', (ROOT / "trace_git.py").read_text(encoding="utf-8"), re.M))
    known = set(json.loads(re.search(r"var GIT_STATES = (\[[^\]]*\]);", APP).group(1)))
    want = {s for s in states if s in trace_git.OK_STATES or s in
            {"disabled", "misconfigured", "idle", "error", "committed"}}
    assert not want - known, f"网页不认识这些同步状态：{want - known}"
    keys = i18n_keys()
    for state in known:
        for lang in ("en", "zh"):
            assert "git.state." + state in keys[lang], f"{lang} 缺 git.state.{state} 的说法"


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
    # 加了第三个视图（数据流）之后判定从「是不是那两个字面量之一」改成查 VIEWS 表，
    # 所以这里跟着改成对新写法断言——要防的那件事没变：**存过偏好就听用户的**，
    # 只有什么都没存过时才按屏宽给默认值。
    m = re.search(r"var view = VIEWS\.indexOf\(savedView\)[\s\S]*?;", APP)
    assert m and "innerWidth" in m.group(0)
    assert ">= 0 ? savedView" in m.group(0), "存过偏好就必须听用户的"
    vs = re.search(r"var VIEWS = (\[[^\]]*\]);", APP)
    assert vs and set(json.loads(vs.group(1))) == {"graph", "list", "flow"}


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


def test_the_insight_buttons_say_the_same_words_that_land_in_project_md():
    """「＋ 有效」这个按钮上的字，必须就是最终写进 project.md 的那个小节名。

    差一个字，人点了按钮却在别的小节里找不到自己刚记的那条。
    i18n 那一侧对着 trace_core.INSIGHT_NAMES 逐字核过，这里核的是
    app.js 用的语义键和 core 的四个键一致。
    """
    import trace_core as core  # noqa: PLC0415

    m = re.search(r"var INSIGHT_KINDS = (\[[^\]]*\]);", APP)
    assert m, "四种洞察的语义键不见了"
    assert set(json.loads(m.group(1))) == set(core.INSIGHT_NAMES)


def test_levels_match_the_core_side():
    import trace_core as core  # noqa: PLC0415

    m = re.search(r"var LEVELS = (\[[^\]]*\]);", APP)
    assert m
    assert json.loads(m.group(1)) == list(core.LEVELS)


@needs_node
def test_repro_states_match_the_core_side():
    import trace_core as core  # noqa: PLC0415

    m = re.search(r"var REPRO_STATES = (\[[^\]]*\]);", APP)
    assert m
    got = set(json.loads(m.group(1)))
    assert set(core.REPRO_STATES) <= got, "core 新增了一个 repro 状态，网页不认识它"
    assert "unknown" in got, "parse_repro 会把认不出来的状态归成 unknown"
    # 标签搬进了 i18n，那就得两种语言都有——少一条，界面上会显示成一个裸 key
    keys = i18n_keys()
    for state in got:
        for lang in ("en", "zh"):
            assert "trace.repro." + state in keys[lang], f"{lang} 缺 trace.repro.{state}"


# ---------------------------------------------------------------- 双语


@needs_node
def test_the_page_loads_i18n_before_app_js():
    """app.js 第一次 paintChrome() 就要 window.i18n；顺序反了就是整页白屏。"""
    order = re.findall(r'<script src="__ASSET__([^"]+)"', HTML)
    assert "i18n.js" in order, "页面根本没有载入文案表"
    assert order.index("i18n.js") < order.index("app.js")


@needs_node
def test_every_script_the_page_loads_is_shipped_by_the_static_export():
    """静态导出漏拷一个脚本 = file:// 打开时白屏，而且是断网之后才发现。"""
    import trace_cli  # noqa: PLC0415

    want = re.findall(r'<script src="__ASSET__([^"]+)"', HTML)
    missing = [x for x in want if x not in trace_cli.STATIC_ASSETS]
    assert not missing, f"这些脚本页面要用，导出却不拷：{missing}"


def test_no_ui_text_is_hardcoded_in_the_page():
    """index.html 里不许再有写死的界面文案——一份文案只能有一处真相。

    注释是中文的（仓库规矩），所以先摘掉注释再看。剩下的中文只允许语言开关上
    那个「中」字：它是语言的自称，两种界面里都念同一个音，翻译它没有意义。
    """
    stripped = re.sub(r"<!--[\s\S]*?-->", "", HTML)
    leftover = {ch for ch in CJK.findall(stripped)}
    assert leftover <= {"中"}, f"index.html 里还有写死的中文：{leftover}"


def closed_vocab_literals() -> set[str]:
    """app.js 里允许出现中文的那几处**封闭词表**（不是界面文案）。

    它们是 project.md 里真实写着的字：小节名、以及「取代」这个连接词。
    切换界面语言绝不能让它们变——变了就解析不回来，用户在框里删不掉自己看见的
    东西，或者一条「· 取代 p1」写得进去、读不回来。
    每一张表都在下面那条测试里对着 trace_core 逐字核过，所以放行它们并没有
    在这条断言上开口子：多写一个字仍然会被那一条抓住。
    """
    out = set(re.search(r"var INSIGHT_HEADINGS = \[([^\]]*)\];", APP).group(1).replace('"', "").split(", "))
    out |= set(re.search(r"var SUPERSEDE_WORDS = \[([^\]]*)\];", APP).group(1).replace('"', "").split(", "))
    body = re.search(r"var INSIGHT_KIND_BY_HEADING = \{([\s\S]*?)\};", APP).group(1)
    out |= set(re.findall(r'"([^"]+)":', body))
    return {x.strip() for x in out}


def test_the_insight_vocabulary_in_app_js_matches_the_core_side():
    """洞察那两张封闭词表必须和 trace_core 逐字一致。

    网页要按条渲染洞察（id、取代了谁、被取代的折叠起来），就得读得懂
    trace_core.parse_insights 认的那套写法。少认一个小节名 = 那一节在界面上
    退化成一段没有关系的 bullet；「取代」少一个字 = 取代关系整条消失，
    而磁盘上它明明写着。
    """
    import trace_core as core  # noqa: PLC0415

    body = re.search(r"var INSIGHT_KIND_BY_HEADING = \{([\s\S]*?)\};", APP).group(1)
    got = dict(re.findall(r'"([^"]+)":\s*"(\w+)"', body))
    assert got == core.INSIGHT_KEY_BY_NAME, "小节名词表和 core 对不上"
    words = re.search(r"var SUPERSEDE_WORDS = \[([^\]]*)\];", APP).group(1)
    assert set(x.strip().strip('"') for x in words.split(",")) == set(core.SUPERSEDE_NAMES.values())


@needs_node
def test_no_ui_text_is_hardcoded_in_app_js():
    """app.js 的字符串字面量里不许再有界面文案。

    例外只有那几张**封闭词表**（见 closed_vocab_literals）：它们不是界面文案，
    是 project.md 里真实写着的字，而且各自被一条对着 trace_core 的一致性断言钉住。
    """
    allowed = closed_vocab_literals()
    bad = [(n, s) for n, s in js_string_literals(APP) if CJK.search(s) and s.strip() not in allowed]
    assert not bad, "app.js 里还有没走 i18n 的中文文案：" + "; ".join(f"{n}:{s[:40]}" for n, s in bad)


@needs_node
def test_every_key_the_page_asks_for_exists_in_both_languages():
    """data-i18n 写错一个字母，页面上就会显示一个点分的 key。两种语言都得有。"""
    keys = i18n_keys()
    want = set(re.findall(r'data-i18n(?:-html|-title|-ph)?="([^"]+)"', HTML))
    assert want, "页面上一个 data-i18n 都没有"
    for lang in ("en", "zh"):
        assert not want - keys[lang], f"{lang} 缺这些 key：{sorted(want - keys[lang])}"


@needs_node
def test_keys_used_from_js_exist_in_both_languages():
    """app.js 里写死的那些 key 同样要两种语言都在。

    只认**当场闭合**的字面量（i18n.t("x.y") / i18n.t("x.y", {…})）；
    拼出来的（"trace.level." + l）由各自对着 core 的一致性测试盯着，
    见 test_repro_states_match_the_core_side / test_git_states_...。
    """
    keys = i18n_keys()
    used = set(re.findall(r'i18n\.t(?:Html)?(?:In)?\(\s*(?:"[a-z]{2}",\s*)?"([a-z][\w.]*)"\s*[,)]', APP))
    assert len(used) > 60, f"只找到 {len(used)} 个 key，扫描八成没生效"
    for lang in ("en", "zh"):
        assert not used - keys[lang], f"{lang} 缺这些 key：{sorted(used - keys[lang])}"


def test_the_language_switch_is_in_the_header_and_always_reachable():
    """找语言开关的人正是看不懂当前语言的人，所以它不能藏在折叠菜单里。"""
    header = HTML.split("</header>")[0]
    assert 'id="langtoggle"' in header
    assert 'data-lang="en"' in header and 'data-lang="zh"' in header
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    for sel, decl in re.findall(r"([^{}]+)\{([^}]*)\}", stripped):
        if "#langtoggle" not in sel:
            continue
        assert "display: none" not in decl and "visibility: hidden" not in decl, \
            f"这条规则把语言开关藏了：{sel.strip()}"


def test_switching_the_language_repaints_and_updates_the_html_lang_attribute():
    """切了语言页面不重画就是「刷新一下才生效」；<html lang> 不跟着改，
    浏览器的断词、朗读、拼写检查全按旧语言来。"""
    assert "i18n.setLang(" in APP, "开关没有真的写进 localStorage"
    m = re.search(r'window\.addEventListener\(i18n\.EVENT, function \(\) \{([\s\S]*?)\n  \}\);', APP)
    assert m, "没有监听 tracelang 事件"
    body = m.group(1)
    assert "paintChrome()" in body
    assert "renderRows()" in body and "renderDiagram()" in body, "树/列表上的标题没跟着换语言"
    assert "document.documentElement.lang" in APP


def test_switching_the_language_never_eats_text_being_typed():
    """切语言会把编辑器整个重渲染——正在敲的字必须留住。"""
    m = re.search(r'window\.addEventListener\(i18n\.EVENT, function \(\) \{([\s\S]*?)\n  \}\);', APP)
    assert "editing && isDirty()" in m.group(1), "脏编辑器没有被保护"


# ---------------------------------------------------------------- 双语 · 内容


def test_the_view_shows_the_translation_and_says_so_when_it_cannot():
    """有译文显示译文，没有就显示原文并如实说明——而且不许猜原文是什么语言。"""
    assert "function stepTitle" in APP and "function stepBody" in APP
    for fn in ("renderRows", "renderDiagram", "renderDetail"):
        body = re.search(r"function " + fn + r"[\s\S]*?\n  \}\n", APP).group(0)
        assert "stepTitle(s)" in body, f"{fn} 里的标题没跟着语言走"
    detail = re.search(r"function renderDetail[\s\S]*?\n  \}\n", APP).group(0)
    assert "stepBody(s)" in detail and "trNotice(s)" in detail
    notice = re.search(r"function trNotice[\s\S]*?\n  \}\n", APP).group(0)
    assert '"unknown"' in notice, "没声明 lang 的那一档不见了"
    assert "langName(rec.lang)" in notice, "声明了 lang 的那一档应当说清是哪种语言"


def test_figures_and_attachments_still_point_at_the_same_step_directory():
    """译文是同一步的另一份文字。fileURL 一旦跟着语言变，翻译一份就等于
    把所有图链接指向不存在的地方。"""
    body = re.search(r"function fileURL[\s\S]*?\n  \}\n", APP).group(0)
    for word in ("uiLang", "edLang", "pickLang", "tr["):
        assert word not in body, f"fileURL 里混进了语言：{word}"


def test_the_body_template_follows_the_content_language_not_the_interface():
    """界面英文的人未必用英文记笔记。给他插一套英文小节名，
    trace_core 就找不到「为什么」，这一步的评级凭空掉到 L0。"""
    assert "function guessContentLang" in APP
    g = re.search(r"function guessContentLang[\s\S]*?\n  \}\n", APP).group(0)
    assert "langByHeadings" in g, "没有按兄弟步骤已经在用的小节名来定"
    assert ".lang" in g, "note.md 自己声明的 lang 才是第一顺位"
    # 模板一律走显式指定语言的 tIn，绝不走跟界面语言的 t
    assert 'i18n.t("template.body")' not in APP
    assert 'templateBody(' in APP
    tb = re.search(r"function templateBody[\s\S]*?\n  \}\n|function templateBody[^\n]*\n", APP).group(0)
    assert "tIn(" in tb


def test_the_new_step_dialog_lets_you_correct_the_guessed_content_language():
    """猜出来的默认值必须能当场推翻，否则猜错就是一个没有出口的坑。"""
    assert 'id="nf-lang"' in HTML
    m = re.search(r'\$\("#nf-lang"\)\.addEventListener\("change", function \(\) \{([\s\S]*?)\n  \}\);', APP)
    assert m, "内容语言的下拉没有接线"
    assert "isPristineTemplate" in m.group(1), "换语言会把人已经写下的正文冲掉"


# ---------------------------------------------------------------- 双语 · 编辑


def test_translations_are_written_through_their_own_endpoint():
    """补翻译的工具永远碰不到原文：译文那条路径只发 title 和 body。"""
    assert "function putTranslation" in APP
    p = re.search(r"function putTranslation[\s\S]*?\n  \}\n", APP).group(0)
    assert '"/tr/"' in APP and 'method: "PUT"' in p
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "st.lang" in save, "保存时不分是原文还是译文"
    tr_branch = save.split("st.lang")[1].split("papi(")[0]
    for forbidden in ("paths:", "status:", "date:", "commit:"):
        assert forbidden not in tr_branch, f"译文的写入路径上出现了结构键：{forbidden}"


def test_the_editor_can_switch_which_language_version_it_edits():
    assert "function switchEditLang" in APP and 'data-edlang' in APP
    m = re.search(r"function switchEditLang[\s\S]*?\n  \}\n", APP).group(0)
    assert "saveDraftNow()" in m, "切语言前不存草稿 = 切一下丢一份"


def test_translation_saves_carry_their_own_expect():
    """乐观并发控制在译文这一份文件上同样成立，而且对的是译文自己的 digest——
    拿 note.md 的摘要去对译文，是一次永远对不上的误报。"""
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "expect: trDigest[trKey(s.id, st.lang)]" in save
    assert "expect: s.digest" in save, "原文那条链不受影响"
    r = re.search(r"function resolveConflict[\s\S]*?\n  \}\n", APP).group(0)
    assert "putTranslation(c.id, c.lang" in r, "409 之后「用我的覆盖」不支持译文"


# ---------------------------------------------------------------- ③ 结构化路径


@needs_node
def test_the_web_serialises_paths_exactly_like_the_core_does():
    """编辑框里那几行 path 的写法，必须和 trace_core.format_path 逐字一样。

    这是这一轮**最贵**的一条：网页的编辑框是整组回写的（框里有什么，磁盘上就
    有什么）。少还原一段 role、少还原一个 `md5=`，用户改一下标题，刚核对完的
    164 条校验和就没了，而且一声不响。两边的实现分头写了两份，那就必须对着
    同一批真实形状逐条比对，不能靠「我看了一眼觉得一样」。
    """
    import trace_core as core  # noqa: PLC0415

    rows = [
        "/orange/lab/pockets | output | 纯 RNA 口袋 | n=4554 size=620756992 md5=7d4e1a9c",
        "/blue/lab/cif_files | input | 原始 CIF | size=61203283968 missing=2026-08-09",
        "/blue/组/用户/data/agnews-clean | 去重后的训练集，12 GB",
        "https://github.com/x/y/tree/9b7d112 | script | 跑这一步的代码",
        "s3://bucket/exports/run042.parquet",
        "/x | 说明里有 | 竖线，还写了 lr=3e-4 的那次运行",
        "/y | evidence | nodes=88 whatever=1",
    ]
    parsed = [dict(p) for p in core.parse_paths("\n".join(rows))]
    want = [core.format_path(p) for p in parsed]
    r = subprocess.run(
        [NODE, "-e",
         "const U=require('./web/app.js');"
         "const rows=JSON.parse(process.argv[1]);"
         "console.log(JSON.stringify(rows.map(U.formatPath)))",
         json.dumps(parsed)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == want


@needs_node
def test_the_web_serialises_inputs_and_code_exactly_like_the_core_does():
    """input / code 两条同理：写回去的那一行要能被 trace_core 原样读回来。"""
    import trace_core as core  # noqa: PLC0415

    inputs = [dict(x) for x in core.parse_inputs("013 | pocket_composition.csv\n014")]
    codes = [dict(x) for x in core.parse_code(
        "snapshot | /orange/lab/run_snapshots/20260809 | manifest=MANIFEST.md5 n=43\n"
        "container | ghcr.io/lab/img | digest=sha256:aa")]
    r = subprocess.run(
        [NODE, "-e",
         "const U=require('./web/app.js');const a=JSON.parse(process.argv[1]);"
         "console.log(JSON.stringify({i:a.i.map(U.formatInput),c:a.c.map(U.formatCode)}))",
         json.dumps({"i": inputs, "c": codes})],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["i"] == [core.format_input(x) for x in inputs]
    assert got["c"] == [core.format_code(x) for x in codes]


def test_the_editor_writes_back_the_structured_form_not_a_flattened_one():
    """`位置 | 说明` 那种老写法回写会静默抹掉 role / 校验和 / 最后核对日期。"""
    m = re.search(r"function pathsToText[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.formatPath" in m, "回写没走结构化序列化"
    assert "p.location + (p.note" not in m, "还留着「位置 | 说明」那条老路"
    code = re.search(r"function codeToText[\s\S]*?\n  \}\n", APP).group(0)
    assert 'c.from !== "commit"' in code, "派生出来的那条 code 不许进编辑框（写回去等于存第二份）"


def test_paths_are_grouped_by_role_and_missing_ones_are_called_out():
    """「别人能看到这一步做了什么」本质上就是分清读进来的/跑的/写出去的/留作凭据的。"""
    m = re.search(r"function renderPaths[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.PATH_ROLES" in m, "没有按 role 分组"
    assert "path.summary.missing" in m, "整块没有汇总「有几处已经不存在了」"
    facts = re.search(r"function pathFacts[\s\S]*?\n  \}\n", APP).group(0)
    for key in ("path.n", "path.checksum", "path.checked", "path.missing", "path.unchecked"):
        assert key in facts, f"{key} 没接上——大小/条目数/校验和/核对状态各要有位置"
    assert "path.attr.unknown.title" in facts, "未知属性被吃掉了：认不出来不等于该删掉"


def test_a_gone_location_is_visible_from_the_whole_project_not_just_one_step():
    """用户这次是手工核对 164 条路径才发现三个目录没了（57 GB 的那个）。"""
    assert 'id="missbar"' in HTML
    m = re.search(r"function renderMissingPaths[\s\S]*?\n  \}\n", APP).group(0)
    assert 'p.state === "missing"' in m and "path.summary.missing" in m
    assert "data-goto" in APP, "汇总里的 id 得能点过去"
    assert "renderMissingPaths();" in APP, "算了却没在刷新时调用"


# ---------------------------------------------------------------- ① 移动


def test_moving_a_step_requires_a_reason_before_anything_is_sent():
    """原因是这条记录里唯一无法自动生成的部分。一个可选的输入框会让人先点了
    确定才发现要写，转头就回去用「把两步的正文对调」那种不留痕迹的老办法。"""
    m = re.search(r"function submitMove[\s\S]*?\n  \}\n", APP).group(0)
    assert "move.err.reason" in m
    assert m.index("if (!reason)") < m.index("papi("), "请求都发出去了才检查原因"
    assert "reason: reason" in m, "原因没跟着请求走"
    assert 'required' in HTML.split('id="dlg-move"')[1].split("</dialog>")[0], "原因框在 HTML 上是可选的"


def test_a_move_that_would_close_a_loop_is_refused_on_the_spot():
    """成环 / 挂到自己的后代下面不是笔误，是想法本身有问题——不能等服务端 400。"""
    m = re.search(r"function paintMoveErr[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.moveError" in m
    for key in ("move.err.self", "move.err.descendant", "move.err.noop", "move.err.missing"):
        assert key in m, f"{key} 没说出来"
    assert '$("#mv-ok").disabled' in m, "判出问题了却还让人点得下去"


def test_the_move_history_is_shown_and_says_who_and_why():
    """一棵移动过的树本来就会和创建顺序对不上；不摆出来，那种对不上会被当成 bug。"""
    m = re.search(r"function renderMoved[\s\S]*?\n  \}\n", APP).group(0)
    for field in ("m.date", "m.from", "m.to", "m.reason", "m.by"):
        assert field in m, f"移动记录里的 {field} 没显示"
    assert "move.from.root" in m and "move.to.root" in m, "从根移走 / 移成根说不出来"
    assert "renderMoved(s)" in APP, "算了却没插进详情面板"
    marks = re.search(r"function nodeMarks[\s\S]*?\n  \}\n", APP).group(0)
    assert "move.badge.title" in marks, "树上没有「这一步被移动过」的标记"


# ---------------------------------------------------------------- ①b 拖着改父节点


def test_dragging_uses_pointer_events_not_the_html5_drag_api():
    """HTML5 的 drag-and-drop 在自定义 SVG / 绝对定位画布上各浏览器表现不一，
    拖影没法控制，触屏基本不可用——而图视图恰恰就是绝对定位的画布。

    页面里另外两处 dragover/drop 是**文件上传**（把截图拖进详情面板），
    那是浏览器和操作系统之间的协议，本来就该用 HTML5 那套。所以这里只断言
    树上的拖拽走 Pointer Events，不是「全文不许出现 dragover」。
    """
    for ev in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
        assert f'"{ev}"' in APP, f"树上的拖拽没有接 {ev}"
    assert 'addEventListener("dragstart"' not in APP, "树上的拖拽退回了 HTML5 那套"
    assert "dataTransfer.setData" not in APP
    # pointermove / pointerup 必须挂在 document 上：指针拖出卡片之后事件还得收得到，
    # 否则松手时高亮会永远卡在屏幕上
    assert 'document.addEventListener("pointermove", onDragMove' in APP
    assert 'document.addEventListener("pointerup", onDragUp' in APP


def test_a_drag_only_starts_after_a_threshold():
    """没有阈值的话，每一次「点一下选中这个节点」都可能变成一次移动——
    而移动要写原因、往 note.md 里追加一条永久审计，不是撤销一下就没事的操作。"""
    m = re.search(r"function onDragMove[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.beyondSlop" in m, "起拖没有阈值"
    assert m.index("U.beyondSlop") < m.index("beginDrag()"), "先起拖了才判阈值"
    assert "var DRAG_SLOP" in APP


def test_the_drop_target_is_judged_by_the_same_function_the_dialog_uses():
    """两套判断迟早会不一致，而不一致的那一刻用户看到的是「能拖，拖完报错」。"""
    aim = re.search(r"function aimDrag[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.moveError" in aim, "落点合法性又写了一套"
    # 落点非法时既不高亮也接不住，而不是松手之后弹错
    assert "drag.ok = code === \"\"" in aim
    assert "drag.ok &&" in aim, "非法目标也被高亮成落点了"
    up = re.search(r"function onDragUp[\s\S]*?\n  \}\n", APP).group(0)
    assert "if (!d.ok) return;" in up, "非法落点松手之后居然还往下走"


def test_a_drag_carries_the_whole_subtree_and_says_how_many():
    """后端的 move_step 本来就是整棵子树跟着走。拖一棵二十步的子树和拖一个光杆
    节点在屏幕上长得一模一样，而后果差二十倍——所以那一片必须看得见。"""
    begin = re.search(r"function beginDrag[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.subtreeIds" in begin
    assert '"dsub"' in begin, "跟着走的那一片没有任何标记"
    ghost = re.search(r"function paintGhost[\s\S]*?\n  \}\n", APP).group(0)
    assert "drag.carry" in ghost, "没说这一拖带走了几步"
    assert ".dsub" in CSS


def test_dropping_a_step_still_asks_why():
    """拖拽省掉的是「在下拉框里翻 id」的那十几秒，不是那句原因。

    用户吃过的亏是：没有移动能力时他把两个节点的正文对调来骗过显示，于是 013b 的
    创建日期和它现在装的内容对不上号，而这件事一条记录都没留下。移动过的树本来
    就会和创建顺序对不上——那句原因是半年后唯一能解释它的东西。
    """
    up = re.search(r"function onDragUp[\s\S]*?\n  \}\n", APP).group(0)
    assert "openMove(" in up, "拖完直接就写进去了，没有问原因"
    assert "papi(" not in up, "拖完就发请求了 —— 原因框被跳过了"
    # 目标由手势填好，焦点落在原因框上
    om = re.search(r"function openMove[\s\S]*?\n  \}\n", APP).group(0)
    assert "preset" in om, "拖拽挑好的父节点没有填进对话框"
    assert '$("#mv-reason").focus()' in om


def test_the_drag_never_touches_inputs():
    """parent 是「我当时接着哪一步想」，inputs 是「这些字节从哪来」。挪了位置
    就跟着改数据依赖的话，数据流图会跟着树形一起骗人。

    所以数据流视图整个不参与拖拽：那张图画的边就是 inputs，在它上面能拖，人立刻
    会以为自己在改数据依赖。在那张图上拖，得到的是一句说明，不是一次移动。
    """
    down = re.search(r"function onDragDown[\s\S]*?\n  \}\n", APP).group(0)
    assert 'view === "flow" ? "flow"' in down, "数据流视图上也能拖"
    mv = re.search(r"function onDragMove[\s\S]*?\n  \}\n", APP).group(0)
    assert 'drag.kind === "flow"' in mv and "drag.flow" in mv, "在数据流上拖没有任何说明"
    # 提交出去的 payload 里只有 parent + reason，没有 inputs
    sm = re.search(r"function submitMove[\s\S]*?\n  \}\n", APP).group(0)
    assert "inputs" not in sm, "移动的请求里混进了 inputs"
    assert 'id="mv-dragnote"' in HTML, "对话框上没写明「只改了 parent」"


def test_promoting_to_a_root_needs_a_deliberate_drop_zone():
    """「没落在任何卡片上就当成提为根」是最容易误触发的判定，而误触发的代价是
    一条永久审计外加一句被逼出来的原因。所以提为根有一条明确的落区。"""
    assert 'id="droot"' in HTML
    aim = re.search(r"function aimDrag[\s\S]*?\n  \}\n", APP).group(0)
    assert "onRoot" in aim
    # 没落在卡片上、也没落在落区上 = 空白 = 什么都不发生，不是提为根
    assert '"away"' in aim, "空白处被当成了提为根"
    assert "#droot { " in CSS or "#droot {" in CSS
    assert "pointer-events: none" in CSS.split("#droot {")[1].split("}")[0], \
        "落区会抢走点击 —— 命中与否该由坐标判"


def test_a_cancelled_drag_leaves_the_tree_exactly_as_it_was():
    """取消 / Esc = 什么都没发生。半截的高亮留在屏幕上比没有高亮更糟。"""
    assert "if (drag && drag.on && e.key === \"Escape\")" in APP, "拖到一半按 Esc 没有出口"
    end = re.search(r"function endDrag[\s\S]*?\n  \}\n", APP).group(0)
    for cls in ("dragging", "dsub", "dtarget"):
        assert cls in end, f"取消之后 {cls} 还留在屏幕上"
    assert '$("#droot").hidden = true' in end
    assert '$("#dghost").hidden = true' in end
    # 别人刚写进来一步 = 树的形状可能变了，而落点判定用的是起拖那一刻的坐标。
    # 不掐掉的话，人看着 A 松手，落到的是 B——而移动会写下永久审计。
    live = APP.split("es.onmessage")[1].split("};")[0]
    assert "cancelDrag" in live, "实时更新会把拖到一半的落点从手底下换掉"


def test_read_only_pages_cannot_drag_and_say_why():
    """静态导出是记录的一张照片。拖不动却不说为什么，人只会认为功能坏了。"""
    down = re.search(r"function onDragDown[\s\S]*?\n  \}\n", APP).group(0)
    assert "!canWrite() ? \"ro\"" in down
    mv = re.search(r"function onDragMove[\s\S]*?\n  \}\n", APP).group(0)
    assert 'drag.kind === "ro"' in mv and "drag.readonly" in mv
    # 抓手光标只在写得进去的时候给：给了却抓不动比不给更像坏了
    assert 'classList.toggle("canwrite", canWrite())' in APP
    assert "body.canwrite" in CSS


def test_the_button_and_dialog_path_survives_untouched():
    """只能靠拖的功能，对键盘用户等于不存在。"""
    assert 'if (name === "move") { openMove(selected()); return; }' in APP
    assert 'id="dlg-move"' in HTML and 'id="mv-parent"' in HTML
    assert '$("#mv-parent").addEventListener("change", paintMoveErr);' in APP


def test_the_drag_highlight_does_not_steal_an_existing_visual_channel():
    """规格里那条硬约束：一个视觉通道只承载一件事。线型=status，不透明度=祖先链
    /搜索命中，颜色只作线型的补强，字形标记那一档也满了。所以拖拽只能用
    outline（画在 border 之外，和 border-style 是两个属性）和两个临时浮层。
    """
    block = CSS.split("①b 拖着改父节点")[1].split("/* -------")[0]
    assert "outline:" in block, "拖拽的高亮没用 outline"
    assert "border-style:" not in block.split("#droot")[0], "拖拽动了线型 —— 那是 status 的通道"
    assert "opacity" not in block.split("#droot.off")[0], "拖拽动了不透明度 —— 那是祖先链的通道"
    # 两个浮层都不许抢点击：命中与否由坐标判
    assert block.count("pointer-events: none") >= 2


# ---------------------------------------------------------------- ② 数据依赖


def test_both_directions_of_the_data_dependency_are_listed_and_clickable():
    m = re.search(r"function renderDeps[\s\S]*?\n  \}\n", APP).group(0)
    assert "s.inputs" in m and "s.consumers" in m, "两个方向少一个"
    assert "input.head" in m and "input.consumers.head" in m
    assert "stepLink(" in m, "清单里的 id 点不过去"
    assert "input.lead" in m, "少了「parent 是我当时接着哪一步想，input 是这些字节从哪来」那句话"
    assert "renderDeps(s)" in APP


def test_the_tree_views_admit_that_some_inputs_are_not_on_the_tree():
    """不给这个标记，数据流视图就是一个没人知道该去点的按钮。"""
    m = re.search(r"function nodeMarks[\s\S]*?\n  \}\n", APP).group(0)
    assert "i.step !== s.parent" in m, "标记没有排掉「input 就是 parent」那种最常见的情况"
    assert "count.inputs" in m and "input.parent.tip" in m


def test_the_flow_view_is_a_third_view_not_a_panel():
    """森林是单父树，数据流是 DAG——同一份文件的两种读法平级。"""
    assert 'data-view="flow"' in HTML and 'id="fwrap"' in HTML
    m = re.search(r"function renderFlow[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.flowLayout" in m, "布局没走纯函数层（那样就测不到，也不保证幂等）"
    assert '"fedge k-" + e.kind' in m.replace("'", '"'), "边没有按 tree/data/both 分档画"
    # 三档必须都真的有样子。少一档 = 那类边和别的长得一样，图例就在说谎。
    for kind in ("k-tree", "k-data", "k-both"):
        assert f".fedge.{kind}" in CSS or f"fedge.{kind}," in CSS, f"边的三档少了 {kind}"
    assert "flowempty" in m, "一条 input 都没有时不说明，人会以为功能坏了"
    ap = re.search(r"function applyView[\s\S]*?\n  \}\n", APP).group(0)
    # `!dev ||` 是⑧加的：数据流仍然是**开发路径**下的第三种画法，切到定稿流程时
    # 它跟另外两种一起收起来。判据没变（还是 view），只是多了一层「哪一份东西」。
    assert '$("#fwrap").hidden = !dev || view !== "flow"' in ap


def test_the_flow_layout_is_deterministic_and_not_force_directed():
    """力导向每次刷新形状都不一样，而形状本身是信息。"""
    for banned in ("Math.random", "d3.force", "simulation"):
        assert banned not in APP, f"数据流视图里出现了 {banned}"


# ---------------------------------------------------------------- ⑤ code


def test_all_three_kinds_of_code_location_are_shown():
    m = re.search(r"function renderCode[\s\S]*?\n  \}\n", APP).group(0)
    assert "code.kind." in m and "code.manifest" in m and "code.files" in m
    assert 'c.from === "commit"' in m, "由 commit 派生出来的那条没有标出来"
    assert "code.from.commit.title" in m
    assert "renderCode(s)" in APP


@needs_node
def test_the_l2_explanation_no_longer_says_only_commit():
    """L2 的判据放宽了：快照目录 + 逐文件校验和在可溯源性上不比 commit 差。"""
    m = re.search(r"function renderTrace[\s\S]*?\n  \}\n", APP).group(0)
    assert "code.l2.note" in m
    r = subprocess.run([NODE, "-e", "const i=require('./web/i18n.js');"
                                    "console.log(i.tIn('en','trace.level.L2.hint'))"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert "snapshot" in r.stdout, "L2 的说明还只提 commit"


# ---------------------------------------------------------------- ④ 洞察


def test_superseded_insights_are_folded_not_deleted():
    """你当初信的那件事是你走到今天的一部分；删了它，后来的更正看着像凭空冒出来。"""
    m = re.search(r"function renderInsightBody[\s\S]*?\n  \}\n", APP).group(0)
    assert "superseded_by" in m, "没有区分被取代的和当前的"
    assert "insight.superseded.show" in m and "insold" in m
    row = re.search(r"function insightRow[\s\S]*?\n  \}\n", APP).group(0)
    assert "insight.supersedes" in row and "insight.superseded" in row, "两侧的标注少一边"
    assert "insight.warn.missing" in row, "supersedes 指向不存在的 id 时不吭声"


def test_one_insight_can_be_edited_by_its_id():
    """重锚一条洞察就是改它文本里的 [[013]]——不该逼人整段重写。"""
    assert "data-ins-edit" in APP and "insight.item.edit.prompt" in APP
    assert "data-ins-sup" in APP and "insight.supersede.prompt" in APP
    m = re.search(r'var ied = e\.target\.closest\("\[data-ins-edit\]"\);[\s\S]*?\n      return;\n    \}\n', APP).group(0)
    assert "add_insight" in m and "id: eid" in m, "改一条洞察没有带上 id（那会变成新增一条）"


def test_the_deleted_section_still_renders_verbatim():
    """「## 已删除」里那几行是步骤被真删之后唯一还 grep 得到的证据。"""
    m = re.search(r"function renderInsightBody[\s\S]*?\n  \}\n", APP).group(0)
    assert "window.md.render(text" in m, "非洞察小节没有原样渲染出来"


# ---------------------------------------------------------------- ⑥ 提示分档


def test_hints_are_separated_from_the_warnings_that_actually_matter():
    """混在一起显示，人很快就不再看警告栏了——而真会降级的条目才是要动手的。"""
    m = re.search(r"function renderWarnings[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.warnLevel" in m
    assert "lint.note" in m, "提示区没有写明「不影响等级」"
    assert "lint.level." in m, "三个级别名没走 i18n"


def test_hint_codes_are_exactly_the_ones_core_emits_without_changing_the_level():
    """这张表要是和 core 对不上，要么真警告被降成提示、要么提示冒充警告。

    ⑦ 之后这张表多了分叉那四条。**这是语义变化，不是把断言放松**：断言仍然是
    「恰好等于这一组」，只是这一组按 core 新发出来的诊断长了四条。四条各自的
    理由和原来那三条是同一个——它们一格 L0–L4 都不降：
      lone_alternative       一组只有一个候选。可能是另一条支漏标了，也可能它
                             本来就是普通延伸；两种读法都不让这一步变得更不可溯源。
      fork_without_decision  没写「在决定什么」。缺的是人的判断，不是记录的完整度。
      undecided_fork         还有两条以上候选活着。core 的措辞自己就写着「同时开
                             几条线是研究的常态，不是错」——把一句安抚话摆进警告栏，
                             等于训练人连真警告一起跳过去。
      decision_without_candidates
                             写了「在决定什么」却一个候选都没标。它是上面第二条的
                             镜像，缺的同样是一句人写的话；而且它最该被人看见的
                             地方是详情面板里那一行，不是警告栏——摆进警告栏
                             只会让人以为自己刚才写坏了什么。
    """
    m = re.search(r"var HINT_CODES = (\[[^\]]*\]);", APP)
    assert m
    got = set(json.loads(m.group(1)))
    src = (ROOT / "trace_core.py").read_text(encoding="utf-8")
    assert '"section_without_prose"' in src
    # 另外两条 core 是拼出来的（f"{kind}_without_explanation"），所以查后缀
    assert '_without_explanation"' in src, "core 不再发这一类 code 了，这张表就该跟着改"
    todo = set(json.loads(re.search(r"var TODO_CODES = (\[[^\]]*\]);", APP).group(1)))
    assert {"section_without_prose", "table_without_explanation", "code_without_explanation",
            "lone_alternative", "fork_without_decision",
            "decision_without_candidates"} == got
    # undecided_fork 从提示里摘出去，进了「待办」——它由 #forkbar 专门说，
    # 两处都说会让人以为自己犯了错。CLI 早就这么分了，这里核的是两边一致。
    assert todo == {"undecided_fork"}
    cli = (ROOT / "trace_cli.py").read_text(encoding="utf-8")
    assert re.search(r'TODO_CODES = \(\s*"undecided_fork",?\s*\)', cli),         "网页和 CLI 对「待办」的划分必须一致，否则同一份数据两个门面说的不是一件事"
    assert "missing_why" not in got, "真正会降级的诊断被降成了提示"
    # 这四条必须真的是 core 发的 warn 级，而不是界面自己编出来降级的
    for code in ("lone_alternative", "fork_without_decision", "undecided_fork",
                 "decision_without_candidates"):
        assert f'"{code}"' in src, f"core 不发 {code} 了，这张表就该跟着改"


def test_server_side_chinese_warnings_are_translated_where_we_know_how():
    """服务端的 warning 是中文的。认得的换成本语言的说法，认不出的原样显示——
    老老实实给中文，好过悄悄吞掉一条待办。"""
    # ⑧ 之后「选哪条文案」被抽进 warnPick（Methods 草稿要同一条判断的纯文本版），
    # warnText 只剩「认得就 tHtml、认不出就退回服务端原句并转义」这两条出口。
    m = re.search(r"function warnText[\s\S]*?\n  \}\n", APP).group(0)
    assert "return esc(w.message)" in m, "认不出来时把整条吞了（而且服务端那句话必须转义）"
    assert "i18n.tHtml(got.key" in m, "文案里的 `行内代码` 会原样显示成反引号"
    pick = re.search(r"function warnPick[\s\S]*?\n  \}\n", APP).group(0)
    assert "return null" in pick, "认不出来时必须说不认得，而不是编一条空文案出来"
    # 曾经还有第二个出口 warnPlain（纯文本版），服务的是浏览器里自己生成的那份
    # Methods 草稿。那份草稿连同整个 JS 生成器一起没了——三样导出只剩 Python
    # 那一份实现——所以这里不再有第二个出口要对齐，反过来它的**回归**才是信号。
    assert "function warnPlain" not in APP, \
        "warnPlain 回来了 —— 那说明网页又开始自己生成导出了，两份实现迟早不一致"
    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    for code in ("dangling_input", "self_input", "input_cycle",
                 "section_without_prose", "table_without_explanation", "code_without_explanation"):
        assert code in table, f"{code} 在界面上会漏出中文"


@needs_node
def test_the_chinese_warnings_really_do_get_translated_on_todays_messages():
    """上一条只查了表在不在；这一条拿 trace_core **此刻真的发出来的那句中文**
    走一遍界面侧的映射，占位符抠不出来就等于英文界面上原样漏出中文。"""
    import trace_core as core  # noqa: PLC0415

    a = core.Step(id="002", parent="", inputs=[{"step": "404", "note": ""}], dirname="002_x")
    b = core.Step(id="003", parent="", inputs=[{"step": "003", "note": ""}], dirname="003_x")
    ws = core.validate_inputs({"002": a, "003": b})
    got = {w["code"]: w["message"] for w in ws}
    assert {"dangling_input", "self_input"} <= set(got)
    r = subprocess.run(
        [NODE, "-e",
         "const src=require('fs').readFileSync('web/app.js','utf8');"
         "const m=/var WARN_MAP = \\{[\\s\\S]*?\\n  \\};/.exec(src)[0];"
         "const WARN_MAP=eval('(' + m.replace(/^var WARN_MAP = /,'').replace(/;$/,'') + ')');"
         "const ws=JSON.parse(process.argv[1]);"
         "console.log(JSON.stringify(ws.map(w=>{const d=WARN_MAP[w.code];"
         "if(!d||!d.pick) return {code:w.code,ok:!!d};"
         "const g=d.pick.exec(w.message);return {code:w.code,ok:!!g,v:g&&g[1].trim()};})))",
         json.dumps([{"code": k, "message": v} for k, v in got.items()])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    out = {x["code"]: x for x in json.loads(r.stdout)}
    assert out["dangling_input"]["ok"] and out["dangling_input"]["v"] == "404", out
    assert out["self_input"]["ok"] and out["self_input"]["v"] == "003", out


# ---------------------------------------------------------------- 拖拽的两处几何

def test_the_drop_target_must_be_somewhere_the_pointer_actually_is():
    """命中测试之前必须先过一道视口闸门。

    #diagram / #rows 的矩形在滚动时会伸到视口外面去，所以纯坐标换算会把
    「指针停在顶栏的搜索框上」「指针停在右边的详情面板上」也算成命中——
    浏览器里实测过：拖到详情面板上，弹出来的新父节点是屏幕上完全看不见的一步。
    松手就是一次挂到看不见的地方的移动，而移动写的是永久审计。
    """
    body = APP[APP.index("function aimAt("):]
    body = body[:body.index("\n  }") + 4]
    assert "inScroller" in body.split("\n")[1], "闸门必须是 aimAt 的第一句，不能等换算完再补"
    assert "withinRect" in APP, "闸门的判据要是可测的纯函数，不许在 DOM 回调里手写一遍"


def test_the_page_is_a_column_so_a_tall_warning_bar_cannot_push_the_bottom_off_screen():
    """main 的高度以前写死成 calc(100% - 41px)——只减了顶栏，没减警告栏。

    于是一有警告，main 就比视口高出警告栏那么多，被顶出屏幕的正是 #left 底部的
    图例、缩放条，以及拖拽时那条「提为根」的落区。浏览器里量过：警告栏到 60px
    落区就出视口了，而它是提为根的**唯一**入口——一个只在「项目足够干净」时
    才存在的功能，比没有更糟。
    """
    # 注释里会引用那个旧值来解释这次修复，所以先把注释剥掉再查活代码。
    live = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert "calc(100% - 41px)" not in live, "别再把顶栏的高度写死进别人的高度里"
    main = re.search(r"^main \{[^}]*\}", live, re.M)
    assert main, "找不到 main 的规则"
    assert "flex: 1" in main.group(0) and "min-height: 0" in main.group(0), \
        "min-height:0 不能省：flex 项默认不肯缩到内容以下，缺了它 main 照样撑出视口"
    assert re.search(r"^body \{[^}]*flex-direction: column", live, re.M), \
        "header / #warnbar / main 要由 body 自己排成一列，谁高谁矮都不用再算"


# ------------------------------------------------- ⑦ 决策分叉 / 支路 / 汇回

# 这一整块钉的是同一件事：**颜色可以承载信息了，但每一种关系必须再配一个非颜色
# 的通道**。规格里「颜色只作线型的补强」按用户的要求放宽了一半，没作废——灰度
# 打印出来、或者看不见颜色的人，丢掉的只该是那一眼，不是那个意思。


def test_the_three_relations_never_steal_the_channels_that_are_already_taken():
    """线型只归 status、不透明度只归祖先链/搜索命中。这两条一格都没放宽。

    抢了会怎样：把互斥候选画成虚线，它就和 wip 撞了；把汇回画淡，它就和
    「不在选中的链上」撞了。两种情况下读者都会读出一个根本不存在的结论。
    """
    live = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    for sel in (r"#dedges \.dedge\.b-alt", r"#dedges \.darrow\.b-alt",
                r"#rails \.edge\.b-alt"):
        m = re.search(sel + r" \{([^}]*)\}", live)
        assert m, f"{sel} 这条规则不见了：候选边不再换色了"
        decl = m.group(1)
        assert "dash" not in decl, f"{sel} 动了线型（那是 status 的）：{decl}"
        assert "opacity" not in decl, f"{sel} 动了不透明度（那是祖先链的）：{decl}"
        assert "stroke-width" not in decl, f"{sel} 借了线宽：{decl}"


def test_a_set_of_alternatives_is_bracketed_and_a_rejoin_is_a_curve():
    """颜色之外的那一半。没有它，灰度打印下三种边长得一模一样。"""
    dia = re.search(r"function renderDiagram\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.forkBracket" in dia, "图上不再画那道把一组候选括起来的括弧了"
    assert "U.rejoinCurve" in dia, "图上不再画汇回那条曲线了"
    assert "drejoinhead" in dia, "汇回没有箭头，就说不出「谁汇进谁」"
    rails = re.search(r"function renderRails\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "railfork" in rails, "轨道图上那道括弧没了"
    assert "U.railRejoin" in rails, "轨道图上的汇回没了"


@needs_node
def test_the_curve_is_what_says_a_rejoin_is_not_a_tree_edge():
    """树边永远是正交折线，汇回永远是曲线——形状本身就是那半个非颜色通道。

    真去问几何：拿一对坐标算一遍，看画出来的到底是不是三次贝塞尔。
    只在源码里 grep 一个 "C" 是查不出「有人把它改回折线」的。
    """
    r = subprocess.run(
        [NODE, "-e",
         "const U=require('./web/app.js');"
         "const c=U.rejoinCurve({x:0,y:0},{x:400,y:300},{nw:100,nh:50});"
         "console.log(JSON.stringify({d:c.d,arrow:c.arrow}))"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert "C" in got["d"], "汇回不是曲线了：" + got["d"]
    assert "V" not in got["d"] and "H" not in got["d"], "汇回混进了正交段：" + got["d"]
    assert got["arrow"].endswith("Z"), "箭头不是闭合三角形：" + got["arrow"]


def test_the_two_new_colours_exist_in_both_themes_and_are_not_the_status_colours():
    """深浅两套都要有。细线对对比度比色块敏感得多，浅色主题那两个值直接搬到
    深色底上是读不出来的。而且它们不能复用 --done/--wip/--dead——一条边上同时
    有「什么状态」和「什么关系」两件事，共用一个色就分不清读到的是哪一件。"""
    root = re.search(r"^:root \{([^}]*)\}", CSS, re.M).group(1)
    dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{([^}]*)\}", CSS).group(1)
    vals = {}
    for block, where in ((root, "light"), (dark, "dark")):
        for name in ("--alt", "--join", "--done", "--wip", "--dead", "--accent"):
            m = re.search(re.escape(name) + r":\s*([^;]+);", block)
            assert m, f"{where} 主题里没有 {name}"
            vals[(where, name)] = m.group(1).strip()
    for where in ("light", "dark"):
        for name in ("--done", "--wip", "--dead", "--accent"):
            assert vals[(where, "--alt")] != vals[(where, name)], f"{where}: --alt 和 {name} 撞了"
            assert vals[(where, "--join")] != vals[(where, name)], f"{where}: --join 和 {name} 撞了"
    assert vals[("light", "--alt")] != vals[("dark", "--alt")], "深色主题直接沿用了浅色的紫"
    assert vals[("light", "--join")] != vals[("dark", "--join")], "深色主题直接沿用了浅色的青"


def test_the_legend_explains_all_three_relations_or_the_colours_are_just_noise():
    """图例是这次改动唯一的解释入口。彩色的边加上去而图例不接，等于噪声。"""
    legend = HTML.split('id="treelegend"')[1].split('id="flowlegend"')[0]
    for key in ("list.legend.extends", "list.legend.alternative", "list.legend.rejoin"):
        assert f'data-i18n="{key}"' in legend, f"图例少了 {key}"
        assert f'data-i18n-title="{key}.title"' in legend, f"{key} 没有解释它为什么长这样的 tooltip"
    assert 'data-i18n-html="list.legend.note"' in legend, \
        "图例没有说明「括弧 + 带箭头的曲线」这两条非颜色通道"
    # 色样必须画出真正的形状（折线 / 括弧 / 带箭头的曲线），不能只是三段彩色横线。
    # 数的是**三种关系那一组**里的色样：⑨ 之后同一条图例里还有章节那一组
    # （底色带 + 跨章记号），按整条图例数就等于把「关系有几种」和「图例里有几样
    # 东西」混成一件事——而正是这条断言在防「彩色的边加上去而图例不接」。
    rels = legend.split('class="lgset lgrels"')[1].split("</span>\n        </span>")[0]
    assert rels.count("<svg") == 3, "三种关系的色样不是形状，只是三段彩色的线"
    assert "currentColor" in legend, "色样写死了颜色，深浅主题下会有一套是错的"


@needs_node
def test_the_fork_state_labels_exist_in_both_languages():
    """forkLabel 出的是 key，不是字面量，所以上面那条「i18n.t("…") 里的 key 都在」
    的扫描抓不到它们。漏一个的后果是括弧旁边直接摆着一个点分的 key。"""
    keys = i18n_keys()
    r = subprocess.run(
        [NODE, "-e",
         "const U=require('./web/app.js');"
         "const out=[];"
         "[{state:'decided',chosen:'012',options:['012'],live:['012']},"
         " {state:'abandoned',options:['a','b'],live:[]},"
         " {state:'open',options:['a','b'],live:['a','b']}]"
         ".forEach(function(g){var l=U.forkLabel(g);out.push(l.key);out.push(l.title);});"
         "console.log(JSON.stringify(out))"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    used = set(json.loads(r.stdout))
    assert len(used) == 6, f"三态没有各自的说法：{sorted(used)}"
    for lang in ("en", "zh"):
        assert not used - keys[lang], f"{lang} 缺这些 key：{sorted(used - keys[lang])}"


def test_the_open_forks_get_a_banner_because_that_is_the_whole_point():
    """「我还有几个岔路口没定」是这件事真正的收益。只画在单步详情里等于没有——
    那要求人先猜到该点哪一步。和「有几处位置已经不在了」同一个位置、同一档语气。"""
    assert 'id="forkbar"' in HTML
    fn = re.search(r"function renderForks\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert 'state === "open"' in fn, "横幅数的不是未决的那些组"
    assert "decision.open.summary" in fn, "横幅上的字没走 i18n"
    assert "stepLink(" in fn, "横幅上的 id 点不进去"
    live = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    bar = re.search(r"#forkbar \{([^}]*)\}", live)
    assert bar, "#forkbar 没有样式"
    # 未决不是警告：同时开几条线是研究的常态。用 --wip / --dead 就是在说它错了。
    assert "--wip" not in bar.group(1) and "--dead" not in bar.group(1), \
        "未决的分叉被画成了警告：" + bar.group(1)


def test_the_detail_panel_answers_all_three_questions():
    """候选：和谁并列、决策问题是什么、定了没有。分叉点：候选有谁、现在什么状态。
    汇回：本步的产物去了哪几步 / 哪几条支线汇进了本步，而且都点得过去。"""
    fork = re.search(r"function renderFork\(s\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "decision.head" in fork and "decision.of.head" in fork
    assert "decision.question.label" in fork and "decision.question.missing" in fork
    assert "decision.siblings" in fork, "候选看不到跟它并列的是谁"
    assert "decision.roots" in fork, "根之间那一组（at 为空）没有说法"
    # 「选了哪个」永远是从其余标 dead 派生的：界面上不许出现一个「标记赢家」的动作
    assert not re.search(r'data-act="(win|choose|chosen|pick)"', APP), \
        "出现了一个「标记赢家」按钮——那就是把双真相源请回来"
    join = re.search(r"function renderRejoin\(s\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "s.merge_in" in join and "s.merge_out" in join, "汇回只画了一个方向"
    assert "rejoin.at" in join, "没说这两条路是在哪儿分开的（core 算好的 LCA）"
    assert "stepLink(" in join, "汇回的两端点不过去"
    # 逐条标注某一行 input 是不是汇回，只能问 merge_in——inputs 是文件的逐字镜像
    deps = re.search(r"function renderDeps\(s\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "s.merge_in" in deps, "依赖清单里分不出哪一行是汇回"
    assert "i.rel" not in deps, "去 inputs 的记录上找派生字段了（那里故意没有）"


def test_marking_a_candidate_writes_only_that_step_s_own_line():
    """落盘的只有这一步自己那一行 `branch:`。往父节点上写一份候选清单、或者另存
    一个「选中了谁」，都是把上一代系统的死因请回来。"""
    body = APP[APP.index('if (name === "branch")'):]
    body = body[:body.index("\n      return;\n    }") + 4]
    assert "U.BRANCH_ALT" in body and "branch:" in body
    for banned in ("options:", "alt:", "rivals:", "chosen:", "winner"):
        assert banned not in body, f"往磁盘上写了派生出来的东西：{banned}"
    assert "expect:" in body, "没带 expect，两个人同时改就会互相无声覆盖"


def test_a_new_step_never_inherits_the_fork_semantics_of_its_parent():
    """branch 说的是「我和我 parent 之间那条边」，decision 说的是「我底下那个岔路口」。
    照抄下去的结果是从候选 A 往下走的每一步都变成候选，一棵树上到处是假岔路口。
    写入层有一条同名的测试钉着默认值，但那条管不到这个对话框。"""
    fn = re.search(r"function openNew\(parentId\)[\s\S]*?\n  \}\n", APP).group(0)
    assert '$("#nf-branch").value = "extends";' in fn
    assert '$("#nf-bnote").value = "";' in fn
    assert '$("#nf-decision").value = "";' in fn
    # 路径和 code 仍然继承（那是对的），所以不能靠「整个函数里没有 p ?」来判
    assert "p ? codeToText(p)" in fn, "顺手把代码位置的继承也删了"


def test_both_writing_surfaces_can_set_branch_and_decision():
    """写入层收了、界面上看不见，等于这个字段不存在。编辑器和新建框都得有。"""
    assert 'id="nf-branch"' in HTML and 'id="nf-decision"' in HTML
    ed = re.search(r"function renderEditor\(s\)[\s\S]*?\n  \}\n", APP).group(0)
    assert 'id="ed-branch"' in ed and 'id="ed-decision"' in ed and 'id="ed-bnote"' in ed
    # 译文里一行结构信息都不许有（写两份就是双真相源，core 会读都不读地丢掉）
    assert ed.index('id="ed-branch"') > ed.index("edLang ?"), \
        "译文那一份也长出了 branch 字段"
    save = re.search(r"function saveEditor\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "branch: branchField(st)" in save and "decision:" in save
    # 说明必须跟着 branch 一起发：分开发会让说明挂在一个不存在的候选身份上
    assert "bnote:" not in save, "把 branch_note 当成独立字段发出去了"


def test_the_new_warning_codes_read_their_values_structurally_not_by_regex():
    """core 发的 vars.n 是**数字**。只认字符串的话整条会退回去抠中文正则，
    抠不出来就在英文界面上原样漏出一整句中文。"""
    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    for code in ("lone_alternative", "fork_without_decision", "undecided_fork"):
        assert code in table, f"{code} 在英文界面上会漏出中文"
    assert "pick:" not in table.split("lone_alternative")[1], \
        "新的三条又去抠中文正则了——core 已经把值结构化发过来了"
    fn = re.search(r"function warnVar\(w, k\)[\s\S]*?\n  \}\n", APP).group(0)
    assert 'typeof v === "string"' not in fn, "又只认字符串了，vars.n 是数字"
    assert "String(v)" in fn


@needs_node
def test_the_hint_texts_really_come_out_in_english_on_todays_messages():
    """上一条只查了表在不在；这一条拿 trace_core **此刻真的发出来的那两条**
    走一遍界面侧的映射，漏一条就是英文界面上摆着一整段中文。"""
    import trace_core as core  # noqa: PLC0415

    by_id = {
        "011": core.Step(id="011", parent=None, dirname="011_x"),
        "012": core.Step(id="012", parent="011", branch="alternative", dirname="012_x"),
        "012b": core.Step(id="012b", parent="011", branch="alternative", dirname="012b_x"),
    }
    groups = core.compute_branch_groups(by_id, {"011": ["012", "012b"]})
    ws = core.validate_branches(by_id, groups)
    codes = {w["code"] for w in ws}
    assert {"fork_without_decision", "undecided_fork"} <= codes, codes
    script = (
        "const i18n=require('./web/i18n.js');"
        "const src=require('fs').readFileSync('web/app.js','utf8');"
        "const m=/var WARN_MAP = [{][^]*?\\n  [}];/.exec(src)[0];"
        "const W=eval('(' + m.replace(/^var WARN_MAP = /,'').replace(/;$/,'') + ')');"
        "const ws=JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(ws.map(function(w){var d=W[w.code];"
        "if(!d) return {code:w.code, ok:false};"
        "var v={};(d.take||[]).forEach(function(k){v[k]=String(w.vars[k]);});"
        "return {code:w.code, ok:i18n.has(d.key), text:i18n.tIn('en',d.key,v)};})))"
    )
    r = subprocess.run(
        [NODE, "-e", script,
         json.dumps([{"code": w["code"], "vars": w["vars"]} for w in ws])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    for got in json.loads(r.stdout):
        assert got["ok"], f"{got['code']} 没有对应的英文文案"
        assert not CJK.search(got["text"]), f"{got['code']} 的英文里漏出了中文：{got['text']}"
        if got["code"] != "lone_alternative":
            assert "2" in got["text"], f"{got['code']} 的数字没插进去：{got['text']}"


# ---------------------------------------------------------------- ⑦ 验收抓到的两处

def test_the_root_group_bracket_fades_like_everything_else():
    """不透明度这个通道只表示「和你选中的那条链有没有关系」。

    根之间那一组没有分叉点可以查，以前就干脆永不淡出——于是一道满亮的括弧
    压在两张已经灰掉的卡片上，读者读到的是「这一组和你有关」，而它在另一棵树上。
    正确的等价判据是「它的候选里有没有一个在你这条链上」。
    """
    body = APP[APP.index("document.querySelectorAll(\"[data-fork]\")"):]
    body = body[:body.index("});") + 3]
    assert "chain[at]" in body, "有分叉点的组仍然按分叉点判"
    assert "options" in body and "some" in body,         "根组要按「候选是否在链上」判，不能整条豁免"
    assert "!!at &&" not in body, "那句「at 为空就永不淡」是被修掉的东西，别又回来了"


def test_the_three_relationship_legend_items_survive_the_phone_breakpoint():
    """手机上紫色的候选轨道和青色的汇回曲线照常在画。

    把解释它们的图例连坐掉，读者看到的就是三种颜色加零个说明——
    而这三种关系正是这次改动的全部内容。760px 那条规则的注释写的也是
    「窄屏上先保住那三个词」，之前两条规则是互相矛盾的。
    """
    live = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    m = re.search(r"@media \(max-width: 480px\) \{(.*?)\n\}", live, re.S)
    assert m, "找不到 480px 那条媒体查询"
    phone = m.group(1)
    assert "#legend .lgm { display: none; }" in phone, "字形那一组仍然让位"
    assert re.search(r"#legend \.lgrels \.lgm \{[^}]*display:\s*inline-flex", phone), \
        "三种关系那一组要显式留下"
    assert 'class="lgset lgrels"' in HTML, "那一组要有自己的钩子，不能只靠位置"


# ------------------------------------------------------------ ⑧ 定稿流程


def test_the_final_pipeline_is_a_second_thing_not_a_fourth_drawing():
    """切换器必须是两级的。

    图 / 列表 / 数据流画的是**同一批步骤**；定稿流程画的是另一批（从 `result:`
    沿 `input:` 反推出来的闭包）。把它做成第四个并排的按钮，等于对读者说
    「这是同一件事的另一种排版」——而 i18n 的 pipeline.pair.note 正是写来挡
    这个误解的。所以一级选「看哪一份东西」，二级选「怎么画」。
    """
    assert 'id="modetoggle"' in HTML and 'data-mode="pipeline"' in HTML
    assert 'data-view="pipeline"' not in HTML, "定稿流程被塞进了画法那一排"
    # 二级只在开发路径下出现：定稿流程只有一种画法，它就是一张方法图
    ap = re.search(r"function applyView[\s\S]*?\n  \}\n", APP).group(0)
    assert '$("#viewtoggle").hidden = !dev' in ap
    assert '$("#pwrap").hidden = dev' in ap
    # 两级在视觉上必须分得出来，否则「分了级」只存在于 DOM 里
    assert ".seg.mode button" in CSS and ".seg.sub button" in CSS


def test_the_pipeline_view_is_a_document_not_a_tree_to_poke_at():
    """它占满整块工作区并收起详情面板。

    详情面板里装的正是这一档不该显示的东西：为什么试这个、当时有几个候选、
    被移动过几次。要看那些就按「回开发路径上看它」——那个按钮必须在。
    """
    assert "body.pipeline-mode #detail { display: none; }" in CSS
    assert re.search(r"body\.pipeline-mode #left \{[^}]*flex:", CSS)
    steps = re.search(r"function pipeSteps[\s\S]*?\n  \}\n", APP).group(0)
    assert "data-devgoto=" in steps, "流程里的每一步都要有一个跳回开发路径的入口"
    assert "pipeline.jump.dev" in steps
    # 按「别人照着做」挑内容：做了什么、命令（它就在那一节的原文里）、代码、产物、等级
    assert "it.what" in steps and "renderCode(s)" in steps and "renderPaths(s)" in steps
    assert "lvChip(it.level" in steps
    # 「为什么试这个」不许出现在这一档：sectionOf 只取 what 那一节
    build = re.search(r"function buildPipeline[\s\S]*?\n  \}\n", APP).group(0)
    assert '"what"' in build and '"why"' not in build


def test_the_two_paths_point_at_each_other():
    """两条路径都留着的全部意义就是这一对入口，缺一个就只剩一条路。"""
    assert "[data-devgoto]" in APP and "[data-pipego]" in APP
    dev = APP[APP.index('e.target.closest("[data-devgoto]")'):]
    dev = dev[:dev.index("return;") + 7]
    assert 'setMode("dev"' in dev and "select(" in dev, "跳回去还得选中它，否则人到了那边要自己找"
    # 开发路径这一侧的回指：标记 + 详情面板里那个反向入口
    marks = re.search(r"function nodeMarks[\s\S]*?\n  \}\n", APP).group(0)
    assert "pl.member" in marks and "cmk pipe" in marks
    detail = re.search(r"function renderPipelineOf[\s\S]*?\n  \}\n", APP).group(0)
    assert "data-pipego=" in detail


def test_the_pipeline_marker_does_not_steal_an_existing_visual_channel():
    """线型只归 status、不透明度只归祖先链/搜索命中、颜色已经归了三种关系。

    所以「这一步在定稿流程里」只能走字形这个第四通道（和 L0 · ↺✕ · ⇢ · Y ·
    ~> · ⇄ · ⊘ 同一档）。
    """
    marks = re.search(r"function nodeMarks[\s\S]*?\n  \}\n", APP).group(0)
    pipe = marks[marks.index("var pl = s.pipeline"):]
    for stolen in ("opacity", "faded", "border-style", "stroke-dasharray", 's-" +'):
        assert stolen not in pipe, f"回指标记动了已经被占用的通道：{stolen}"
    live = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    m = re.search(r"\.cmk\.pipe \{([^}]*)\}", live)
    assert m, "回指标记没有样式"
    for stolen in ("opacity: .", "stroke", "font-style"):
        assert stolen not in m.group(1), f".cmk.pipe 借了别的通道：{stolen}"
    assert 'class="cmk pipe"' in HTML, "图例里没有它，那个 [3] 就是个谜"


def test_the_empty_state_teaches_the_line_you_can_always_write_by_hand():
    """绝大多数项目打开这一档看到的就是空态，它是这个功能的门面。

    按钮那条路要服务端配合（见 seams），而在 project.md 里手写一行 `result:`
    永远有效——所以那一行的真实写法必须印在空态上，不能只藏在文档里。
    """
    empty = re.search(r"function pipeEmpty[\s\S]*?\n  \}\n", APP).group(0)
    for key in ("pipeline.empty.title", "pipeline.empty.what", "pipeline.empty.why",
                "pipeline.empty.how", "pipeline.empty.act"):
        assert key in empty, f"空态少了 {key}"
    assert 'act: i18n.t("pipeline.empty.act")' in empty, \
        "{act} 硬编码了按钮名，切语言时那句话和按钮会各说各的"
    # 「两条路径不是详略两版」这句必须在空态上就说清楚
    assert "pipeline.pair.note" in empty


def test_the_pipeline_diagnostics_never_reach_the_top_warning_bar():
    """顶栏不许因为定稿流程多出提示。

    现存项目一个 `result:` 都没声明，把这些挂进全局警告栏等于每个项目每次打开
    都被念一遍，而那正是让人从此不看警告的做法。
    """
    checks = re.search(r"function pipeChecks[\s\S]*?\n  \}\n", APP).group(0)
    assert "PIPE.diagnostics" in checks
    warn = re.search(r"function renderWarnings[\s\S]*?\n  \}\n", APP).group(0)
    assert "pipeline" not in warn, "定稿流程的诊断混进了顶栏那条警告栏"
    # 但换文案用的是同一张表：同一个 code 在两处说两句不同的话，比漏翻还糟
    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    for code in ("pipeline_dead_step", "pipeline_weak_step", "pipeline_no_result"):
        assert code in table, f"{code} 在英文界面上会原样漏出中文"


def test_the_pipeline_codes_the_ui_maps_are_really_the_ones_core_sends():
    """表里写着而 core 不发的 code 是一句空话，反过来是英文界面上漏中文。"""
    src = (ROOT / "trace_core.py").read_text(encoding="utf-8")
    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    for code in ("pipeline_dead_step", "pipeline_weak_step", "pipeline_no_result"):
        assert f'"{code}"' in src, f"core 不发 {code} 了，这张表就该跟着改"
    assert "bad_pipeline" in src, "core 侧 `pipeline:` 写错值的降级提醒不见了"
    # 反向：core 现在发的每一条 pipeline_* 诊断，界面认不出的会退回中文原句。
    # 这一条不当成失败（退回原句是有意的兜底），但把缺口显式列出来，
    # 免得「反正会退回」变成再也没人补文案的理由。
    sent = set(re.findall(r'warn\(\s*"(?:warn|info)",\s*"(pipeline_\w+)"', src))
    assert {"pipeline_dead_step", "pipeline_weak_step"} <= sent, sent


# 抠一个顶层函数的源码。写成模板是因为反斜杠密集的正则重复写六遍，
# 抄错一处只会让断言静默地什么都不查。
RE = r"function %s[\s\S]*?\n  \}\n"


def test_the_three_exports_have_no_second_implementation_in_the_browser():
    """图 / Methods 草稿 / 独立页面在网页里**一份都不生成**。

    曾经这里有第二份实现（JS 各画一遍 SVG 和 markdown），于是同一个项目在屏幕上
    和在 `trace_cli.py pipeline --svg` 里是两张不同的图、两份不同的草稿。两份都
    看着对，而**其中一份会进论文**。现在三样只有 Python 那一份实现，网页拿到的
    是它的产物：服务模式 fetch 三条只读路由，静态导出读 build 写在同目录的三个
    文件。
    """
    for gone in ("pipelineSVG", "pipelineMarkdown", "pipelinePage", "pipelineLayout",
                 "PIPELINE_SVG_CSS", "PIPELINE_PAGE_CSS"):
        assert gone not in APP, f"{gone} 回来了 —— 网页又在自己生成导出"
    # 三个按钮指到的是那一份实现的三个出口，不是就地拼出来的 Blob
    exp = re.search(RE % "pipeExport", APP).group(0)
    for key in ("export.figure", "export.methods", "export.page", "export.draft.note"):
        assert key in exp, f"导出那一块少了 {key}"
    assert "download=" in exp and "exportURL(" in exp, "导出按钮不再是 <a download>"
    assert "createObjectURL" not in APP, "Blob 下载意味着那批字节是这一页自己拼的"
    url = re.search(RE % "exportURL", APP).group(0)
    assert "/pipeline/" in url and 'MODE === "static"' in url, \
        "两种模式都得指到同一份实现的产物上"
    # 屏幕上那张图就是导出的那一份，一个字节都不差：它们出自同一个出口。
    # ⑨ 之后「此刻在导哪一章」是**一份**页面状态（exportChapter），图和三个按钮
    # 都从它来，所以这两个函数的签名一个字都没变。
    screen = re.search(RE % r"pipeFigure\(\)", APP).group(0)
    assert "PIPE_SVG" in screen, "屏幕上画的是另一张图，人会对着一张、发出去另一张"
    fetcher = re.search(RE % "fetchFigure", APP).group(0)
    assert 'exportURL("figure")' in fetcher, "屏幕上那张图和导出的那张不是同一个出口"


def test_the_exports_never_ask_what_time_it_is():
    """逐字节确定是 P3。混进一个时间戳，「重新生成永远比留一份拷贝划算」就成了
    一句假话，人就会把导出结果存进仓库。

    生成器现在只在 Python 那一侧，所以这一条也跟过去查那边的源码 —— 留在这里查
    JS 等于对着一个已经不存在的实现做保证。"""
    src = (ROOT / "trace_mcp.py").read_text(encoding="utf-8")
    gen = src[src.index("def fmt_pipeline"):src.index("def _section(")]
    for banned in ("datetime.now", "time.time", "random.", "date.today", "utcnow"):
        assert banned not in gen, f"导出生成器里出现了 {banned}"


def test_the_section_order_matches_the_core_side():
    """Methods 草稿取的是「做了什么」那一节。下标错一位就写成了「为什么」——
    而「为什么」恰恰是定稿流程唯一不该显示的东西。"""
    import trace_core as core  # noqa: PLC0415

    got = re.search(r"var SECTION_ORDER = \[([^\]]*)\];", APP).group(1)
    keys = [x.strip().strip('"') for x in got.split(",") if x.strip()]
    assert keys == list(core.SECTION_NAMES), "小节顺序和 trace_core 对不上"


def test_the_editor_never_erases_a_pipeline_line_it_cannot_see():
    """`pipeline: exclude` 完全可以先于任何 `result:` 写下来。

    那时 forest 里根本没有 pipeline 这个键（现存项目必须完全无感），界面也就
    不知道磁盘上那一行写着什么。照空值发回去 = 一次无关的正文编辑悄悄把它删了，
    而人不会收到任何提示。所以：控件不画、键不发。
    """
    field = re.search(r"function pipelineField[\s\S]*?\n  \}\n", APP).group(0)
    assert "if (!F.pipeline) return \"\";" in field
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "if (F.pipeline && !st.lang) {" in save and "payload.pipeline =" in save
    # 理由必填是写入侧的硬规矩（不写就 400）。界面得在这里拦住，而不是让人走一趟
    # 服务端再收到一句读不懂的中文报错——上一版界面还印着「可不写」。
    assert "editor.pipeline.note.required" in save, "选了取值不填理由时没人当场拦住"
    # 译文里一行结构键都不许有（和 paths / inputs / code 同一条）
    assert "!st.lang" in save
    # 草稿也要跟上，否则改了取值刷新一下就没了
    st = re.search(r"function editorState[\s\S]*?\n  \}\n", APP).group(0)
    assert "ed-pipe" in st and "ed-pnote" in st
    same = re.search(r"function sameAsStep[\s\S]*?\n  \}\n", APP).group(0)
    assert "st.pipe" in same and "st.pnote" in same


def test_declaring_a_result_has_its_own_entry_and_no_member_list_editor():
    """「哪一步是成果」是整条流程里唯一写下来的事，所以它有自己的入口；
    而「谁在流程里」是算出来的，界面上永远不会有一个编辑成员清单的地方——
    那就是第二份真相。"""
    assert 'data-act="result"' in APP and "pipeline.result.mark" in APP
    assert "/results" in APP, "声明成果没有写入路径"
    for forbidden in ("pipeline.members", "editMembers", "data-act=\"pipeline-members\""):
        assert forbidden not in APP, f"界面上出现了成员清单编辑口：{forbidden}"


# ---------------------------------------------------------------- ⑨ 章节
#
# 章节是**横切**的：顶栏一个筛选器，开发路径按它 dim、定稿流程按它换编哪一条流程。
# 这一组钉的全是「整块能力有没有真的接上线」，以及三条最容易被后来人拆掉的硬规矩：
# 现存项目完全无感 · 筛选只 dim 不 hide · 不许抢占已经被占住的视觉通道。


def test_the_chapter_filter_is_not_a_third_level_of_the_view_switcher():
    """一级选「看哪一份东西」、二级选「同一份东西怎么画」，而章节两件都不改——
    它选的是**看多大范围**，横切在那两级之上。塞进那两排按钮里，等于对读者说
    「章节是第三种画法」，而它同时作用于开发路径和定稿流程。"""
    assert 'id="chapfilter"' in HTML, "顶栏没有章节筛选器"
    switcher = HTML[HTML.index('id="modeswitch"'):HTML.index('id="btn-new"')]
    assert "chapfilter" not in switcher, "章节被塞进了两级切换器里"
    assert 'data-mode="pipeline"' in switcher and 'data-view="flow"' in switcher, \
        "锚点挪了，这条断言不再对着那两排按钮"
    # 和搜索是同一类动作（缩小注意力范围），所以它挨着搜索框，不在视图按钮之间
    assert HTML.index('id="search"') < HTML.index('id="chapfilter"') < HTML.index('id="modeswitch"')


def test_a_project_without_a_single_chapter_line_is_completely_untouched():
    """这一轮最硬的一条要求：现存项目一个 `chapter:` 都没有，它们的界面必须
    一个字、一个属性、一个像素都不多。

    `forest.chapters` 在那种项目里整个键都不存在（core 已经这么做了），所以
    界面这一侧的判据只有一条：CH.declared。筛选器、图例那一组、卡片上的色条、
    详情面板那一块、项目主页那块面板，全部挂在它上面。"""
    f = re.search(RE % "renderChapFilter", APP).group(0)
    assert "box.hidden = !CH.declared" in f, "没有章节时筛选器还在栏上（一个恒灰的下拉框）"
    assert "lg.hidden = !CH.declared" in f, "图例里那一组没跟着收起来"
    attrs = re.search(RE % "chapAttrs", APP).group(0)
    assert 'if (!c) return "";' in attrs, "没有章节时卡片上还是多了属性"
    assert "CH.declared &&" in attrs
    for fn in ("renderChapterOf", "renderChapters"):
        body = re.search(RE % fn, APP).group(0)
        assert 'if (!CH.declared) return "";' in body, f"{fn} 在没有章节时还画东西"
    # HTML 里那个下拉框出厂就是 hidden 的：首帧渲染之前它也不该闪一下
    assert '<select id="chapfilter" hidden>' in HTML
    assert 'id="chaplegend" hidden' in HTML


def test_filtering_by_chapter_only_dims_and_never_hides():
    """规格里那条「搜索只 dim 不 hide」的理由逐字适用：隐藏会打乱轨道对齐、
    改变图的形状，而**形状本身是信息**——「消融是从主实验哪一步分出去的」这句话，
    正是靠被筛掉的那些节点还在原位才看得见。"""
    sel = re.search(RE % "renderSelection", APP).group(0)
    assert 'el.classList.toggle("offchap", !inChapFilter(id));' in sel
    for stolen in (".hidden = ", "display", "remove()"):
        assert stolen not in sel.split("offchap")[1][:400], \
            f"章节筛选动了「在不在」而不是「淡不淡」：{stolen}"
    live = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    m = re.search(r"#dnodes \.card\.offchap[^{]*\{([^}]*)\}", live)
    assert m and "opacity" in m.group(1), ".offchap 不是靠不透明度实现的"
    assert "display" not in m.group(1) and "visibility" not in m.group(1)


def test_chapters_do_not_steal_a_visual_channel_that_is_already_taken():
    """线型只归 status、不透明度只归「和你此刻的关注有没有关系」、边的颜色已经
    归了三种关系（延伸 / 互斥候选 / 汇回）。章节只能用新通道：
    一块底色带 + 卡片左侧一道窄条，外加跨章那一对小斜杠。"""
    live = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    band = re.search(r"#dedges \.chband \{([^}]*)\}", live)
    assert band, "底色带没有样式"
    for stolen in ("stroke-dasharray", "border-style"):
        assert stolen not in band.group(1), f"底色带借了别的通道：{stolen}"
    stripe = re.search(r"\.card\[data-chap\]::before[^{]*\{([^}]*)\}", live)
    assert stripe, "卡片上那道章节色条没了"
    for stolen in ("border-style", "stroke-dasharray", "opacity: .3"):
        assert stolen not in stripe.group(1), f"色条借了别的通道：{stolen}"
    # 卡片的 class 那一格已经被 status 占着，所以色相走属性（data-chi）
    assert '[data-chi="0"]' in CSS, "色相没有属性入口，卡片上的色条会是空的"
    cross = re.search(r"\.xchap \{([^}]*)\}", live)
    assert cross and "stroke-dasharray" not in cross.group(1), \
        "跨章记号用了虚线 —— 那是 status 的通道"
    # 六个色相在两种主题下各有一套：深色底上太暗的色相压根显不出来
    assert CSS.count("--ch0:") == 2 and CSS.count("--ch5:") == 2
    # 图例里必须有它们，否则底色带和那对斜杠就是两个谜
    assert 'id="chaplegend"' in HTML and 'data-i18n="chapter.cross.legend"' in HTML


def test_the_chapter_a_step_belongs_to_is_read_off_core_not_off_the_step_itself():
    """「这一步属于哪个章节」只有 core.resolve_chapters 一份判据，界面拿到的是
    `forest.chapters.of`。拿「这一步自己写没写 chapter:」当归属判断，会让继承
    下来的二十步集体看着像未分章——那是 `.declared`，说的是这一行**写在哪**。"""
    of = re.search(RE % "chapOf", APP).group(0)
    assert "CH.of[id]" in of, "归属不是从 core 那份判据读的"
    assert "declared" not in of, "拿 declared 当归属判断了"
    # 图上那块「章节从这里开始」的锚点用的才是 declared（它回答的是另一个问题）
    src = re.search(RE % "chapterLabels", APP).group(0)
    assert "chapter.declared" in src and "chapter.badge.title" in src


def test_the_four_chapter_diagnostics_are_the_ones_core_really_sends():
    """认不出 code 时 warnText 退回服务端原句，也就是在英文界面上原样漏出一整句
    中文。这四条恰恰是给写错字的人看的。"""
    core_src = (ROOT / "trace_core.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'"(chapter_[a-z_]+|bad_chapter)"', core_src))
    assert codes, "core 那边一条章节诊断都没有了？"
    for code in codes:
        assert re.search(rf"\b{code}:\s*\{{ key:", APP), f"WARN_MAP 少了 {code}"
    # 占位符必须逐字用 core 放进 vars 的那几个名字（warnText 不改名）
    assert 'chapter_note_conflict: { key: "lint.chapter.desc.conflict", take: ["name", "ids", "id"] }' in APP
    assert 'bad_chapter: { key: "lint.chapter.unnamed", take: ["note"] }' in APP


def test_the_chapter_diagnostics_never_reach_the_top_warning_bar():
    """和定稿流程那三条同一条规矩：`forest.chapters.diagnostics` 和
    `forest.warnings` 是两份东西。章节的事让每个项目每次打开都多一条顶栏提示，
    人很快连真警告一起不看了。"""
    warn = re.search(RE % "renderWarnings", APP).group(0)
    assert "F.warnings" in warn and "CH.diagnostics" not in warn
    panel = re.search(RE % "renderChapters", APP).group(0)
    assert "CH.diagnostics" in panel, "那三条诊断没人显示 = 白算"


def test_the_editor_never_erases_a_chapter_line_it_cannot_see():
    """`chapter: | 只写了说明没写名字` 那种写坏的行在 forest 里没有任何痕迹
    （core 报一条 bad_chapter、归属退回继承，而那半句人写的话原样留在文件里）。
    照空值发回去 = 一次无关的正文编辑悄悄把它删了。所以：值没变就不发这个键。"""
    save = re.search(RE % "saveEditor", APP).group(0)
    assert "if (chapterFieldValue(st) !== chapterFieldValue(was)) {" in save
    assert "payload.chapter = chapterFieldValue(st);" in save
    assert "!st.lang" in save, "译文里一行结构键都不许有"
    # 草稿也要跟上，否则填了一半刷新一下就没了
    st = re.search(RE % "editorState", APP).group(0)
    assert "ed-chap" in st and "ed-chnote" in st
    same = re.search(RE % "sameAsStep", APP).group(0)
    assert "st.chapter" in same and "st.chnote" in same


def test_neither_writing_surface_inherits_the_parent_chapter_into_the_form():
    """章节本来就是沿树继承的。把父步骤那个名字预填进新建框，等于把一次继承
    展开成一份会过期的拷贝——父步骤改个章节名，这一步就留在旧章里，而磁盘上
    谁都看不出这是抄来的。编辑器里放的也只是**这一步自己写的**那个名字。"""
    new = re.search(RE % "openNew", APP).group(0)
    assert '$("#nf-chap").value = "";' in new, "新建框预填了父步骤的章节"
    assert "inheritPath" in new, "锚点没了，这条断言就不再对着同一段代码"
    tgt = re.search(RE % "editTarget", APP).group(0)
    assert "(s.chapter && s.chapter.declared) ? s.chapter.name" in tgt, \
        "编辑器里放的是继承来的归属，一按保存就在这一步身上多写一行"


def test_starting_a_chapter_says_how_many_steps_come_with_it():
    """换章磁盘上一个字节都没变（只有这一行），二十步集体转章，diff 里只看得见
    那一行。用户吃过的亏正是「移动之后树形和创建顺序对不上」，这是同一类事、
    只是更隐蔽。所以带走了几步要当场说出来，而且得**在写之前**数。"""
    act = APP[APP.index('if (name === "chapter") {'):]
    act = act[:act.index('if (name === "chapter-note")')]
    assert "U.chapterCarry(IDX, cs.id).length" in act
    assert "toast.chapter.carry" in act and "toast.chapter.set" in act
    assert "toast.chapter.cleared" in act, "撤销之后没人说一声"
    # 批量入口是**刻意没有**的：那会在二十个文件里各留一行会过期的拷贝
    for forbidden in ("data-act=\"chapter-bulk\"", "markChapterAll", "chapter.bulk"):
        assert forbidden not in APP, f"界面上出现了批量标章节的入口：{forbidden}"


def test_the_chapter_description_is_written_where_core_says_it_takes_effect():
    """说明跟着**章节**走，不跟着这一步走。core 的裁决是「id 序最早的那个带说明
    的声明」生效，写到别处的结果是人改了说明、屏幕上显示的还是原来那一句。"""
    act = APP[APP.index('if (name === "chapter-note")'):]
    act = act[:act.index("if (name === \"edit-insights\")")]
    assert "U.chapterNoteHome(entry, IDX)" in act
    assert "toast.chapter.desc.saved" in act


def test_exporting_one_chapter_asks_the_server_first():
    """按章节导出的那三样字节只有 Python 一份实现，这一页只是指过去。所以得先
    问清楚这台服务端会不会按章节编：一台还没升级的会**忽略** `?chapter=`、
    老老实实回整个项目那一份，而文件名上写着「消融」——那正是这一整档设计要挡的
    「屏幕上讨论一张、投出去另一张」，何况这次连文件名都在撒谎。"""
    probe = re.search(RE % "probeChapterPipeline", APP).group(0)
    # 判据必须是服务端**自己说**它编的是哪一章。以前这里钉的是
    # `d.chapter === name` 那一串字面量 —— 而 payload 里那个 `chapter` 是一个
    # **对象**，那句比较永远不成立，于是按章节导出整块静默消失，而这条测试
    # 一路绿着：它钉的是写法，没人去问服务端到底回的是什么形状。现在改成钉
    # 那个剥出来的纯函数，形状由 tests/test_seams_chapter.py 拿真响应当场量。
    assert "U.chapterEcho(d)" in probe, "判据不是服务端自己说它编的是哪一章"
    assert "d.chapter ===" not in probe, "又拿整个对象去和章节名比了"
    # 「此刻在导哪一章」只有一份判据，图、三个按钮、那句 toast 全从它来 ——
    # 有第二个入口，就有「屏幕上讨论的和投出去的不是同一份」的第二种得法。
    scope = re.search(RE % "exportChapter", APP).group(0)
    assert "chapExportable(ch) ? ch : null" in scope, \
        "服务端不认按章节导时，按钮仍然挂着一个名不副实的文件名"
    url = re.search(RE % "exportURL", APP).group(0)
    assert '"?chapter=" + encodeURIComponent(ch)' in url and "exportChapterParam()" in url
    # 文件名是**派生**出来的：章节名可以是 `主实验/数据准备`、`CON`
    name = re.search(RE % "exportName", APP).group(0)
    assert "CHAP_STEM[ch]" in name and "exportChapter()" in name
    stems = re.search(RE % "chapStems", APP).group(0)
    assert "U.chapterFileStems" in stems
    assert "chapter.name" not in name, "章节名被直接拼进了文件名"


def test_each_chapter_compiles_its_own_pipeline_and_the_ui_never_recomputes_it():
    """主实验一段 Methods、消融一段，论文里本来就是两段。切分那一张 DAG 的事
    core 已经做完了（`pipeline.chapters`）；浏览器里再算一遍闭包，就会出现
    「屏幕上讨论的图和投出去的图不是一张」。"""
    r = re.search(RE % "renderPipeline", APP).group(0)
    assert "pipeGroup(focus)" in r and "U.pipelineChapterSteps(PIPE, group)" in r
    assert "depClosure" not in r, "定稿流程这一屏自己算起闭包来了"
    grp = re.search(RE % "pipeGroup", APP).group(0)
    assert "PIPE.chapters" in grp
    # 借来的上游要标出来：一个输入不在流程里的成员，写进 Methods 就是一句断了的话
    steps = re.search(RE % "pipeSteps", APP).group(0)
    assert "group.external" in steps and "chapter.of.head" in steps
    # 这一章还没有成果时是一句邀请，不是一条错误
    empty = re.search(RE % "pipeChapterEmpty", APP).group(0)
    assert "chapter.pipeline.none" in empty


def test_the_figure_on_screen_is_never_a_figure_of_another_chapter():
    """按章节看和看整项目是两张图。谁也不许暂时顶替谁——一张顶替上去的图会被
    当成这一章的方法图，那比空着糟得多。"""
    fig = re.search(RE % r"pipeFigure\(\)", APP).group(0)
    assert "PIPE_SVG_AT === figureKey(FOREST_SEQ)" in fig, \
        "手上这份图不必正好是这一屏要的那一份（这一版记录 × 这一章）"
    key = re.search(RE % "figureKey", APP).group(0)
    assert "exportChapterParam()" in key, "缓存键里没有章节 —— 换一章之后旧图会留在屏幕上"
    # 服务端不认按章节编时 exportChapter() 回 **null**：取的、画的、导的都是整项目
    # 那份，三处一致；而按章节看的那一屏干脆不摆图，绝不拿整项目那张顶替。
    # 判的是 `=== null` 而不是真值：**未分章那一组的名字就是空串**，它是一组真的
    # 成果（多数项目的主线从没起过名字），`!""` 会把它错当成「没在导某一章」，
    # 于是整项目那张图又被挂到了那一屏上——正是这条测试要挡的事。
    r = re.search(RE % "renderPipeline", APP).group(0)
    assert '(group && exportChapter() === null ? "" : pipeFigure())' in r, \
        "服务端不认按章节编的时候，整项目那张图会被当成这一章的方法图挂上去"


def test_the_cross_chapter_edges_are_drawn_not_tidied_away():
    """消融当然要吃主实验的产物（`input: 023`），那条边说的正是「消融是对着
    主结果测的」——它是两个章节之间唯一的连接，值得画出来。"""
    d = re.search(RE % "renderDiagram", APP).group(0)
    assert "CH.crossings" in d and "U.crossTick(" in d
    f = re.search(RE % "renderFlow", APP).group(0)
    assert "crossAt[e.from" in f and "U.crossTick(" in f
    # 两种边各说各的意思（一条是「从哪儿分出来的」，一条是「字节从哪来」）
    for key in ("chapter.cross.parent", "chapter.cross.input"):
        assert key in APP, f"跨章节的边少了 {key}"


def test_the_project_cards_show_chapters_only_when_the_server_sends_them():
    """索引页拿到的是 `/api/projects`，里面没有 forest。服务端顺手带上
    `chapters: [{name, n}]` 就显示，没带就一个字都不多——绝不显示一行
    「0 个章节」的谎话。"""
    fn = re.search(RE % "projectChapters", APP).group(0)
    assert 'if (!cs.length) return "";' in fn
    assert "count.chapters" in fn
    assert "projectChapters(p)" in re.search(RE % "renderHome", APP).group(0)
