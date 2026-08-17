"""⑨「一个项目里并列的几块」这一轮的接缝断言：章节（`chapter:`）。

按文件所有权分派不出冲突，**缺陷长在接缝上**。这一轮真实抓到的，全是这个形状：

  · **网页拿一个对象去和一个字符串比。** 服务端按约定在 payload 里回执
    「我编的是哪一章」，回的是 `{"name": …, "label": …, …}`；网页那侧的探针写的是
    `d.chapter === name`。这句比较永远不成立，于是网页判定「这台服务端不认按章节
    导」，**按章节导出整块静默消失**——没有报错，只是那三个按钮不出现、那张图不摆，
    看起来像这个功能压根没做。更麻烦的是有一条测试逐字钉着 `d.chapter === name`
    这个**写法**，一路绿着：它没人去问服务端真回的是什么形状。
  · **同一份导出在两处叫两个名字。** 未分章那一组在 `web/app.js` 里 slug 成
    `unchaptered`，在 `trace_mcp.chapter_export_name` 里是 `unassigned`；
    Windows 设备名一个加前缀 `ch-`、一个加后缀 `-ch`。两侧派生的是**同一批字节**
    的文件名。
  · **消歧只试一次就放弃。** `a` / `a-3` / `A` 三个章节：`A` 撞了 `a`，退到
    `a-3`，而那正是第二章的文件名——第三章的 Methods 静默盖掉第二章那份。
  · **写坏的那一行被任何一次无关的保存删掉。** `chapter: | 只写了说明没写名字`
    读侧当没声明并报一条 bad_chapter，而 `render_note` 不写回它：改一个不相干的
    标题就把人写的那半句话删了，那条催人补名字的警告也跟着消失。
  · **未分章那一组在网页上导不出来。** Python 侧专门为它留了记号（`?chapter=-`），
    理由是「多数人只给消融起了名字，主线一直没起」；网页那侧却把它当成「没在导
    某一章」，于是最该单独导一份 Methods 的那一块反而只能连着别人一起导。
  · **索引页卡片上的章节行没有人发。** 网页的渲染器写好了，`/api/projects` 和
    `build` 灌进静态页的那份都不带这个键。
  · **章节名 grep 得到、站内搜索搜不到。** i18n 里那一档文案（`search.where.chapter`）
    一直是死的。
  · **一台还不认 `?chapter=` 的服务端，MCP 会把整个项目那份当成消融那一章交出去。**

所以这个文件按**接缝**组织，不按模块：每一节的标题是「谁和谁之间」。
"""

from __future__ import annotations

import json
import re
import subprocess
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
I18N = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
SERVER_SRC = (REPO / "trace_server.py").read_text(encoding="utf-8")
CLI_SRC = (REPO / "trace_cli.py").read_text(encoding="utf-8")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="这台机器没有 node，跳过 JS 断言")

TOKEN = "t0ken"
AUTH = {"Authorization": "Bearer " + TOKEN}
WHAT = "## 做了什么\n跑了 run.py\n\n```bash\npython run.py\n```\n"


@pytest.fixture()
def data(tmp_path: Path) -> Path:
    """一个分了章、而且章节之间真的有边的项目。

      001 读数据          ┐
      002 清洗             │ 未分章（主线从没起过名字——多数项目就是这样）
      003 主结果（成果）   ┘
      003b 消融起点  `chapter: 消融实验 | …`   parent 跨章、`input: 003` 跨章
      004  去掉注意力（继承消融实验，成果）
      002b 数据准备        `chapter: 主实验/数据准备`（斜杠只分组显示，章节不嵌套）
    """
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    sd = core.steps_dir_of(tmp_path, "课题")
    W.create_step(sd, title="读数据", status="done", body=WHAT)
    W.create_step(sd, parent="001", title="清洗", status="done", body=WHAT, inputs=["001"])
    W.create_step(sd, parent="002", title="主结果", status="done", body=WHAT,
                  inputs=["002 | clean.csv"])
    W.create_step(sd, parent="002", title="消融起点", status="done", body=WHAT,
                  inputs=["003 | 主结果"],
                  chapter="消融实验 | 逐个拿掉模块，对着主实验的 003 比")
    W.create_step(sd, parent="003b", title="去掉注意力", status="done", body=WHAT,
                  inputs=["003b"])
    W.create_step(sd, parent="001", title="数据准备", status="done", body=WHAT,
                  chapter="主实验/数据准备 | 只做一次")
    W.set_result(tmp_path, "课题", "003", "主结果")
    W.set_result(tmp_path, "课题", "004", "消融结果")
    return tmp_path


def client(root: Path) -> TestClient:
    return TestClient(S.create_app({"data_dir": str(root), "space": "", "token": TOKEN,
                                    "git": {"enabled": False}}))


def forest_of(root: Path, slug: str = "课题") -> dict:
    return core.compile_forest(core.steps_dir_of(root, slug))


def payload_of(root: Path, chapter: str = "", slug: str = "课题") -> dict:
    return M.pipeline_payload(forest_of(root, slug), slug, core.compute_pipeline({}, []),
                              slug, chapter)


def build_into(root: Path, out: Path) -> Path:
    """跑一次静态导出。`load_config` 是 CLI 唯一读盘的地方，换掉它就够了。"""
    old = C.load_config
    C.load_config = lambda: {"data_dir": str(root), "title": "T"}          # type: ignore[assignment]
    try:
        a = type("A", (), {"out": str(out)})()
        C.cmd_build(a)
    finally:
        C.load_config = old                                                # type: ignore[assignment]
    return out


def cli_pipeline(root: Path, **kw):
    """跑一次 `trace_cli.py pipeline`（默认 -P 课题）。"""
    old = C.load_config
    C.load_config = lambda: {"data_dir": str(root), "title": "T"}          # type: ignore[assignment]
    try:
        args = type("A", (), dict({"project": "课题", "chapter": "", "methods": False,
                                   "svg": "", "page": ""}, **kw))()
        return C.cmd_pipeline(args)
    finally:
        C.load_config = old                                                # type: ignore[assignment]


def node_call(expr: str, *argv: str) -> str:
    r = subprocess.run([NODE, "-e", "const U=require('./web/app.js');" + expr, *argv],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


# ═════════════════ 接缝：三个门面写 ↔ 磁盘 ↔ core 的派生
#
# 历史上每一个新字段都在这条链上断过一次，而且断的方式一律是**静默**：
# 调用方填了、201 回来了、磁盘上没有。


def test_a_chapter_written_through_any_front_door_ends_up_as_one_line_on_disk(tmp_path: Path):
    """REST 建、REST 改、MCP 建 —— 三条路都必须真的把 `chapter:` 落到 note.md。

    少透传一个字段不会报错，只会让 agent 以为标好了，然后在导出里找不到那一章。
    """
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    sd = core.steps_dir_of(tmp_path, "课题")
    cl = client(tmp_path)

    a = cl.post("/api/p/课题/steps", json={"title": "消融起点", "chapter": "消融实验 | 说明"},
                headers=AUTH).json()
    line = (sd / W.load(sd)[a["id"]].dirname / core.NOTE_NAME).read_text(encoding="utf-8")
    assert "chapter: 消融实验 | 说明" in line, "POST /steps 把 chapter 丢了（201 却没落盘）"

    b = cl.post("/api/p/课题/steps", json={"title": "另一条线"}, headers=AUTH).json()
    cl.patch(f"/api/p/课题/steps/{b['id']}", json={"chapter": "数据准备"}, headers=AUTH)
    assert "chapter: 数据准备" in (sd / W.load(sd)[b["id"]].dirname
                                   / core.NOTE_NAME).read_text(encoding="utf-8")

    be = M.LocalBackend(tmp_path)
    c = be.create("课题", {"title": "MCP 建的", "chapter": "主实验"})
    assert "chapter: 主实验" in (sd / W.load(sd)[c["id"]].dirname
                                 / core.NOTE_NAME).read_text(encoding="utf-8"), \
        "LocalBackend.create 的白名单里漏了 chapter"


def test_the_whole_subtree_follows_without_a_second_line_on_disk(data: Path):
    """继承的全部好处就在这里：整条子树归它，而磁盘上只有一行。

    每一步各标一遍是二十份会漂移的拷贝，**而没有任何机制会告诉你它们过期了**。
    """
    sd = core.steps_dir_of(data, "课题")
    on_disk = sum((sd / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8").count("chapter: ")
                  for s in W.load(sd).values())
    f = forest_of(data)
    assert f["chapters"]["of"]["004"] == "消融实验", "继承没生效"
    assert on_disk == 2, f"磁盘上有 {on_disk} 行 chapter:，继承被展开成了拷贝"
    # 归属（继承来的）和「这一行写在哪」是两件事，混用会让整条子树看着像未分章
    got = {s["id"]: (s.get("chapter") or {}).get("declared") for s in f["steps"]}
    assert got["003b"] is True and got["004"] is False


def test_the_half_written_chapter_line_survives_an_unrelated_save(tmp_path: Path):
    """`chapter: | 只写了说明没写名字` 是写坏的一行：读侧当没声明、报一条 bad_chapter。

    但那半句话是**人写的字**。render_note 是全量重写 front-matter 的地方，不写回去
    的话，改一个不相干的标题就把它删了——人收不到任何提示，那条本来在催他补上
    章节名的警告也跟着一起消失。这正是 trace_write 自己在 `_hydrate` 注释里立的
    规矩（原文里是什么就读回什么）的另一半。
    """
    sd = tmp_path / "steps"
    sd.mkdir(parents=True)
    W.create_step(sd, title="a")
    note = sd / W.load(sd)["001"].dirname / core.NOTE_NAME
    note.write_text(note.read_text(encoding="utf-8")
                    .replace("status:", "chapter: | 逐个拿掉模块\nstatus:", 1), encoding="utf-8")

    W.update_step(sd, "001", {"title": "改个不相干的标题"})
    text = note.read_text(encoding="utf-8")
    assert "chapter: | 逐个拿掉模块" in text, "一次无关的保存把人写的那半句话删了"
    W.update_step(sd, "001", {"body": "## 为什么\n换个正文\n"})
    assert "chapter: | 逐个拿掉模块" in note.read_text(encoding="utf-8"), "第二次保存才删的"

    (w,) = [x for x in core.compile_forest(sd)["warnings"] if x["code"] == "bad_chapter"]
    assert w["vars"]["note"] == "逐个拿掉模块", "警告还在催人补名字"
    # 撤销声明**仍然**不留一行空的 chapter:（名字和说明一起清掉）
    W.update_step(sd, "001", {"chapter": ""})
    assert "chapter:" not in note.read_text(encoding="utf-8")


# ═════════════════ 接缝：服务端的回执 ↔ 网页的探针
#
# 按章节导出的那三样字节只有 Python 一份实现，网页只是指过去。于是网页必须先问
# 清楚「这台服务端会不会按章节编」——问法错了，症状不是报错，是整块功能消失。


@needs_node
def test_the_web_probe_reads_the_field_the_server_actually_sends(data: Path):
    """把**真服务端的响应**喂给网页那侧的判据函数，当场量一次。

    以前这里只有一条钉写法的断言（`d.chapter === name`），而 payload 里那个
    `chapter` 是一个 dict —— 比较永远不成立，探针永远说「不认」，按章节导出
    整块静默消失，测试却一路绿着。所以这一条不看源码，看**两侧的值**。
    """
    d = client(data).get("/api/p/课题/pipeline", params={"chapter": "消融实验"}).json()
    assert node_call("console.log(JSON.stringify(U.chapterEcho(JSON.parse(process.argv[1]))))",
                     json.dumps(d)) == '"消融实验"', \
        "网页读不出服务端说的那一章 —— 按章节导出会静默降级成整项目那一份"
    # 未分章那一组的名字**就是空串**：那是一个回答，不是「没回答」
    dn = client(data).get("/api/p/课题/pipeline", params={"chapter": M.CHAPTER_NONE}).json()
    assert node_call("console.log(JSON.stringify(U.chapterEcho(JSON.parse(process.argv[1]))))",
                     json.dumps(dn)) == '""'
    # 一台还没升级的服务端会忽略这个参数、回整个项目那份（payload 里没有这个键）
    old = client(data).get("/api/p/课题/pipeline").json()
    assert node_call("console.log(JSON.stringify(U.chapterEcho(JSON.parse(process.argv[1]))))",
                     json.dumps(old)) == "null", "判不出「这台服务端不认」就会挂一个说谎的文件名"


def test_the_probe_is_the_only_gate_and_it_fails_closed():
    """认不出来就一个按章节的按钮都不画、那张图也不摆。

    少一个按钮是遗憾，一份名不副实的 Methods 草稿是事故——它会被投出去。
    """
    fn = re.search(r"function chapExportable\(name\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "CH_PIPE_OK !== 1" in fn, "没问过服务端就敢挂按章节导出的按钮"
    probe = re.search(r"function probeChapterPipeline\(name\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "U.chapterEcho(d)" in probe and "d.chapter ===" not in probe


@needs_node
def test_the_web_and_python_derive_the_very_same_export_filename():
    """两侧派生的是**同一批字节**的文件名。各起各的，同一份导出在浏览器里下载下来
    叫一个名、`build` 写到磁盘上叫另一个名，而人只会以为自己手上有两份东西。

    章节名**不是路径安全的**：`主实验/数据准备` 是合法名字（设计要求按 `/` 分组
    显示），`CON` 也是，`..` 也是。所以两边都得派生，而且得派生成同一个。
    """
    names = ["主实验/数据准备", "CON", "", "../../etc", "  Ablation study  ", "章" * 80,
             "lpt1", "a-3"]
    py = M.chapter_export_name(names)                      # {名字: "pipeline-<slug>"}
    js = json.loads(node_call(
        "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(U.chapterSlug)))",
        json.dumps(names)))
    for name, slug in zip(names, js):
        assert py[name] == f"pipeline-{slug}", f"「{name}」在两侧 slug 成了两个名字"
        assert "/" not in slug and ".." not in slug and slug


@needs_node
def test_two_chapters_that_slug_alike_never_share_one_file_name():
    """大小写是**故意**不折叠的（core 的 chapter_near_duplicate 专门逮这种笔误），
    于是两个不同章节完全可能 slug 成同一个词。消歧只试一次是不够的：
    `a` / `a-3` / `A` —— `A` 撞了 `a`、退到 `a-3`，而那正是第二章的文件名，
    第三章的 Methods 就静默盖掉了第二章那份。
    """
    names = ["a", "a-3", "A"]
    py = M.chapter_export_name(names)
    assert len(set(py.values())) == 3, f"两章落到了同一个文件名上：{py}"
    js = json.loads(node_call(
        "console.log(JSON.stringify(U.chapterFileStems(JSON.parse(process.argv[1]))))",
        json.dumps(names)))
    assert len(set(js)) == 3, f"浏览器那侧的下载名撞了：{js}"


@needs_node
def test_both_sides_number_the_chapters_in_the_same_order_when_two_slug_alike(data: Path):
    """消歧的序号跟着**顺序**走，所以两侧还得喂同一个顺序：章节清单那一份
    （core 按「章节被开启的先后」排），未分章那一组接在最后。喂不同的顺序，
    撞名的那两章在磁盘上和在浏览器里就会拿到不同的后缀——同一份导出两个名字。"""
    sd = core.steps_dir_of(data, "课题")
    W.create_step(sd, parent="003b", title="大写那份", body=WHAT, chapter="Ablation")
    W.create_step(sd, parent="003b", title="小写那份", body=WHAT, chapter="ablation")
    f = forest_of(data)
    order = [c["name"] for c in f["chapters"]["chapters"]] + [""]
    py = M.chapter_export_name(order)
    js = json.loads(node_call(
        "console.log(JSON.stringify(U.chapterFileStems(JSON.parse(process.argv[1]))))",
        json.dumps(order)))
    assert [py[n] for n in order] == [f"pipeline-{s}" for s in js], \
        f"两侧给撞名的章节编了不同的号：{py} / {js}"
    assert len(set(js)) == len(order), "撞名的两章共用了一个文件名"


def test_the_sentinel_for_the_unassigned_group_is_one_constant():
    """未分章那一组在查询串上只能用记号指。CLI / REST / MCP / 网页必须是同一个字节。"""
    assert M.CHAPTER_NONE == "-"
    got = re.search(r'var CHAP_SENT = "([^"]*)";', APP)
    assert got and got.group(1) == M.CHAPTER_NONE, \
        "网页发出去的记号和 trace_mcp.CHAPTER_NONE 分家了"
    # 页面内部那个筛选器哨兵是**另一回事**：它只活在这一页里，所以敢用竖线
    # （写入侧拒收带竖线的章节名，真章节名不可能长成那样），它绝不能被发出去。
    inner = re.search(r'var CHAP_NONE = "([^"]*)";', APP)
    assert inner and "|" in inner.group(1) and inner.group(1) != M.CHAPTER_NONE


def test_the_unassigned_group_is_exportable_too_because_it_is_often_the_main_line(data: Path):
    """多数人只给消融起了名字，主线一直没起 —— 最该单独导一份 Methods 的恰恰是它。

    它的名字是空串，而空串在查询串上和「没给」长得一模一样，所以有那个记号。
    """
    cl = client(data)
    got = cl.get("/api/p/课题/pipeline", params={"chapter": M.CHAPTER_NONE}).json()
    assert got["chapter"]["name"] == "" and got["chapter"]["label"] == "（未分章）"
    assert got["pipeline"]["order"] == ["001", "002", "003"], "未分章那一组自己的那条流程"
    assert "004" not in got["pipeline"]["order"], "消融那一章混进来了"
    # 网页那侧也认：exportChapter() 回空串（一章），不是 null（整项目）
    fn = re.search(r"function exportChapterParam\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "ch || CHAP_SENT" in fn, "未分章那一组在网页上导不出来"


def test_a_real_chapter_named_like_the_sentinel_wins_and_nothing_is_written_under_a_lie(
        tmp_path: Path):
    """`-` 通过得了写入侧的章节名校验，所以这个歧义理论上存在。取舍是**真名优先**。

    于是 `build` 要检查自己拿回来的**真的是**要的那一章：不检查的话，
    `pipeline-unassigned.md` 里装的是那个叫 `-` 的章节的 Methods —— 一份文件名
    写着「未分章」、内容却是别人那一章的草稿，而它可能就是投出去的那一份。
    """
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    sd = core.steps_dir_of(tmp_path, "课题")
    W.create_step(sd, title="未分章的主线", status="done", body=WHAT)
    W.create_step(sd, parent="001", title="怪章节", status="done", body=WHAT, chapter="-")
    W.set_result(tmp_path, "课题", "001", "主线成果")
    W.set_result(tmp_path, "课题", "002", "怪章节成果")

    got = M.pipeline_payload(forest_of(tmp_path), "课题", None, "课题", M.CHAPTER_NONE)
    assert got["chapter"]["name"] == "-", "真名优先这条变了，文档和测试都得跟着改"

    out = build_into(tmp_path, tmp_path / "site")
    pd = out / "p" / "课题"
    assert (pd / "pipeline.md").is_file(), "合起来的那一份照样在"
    for p in pd.glob("pipeline-*.md"):
        # 每一份分章导出里印着的抬头，必须就是它文件名指的那一章
        assert "unassigned" not in p.name, "写出了一份名不副实的「未分章」Methods"


# ═════════════════ 接缝：按章节导出 ↔ 四个门面（这三样字节只有一份实现）


def test_every_front_door_hands_out_the_very_same_bytes_for_one_chapter(data: Path,
                                                                        tmp_path: Path):
    """REST / CLI / 静态导出 / 直接调，四处按同一章导出必须逐字节相同。

    按章节切开是**切**同一张 DAG，不是各算一遍闭包——各算一遍的那天，
    屏幕上讨论的图和投出去的图就不是一张了，而投出去的那张会进论文。
    """
    pay = payload_of(data, "消融实验")
    svg, md = M.pipeline_svg(pay), M.pipeline_methods(pay)

    cl = client(data)
    assert cl.get("/api/p/课题/pipeline/figure.svg", params={"chapter": "消融实验"}).text == svg
    assert cl.get("/api/p/课题/pipeline/methods.md", params={"chapter": "消融实验"}).text == md
    assert cl.get("/api/p/课题/pipeline/page.html", params={"chapter": "消融实验"}).status_code == 200

    svg_at = tmp_path / "one.svg"
    cli_pipeline(data, chapter="消融实验", svg=str(svg_at))
    assert svg_at.read_text(encoding="utf-8") == svg, "CLI 那条路出的是另一张图"

    out = build_into(data, tmp_path / "site")
    stem = M.chapter_export_name([g["name"] for g in
                                  forest_of(data)["pipeline"]["chapters"]])["消融实验"]
    assert (out / "p" / "课题" / f"{stem}.md").read_text(encoding="utf-8") == md
    assert (out / "p" / "课题" / f"{stem}.svg").read_text(encoding="utf-8") == svg


def test_no_front_door_filters_the_pipeline_by_itself():
    """按章节切开只有 `pipeline_payload` 一个入口。CLI 和服务端各筛一遍的那天，
    两份产物会在某一步上分家，而人不会知道自己投出去的是哪一份。"""
    fn = re.search(r"    def _pipeline\(project[\s\S]*?\n    @app\.get", SERVER_SRC).group(0)
    assert "pipeline_payload" in fn and "chapter" in fn
    for src, where in ((SERVER_SRC, "trace_server"), (CLI_SRC, "trace_cli")):
        # 注释里提到判据在哪是好事，**调用**它才是第二份实现
        live = re.sub(r"#.*", "", src)
        assert "_chapter_slice" not in live, f"{where} 里长出了第二份切分实现"
        assert "compute_chapters(" not in live, f"{where} 自己算起章节清单来了"
        assert "resolve_chapters(" not in live, f"{where} 自己算起归属来了"
    assert "def _chapter_slice" in (REPO / "trace_mcp.py").read_text(encoding="utf-8")


def test_the_borrowed_upstream_is_labelled_in_every_export(data: Path):
    """消融吃着主实验的 003，那几步当然要出现在消融的 Methods 里（一个输入不在
    流程里的成员，写进 Methods 就是一句断了的话），但它们是**借来的**。
    不标出来，消融那份 Methods 就把主实验做的几步写成了自己做的。"""
    pay = payload_of(data, "消融实验")
    assert pay["chapter"]["external"], "这一章一步借来的上游都没有？夹具坏了"
    assert "借自" in M.pipeline_methods(pay)
    assert "借自" in M.pipeline_svg(pay)
    assert "借自" in M.pipeline_page(pay)


def test_a_server_that_ignores_the_chapter_parameter_is_not_silently_accepted(data: Path):
    """远端后端对着一台**还不认** `?chapter=` 的服务端：它会忽略参数、回整个项目
    那一份。不出声的话，agent 会把整个项目的 Methods 当成消融那一段写进论文，
    而里面有主实验的每一步——读的人不会发现。"""
    whole = payload_of(data)                      # 整项目那份：没有 chapter 键

    class OldServer:
        def pipeline(self, project, chapter=""):
            return whole                          # 老服务端：参数被忽略

    with pytest.raises(M.ToolError) as e:
        M.t_pipeline(OldServer(), {"project": "课题", "chapter": "消融实验"})
    assert "消融实验" in str(e.value) and "整个项目" in str(e.value)
    # 不要章节时一个字都不多说（现存用法完全不受影响）
    assert "还不支持" not in M.t_pipeline(OldServer(), {"project": "课题"})


def test_an_unknown_chapter_name_is_a_404_that_lists_the_real_ones(data: Path):
    """章节名是人起的中文，打错一个字是最常见的失败方式。

    **不做**大小写折叠或近似匹配：替人猜一次，「导出的是哪一章」就取决于猜法，
    而其中一份会进论文。
    """
    r = client(data).get("/api/p/课题/pipeline", params={"chapter": "消融試驗"})
    assert r.status_code == 404
    assert "消融实验" in r.json()["error"]
    r2 = client(data).get("/api/p/课题/pipeline/methods.md", params={"chapter": "ABLATION"})
    assert r2.status_code == 404, "大小写被折叠了 —— 那就等于替人猜导的是哪一章"


# ═════════════════ 接缝：章节 ↔ 既有功能


def test_moving_a_subtree_into_another_chapter_is_reported_because_the_disk_says_nothing(
        data: Path):
    """换章**磁盘上一个字节都没变**：整条子树的归属跟着新的 parent 走，
    diff 里只有一行 `moved:`。不当场说出来，事后只能靠重新拉一遍森林才看得见。"""
    sd = core.steps_dir_of(data, "课题")
    before = forest_of(data)["chapters"]["of"].get("002b")
    # 002b 自己声明了「主实验/数据准备」，所以先撤掉它那一行，让它跟着继承走
    W.update_step(sd, "002b", {"chapter": ""})
    info = W.move_step(sd, "002b", "003b", "这一步其实是消融的一部分")
    assert before == "主实验/数据准备" and info["chapter"]["to"] == "消融实验"
    assert info["chapter"]["changed"] is True and info["chapter"]["steps"] == ["002b"]
    # 归属只有 core.resolve_chapters 一份判据 —— 写入侧只做差集，不重写继承规则
    assert "resolve_chapters" in (REPO / "trace_write.py").read_text(encoding="utf-8")
    assert forest_of(data)["chapters"]["of"]["002b"] == "消融实验"
    # 没有章节的两头一个字都不多说（现存项目完全无感）
    assert W.move_step(sd, "003", "001", "挂到根下面去")["chapter"] is None


def test_a_translation_can_never_change_which_chapter_a_step_belongs_to(data: Path):
    """`chapter` 沿树继承，所以译文里多写一行改的不是这一步，是**整棵子树**在
    另一种语言的页面上的归属：同一个项目会导出两份互相打架的 Methods。"""
    sd = core.steps_dir_of(data, "课题")
    tr = sd / W.load(sd)["003b"].dirname / "note.en.md"
    tr.write_text("---\nid: 003b\nchapter: Ablation\ntitle: ablation start\n---\n\nbody\n",
                  encoding="utf-8")
    f = forest_of(data)
    assert f["chapters"]["of"]["003b"] == "消融实验" and f["chapters"]["of"]["004"] == "消融实验"
    assert "Ablation" not in [c["name"] for c in f["chapters"]["chapters"]]
    assert "chapter" in core.TR_STRUCT_KEYS
    (w,) = [x for x in f["warnings"]
            if x["code"] == "translation_structural_key" and "chapter" in x["message"]]
    assert w["level"] in ("warn", "info"), "译文里那一行被读都不读地丢掉，但要说一声"


def test_a_broken_parent_chain_never_hangs_or_breaks_the_inheritance():
    """十年后的日志一定是残缺的。悬空 / 成环都当「这条链到此为止」：算出未分章，
    绝不死循环、绝不中断构建 —— 一条脏 parent 边不该炸掉整份章节清单。"""
    by_id = {}
    for sid, parent, ch in (("001", "999", ""), ("002", "003", ""),
                            ("003", "002", "消融实验"), ("004", "004", ""),
                            ("005", None, "主实验")):
        s = core.Step(id=sid, dirname=sid + "-x", parent=parent, title=sid)
        s.chapter = ch
        by_id[sid] = s
    got = core.resolve_chapters(by_id)
    assert got == {"003": "消融实验", "002": "消融实验", "005": "主实验"}
    out = core.compute_chapters(by_id, [])
    assert [c["name"] for c in out["chapters"]] == ["消融实验", "主实验"]
    assert out["unassigned"] == ["001", "004"], "够不到章节的那几步照样得出现"


def test_a_fork_inside_a_chapter_stays_a_fork(data: Path):
    """章节和分叉是两样东西，而误用的方向是固定的（人拿章节去表达分叉）。
    一个章节**里面**当然可以有互斥候选，两套派生互不干扰。"""
    sd = core.steps_dir_of(data, "课题")
    for t in ("候选 A", "候选 B"):
        W.create_step(sd, parent="003b", title=t, body=WHAT,
                      branch="alternative | " + t, decision="消融用哪种掩码")
    f = forest_of(data)
    (g,) = [x for x in f["branch_groups"] if x["at"] == "003b"]
    assert {f["chapters"]["of"][o] for o in g["options"]} == {"消融实验"}, \
        "同一组候选被章节劈开了"
    assert not any(c["name"] == "候选 A" for c in f["chapters"]["chapters"])


def test_a_chapter_name_is_never_used_as_a_path_and_never_normalised_away(tmp_path: Path):
    """名字是人起的：`grep -r "chapter: 消融实验"` 要原样捞到（G4），所以不折叠
    大小写、不改字。反过来它就**不是路径安全的**，任何按章生成文件的地方都得派生。"""
    sd = tmp_path / "steps"
    sd.mkdir(parents=True)
    # 抹平的三种（同一个章节被静悄悄劈成两半的三种方式）
    assert W.norm_chapter("  主实验 ")["name"] == "主实验"
    assert W.norm_chapter("Main  Experiment")["name"] == "Main Experiment"
    assert W.norm_chapter("a\nb")["name"] == "a b"
    # 拒绝的三种
    for bad in ({"name": "a|b"}, {"name": "a\x00b"}, {"name": "x" * (W.MAX_CHAPTER + 1)}):
        with pytest.raises(W.WriteError):
            W.norm_chapter(bad)
    # 不折叠大小写：Ablation 和 ablation 是两个章节，交给读侧点名
    W.create_step(sd, title="a", chapter="Ablation")
    W.create_step(sd, title="b", chapter="ablation")
    f = core.compile_forest(sd)
    assert [c["name"] for c in f["chapters"]["chapters"]] == ["Ablation", "ablation"]
    (d,) = [x for x in f["chapters"]["diagnostics"] if x["code"] == "chapter_near_duplicate"]
    assert d["level"] == "warn"
    assert d not in f["warnings"], "章节的诊断混进了顶栏警告栏"


def test_the_chapter_diagnostics_never_touch_the_level_or_the_exit_code(data: Path):
    """一条都不影响 L0–L4，也不进 --strict。混进那两栏，人会以为「两个人各写了
    一句章节说明」和「dead 没写结论」一样严重，然后开始整体忽略这一段。"""
    sd = core.steps_dir_of(data, "课题")
    before = {s["id"]: s["trace"]["chain"] for s in forest_of(data)["steps"]}
    W.create_step(sd, parent="004", title="又声明一次", body=WHAT,
                  chapter="消融实验 | 另一个人写的另一句说明")
    f = forest_of(data)
    codes = [d["code"] for d in f["chapters"]["diagnostics"]]
    assert "chapter_note_conflict" in codes
    assert all(w["code"] not in codes for w in f["warnings"])
    after = {s["id"]: s["trace"]["chain"] for s in f["steps"]}
    assert all(after[k] == v for k, v in before.items()), "章节诊断改了某一步的等级"


# ═════════════════ 接缝：站内搜索 ↔ grep（G4）


@needs_node
def test_the_in_app_search_finds_a_chapter_the_way_grep_does(data: Path):
    """`grep -rn 消融实验 projects/` 命中的是**声明它的那一步**那一个文件；
    继承来的那些文件里一个「消融」都没有。三处搜索（服务端 / MCP / 网页）
    必须和 grep 给出同一批答案——工具比 grep 弱的地方，恰好是 agent 唯一
    够得到的地方：它拿到「没搜到」，会读成「没记过」。"""
    sd = core.steps_dir_of(data, "课题")
    on_disk = sorted(s.id for s in W.load(sd).values()
                     if "逐个拿掉模块" in (sd / s.dirname / core.NOTE_NAME).read_text(encoding="utf-8"))
    assert on_disk == ["003b"], "夹具变了"

    r = client(data).get("/api/search", params={"q": "逐个拿掉模块"}).json()
    assert [h["id"] for h in r["hits"]] == ["003b"]
    assert "chapter" in r["hits"][0]["where"], "命中落在章节上，界面上却说不出是哪一档"
    assert "search.where.chapter" in I18N, "那一档的文案没了"

    assert "003b" in M.t_search(M.LocalBackend(data), {"query": "逐个拿掉模块"})

    f = forest_of(data)
    hit = json.loads(node_call(
        "const f=JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(f.steps.filter(s=>U.matches(s,process.argv[2])).map(s=>s.id)))",
        json.dumps(f), "逐个拿掉模块"))
    assert hit == ["003b"], "网页那份退路干草堆和服务端搜到的不是同一批"

    # 章节名同理，而且**只命中声明的那一步**：继承来的二十步一起命中会把
    # 真正的答案（这条线从哪儿开始）埋掉。
    r2 = client(data).get("/api/search", params={"q": "消融实验"}).json()
    assert [h["id"] for h in r2["hits"]] == ["003b"]
    # 网页那份退路干草堆得真的把它拼进去（三处一份判据，各写各的就是两种答案）
    assert "function chapterHay" in APP and "chapterHay(step)" in APP


def test_the_mcp_fallback_haystack_agrees_with_core(data: Path):
    """trace_mcp.py 会被单独拷到只有 TRACE_URL 的机器上，那里没有 trace_core，
    所以它有一份退路实现。两份必须逐字同结果——不然「在服务器上搜得到、在那台
    机器上搜不到」，而没有任何一处会报错。"""
    import inspect
    import textwrap

    src = inspect.getsource(M._chapter_haystack)
    body = textwrap.dedent(src).split("except Exception:", 1)[1]
    ns: dict = {}
    exec("def fallback(s):\n" + textwrap.indent(textwrap.dedent(body), "    "), ns)  # noqa: S102
    for s in forest_of(data)["steps"]:
        assert ns["fallback"](s) == core.chapter_haystack(s), s["id"]
    assert core.chapter_haystack({"id": "001"}) == "", "没有章节的项目搜索行为一个字不变"


def test_moving_one_step_does_not_claim_it_carried_a_subtree(data: Path):
    """`chapter.steps` 里第一个多半就是被移的那一步（它自己也是靠继承换的章）。
    照数打出来，「移了一步」会听成「带走了一支」——而「你移的是一步还是一支」
    正是移动这件事上最要紧的那句回执。"""
    sd = core.steps_dir_of(data, "课题")
    W.update_step(sd, "002b", {"chapter": ""})
    info = W.move_step(sd, "002b", "003b", "挪进消融")
    assert info["chapter"]["steps"] == ["002b"], "夹具变了：这一步底下没有别人"
    be = M.LocalBackend(data)

    class Echo:
        def move(self, project, sid, payload):
            return info

    out = M.t_move_step(Echo(), {"project": "课题", "step": "002b", "parent": "003b",
                                 "reason": "挪进消融"})
    assert "跟着换章的还有" not in out, out
    assert "消融实验" in out and be is not None


# ═════════════════ 不变量：没有 chapter: 的项目必须完全无感


def test_a_project_without_any_chapter_notices_nothing_at_all(tmp_path: Path):
    """现存项目全是这个状态：不多一个键、不多一条警告、布局一个数不变、
    索引页那份 JSON 逐字节一样。"""
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    sd = core.steps_dir_of(tmp_path, "课题")
    W.create_step(sd, title="根", status="done", body=WHAT)
    W.create_step(sd, parent="001", title="子", status="done", body=WHAT, inputs=["001"])
    W.set_result(tmp_path, "课题", "002", "主结果")

    f = forest_of(tmp_path)
    assert "chapters" not in f
    assert all("chapter" not in s for s in f["steps"])
    assert "chapters" not in f["pipeline"]
    assert "chapter" not in payload_of(tmp_path)
    cl = client(tmp_path)
    assert "chapter" not in cl.get("/api/p/课题/pipeline").json()
    assert "chapters" not in cl.get("/api/projects").json()["projects"][0], \
        "没分章的项目卡片上多了一行「0 个章节」的谎话"
    assert core.compute_chapters(W.load(sd), f["order"])["diagnostics"] == []
    ch = cl.get("/api/p/课题/chapters").json()
    assert ch["declared"] is False and ch["chapters"] == [] and ch["diagnostics"] == []
    assert not [w for w in f["warnings"] if "chapter" in w["code"]]

    # 布局一个数都没变：加一行 chapter: 之后，lane / row / 树尺寸原样
    layout = [(s["id"], s["lane"], s["row"]) for s in f["steps"]]
    W.update_step(sd, "002", {"chapter": "消融实验"})
    g = forest_of(tmp_path)
    assert [(s["id"], s["lane"], s["row"]) for s in g["steps"]] == layout
    assert g["tree"]["w"] == f["tree"]["w"] and g["tree"]["h"] == f["tree"]["h"]
    # 多出来的**恰好**是那两个键，一个不多
    assert set(g) - set(f) == {"chapters"}
    assert set(g["steps"][0]) - set(f["steps"][0]) == {"chapter"}


def test_the_project_card_says_how_many_chapters_only_when_there_are_any(data: Path,
                                                                         tmp_path: Path):
    """索引页拿不到 forest（那是整棵树，几十个项目就是几十棵），所以这一行只能由
    服务端顺手带上。带上了就显示，没带就一个字都不多。服务模式和静态导出用的是
    网页里**同一个**渲染器，所以两处得发同一个形状。"""
    got = client(data).get("/api/projects").json()["projects"][0]
    # 章节之间按「最早那一步的 id」排 = 它们被开启的先后，和步骤列表同向
    assert got["chapters"] == [{"name": "主实验/数据准备", "n": 1},
                               {"name": "消融实验", "n": 2}]
    out = build_into(data, tmp_path / "site")
    html = (out / "index.html").read_text(encoding="utf-8")
    inj = re.search(r'<script id="projects-data" type="application/json">(.*?)</script>',
                    html, re.S)
    assert inj, "静态导出没有把项目清单灌进页面"
    assert json.loads(inj.group(1))[0]["chapters"] == got["chapters"]
    assert "function projectChapters" in APP


def test_compiling_and_exporting_one_chapter_twice_gives_the_same_bytes(data: Path,
                                                                        tmp_path: Path):
    """P3：视图是文件系统的纯函数，输出逐字节确定。分章之后这条不许松——
    不确定的话「重新生成」会在 diff 里制造假变更，人就会开始把导出存进仓库
    当第二份真相。"""
    assert json.dumps(forest_of(data), ensure_ascii=False) \
        == json.dumps(forest_of(data), ensure_ascii=False)
    for name in ("消融实验", M.CHAPTER_NONE, "主实验/数据准备"):
        a, b = payload_of(data, name), payload_of(data, name)
        assert M.pipeline_svg(a) == M.pipeline_svg(b)
        assert M.pipeline_methods(a) == M.pipeline_methods(b)
    first = build_into(data, tmp_path / "site")
    snap = {p.name: p.read_bytes() for p in (first / "p" / "课题").glob("pipeline*")}
    build_into(data, tmp_path / "site")
    assert {p.name: p.read_bytes() for p in (first / "p" / "课题").glob("pipeline*")} == snap
    assert len(snap) >= 6, "分章那几份没写出来"


def test_the_standalone_page_of_one_chapter_is_still_self_contained(data: Path):
    """发出去的那一页：无脚本、无外部资源，断网双击就能开。
    按章节导之后它仍然是同一份包装，不许因为多了「借自」几个字就引进什么。"""
    html = M.pipeline_page(payload_of(data, "消融实验"))
    assert "<script" not in html
    assert not re.search(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", html, re.I)
    assert not re.findall(r"url\((?!#)", html)


def test_the_export_is_gated_by_a_token_only_for_writing(data: Path):
    """读是公开的（用户的明确选择），写要令牌。按章节多了几条查询串，
    这条硬边界一个字都不该动。"""
    cl = client(data)
    for path in ("/api/p/课题/chapters", "/api/p/课题/pipeline",
                 "/api/p/课题/pipeline/methods.md"):
        assert cl.get(path, params={"chapter": "消融实验"} if "pipeline" in path else None) \
            .status_code == 200
    assert cl.patch("/api/p/课题/steps/004", json={"chapter": "别的章"}).status_code == 401
    assert forest_of(data)["chapters"]["of"]["004"] == "消融实验"


# ---------------------------------------------------------------- 验收抓到的四条

def test_a_zero_width_character_cannot_split_one_chapter_in_two():
    """`消融实验` 和 `消融<U+200B>实验` 在屏幕上逐像素一样，字节上是两个章节。

    各导出一份 Methods、各有一个等级，两边看着都对——而这是肉眼**绝对**发现不了的
    一类笔误。写入侧的控制字符检查只覆盖 Cc，读侧的折叠用 str.split() 只认空白，
    Cf 整个类别从两者中间漏过去。近似检查存在的全部意义就是逮住这种。
    """
    import trace_core as core
    plain = "消融实验"
    for cp in (0x200B, 0xFEFF, 0x00AD, 0x200E, 0x2060):
        sneaky = "消融" + chr(cp) + "实验"
        assert sneaky != plain, f"U+{cp:04X} 这个用例本身要能造出不同的字节"
        assert core._chapter_fold(sneaky) == core._chapter_fold(plain), \
            f"U+{cp:04X} 没被折叠掉，两个看不出区别的章节会静悄悄并存"


def test_the_source_of_that_guard_is_itself_readable():
    """那条正则**不许**把字面的不可见字符敲进来。

    敲进来的话，这一行自己就是不可见的：读代码的人看到的是一对空方括号，
    grep 也搜不到，改的人不知道自己在改什么。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "trace_core.py").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if "_ZERO_WIDTH_RE" in l and "=" in l)
    block = src[src.index(line):src.index(line) + 400]
    block = block[:block.index(")\n") + 1]
    assert "chr(0x" in block, "要用 chr()/转义写，别把不可见字符敲进源码"
    for cp in (0x200B, 0xFEFF, 0x00AD):
        assert chr(cp) not in block, f"源码里有字面的 U+{cp:04X}"


def test_a_per_chapter_methods_never_names_a_step_it_does_not_contain():
    """它是一份要递给合作者的文档。

    以前按章节导出时诊断照抄整项目那一份，于是主实验的 Methods 里印着
    「整条流程的等级 = L0，卡在 005」——而 005 属于消融，在这份文档里
    从头到尾不出现。一句免责声明救不了这件事：读的人只会以为文档漏了两步。
    """
    import tempfile
    from pathlib import Path
    import trace_core as core
    import trace_mcp as M
    import trace_write as W

    root = Path(tempfile.mkdtemp())
    core.ensure_layout(root)
    W.create_project(root, "p")
    d = core.steps_dir_of(root, "p")
    full = "## 为什么\nH\n## 做了什么\nran\n## 结论\nok"
    a, _ = W.create_step(d, title="主-完整", status="done", chapter="主实验",
                         commit="a1b2c3d", paths=["/o/a | output | 产物"], body=full)
    b, _ = W.create_step(d, title="消融-缺东西", status="done", chapter="消融实验", body=full)
    W.set_result(root, "p", a.id, "主结果")
    W.set_result(root, "p", b.id, "消融结果")

    be = M.LocalBackend(root)
    for chapter, mine, theirs in (("主实验", a.id, b.id), ("消融实验", b.id, a.id)):
        md = M.pipeline_methods(be.pipeline("p", chapter=chapter))
        assert f"`{mine}`" in md, f"{chapter} 的文档里得有它自己那一步"
        assert theirs not in md, \
            f"{chapter} 的 Methods 里出现了不属于本章的 {theirs}——读的人会以为文档漏了"


def test_every_lint_code_the_kernel_emits_has_a_translation():
    """没有的那几条会在英文界面上**原样漏出整句中文**。

    退回 esc(w.message) 那条兜底是对的（绝不吞警告），但漏出来的恰好是
    「记录里少了半年后补不回来的那部分」这几条——最需要被读懂的那批。
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    mapped = set(re.findall(r"^\s{4}(\w+): \{ key:", app, re.M))
    for code in ("missing_why", "missing_what", "missing_conclusion",
                 "figure_without_caption", "bad_chapter", "bad_branch", "bad_pipeline"):
        assert code in mapped, f"{code} 没进 WARN_MAP，英文界面上会漏出中文"
