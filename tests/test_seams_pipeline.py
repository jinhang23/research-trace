"""⑧「两条路径」这一轮的接缝断言：开发路径（记录）与定稿流程（方法）。

按文件所有权分派不出冲突，**缺陷长在接缝上**。这一轮真实抓到的，全是这个形状：

  · **三样导出有两份实现。** SVG 和 Methods 草稿在 `trace_mcp.py` 里有一份
    Python 的（CLI / REST / MCP / 静态导出都走它），在 `web/app.js` 里有一份
    JS 的（网页上那三个按钮）。两份都能跑、都通过了各自的测试，输出却是两份不同
    的文件：屏幕上讨论的是一张图，`trace_cli.py pipeline --svg` 出的是另一张，
    **而其中一份会进论文**。这是本轮最该收口的一处。
  · **「标成成果」那个按钮打的是一条不存在的路由。** 网页那一波按自己的接缝清单
    写了 `POST /api/p/{项目}/results`，服务端那一波按自己的判断开的是
    `PUT /api/p/{项目}/results/{id}`。两边各自都对，按钮 404。
  · **七条诊断只翻译了三条。** 剩下四条在英文界面上原样漏出整句中文——而漏出来的
    恰好是「记录里两句话打架」那几条，最需要人读懂的那几条。`bad_pipeline` 同理。
  · **界面许了一个写入侧不认的诺。** `editor.pipeline.note.label` 印着「可不写 /
    optional」，而 `norm_pipeline` 对 `pipeline: <取值>` 强制要求理由，不写 400。
  · **`.claude-plugin` 里写着 14 个 MCP 工具**，实际 16 个——用户装之前唯一看得到
    的规格，对不上就是虚标。
  · **「CLI 子命令都写进 README 了没有」那道闸门自己是一份手抄名单**，于是
    `result` / `pipeline` 两条新命令恰好不在它的看管范围内。

所以这个文件按**接缝**组织，不按模块：每一节的标题是「谁和谁之间」。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import trace_cli as C
import trace_core as core
import trace_mcp as M
import trace_server as S
import trace_write as W

REPO = Path(__file__).resolve().parent.parent
APP = (REPO / "web" / "app.js").read_text(encoding="utf-8")
CSS = (REPO / "web" / "style.css").read_text(encoding="utf-8")
I18N = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
HTML = (REPO / "web" / "index.html").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")
MCP_SRC = (REPO / "trace_mcp.py").read_text(encoding="utf-8")

TOKEN = "t0ken"

WHAT = "## 做了什么\n跑了 clean.py\n\n```bash\npython clean.py --dedup\n```\n"


@pytest.fixture()
def data(tmp_path: Path) -> Path:
    """一个刚好把七条诊断里几条都摸到的项目。

    001 清洗 → 002 试了 focal loss（dead）→ 003 重训 → 004 评估（成果）
    002b 画了一眼分布图（`pipeline: exclude`）
    005 收尾（`pipeline: include`，闭包够不到它）
    """
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    sd = core.steps_dir_of(tmp_path, "课题")
    W.create_step(sd, title="清洗数据", status="done", body=WHAT, commit="c1d2e3f",
                  paths=["/blue/lab/clean | output | 去重后的训练集 | sha256=aabbccdd"])
    W.create_step(sd, parent="001", title="试了 focal loss", status="dead",
                  inputs=["001"], body="## 结论\n没提升，放弃\n")
    W.create_step(sd, parent="002", title="主模型：重训", status="done",
                  inputs=["002 | clean.jsonl"], body=WHAT,
                  code=["snapshot | /orange/lab/snap | manifest=MANIFEST.md5"])
    W.create_step(sd, parent="003", title="评估：AUC 0.91", status="done",
                  inputs=["003 | best.pt"], body=WHAT)
    W.create_step(sd, parent="001", title="画了一眼分布图", status="done", body=WHAT,
                  pipeline="exclude | 探索性的，没进最终流程")
    W.create_step(sd, parent="004", title="收尾：导出表格", status="done", body=WHAT,
                  pipeline="include | 流程的最后一环，没人吃它的产物")
    W.set_result(tmp_path, "课题", "004", "主结果：亲和力预测 AUC 0.91")
    return tmp_path


def payload_of(root: Path, slug: str = "课题") -> dict:
    f = core.compile_forest(core.steps_dir_of(root, slug))
    return M.pipeline_payload(f, slug, core.compute_pipeline({}, []))


def client(root: Path) -> TestClient:
    return TestClient(S.create_app({"data_dir": str(root), "space": "", "token": TOKEN,
                                    "git": {"enabled": False}}))


def build_into(root: Path, out: Path) -> Path:
    """跑一次静态导出。`load_config` 是 CLI 唯一读盘的地方，换掉它就够了。"""
    old = C.load_config
    C.load_config = lambda: {"data_dir": str(root), "title": "T"}          # type: ignore[assignment]
    try:
        class A:
            pass
        a = A()
        a.out = str(out)                                                   # type: ignore[attr-defined]
        C.cmd_build(a)
    finally:
        C.load_config = old                                                # type: ignore[assignment]
    return out


# ═════════════════ 接缝：三样导出 ↔ 四个门面（本轮的头号缺陷）
#
# markdown 和 SVG 各写一份 Python 一份 JS，两份迟早不一致，**而其中一份会进论文**。
# 收口的办法不是「让两份长得一样」（那要逐字节对齐两套排版代码，成本高于删掉一份，
# 而且下一次改动又会分家），是**只留一份**：Python 那一份。网页拿到的是它的产物。
#
# 为什么留 Python 那一份而不是 JS 那一份：它同时服务 CLI、REST、MCP、`build`
# 四个门面，其中 MCP 是 agent 写 Methods 时唯一的通道；JS 那份只服务浏览器一个。
# 从浏览器调 Python 只是一次同源 GET，从 Python 调 JS 要一个 node 依赖。
#
# 为什么不搬进 trace_core.py（上一轮留的「⚠ 待搬」）：`trace_mcp.py` 对
# trace_core 是**软 import**，「只把 trace_mcp.py 拷到一台只有 TRACE_URL 的机器上」
# 那条路今天是通的。搬进 core 之后那条路会断在 Methods 草稿上——而那台机器
# （超算上的 agent）正是最需要 Methods 草稿的那一台。


def test_the_three_exports_have_exactly_one_implementation():
    """网页里一份都没有，Python 里一份。**两份实现是这一轮的头号缺陷。**"""
    for gone in ("pipelineSVG", "pipelineMarkdown", "pipelinePage", "pipelineLayout",
                 "PIPELINE_SVG_CSS", "PIPELINE_PAGE_CSS"):
        assert gone not in APP, f"web/app.js 里又长出了 {gone} —— 第二份实现回来了"
    assert "createObjectURL" not in APP, "Blob 下载意味着那批字节是浏览器自己拼的"
    for fn in ("def pipeline_svg", "def pipeline_methods", "def pipeline_page"):
        assert fn in MCP_SRC, f"{fn} 不见了 —— 那唯一一份实现在哪"


def test_the_renderers_still_work_on_a_machine_that_has_no_trace_core():
    """这是「不搬进 trace_core.py」那个决定的**全部依据**，所以它得有人量着。

    `trace_mcp.py` 对 trace_core 一律软 import，于是「只把这一个文件拷到一台只有
    TRACE_URL 的机器上」是通的。搬进 core，那条路就断在 Methods 草稿上——而那台
    机器（超算上的 agent）恰恰是最需要草稿的一台。
    """
    import subprocess
    import sys
    import textwrap

    prog = textwrap.dedent(f'''
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name == "trace_core":
                    raise ImportError("blocked")
        sys.meta_path.insert(0, Block())
        sys.path.insert(0, r"{REPO}")
        import trace_mcp as M
        assert "trace_core" not in sys.modules
        pay = {{"project": "p", "name": "课题", "declared": True,
                "pipeline": {{"declared": True, "order": ["001"], "edges": [],
                             "results": [{{"step": "001", "note": "r", "members": ["001"]}}],
                             "why": {{"001": {{"kind": "result", "id": ""}}}},
                             "levels": {{"001": "L2"}}, "level": "L2", "weakest": "001",
                             "weak": [], "dead": [], "excluded": [], "included": [],
                             "diagnostics": []}},
                "steps": [{{"id": "001", "title": "清洗", "status": "done", "date": "",
                           "body": "## 做了什么\\n跑了 clean.py\\n", "paths": [],
                           "code": [{{"kind": "git", "location": "", "note": "",
                                     "attrs": {{"commit": "c1d2e3f"}}, "from": "commit"}}],
                           "inputs": [], "trace": {{"missing": []}}}}],
                "titles": {{"001": "清洗"}}}}
        md = M.pipeline_methods(pay)
        assert "跑了 clean.py" in md, "没有 core 时「做了什么」那条退路失效了"
        assert "`commit:` c1d2e3f" in md
        assert "课题" in M.pipeline_svg(pay)
        M.pipeline_page(pay); M.fmt_pipeline(pay)
        print("ok")
    ''')
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


def test_every_front_door_hands_out_the_very_same_bytes(data: Path, tmp_path: Path):
    """REST / CLI / 静态导出 / 直接调，四处必须逐字节相同。

    这一条是上面那条的**行为**版：删掉第二份实现只是让分家不再可能发生，
    而「现在真的没分家」得有人当场量一遍。
    """
    pay = payload_of(data)
    svg, md = M.pipeline_svg(pay), M.pipeline_methods(pay)

    cl = client(data)
    assert cl.get("/api/p/课题/pipeline/figure.svg").text == svg
    assert cl.get("/api/p/课题/pipeline/methods.md").text == md
    assert cl.get("/api/p/课题/pipeline/page.html").status_code == 200

    out = build_into(data, tmp_path / "dist")
    pd = out / "p" / "课题"
    assert (pd / "pipeline.svg").read_text(encoding="utf-8") == svg
    assert (pd / "pipeline.md").read_text(encoding="utf-8") == md
    assert (pd / "pipeline.html").exists()

    # 静态导出里那张图**灌进了页面本身**：file:// 下 fetch 一个相对路径会被当成
    # 跨源，断网双击打开时图会是一块空白。灌进去的必须还是同一批字节。
    html = (pd / "index.html").read_text(encoding="utf-8")
    got = re.search(r'<script id="pipeline-svg" type="application/json">(.*?)</script>',
                    html, re.S)
    assert got, "静态导出没有把那张图灌进页面"
    assert json.loads(got.group(1)) == svg
    assert "__PIPESVG__" not in html, "占位符没被替换 —— 页面上会显示一串大写字母"


def test_the_exports_are_headed_with_the_display_name_not_the_directory_name(data: Path):
    """显示名和目录名是两件事（改显示名不动目录名，已发出去的链接才不会失效）。

    三样导出是要**交出去**的产物，抬头写着 `my-project-2` 而不是课题名，收到的人
    只会以为拿错了文件。四个门面都得把显示名传下去——少传一个，那个门面出的
    就是另一份抬头。
    """
    W.update_project(data, "课题", name="口袋亲和力预测")
    slug, name = "课题", "口袋亲和力预测"

    f = core.compile_forest(core.steps_dir_of(data, slug))
    direct = M.pipeline_payload(f, slug, core.compute_pipeline({}, []), name)
    assert name in M.pipeline_svg(direct) and name in M.pipeline_methods(direct)

    cl = client(data)
    for path in ("pipeline/figure.svg", "pipeline/methods.md", "pipeline/page.html"):
        assert name in cl.get(f"/api/p/{slug}/{path}").text, path
    assert M.LocalBackend(data).pipeline(slug)["name"] == name, "MCP 本地后端没带显示名"
    assert cl.get(f"/api/p/{slug}/pipeline").json()["name"] == name, "REST 没带显示名"
    # 没给 name 时退回 slug，而不是留一个空抬头
    assert M.pipeline_payload(f, slug, core.compute_pipeline({}, []))["name"] == slug


def test_the_page_never_shows_a_figure_that_is_older_than_the_records(data: Path):
    """那张图是服务端画的，取回来之后得知道手上这份对不对得上当前的记录。

    上一版拿 `F.version` 当版本，而 `/forest` 的响应里根本没有那一项——undefined
    会让缓存永远命中，于是改完一步图再也不更新，而一张过期的方法图会被当成
    现在的方法图。
    """
    assert "F.version" not in APP, "又拿一个 forest 里不存在的字段当版本了"
    apply_fn = re.search(r"function apply\(data\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "FOREST_SEQ++" in apply_fn, "编译了新的一份，却没人告诉那张图它过期了"
    fetcher = re.search(r"function fetchFigure[\s\S]*?\n  \}\n", APP).group(0)
    assert "FOREST_SEQ" in fetcher and "PIPE_SVG_WANT" in fetcher, \
        "同一版会被重复请求，或者旧的响应会盖掉新的"


def test_the_server_leaves_the_figure_placeholder_empty(data: Path):
    """服务模式**不**预灌那张图：它随记录变，灌进 HTML 就成了一份会过期的拷贝。"""
    body = client(data).get("/p/课题/").text
    assert "__PIPESVG__" not in body
    got = re.search(r'<script id="pipeline-svg" type="application/json">(.*?)</script>',
                    body, re.S)
    assert got and not got.group(1).strip(), "服务端把一份会过期的图灌进了页面"


def test_the_page_points_the_three_buttons_at_that_one_implementation():
    """屏幕上那张图和三个按钮下载到的，必须是同一个出口的同一批字节。"""
    url = re.search(r"function exportURL[\s\S]*?\n  \}\n", APP).group(0)
    for kind in ("figure", "methods", "page"):
        assert kind in url or kind in APP
    assert "/pipeline/" in url and 'MODE === "static"' in url, \
        "服务模式和静态模式得各有一条通往同一份实现的路"
    fig = re.search(r"function fetchFigure[\s\S]*?\n  \}\n", APP).group(0)
    assert 'exportURL("figure")' in fig, "屏幕上那张图不是从导出那个出口来的"
    exp = re.search(r"function pipeExport[\s\S]*?\n  \}\n", APP).group(0)
    assert "download=" in exp and "exportURL(" in exp, "导出按钮不是 <a download>"
    # 拦下点击就得在这一页里再造一份字节 —— 那正是刚删掉的那条路
    at = APP.index('closest("[data-export]")')
    click = APP[at:APP.index("\n", APP.index("\n", at) + 1)]
    assert "preventDefault" not in click, "拦下了默认行为，下载就得由这一页自己生成"


def test_the_export_links_survive_in_a_static_export_too():
    """静态导出没有服务端，三个按钮指到 `build` 写在同目录的三个文件。"""
    files = re.search(r"var EXPORT_FILE = \{[^}]*\}", APP).group(0)
    for name in ("pipeline.svg", "pipeline.md", "pipeline.html"):
        assert name in files, f"静态导出里少了 {name}"
        assert f'"{name}"' in (REPO / "trace_cli.py").read_text(encoding="utf-8"), \
            f"build 不写 {name}，那三个链接在静态导出里全是 404"
    assert 'id="pipeline-svg"' in HTML, "页面上没有装那张图的地方"


# ═════════════════ 接缝：那张图的视觉契约（这一份现在归 Python）
#
# 下面这几条断言原本钉在 tests/app.test.js 上，钉的是 JS 那份生成器。JS 那份没了，
# 断言一条都没丢，只是跟着搬到真正会被发出去的那批字节上。


def test_the_figure_is_self_contained(data: Path):
    """自包含：审稿系统会把带脚本的 SVG 直接拒掉，引了外部字体的图在别人机器上
    会换一套字宽、排版全乱。"""
    svg = M.pipeline_svg(payload_of(data))
    assert svg.startswith("<svg ")
    assert not re.search(r"<script", svg, re.I), "图里有脚本"
    assert not re.search(r"(?:src|href|xlink:href)\s*=", svg, re.I), "图引用了外部资源"
    assert "@import" not in svg
    # url(#tip) 是图内部的 marker 引用，不是外部资源；url(http…) 才是。
    assert not re.findall(r"url\((?!#)", svg), "图里有指向外部的 url()"


def test_the_figure_never_says_anything_in_colour_alone(data: Path):
    """黑白打印可读：期刊印成灰的次数比谁预想的都多，而色觉障碍的人一直都在。

    所以关系一律靠**线型 + 文字标注**，等级直接印成字，成果靠更粗的框加一个
    字形徽记。全图不许有任何一个有色相的颜色——它们在灰度下会塌成同一种灰。
    """
    svg = M.pipeline_svg(payload_of(data))
    for col in set(re.findall(r"#([0-9a-fA-F]{6})\b", svg)):
        r, g, b = col[0:2].lower(), col[2:4].lower(), col[4:6].lower()
        assert r == g == b, f"图里出现了有色相的颜色：#{col}"
    assert "stroke-dasharray" in svg, "「中间经过了被剔掉的步骤」没有非颜色通道"
    assert "★" in svg, "成果没有字形通道"
    assert ">L" in svg or "[L" in svg, "等级不是印在图上的字"


def test_a_dashed_edge_always_says_who_it_passed_through(data: Path):
    """一条没有解释的虚线放进论文，读者只能自己编一个意思出来。

    004 的字节确实来自 001，只是路上经过 002（dead，已剔除）。虚线说「中间还有
    东西」，边上那几个 id 说清是谁——两句都得说。
    """
    pay = payload_of(data)
    vias = [e for e in pay["pipeline"]["edges"] if e.get("via")]
    assert vias, "这个 fixture 本该有一条接过去的边"
    svg = M.pipeline_svg(pay)
    for e in vias:
        for sid in e["via"]:
            assert f"经 {sid}" in svg or sid in svg, f"虚线上没说它路过了 {sid}"
    assert "已剔除" in svg


def test_the_figure_is_byte_for_byte_deterministic(data: Path):
    """逐字节确定是 P3，也是「该重新生成，而不是把导出存进仓库」的全部依据。"""
    pay = payload_of(data)
    assert M.pipeline_svg(pay) == M.pipeline_svg(payload_of(data))
    assert M.pipeline_methods(pay) == M.pipeline_methods(payload_of(data))
    assert M.pipeline_page(pay, title="x") == M.pipeline_page(payload_of(data), title="x")
    for out in (M.pipeline_svg(pay), M.pipeline_methods(pay), M.pipeline_page(pay)):
        assert not re.search(r"\b20\d{2}-\d{2}-\d{2}T", out), "导出里出现了时间戳"


# ═════════════════ 接缝：Methods 草稿 ↔ 记录原文（G4）


def test_the_methods_draft_carries_the_commands_the_code_and_the_checksums(data: Path):
    """别人照着做需要的东西：完整命令、代码在哪、产物路径与校验和。"""
    md = M.pipeline_methods(payload_of(data))
    assert "python clean.py --dedup" in md, "命令没进去，别人照着做不出来"
    assert "MANIFEST.md5" in md, "代码快照的 manifest 丢了"
    assert "sha256=aabbccdd" in md, "校验和丢了，产物对不对上没法验"
    assert "/blue/lab/clean" in md
    assert "试了 focal loss" not in md.split("被剔掉的步骤")[0], \
        "被剔掉的 dead 步混进了流程正文"


def test_a_commit_comes_back_as_a_commit_line(data: Path):
    """`commit:` 在文件里就是一行 `commit: c1d2e3f`。

    core 把它折算成一条**派生**的 `code: git`（位置那一段是空的），照 code 的格式
    印出来是「git commit=c1d2e3f」。收到草稿的人 grep 的是 `commit:`，而 G4 说的
    就是「删掉全部程序，grep 还能把人带回 note.md」。
    """
    md = M.pipeline_methods(payload_of(data))
    assert "`commit:` c1d2e3f" in md, md[md.index("代码在哪"):][:200]
    assert "git |  |" not in md and "git commit=" not in md


def test_the_methods_draft_never_writes_a_sentence_of_paper_prose(data: Path):
    """它只把记录里**已有的事实**按 Methods 的骨架排好。

    凭空生成的句子读起来最像成品，而它描述的是一件没有发生过的事——那种句子
    会被原样投出去，因为它读着不像需要改的东西。
    """
    md = M.pipeline_methods(payload_of(data))
    for bad in ("我们提出", "本文", "实验表明", "结果显示", "综上所述"):
        assert bad not in md, f"生成器替用户编了一句论文腔：{bad}"
    assert "这是初稿" in md, "没说清这是初稿，就会被原样投出去"


def test_a_missing_what_section_is_said_out_loud_not_skipped(data: Path):
    """「做了什么」没写时如实说一句。安静跳过的话，读的人会以为那一步不需要做什么。"""
    sd = core.steps_dir_of(data, "课题")
    W.update_step(sd, "003", {"body": "## 为什么\n只写了这个\n"})
    md = M.pipeline_methods(payload_of(data))
    assert "这一节是空的" in md


def test_the_standalone_page_is_self_contained_and_holds_only_the_pipeline(data: Path):
    """发给合作者的那一页：断网双击就能开，而且**不含开发路径**。"""
    page = M.pipeline_page(payload_of(data), title="课题 · 定稿流程")
    assert not re.search(r"<script", page, re.I), "页面里有脚本"
    assert not re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', page), "页面引用了外部主机"
    assert "@import" not in page
    assert "<style>" in page, "样式没内联，断网打开就是一页裸文本"
    assert "python clean.py --dedup" in page
    assert "试了 focal loss" not in page.split("被剔掉的步骤")[0], \
        "走不通的那一步跟着发出去了"


# ═════════════════ 接缝：网页 ↔ REST（「标成成果」那个按钮）


def test_the_mark_as_result_button_calls_a_route_that_exists(data: Path):
    """网页那一波写的是 `POST /results`，服务端那一波开的是 `PUT /results/{id}`。

    两边各自都对，按钮 404 —— 这就是接缝缺陷的标准形状。**按 id 发增删**是对的
    那一半：用打开页面那一刻的旧列表整组提交，会静默删掉这期间 agent 刚声明的
    那一条（和洞察那边 `_merge_insights` 挡的是同一次事故）。
    """
    act = re.search(r'if \(name === "result" \|\| name === "result-mark"\)[\s\S]*?\n    \}\n',
                    APP).group(0)
    assert 'method: "PUT"' in act and '"/results/"' in act, \
        "「标成成果」打的不是 PUT /results/{id}"
    assert 'method: "POST"' not in act, "POST /results 这条路由不存在，按钮会 404"

    cl = client(data)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert cl.post("/api/p/课题/results", json={"step": "001", "note": "x"},
                   headers=auth).status_code == 404, "整组提交那条路不该存在"
    r = cl.put("/api/p/课题/results/001", json={"note": "第二个成果"}, headers=auth)
    assert r.status_code == 201 and r.json()["line"] == "result: 001 | 第二个成果"
    assert cl.put("/api/p/课题/results/001", json={"note": "改一下"},
                  headers=auth).status_code == 200, "就地改写该回 200"
    assert cl.delete("/api/p/课题/results/001", headers=auth).status_code == 200


def test_writing_a_result_needs_a_token_and_reading_the_exports_does_not(data: Path):
    """写端点一律要令牌；导出是读，公开——合作者拿到的是一个链接，不是一把钥匙。"""
    cl = client(data)
    assert cl.put("/api/p/课题/results/001", json={"note": "x"}).status_code == 401
    assert cl.delete("/api/p/课题/results/004").status_code == 401
    for path in ("pipeline", "pipeline/figure.svg", "pipeline/methods.md", "pipeline/page.html"):
        assert cl.get(f"/api/p/课题/{path}").status_code == 200, path


def test_there_is_still_no_way_to_edit_the_member_list(data: Path):
    """「谁在流程里」是算出来的。一个能编辑成员清单的入口就是第二份真相——
    和「标记赢家」那个按钮不存在是同一条理由。"""
    for forbidden in ("pipeline.members", "editMembers", 'data-act="pipeline-members"'):
        assert forbidden not in APP, forbidden
    cl = client(data)
    for method in ("put", "post"):
        r = getattr(cl, method)("/api/p/课题/pipeline", json={"order": ["001"]},
                                headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code in (404, 405), "流程本身不该有写入口"


# ═════════════════ 接缝：trace_core 的诊断 ↔ 界面文案


def test_every_diagnostic_core_can_emit_has_a_line_in_both_languages():
    """认不出 code 时 warnText 退回服务端那句中文——那条兜底是对的（绝不吞警告），
    但它同时意味着**缺一条文案 = 英文界面上原样漏出一整句中文**。

    上一轮七条只翻了三条，漏出来的恰好是「记录里两句话打架」那四条。
    """
    src = (REPO / "trace_core.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'warn\(\s*"(?:warn|info)",\s*"(pipeline_\w+|dangling_result)"', src))
    assert len(codes) >= 7, f"core 的诊断少了？只找到 {codes}"
    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    for code in codes | {"bad_pipeline"}:
        assert code in table, f"{code} 没进 WARN_MAP，英文界面上会漏出中文"
        key = re.search(rf"{code}: \{{ key: \"([^\"]+)\"", table)
        assert key, f"{code} 在 WARN_MAP 里没写 key"
        # en / zh 两边都得有这条文案，否则换一种语言又漏一次
        assert I18N.count(f'"{key.group(1)}"') >= 2, f"{key.group(1)} 只有一种语言"


def test_the_diagnostics_take_their_values_from_vars_not_from_the_chinese(data: Path):
    """带变量的一律 take（core 已经把 ids 拼好、n 是数字），绝不从中文句子里抠。

    抠正则那条老路脆得离谱：那几句中文改一个字，英文界面上就原样漏出中文。
    """
    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    block = table[table.index("pipeline_no_result"):]
    assert "pick:" not in block, "定稿流程那几条又开始从中文句子里抠值了"
    # core 真的发得出这些 vars
    p = payload_of(data)["pipeline"]
    by = {d["code"]: d for d in p["diagnostics"]}
    assert "pipeline_dead_step" in by, by
    assert by["pipeline_dead_step"]["vars"].keys() >= {"ids", "n"}


def test_the_editor_does_not_promise_something_the_write_side_refuses():
    """界面上印着「可不写」，而 `norm_pipeline` 不写就 400。

    人选了 exclude、跳过那一栏、按保存，收到的是服务端一句读不懂的中文报错，
    最可能的反应是再按一次。界面和写入侧问的必须是同一件事。
    """
    for label in ('"editor.pipeline.note.label": "Why, in one line"',
                  '"editor.pipeline.note.label": "一句话说清为什么"'):
        assert label in I18N, "界面还在许一个写入侧不认的诺"
    assert "optional" not in I18N.split('"editor.pipeline.note.label"')[1][:80]
    assert "可不写" not in I18N.split('"editor.pipeline.note.label"')[2][:80]
    assert I18N.count('"editor.pipeline.note.required"') >= 2, "两种语言都要有那句解释"
    save = re.search(r"function saveEditor[\s\S]*?\n  \}\n", APP).group(0)
    assert "editor.pipeline.note.required" in save, "没人当场拦住，还是让人走一趟服务端"
    # 写入侧那条硬规矩本身还在
    with pytest.raises(W.WriteError):
        W.norm_pipeline("exclude")


# ═════════════════ 接缝：视觉通道（线型 / 不透明度 / 颜色都不许被借走）


def test_the_pipeline_badge_did_not_borrow_a_channel_that_is_already_taken():
    """线型归 `status`、不透明度归「在不在选中的祖先链上 / 命中搜索」、
    颜色归三种关系。定稿流程的标记只能用**第四通道**：字形。

    借走任何一个，「这条边是虚的」就再也说不清是「还在跑」还是「它是个候选」。
    """
    mark = re.search(r"\.cmk\.pipe\b[^}]*\}", CSS)
    assert mark, "开发路径上那个「在流程里」的标记没有样式"
    body = mark.group(0)
    for stolen in ("border-style", "stroke-dasharray"):
        assert stolen not in body, f"定稿流程的标记借走了 {stolen} 这个通道"
    # 不透明度归「在不在选中的祖先链上 / 命中搜索」。写死 opacity: 1 是**拒绝**
    # 参与那个通道（不跟着淡出），写别的值就是把它借走了。
    for got in re.findall(r"opacity:\s*([\d.]+)", body):
        assert float(got) == 1, f"定稿流程的标记借走了不透明度这个通道：{got}"
    # 它是方括号里的一个序号（字形），不是一种颜色
    render = re.search(r'var pl = s\.pipeline;[\s\S]{0,500}', APP).group(0)
    assert "'[' + esc(num)" in render or '"["' in render or "[' +" in render, \
        "那个标记不是字形通道了"


def test_the_figure_uses_no_hue_and_the_screen_shows_that_same_figure(data: Path):
    """屏幕上就是要进论文的那张图，不做一套「屏幕好看版」。

    换一套的代价是人对着一张图讨论、发出去另一张——而讨论的时候没人会去核对
    那两张是不是同一张。
    """
    screen = re.search(r"function pipeFigure\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "PIPE_SVG" in screen and "svg" not in screen.replace("PIPE_SVG", ""), \
        "屏幕上那张图不是取回来的那一份"
    assert "prefers-color-scheme" not in re.search(
        r"function pipeFigure\(\)[\s\S]*?\n  \}\n", APP).group(0)


# ═════════════════ 接缝：清单 ↔ 真相（那些会漂移的手抄名单）


def test_the_advertised_tool_count_is_not_a_hand_written_number_that_drifted():
    """`.claude-plugin` 里的描述是**用户装之前唯一看得到的规格**，对不上就是虚标。"""
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if plugin["version"].startswith("2."):
        from research_trace_v2.mcp import TOOLS as registered_tools
        n = len(registered_tools)
    else:
        n = len(M.TOOLS)
    for name in ("plugin.json", "marketplace.json"):
        text = json.loads((REPO / ".claude-plugin" / name).read_text(encoding="utf-8"))
        desc = text.get("description") or text["plugins"][0]["description"]
        assert {int(x) for x in re.findall(r"(\d+)\s*个 MCP 工具", desc)} == {n}, name


def test_the_readme_gate_asks_argparse_instead_of_a_hand_written_list():
    """那道「加了子命令却没人知道」的闸门，上一版自己是一份手抄名单，
    于是 `result` / `pipeline` 两条新命令恰好不在它的看管范围内。"""
    import argparse

    subs = [a for a in C.build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)]
    names = set(subs[0].choices)
    assert {"result", "pipeline"} <= names
    for sub in names:
        assert re.search(rf"trace_cli\.py {re.escape(sub)}\b", README), sub


def test_the_cli_and_the_web_and_the_mcp_call_the_same_thing_the_same_name():
    """同一个概念在五个门面上必须是同一个名字。不同名的东西人会当成两件事。"""
    # front-matter 的键
    assert "result" in core.TR_STRUCT_KEYS and "pipeline" in core.TR_STRUCT_KEYS
    assert core.TR_STRUCT_KEYS == M.TR_STRUCT_KEYS, "MCP 那份镜像和 core 分家了"
    # MCP 工具名 / 参数名
    tools = {t["name"]: t for t in M.TOOLS}
    assert {"trace_result", "trace_pipeline"} <= set(tools)
    assert set(tools["trace_result"]["inputSchema"]["properties"]) >= {"project", "step",
                                                                      "note", "drop"}
    # CLI 子命令
    import argparse
    subs = [a for a in C.build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)][0].choices
    assert "result" in subs and "pipeline" in subs
    # REST 路径
    src = (REPO / "trace_server.py").read_text(encoding="utf-8")
    assert '/api/p/{project}/results/{sid}' in src and '/api/p/{project}/pipeline' in src
    # 网页的模式名
    assert '"pipeline"' in APP and "trace.mode" in APP


# ═════════════════ 接缝：定稿流程 ↔ 已有功能（删除 / 移动 / 候选 / 成环 / 双语）


def test_deleting_the_step_a_result_points_at_says_so_out_loud(data: Path):
    """不替人撤那一行。撤了之后「流程曾经指向一步被删的记录」就没人看得见了，
    而不报出来更糟：id 会被重用，下一个拿到该号的步骤会无声地变成论文的主结果。"""
    sd = core.steps_dir_of(data, "课题")
    info = W.delete_step(sd, "004", "误建")
    assert info["dangling_results"] == ["result: 004 | 主结果：亲和力预测 AUC 0.91"]

    # 写入侧备好了这一项，而**三个门面一个都没接**——这是接缝缺陷的另一种形状：
    # 不是有人做错了，是没人负责把它显示出来，于是删掉成果那一步只会安静地
    # 让整条流程消失。三处必须同时说出来。
    assert "dangling_results" in (REPO / "trace_cli.py").read_text(encoding="utf-8"), \
        "CLI 的 rm 不提这一条"
    assert "dangling_results" in MCP_SRC, "MCP 的 trace_delete_step 回执不提这一条"
    assert "dangling_results" in APP and "toast.deleted.result" in APP, "网页删完不提这一条"
    assert I18N.count('"toast.deleted.result"') >= 2, "那句话只有一种语言"

    p = core.compile_forest(sd)["pipeline"]
    assert "dangling_result" in [d["code"] for d in p["diagnostics"]]
    assert "004" not in p["order"], "被删掉的那一步还留在流程里"
    for r in p["results"]:
        assert r["step"] != "004", "悬空的成果被当成了一个真的起点"


def test_moving_a_step_moves_the_pipeline_with_it(data: Path):
    """成员清单一个字都不存，所以移动一步、补一条 input、把某支标 dead，
    流程下一次读就跟着变了。存一份落盘清单只会理直气壮地列着已经不对的东西。"""
    sd = core.steps_dir_of(data, "课题")

    # ① 补一条真正的 `input:`：003 其实直接读的是 001 的清洗结果，中间那条 dead
    #    的路一个字节都没参与。补上之后 002 就不再是它的上游，那条虚线该消失。
    before = core.compile_forest(sd)["pipeline"]
    assert [e["via"] for e in before["edges"] if e["to"] == "003"] == [["002"]]
    W.update_step(sd, "003", {"inputs": ["001 | 直接读清洗结果"]})
    after = core.compile_forest(sd)["pipeline"]
    assert [e["via"] for e in after["edges"] if e["to"] == "003"] == [[]], after["edges"]
    assert "002" not in after["dead"], "那条被放弃的路已经不在成果的上游了"

    # ② 真的**移动**一步。005 一条 `input:` 都没写，闭包退回它的 parent，
    #    所以改挂到哪里，它在流程里的上游就跟着换到哪里——没有任何地方要同步。
    W.move_step(sd, "005", "001", "它其实接的是清洗那一步")
    moved = core.compile_forest(sd)["pipeline"]
    assert [e["from"] for e in moved["edges"] if e["to"] == "005"] == ["001"], moved["edges"]


def test_a_result_marked_dead_afterwards_is_kept_and_named(data: Path):
    """已声明的成果后来被判死了**不拦**（P4：dead 是结论不是错误），
    但那正是最该留下来的一段历史，所以读侧 warn 级点名。"""
    sd = core.steps_dir_of(data, "课题")
    W.update_step(sd, "004", {"status": "dead"})
    p = core.compile_forest(sd)["pipeline"]
    assert "004" in p["order"], "成果永远在流程里，否则这条流程连终点都没有"
    assert "pipeline_dead_step" in [d["code"] for d in p["diagnostics"]]
    # 反方向仍然是硬的：不能把一个 dead 的步骤**新**定成成果
    with pytest.raises(W.WriteError):
        W.set_result(data, "课题", "004", "另一个成果")


def test_two_results_share_one_graph_and_each_still_knows_its_own_chain(data: Path):
    """多个成果合成**一张** DAG：拆成几条链会把共用的数据准备步各画一遍，
    读的人得自己对着 id 去重。同时每个成果各带一份 members。"""
    W.set_result(data, "课题", "005", "图 4 的消融")
    p = core.compile_forest(core.steps_dir_of(data, "课题"))["pipeline"]
    members = {r["step"]: r["members"] for r in p["results"]}
    assert set(members) == {"004", "005"}
    assert "001" in members["004"] and "001" in members["005"], "共用的上游两边都要看得见"
    assert p["order"].count("001") == 1, "共用的步骤在图上出现了两次"


def test_a_dependency_cycle_still_produces_a_figure_and_says_why_it_cannot_order_it(
        data: Path):
    """数据依赖成环时不许死循环，也不许假装排出了顺序。"""
    sd = core.steps_dir_of(data, "课题")
    W.update_step(sd, "001", {"inputs": ["003"]})
    p = core.compile_forest(sd)["pipeline"]
    assert "pipeline_cycle" in [d["code"] for d in p["diagnostics"]]
    assert set(p["order"]) >= {"001", "003"}
    M.pipeline_svg(payload_of(data))          # 画得出来就行，不许在这里转死


def test_a_translation_can_never_change_which_steps_are_in_the_pipeline(data: Path):
    """译文的 front-matter 里只准有 title。写一句 `pipeline: exclude` 进去会被
    读侧一个字不看地丢掉——但必须**报一声**，否则「我明明把它排除了」和
    「它还在 Methods 里」同时成立，而人只会去怀疑推导错了。"""
    sd = core.steps_dir_of(data, "课题")
    d = next(x for x in sd.iterdir() if x.name.startswith("003"))
    (d / "note.en.md").write_text(
        "---\ntitle: Retrain\npipeline: exclude | nope\nresult: 001 | nope\n---\n\n## What\nx\n",
        encoding="utf-8")
    f = core.compile_forest(sd)
    assert "003" in f["pipeline"]["order"], "译文改动了流程成员 —— 双真相源回来了"
    codes = [w["code"] for w in f["warnings"] if "translation" in w["code"]]
    assert codes, "译文里的结构键被静默丢掉了"


def test_a_typo_in_the_pipeline_value_is_a_warning_not_a_silent_drop(data: Path):
    """`pipeline: exclud` 报一声、当没写、继续建树（和 bad_branch 同一条路）。
    磁盘上那一行原样留着，不许替人抹平。"""
    sd = core.steps_dir_of(data, "课题")
    d = next(x for x in sd.iterdir() if x.name.startswith("003"))
    n = d / "note.md"
    n.write_text(n.read_text(encoding="utf-8").replace("status:", "pipeline: exclud | 笔误\nstatus:"),
                 encoding="utf-8")
    f = core.compile_forest(sd)
    bad = [w for w in f["warnings"] if w["code"] == "bad_pipeline"]
    assert bad and bad[0]["vars"] == {"pipeline": "exclud"}
    assert "003" in f["pipeline"]["order"], "写错一个字就把这一步从流程里踢了"
    W.update_step(sd, "003", {"title": "改个标题"})
    assert "pipeline: exclud" in n.read_text(encoding="utf-8"), "笔误被悄悄抹平了"


# ═════════════════ 接缝：不变量（改这块时最容易破的几条）


def test_a_project_with_no_result_notices_nothing_at_all(tmp_path: Path):
    """现存项目必须**完全无感**：forest 里不多一个键、不多一条警告、
    步骤上不多一个字段。"""
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "老项目")
    sd = core.steps_dir_of(tmp_path, "老项目")
    W.create_step(sd, title="A", status="done", body=WHAT)
    f = core.compile_forest(sd)
    assert "pipeline" not in f
    assert "pipeline" not in f["steps"][0]
    assert not [w for w in f["warnings"] if "pipeline" in w["code"]]
    # 空态那句「教你怎么办」只有主动问起来的那条路上拿得到
    pay = M.pipeline_payload(f, "老项目", core.compute_pipeline({}, []))
    assert pay["declared"] is False
    assert [d["code"] for d in pay["pipeline"]["diagnostics"]] == ["pipeline_no_result"]


def test_declaring_a_result_moves_the_layout_by_not_one_number(data: Path):
    """布局归开发路径，一个数都不许因为定稿流程而变。"""
    sd = core.steps_dir_of(data, "课题")
    with_result = core.compile_forest(sd)
    W.drop_result(data, "课题", "004")
    without = core.compile_forest(sd)
    for key in ("order", "lanes", "lane_count", "tree", "branch_groups", "merges",
                "row_h", "lane_w"):
        assert with_result[key] == without[key], f"{key} 因为定稿流程动了"


def test_compiling_twice_gives_the_same_bytes_even_with_a_pipeline(data: Path):
    """P3：视图是文件系统的纯函数。"""
    sd = core.steps_dir_of(data, "课题")
    a = json.dumps(core.compile_forest(sd), ensure_ascii=False, sort_keys=True)
    b = json.dumps(core.compile_forest(sd), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_nothing_stores_a_member_list_anywhere_on_disk(data: Path):
    """P1：派生的东西绝不存储。流程的成员清单在磁盘上一个字都不许有。"""
    for note in core.steps_dir_of(data, "课题").rglob("note*.md"):
        text = note.read_text(encoding="utf-8")
        head = text.split("---")[1] if text.startswith("---") else ""
        for banned in ("members:", "pipeline_order:", "chain:"):
            assert banned not in head, f"{note.name} 存了一份会漂移的成员清单"
    proj = (data / "projects" / "课题" / "project.md").read_text(encoding="utf-8")
    assert "members" not in proj
    assert proj.count("result:") == 1


def test_g4_the_two_declared_things_are_greppable_without_any_program(data: Path):
    """删掉全部程序，`grep -r` 还得答得出「哪一步是成果」「哪一步被排除了，为什么」。"""
    hits = []
    for f in list((data / "projects").rglob("*.md")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("result:") or line.startswith("pipeline:"):
                hits.append(line)
    assert any(h.startswith("result: 004") for h in hits)
    assert any(h.startswith("pipeline: exclude") and "探索性" in h for h in hits), hits
    assert any(h.startswith("pipeline: include") for h in hits)


def test_the_static_export_is_still_self_contained_with_the_pipeline_page(
        data: Path, tmp_path: Path):
    """静态导出不许引任何外部主机——它是要拷进 U 盘、发进邮件的那一份。"""
    out = build_into(data, tmp_path / "dist")
    for name in ("index.html", "pipeline.html"):
        text = (out / "p" / "课题" / name).read_text(encoding="utf-8")
        assert not re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', text), name


@pytest.mark.skipif(shutil.which("node") is None, reason="这台机器没有 node")
def test_the_page_still_parses_after_the_generators_were_removed():
    """删掉三百行之后语法还在。"""
    import subprocess

    for f in ("app.js", "i18n.js", "md.js"):
        r = subprocess.run(["node", "--check", str(REPO / "web" / f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- 导出只有一份

def test_the_three_exports_have_exactly_one_implementation():
    """其中一份产物**会进论文**。

    CLI 一份、网页一份的话，两份迟早不一致，而不一致的那天你不会知道
    自己投出去的是哪一份。所以这条钉的是「只有一个地方定义它们」，
    以及「另外两个门面都是去调它」。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = {n: (root / n).read_text(encoding="utf-8")
           for n in ("trace_mcp.py", "trace_core.py", "trace_cli.py", "trace_server.py")}

    for fn in ("pipeline_svg", "pipeline_methods", "pipeline_page"):
        defs = [n for n, t in src.items() if re.search(rf"^def {fn}\(", t, re.M)]
        assert defs == ["trace_mcp.py"], f"{fn} 定义在 {defs}，应当只有 trace_mcp.py 一处"
        for n in ("trace_cli.py", "trace_server.py"):
            assert f"mcp.{fn}(" in src[n], f"{n} 没有去调那一份，多半是自己又写了一遍"

    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"function\s+\w*(?:[Mm]ethodsMarkdown|[Ss]vgFigure)\w*\s*\(", app), \
        "网页不许自己生成导出，它应该去服务端取"


def test_the_kernel_points_at_where_the_exports_live():
    """下一个人在 trace_core 里找不到导出时，最省事的动作是再写一份。"""
    from pathlib import Path
    core_src = (Path(__file__).resolve().parent.parent / "trace_core.py").read_text(encoding="utf-8")
    head = core_src[:core_src.index('"""', 3)]
    assert "trace_mcp" in head and "导出" in head, "内核顶部要留一行指路"
