"""这一轮六条改动的接缝上的断言。

前两轮的教训一模一样：按文件所有权分派没出过冲突，**缺陷长在两份文件的接缝上**。
每个 agent 在自己的文件里做对了，然后在报告里写「不是我的文件」，
于是那个口子谁也没开。这一轮真实抓到的四个，全都是这个形状：

  · `core.parse_paths` 说 `checksum` 是字符串 `"md5:7d4e1a9c"`，
    `trace_mcp._fmt_attrs` 当字典用 —— 于是任何一条真写了 `md5=` 的记录都会让
    `trace_read` 抛 AttributeError。而 agent 看不到网页，那是它唯一的入口。
  · 网页「从这里派生」照抄父步骤的 `path:`，把 `checked=` / `missing=` / `md5=`
    一起抄进一个还没跑过的步骤 —— 凭空造出一条看起来像证据的假记录。
  · `delete_step` 报孤儿、报 `[[006]]`，唯独不报 `input: 006` —— 而 id 会被重用。
  · `path:` / `code:` 里的位置搜不到 —— `grep -rn best.pt` 一秒答得出，
    而 agent 只有 `trace_search`。

所以这个文件按**接缝**组织，不按模块：每一节的标题是「谁和谁之间」。
"""

import ast
import inspect
import io
import json
import re
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import trace_cli as C
import trace_core as core
import trace_mcp as M
import trace_write as W

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import trace_server as S  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TOKEN = "flex-seam-token"
AUTH = {"Authorization": "Bearer " + TOKEN}

# 一条把所有已知属性都写满的 path。上面那个 checksum 崩溃就是被它抓出来的：
# 只有 `md5=` 真的在场时 `p["checksum"]` 才是非空字符串，`or {}` 兜不住。
FAT_PATH = ("/blue/lab/cif | input | 原始 CIF | "
            "n=4554 size=61203283968 md5=7d4e1a9c checked=2026-08-09 nodes=12")
GONE_PATH = "/orange/lab/ckpt | output | 权重 | size=277872640 sha256=aabbcc missing=2026-08-10"


@pytest.fixture()
def proj(tmp_path: Path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": TOKEN,
                        "git": {"enabled": False}, "paths": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    with TestClient(app) as c:
        c.root = tmp_path
        c.sd = core.steps_dir_of(tmp_path, "课题")
        yield c


def app_js() -> str:
    return (REPO / "web" / "app.js").read_text(encoding="utf-8")


def cli_out(fn, **kw) -> str:
    """跑一个 cmd_* 并把它打印的东西收回来。"""
    class A:
        project = "课题"; kind = None; missing = False
        check = False; count = False; strict = False
    for k, v in kw.items():
        setattr(A, k, v)
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(A())
    return buf.getvalue()


@pytest.fixture()
def cli(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "load_config", lambda *a, **k: {"data_dir": "."})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return tmp_path, core.steps_dir_of(tmp_path, "课题")


# ═════════════════════ 接缝：core 的 path 形状 ↔ 四个渲染出口
#
# core 定义了 path 上八个派生键，四个出口各自渲染。任何一处对形状的假设和 core
# 不一致，都只在「那个字段真的有值」的时候才炸 —— 也就是这套结构最该管用的时候。


def test_checksum_is_a_string_not_a_dict(proj):
    """契约本身。`"md5:7d4e1a9c"`，算法在冒号左边，不是 {算法: 值}。

    写成字典的那一版 `_fmt_attrs` 在 `checksum` 为空串时靠 `or {}` 蒙混过关，
    一旦有人真写了 `md5=` 就抛 AttributeError。所以先把形状钉死。
    """
    p = core.parse_paths(FAT_PATH)[0]
    assert isinstance(p["checksum"], str)
    assert p["checksum"] == "md5:7d4e1a9c"
    assert core.parse_paths("/x | y")[0]["checksum"] == ""


def test_every_exit_renders_a_path_with_a_checksum_without_blowing_up(proj):
    """四个出口各渲染一遍写满属性的 path。

    这一条不是「多测一种输入」：`trace_read` 是 agent 看一步的**唯一**入口，
    它一崩，agent 拿到的是一条工具错误，而不是那一步的内容。
    """
    root, d = proj
    W.create_step(d, title="满", body="## 为什么\n因为\n", paths=[FAT_PATH, GONE_PATH],
                  code=["snapshot | /orange/snap | manifest=MANIFEST.md5 n=43"])
    be = M.LocalBackend(root)

    step_text = M.dispatch(be, "trace_read", {"project": "课题", "step": "001"})
    assert "md5=7d4e1a9c" in step_text, "校验和要真的显示出来，不是被吞掉"
    assert "sha256=aabbcc" in step_text
    assert "nodes=12" in step_text, "认不出来的属性照样保留（半年后有人写了 nodes=…）"

    tree_text = M.dispatch(be, "trace_read", {"project": "课题"})
    assert "1 条已不存在" in tree_text

    flow_text = M.dispatch(be, "trace_flow", {"project": "课题", "step": "001"})
    assert "001" in flow_text

    f = core.compile_forest(d)
    assert json.dumps(f, ensure_ascii=False), "forest 必须能整份 JSON 化"


def test_the_cli_shows_the_checksum_too(cli):
    """`paths` 和 `check` 以前一个字都不提校验和。

    核对一份产物时「拿到的还是不是当时那份」比「它有多大」有用得多，
    而 CLI 是唯一一条不需要起服务、不需要 agent 的路。
    """
    _root, d = cli
    W.create_step(d, title="满", body="## 为什么\n因为\n", paths=[FAT_PATH])
    assert "md5=7d4e1a9c" in cli_out(C.cmd_paths)


def test_check_says_out_loud_that_a_location_is_gone(cli):
    """`check` 是唯一一个人天天跑的出口，而它以前对失效的路径一个字都不说。

    网页顶上有横幅、`paths` 有汇总，只有 `check` 静默 —— 于是「57 GB 那个目录
    没了」在 CI 里永远看不见。但它**不是警告、不进退出码**：路径没了是溯源结论
    （P4），不是这份记录写错了。
    """
    _root, d = cli
    W.create_step(d, title="没了", body="## 为什么\n因为\n", paths=[GONE_PATH])
    out = cli_out(C.cmd_check)
    assert "已确认不存在" in out
    assert "/orange/lab/ckpt" in out


def test_a_gone_location_alone_never_fails_strict(cli):
    """`--strict` 是给 CI 用的闸门。路径没了是溯源结论（P4），不是这份记录写错了——
    用它拦住一次合并，只会让人下次加 --no-verify，然后连真警告一起不看了。

    对照组是同一份记录去掉那条失效路径：两次退出码必须一样。
    """
    _root, d = cli
    body = ("## 为什么\n因为\n\n## 做了什么\n跑了\n\n## 结果\n0.9\n\n"
            "## 结论\n成立\n\n## 下一步\n继续\n")
    W.create_step(d, title="干净", status="done", commit="c1d2e3f", body=body,
                  paths=["/orange/lab/a | output | 权重"])
    clean = C.cmd_check(type("A", (), {"project": "课题", "strict": True})())
    W.update_step(d, "001", {"add_paths": [GONE_PATH]})
    assert C.cmd_check(type("A", (), {"project": "课题", "strict": True})()) == clean == 0


def test_the_web_reads_the_checksum_as_a_string_as_well(proj):
    """网页那一侧 split(':')，和 core 是同一个约定。两处一起钉住才拦得住漂移。"""
    src = app_js()
    assert 'String(p.checksum).split(":")' in src


# ═════════════════════ 接缝：网页「从这里派生」↔ path 上的核对结论
#
# 继承路径这件事本身是对的（同一条线上数据在哪多半没变）。错的是把「有人真去
# 看过一眼」的结论一起抄进一个还没跑过的步骤。


def test_the_new_step_dialog_does_not_inherit_measurements():
    """`checked=` / `missing=` / `md5=` / `size=` / `n=` 一个都不许跟着抄。

    最荒唐的是 `missing=`：一个今天才建出来的步骤，一出生就声称那份数据没了。
    而这一整条 ③ 需求的来历正是「假结论比没结论贵」。
    """
    src = app_js()
    assert "function inheritPath" in src
    # 纯函数层在 node 下 module.exports 出去，这里直接按 JS 语义复算一遍判据
    m = re.search(r"var MEASURED_ATTRS = \[(.*?)\];", src, re.S)
    assert m, "抹掉哪些属性必须是一张显式的表，不是散在调用处的 if"
    got = {x.strip().strip('"') for x in m.group(1).split(",") if x.strip()}
    assert got == {"size", "n", "md5", "sha256", "checked", "missing"}
    # 定义了不用等于没做。断言的是**赋值那一行**，不是「源码里出现过这个名字」——
    # 后者会被上面那句注释满足（这条测试自己就被这么骗过一次）。
    assert re.search(r'#nf-paths"\)\.value\s*=[^;]*inheritPath', src), \
        "新建对话框那一行必须真的过一遍 inheritPath"
    assert re.search(r"if \(MEASURED_ATTRS\.indexOf\(k\) < 0\)", src), \
        "过滤那一行本身也要钉住：只留一张表、不真的用它是同一个洞"


def test_the_stripped_set_is_exactly_the_measured_attributes():
    """这张表要和 core 认得的「机器属性」对得上，别一边加一边忘。

    role / 说明 / 位置是人写的判断，恰恰**应该**继承；认不出来的属性也留着——
    我们不知道 `nodes=…` 是度量还是描述，替人删掉别人写的字比多留一个字更糟。
    """
    src = app_js()
    m = re.search(r"var MEASURED_ATTRS = \[(.*?)\];", src, re.S)
    got = {x.strip().strip('"') for x in m.group(1).split(",") if x.strip()}
    assert set(core.CHECKSUM_KEYS) <= got
    assert {"checked", "missing"} <= got
    assert "role" not in got and "note" not in got


# ═════════════════════ 接缝：删除 ↔ 数据依赖
#
# 删除会打断三种边。子步骤和 `[[006]]` 一直都报，`input: 006` 是这一版新加的边，
# 没有任何一个模块觉得它归自己管。而它的后果最重：可溯源性沿着它上溯，
# 而且 id 会被重用 —— 下一个拿到这个号的步骤会静悄悄地接手这些边。


def test_delete_reports_who_was_eating_this_steps_output(proj):
    _root, d = proj
    W.create_step(d, title="产", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="吃", body="## 为什么\na\n",
                  inputs=["001 | train.jsonl"])
    info = W.delete_step(d, "001", "误建", by="human")
    assert info["dangling_inputs"] == ["002"], \
        "谁声明了 input: 001，删的时候就必须当场说出来"


def test_a_body_reference_and_a_data_dependency_are_reported_separately(proj):
    """两件事不能混成一条：`[[006]]` 是给人读的一句话，`input:` 是数据依赖的声明。"""
    _root, d = proj
    W.create_step(d, title="产", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="提到它", body="## 为什么\n见 [[001]]\n")
    W.create_step(d, parent="001", title="吃它", body="## 为什么\na\n",
                  inputs=["001 | x.csv"])
    info = W.delete_step(d, "001", "误建", by="human")
    assert info["dangling_refs"] == ["002"]
    assert info["dangling_inputs"] == ["002b"]


def test_all_three_exits_surface_the_dangling_inputs(proj):
    """CLI / MCP / REST。少一处，那条边就在那一处永远看不见。"""
    root, d = proj
    W.create_step(d, title="产", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="吃", body="## 为什么\na\n", inputs=["001 | x.csv"])

    be = M.LocalBackend(root)
    text = M.dispatch(be, "trace_delete_step",
                      {"project": "课题", "step": "001", "reason": "误建"})
    assert "002" in text and "input" in text

    assert "dangling_inputs" in inspect.getsource(C.cmd_rm), "CLI 也要打这一行"


def test_the_rest_delete_passes_the_new_key_through(client):
    W.create_step(client.sd, title="产", body="## 为什么\na\n")
    W.create_step(client.sd, parent="001", title="吃", body="## 为什么\na\n",
                  inputs=["001 | x.csv"])
    r = client.request("DELETE", "/api/p/课题/steps/001", headers=AUTH,
                       json={"reason": "误建", "by": "human"})
    assert r.status_code == 200
    assert r.json()["dangling_inputs"] == ["002"]


def test_the_web_warns_before_the_delete_not_after():
    """网页手上就有 `consumers`，所以这句话应该出现在**确认框里**。

    删完再说「顺便一提，有三步的 input 现在指空了」，人已经没有第二次机会了。
    """
    src = app_js()
    assert "confirm.delete.consumers" in src
    assert "d.consumers" in src, "用的是服务端算好的反向边，不要自己再扫一遍"
    assert "confirm.delete.reuse" in src, "还要说清 id 会被重用这条叠加后果"


# ═════════════════════ 接缝：grep 能答的 ↔ 三处搜索能答的
#
# G4 的底线是「删掉全部程序，grep -r 还能回答」。`grep -rn best.pt projects/`
# 一秒答得出「这个 checkpoint 是哪一步产出的」，而三处搜索都答不出 ——
# 工具比 grep 弱的那部分，恰好是 agent 唯一够得到的那部分。


def test_the_haystack_covers_locations_but_not_checksums(proj):
    """位置和说明进，校验和与日期不进。

    搜一串 md5 是核对不是找东西，而把日期拼进干草堆只会让「12」这种短查询
    命中一堆无关的步骤。
    """
    _root, d = proj
    W.create_step(d, title="满", body="## 为什么\n因为\n", paths=[FAT_PATH],
                  code=["snapshot | /orange/snap/20260809 | manifest=M.md5"],
                  inputs=["001 | pocket_composition.csv"])
    s = core.compile_forest(d)["steps"][0]
    hay = core.locations_haystack(s)
    assert "/blue/lab/cif" in hay and "原始 CIF" in hay
    assert "/orange/snap/20260809" in hay
    assert "pocket_composition.csv" in hay
    assert "7d4e1a9c" not in hay and "2026-08-09" not in hay


def test_mcp_search_finds_a_checkpoint_by_its_path(proj):
    root, d = proj
    W.create_step(d, title="训练", body="## 为什么\n因为\n",
                  paths=["/orange/lab/ckpt/run042/best.pt | output | 权重"])
    be = M.LocalBackend(root)
    out = M.dispatch(be, "trace_search", {"query": "best.pt"})
    assert "课题/001" in out
    assert "best.pt" in out.split("\n", 2)[-1], \
        "命中只落在位置上时要把那一行摆出来——光给 id 和标题，看的人没法判断是不是误命中"


def test_the_rest_search_finds_the_same_thing(client):
    W.create_step(client.sd, title="训练", body="## 为什么\n因为\n",
                  paths=["/orange/lab/ckpt/run042/best.pt | output | 权重"])
    r = client.get("/api/search", params={"q": "best.pt"})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert [h["id"] for h in hits] == ["001"]
    assert "paths" in hits[0]["where"], "要说清是在哪儿命中的"


def test_the_web_search_uses_the_same_haystack():
    """人在网页里搜和 agent 用 trace_search 搜，必须搜到同一批东西。

    FORMAT.md 第 0 节的「信息对等」——不然「我明明记过」和「工具说没有」
    会同时成立，而人会相信工具。
    """
    src = app_js()
    assert "function locationsHay" in src
    assert "locationsHay(step)" in src, "hay() 里要真的拼进去"


def test_the_mcp_fallback_haystack_agrees_with_core(proj):
    """trace_mcp.py 会被单独拷到只有 TRACE_URL 的机器上，那里没有 trace_core。

    所以它有一份退路实现。两份必须逐字同结果——不然「在服务器上搜得到、
    在那台机器上搜不到」，而没有任何一处会报错。
    """
    _root, d = proj
    W.create_step(d, title="满", body="## 为什么\na\n", paths=[FAT_PATH, GONE_PATH],
                  code=["snapshot | /orange/snap | manifest=M", "git | https://x/y"],
                  inputs=["001 | a.csv", "099 | b.csv"])
    s = core.compile_forest(d)["steps"][0]

    src = inspect.getsource(M._locations_haystack)
    ns: dict = {}
    # 把 try 那半段拆掉，只跑 except 里的退路实现
    body = textwrap.dedent(src).split("except Exception:", 1)[1]
    exec("def fallback(s):\n" + textwrap.indent(textwrap.dedent(body), "    "), ns)
    assert ns["fallback"](s) == core.locations_haystack(s)


# ═════════════════════ 接缝：core 的中文警告 ↔ 界面要说的语言
#
# 以前 core 只发一句拼好的中文，web/app.js 只能拿正则从那句里把 {id} 抠回来。
# 那条正则脆得离谱：中文改一个字，英文界面上就原样漏出中文。


@pytest.mark.parametrize("code,key", [
    ("dangling_input", "id"), ("self_input", "id"),
    ("input_cycle", "chain"), ("section_without_prose", "section"),
])
def test_the_warnings_with_a_value_in_them_carry_it_structurally(proj, code, key):
    _root, d = proj
    W.create_step(d, title="根", body="## 为什么\na\n")
    # 「做了什么」下面**只有**子标题、一个字散文都没有 —— ⑥ 那条诊断说的就是这个
    W.create_step(d, parent="001", title="乱", status="done",
                  body="## 为什么\na\n\n## 做了什么\n### 甲\n### 乙\n\n## 结果\nr\n\n## 结论\nc\n",
                  inputs=["002 | self.csv", "099 | ghost.csv", "003 | x.csv"])
    W.create_step(d, parent="002", title="环", body="## 为什么\na\n",
                  inputs=["002 | back.csv"])
    hit = [w for w in core.compile_forest(d)["warnings"] if w["code"] == code]
    assert hit, f"{code} 没发出来，这条断言就测不到东西了"
    assert hit[0].get("vars", {}).get(key), f"{code} 必须结构化地带上 {key}"


def test_warnings_without_a_value_stay_exactly_as_they_were(proj):
    """不带变量的警告不该多出一个空 vars —— 静态导出里几十条各多四个字节，
    而它们一个变量都没有。形状不变，下游也就一个字都不用改。"""
    _root, d = proj
    W.create_step(d, title="没图注", body="## 为什么\n试\n\n![](loss.png)\n")
    w = next(x for x in core.compile_forest(d)["warnings"]
             if x["code"] == "figure_without_caption")
    assert set(w) == {"level", "code", "message", "where"}


def test_the_web_prefers_vars_and_keeps_the_regex_only_as_a_fallback():
    """两件事都要：优先读 w.vars，同时不能把退路删掉。

    退路留着是为了静态导出出来的旧数据 —— 那些 JSON 是当时那一版写的，
    它们不会因为 core 改了而重新生成。
    """
    src = app_js()
    assert "w.vars" in src
    i_vars, i_pick = src.index("w.vars"), src.index("m.pick.exec")
    assert i_vars < i_pick, "vars 必须在抠正则之前"
    assert "return esc(w.message)" in src, "两条路都认不出时原样显示中文，绝不吞"


def test_the_english_ui_does_not_leak_chinese_for_these_four(proj):
    """端到端：core 此刻真发出的这四条，在英文界面上都有对应文案。"""
    i18n = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
    for key in ("input.warn.missing", "input.warn.self", "input.warn.cycle", "lint.subheads"):
        assert f'"{key}"' in i18n


# ═════════════════════ 接缝：MCP schema ↔ 处理函数 ↔ 写入层签名
#
# 「新字段在 create_step 加了、但 LocalBackend 的白名单没加」是前两轮真出过的
# 缺陷形状：声明和实现各在一个文件里，两边都自洽，中间那一格没人看。
# 这三条是机械核对，加新字段时会自己红。


def test_every_declared_mcp_parameter_is_actually_read():
    def keys_read(fn):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        got = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                    and node.value.id == "args" and isinstance(node.slice, ast.Constant)):
                got.add(node.slice.value)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "args" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                got.add(node.args[0].value)
            if isinstance(node, ast.Tuple):      # payload = {k: args[k] for k in (…)}
                vals = [e.value for e in node.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len(vals) >= 3:
                    got.update(vals)
        return got

    for t in M.TOOLS:
        props = set(t["inputSchema"].get("properties", {}))
        unread = sorted(props - keys_read(M.HANDLERS[t["name"]]))
        assert not unread, (
            f"{t['name']} 的 schema 里声明了 {unread}，处理函数一个都没读——"
            "agent 会照着 schema 填，然后发现什么都没发生")


@pytest.mark.parametrize("where,fn", [
    ("trace_server.api_create", None), ("trace_mcp.LocalBackend.create", M.LocalBackend.create)])
def test_both_front_doors_pass_every_create_step_parameter(where, fn):
    """REST 和本地后端都要把 create_step 收的每个参数传下去。

    漏一个的症状是**静默丢字段**：调用方填了 inputs，返回 201，磁盘上没有。
    """
    want = {p for p in inspect.signature(W.create_step).parameters} - {"steps_dir"}
    if fn is None:
        src = inspect.getsource(S.create_app)
        node = next(n for n in ast.walk(ast.parse(textwrap.dedent(src)))
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "api_create")
    else:
        node = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    passed = {k.arg for c in ast.walk(node) if isinstance(c, ast.Call) for k in c.keywords if k.arg}
    assert not (want - passed), f"{where} 漏传了 {sorted(want - passed)}"


def test_the_mutable_field_list_is_reachable_from_the_mcp_tool():
    """写入层收的可变字段，agent 得有办法用上——否则那个字段等于不存在。"""
    schema = next(t for t in M.TOOLS if t["name"] == "trace_update_step")["inputSchema"]
    props = set(schema["properties"])
    for k in ("inputs", "add_inputs", "code", "add_code", "paths", "add_paths"):
        assert k in W.MUTABLE and k in props, f"{k} 在写入层和 MCP schema 上必须同时在"


# ═════════════════════ 接缝：CLI 真有的子命令 ↔ 文档写出来的命令
#
# 这一轮真实发生过：CLI 那一波加了 mv / paths --check / paths --missing，
# 文档那一波因为「写的时候还不存在」刻意没写，两边都做对了，命令就此没人知道。
# 已有的 test_docs 只查一个方向（文档里写的必须存在），所以反向再钉一条。


def _readme() -> str:
    return (REPO / "docs" / "V1_REFERENCE.md").read_text(encoding="utf-8")


def _cli_subcommands() -> list[str]:
    """**从 argparse 自己身上问**，不再手写一份名单。

    上一版这里是一个 parametrize 字面量，于是它自己就是那种会漂移的中心索引：
    `result` / `pipeline` 两条新子命令加进来的时候没人想起改它，那道「加了子命令
    却没人知道」的闸门对新命令**恰好是不设防的**——它只看得见名单里已经有的那些。
    名单和真相分家，正是这个仓库到处在防的事。
    """
    import argparse

    subs = [a for a in C.build_parser()._actions
            if isinstance(a, argparse._SubParsersAction)]
    assert subs, "trace_cli 的 argparse 里找不到子命令"
    return sorted(subs[0].choices)


@pytest.mark.parametrize("sub", _cli_subcommands())
def test_every_cli_subcommand_is_named_somewhere_in_the_readme(sub):
    """`serve` 和 `init` 在「30 秒上手」里，别的都该出现在命令一节。"""
    assert re.search(rf"trace_cli\.py {re.escape(sub)}\b", _readme()), \
        f"README 里没有 `{sub}`——加了子命令却没人知道，等于没加"


@pytest.mark.parametrize("flag", ["--missing", "--check"])
def test_the_path_checking_flags_are_documented(flag):
    """用户那 164 条路径的实际用法就是这两个开关。"""
    assert flag in _readme()


def test_the_readme_does_not_still_claim_patching_parent_is_a_409():
    """服务端早就把 `PATCH {parent, reason}` 当成移动并回 200 了。

    文档和实现三方不一致时，人会按最吓人的那一处理解，然后回去用对调正文的老办法
    ——而那正是这一轮要堵的。
    """
    for f in ("docs/V1_REFERENCE.md", "skills/research-trace/SKILL.md"):
        text = (REPO / f).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "parent" in line and "409" in line:
                assert "id" in line, f"{f} 里这一句还在说 parent 会 409：{line.strip()}"


def test_patching_parent_with_a_reason_really_is_a_200(client):
    """把上面那条文档断言钉在真实行为上，而不是钉在另一份文档上。"""
    W.create_step(client.sd, title="根", body="## 为什么\na\n")
    W.create_step(client.sd, parent="001", title="子", body="## 为什么\na\n")
    r = client.patch("/api/p/课题/steps/002", headers=AUTH,
                     json={"parent": None, "reason": "其实是独立起点"})
    assert r.status_code == 200
    assert r.json()["moved"].startswith("moved: ")
    bad = client.patch("/api/p/课题/steps/002", headers=AUTH, json={"parent": "001"})
    assert bad.status_code == 400, "没写原因是 400，不是 409"


# ═════════════════════ 接缝：双语 ↔ 这一轮新加的三个结构键
#
# 上一代系统的死因是双真相源。`input:` / `code:` / `moved:` 是新键，
# 它们在译文里同样必须被读都不读地丢掉，并且报出来。


@pytest.mark.parametrize("key,line", [
    ("input", "input: 007 | x.csv"),
    ("code", "code: git | https://x/y"),
    ("moved", "moved: 2026-01-01 | a | b | h | r"),
])
def test_the_new_structural_keys_are_ignored_in_a_translation(proj, key, line):
    _root, d = proj
    W.create_step(d, title="原文", body="## 为什么\na\n")
    tr = next(d.glob("001*")) / "note.en.md"
    tr.write_text(f"---\ntitle: T\n{line}\n---\n\n## Why\na\n", encoding="utf-8")
    f = core.compile_forest(d)
    s = f["steps"][0]
    assert s["inputs"] == [] and s["code"] == [] and s["moved"] == [], \
        "译文里的结构键一个字节都不许生效——那就是双真相源"
    hit = [w for w in f["warnings"] if w["code"] == "translation_structural_key"]
    assert any(f"`{key}:`" in w["message"] for w in hit), "而且必须说出来，不能静默丢"


def test_the_three_key_lists_still_agree(proj):
    """core / MCP 镜像 / 文档三份清单，逐字一致。"""
    assert M.TR_STRUCT_KEYS == core.TR_STRUCT_KEYS
    for k in ("input", "code", "moved"):
        assert k in core.TR_STRUCT_KEYS


def test_the_translation_tool_refuses_a_hand_rolled_front_matter(proj):
    """agent 拼一段 `---` 塞进 body 的话，它会原样落进正文：不报错、不生效、
    看着却像写上了。这种「静默地什么都没发生」比报错难查得多。"""
    with pytest.raises(M.ToolError) as e:
        M._reject_front_matter("---\ninput: 007 | x.csv\n---\n\n## Why\na\n")
    assert "input" in str(e.value)


# ═════════════════════ 接缝：移动 ↔ 布局 / 溯源 / 数据依赖
#
# 移动改的是树。行序、轨道、面包屑、可溯源链全是从树推出来的派生结果，
# 而 inputs 不是 —— 它一个字都不该动。


def test_moving_a_subtree_keeps_the_forest_consistent(proj):
    _root, d = proj
    W.create_step(d, title="根", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="二", body="## 为什么\na\n")
    W.create_step(d, parent="002", title="三", body="## 为什么\na\n")
    W.create_step(d, parent="002", title="三b", body="## 为什么\na\n")

    info = W.move_step(d, "002", None, "其实是独立起点", by="human")
    assert info["subtree"] == ["003", "003b"], \
        "「你移的是一步」和「一步加两步」是两个决定，事后才发现就晚了"

    f = core.compile_forest(d)
    by_id = {s["id"]: s for s in f["steps"]}
    assert by_id["002"]["parent"] is None
    assert by_id["003"]["parent"] == "002", "子步骤跟着走，parent 一个字都不改"
    assert not [w for w in f["warnings"] if w["level"] == "error"]
    rows = [s["row"] for s in f["steps"]]
    assert rows == sorted(rows) and len(set(rows)) == len(rows), "行序仍然是一个全序"


def test_a_move_never_touches_the_data_dependencies(proj):
    """树形改对了，`input:` 该怎么写还是怎么写 —— 它答的是另一个问题。"""
    _root, d = proj
    W.create_step(d, title="a", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="b", body="## 为什么\na\n")
    W.create_step(d, parent="002", title="c", body="## 为什么\na\n",
                  inputs=["001 | x.csv", "002 | y.csv"])
    before = core.compile_forest(d)["steps"][-1]["inputs"]
    W.move_step(d, "003", "001", "c 读的是 001 的产物", by="human")
    after = {s["id"]: s for s in core.compile_forest(d)["steps"]}["003"]
    assert after["inputs"] == before
    assert [m["to"] for m in after["moved"]] == ["001"]


def test_the_audit_line_is_append_only_and_ordered(proj):
    _root, d = proj
    W.create_step(d, title="a", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="b", body="## 为什么\na\n")
    W.move_step(d, "002", None, "先提成根", by="human", date="2026-08-01")
    W.move_step(d, "002", "001", "查清楚了，还是接着 001", by="human", date="2026-08-02")
    moved = core.compile_forest(d)["steps"][1]["moved"]
    assert [(m["from"], m["to"]) for m in moved] == [("001", ""), ("", "001")], \
        "顺序即历史：第一条不许被第二条覆盖掉"
    raw = (next(d.glob("002*")) / "note.md").read_text(encoding="utf-8")
    assert raw.count("moved: ") == 2, "grep -rn '^moved:' 要数得出两次（G4）"


def test_moving_does_not_break_a_dangling_input_into_an_error(proj):
    """残缺数据上移动仍然只是警告。构建器必须能在残缺输入上产出部分结果。"""
    _root, d = proj
    W.create_step(d, title="a", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="b", body="## 为什么\na\n",
                  inputs=["099 | ghost.csv"])
    W.move_step(d, "002", None, "独立", by="human")
    f = core.compile_forest(d)
    assert [w["level"] for w in f["warnings"]] == ["warn"]


# ═════════════════════ 不变量：这一轮的新字段没有破坏原有的四条
#
# 派生信息一律扫描计算、绝不存储（P1）；编译是纯函数、逐字节确定（P3）；
# 写端点一律要令牌；删掉程序之后 .md 仍然回答得了问题（G4）。


def _fat_project(d: Path) -> None:
    W.create_step(d, title="根", body="## 为什么\na\n")
    W.create_step(d, parent="001", title="满", status="done", commit="c1d2e3f",
                  body="## 为什么\na\n\n## 做了什么\nb\n\n## 结果\nc\n\n## 结论\nd\n",
                  paths=[FAT_PATH, GONE_PATH],
                  code=["snapshot | /orange/snap | manifest=M.md5 n=43",
                        "container | reg.io/img:1 | digest=sha256:dead"],
                  inputs=["001 | a.csv"])
    W.move_step(d, "002", None, "其实是独立起点", by="human", date="2026-08-01")
    W.move_step(d, "002", "001", "查清楚了", by="human", date="2026-08-02")


def test_compiling_twice_gives_byte_identical_json(proj):
    _root, d = proj
    _fat_project(d)
    a = json.dumps(core.compile_forest(d), ensure_ascii=False, sort_keys=False)
    b = json.dumps(core.compile_forest(d), ensure_ascii=False, sort_keys=False)
    assert a == b, "静态导出要求逐字节确定：扫描顺序不许漏进产物"


def test_the_derived_fields_are_nowhere_on_disk(proj):
    """`consumers` / `state` / `superseded_by` 都是算出来的，磁盘上一个字都不该有。"""
    root, d = proj
    _fat_project(d)
    W.add_insight(root, "课题", "pitfall", "以为是 1099 个")
    W.add_insight(root, "课题", "pitfall", "查清楚是 944 个", supersedes="p1")
    blob = "\n".join(p.read_text(encoding="utf-8")
                     for p in (root / "projects" / "课题").rglob("*.md"))
    for banned in ("consumers", "superseded", "state:", "被取代"):
        assert banned not in blob, f"{banned} 是派生的，写进文件就是第二份真相"
    assert core.compile_forest(d)["steps"][0]["consumers"] == ["002"]


def test_grep_still_answers_the_four_new_questions(proj):
    """G4：删掉所有程序，这四个问题还得答得出来。"""
    root, d = proj
    _fat_project(d)
    W.add_insight(root, "课题", "pitfall", "以为是 1099 个")
    W.add_insight(root, "课题", "pitfall", "查清楚是 944 个", supersedes="p1")
    files = list((root / "projects" / "课题").rglob("*.md"))
    blob = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert re.search(r"^missing=|missing=2026-08-10", blob, re.M), "哪个位置没了"
    assert re.search(r"^moved: .* \| .* \| human \| ", blob, re.M), "这一步被谁挪过、为什么"
    assert re.search(r"^input: 001 \| a\.csv", blob, re.M), "这些字节从哪来"
    assert "· 取代 p1" in blob, "哪条判断取代了哪条"


def test_the_path_check_endpoint_needs_a_token(client):
    """③ 的写接口和别的写接口一样是公网上的任意写风险。"""
    W.create_step(client.sd, title="a", body="## 为什么\na\n", paths=[FAT_PATH])
    body = {"loc": "/blue/lab/cif", "exists": False, "date": "2026-08-10"}
    assert client.post("/api/p/课题/steps/001/paths/check", json=body).status_code == 401
    assert client.post("/api/p/课题/steps/001/paths/check",
                       headers=AUTH, json=body).status_code == 200


def test_a_missing_exists_flag_is_refused_rather_than_defaulted(client):
    """漏填不能变成「已确认不存在」。missing 是要被后来人当结论读的。"""
    W.create_step(client.sd, title="a", body="## 为什么\na\n", paths=[FAT_PATH])
    r = client.post("/api/p/课题/steps/001/paths/check", headers=AUTH,
                    json={"loc": "/blue/lab/cif"})
    assert r.status_code == 400
    assert "exists" in r.json()["error"]


def test_recording_a_check_never_touches_what_a_human_wrote(proj):
    """只动机器字段：role 和说明是人写的判断，机器没有资格改。size 也留着——
    「没了的那个有 57 GB」正是要留下来的信息。"""
    _root, d = proj
    W.create_step(d, title="a", body="## 为什么\na\n", paths=[FAT_PATH])
    info = W.record_path_check(d, "001", "/blue/lab/cif", exists=False, date="2026-08-10")
    p = info["path"]
    assert p["role"] == "input" and p["note"] == "原始 CIF"
    assert p["size"] == 61203283968 and p["attrs"]["nodes"] == "12"
    assert p["checked"] == "" and p["missing"] == "2026-08-10" and p["state"] == "missing"


def test_the_static_export_carries_the_new_fields_and_stays_self_contained(cli, tmp_path):
    """静态导出要能 `file://` 断网打开，而且这一轮的字段一个都不能掉。"""
    _root, d = cli
    _fat_project(d)
    out = tmp_path / "site"
    assert C.cmd_build(type("A", (), {"out": str(out)})()) == 0
    page = (out / "p" / "课题" / "index.html").read_text(encoding="utf-8")
    for key in ('"consumers"', '"moved"', '"inputs"', '"code"', '"state"', '"role"', '"via"'):
        assert key in page, f"静态页里没有 {key}，那一块在离线视图上就是空的"
    assert "http://" not in page.replace("http://www.w3.org", ""), "不许引外部资源"
    assert (out / "app.js").exists() and (out / "i18n.js").exists()
