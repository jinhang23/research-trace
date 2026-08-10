"""⑦「树上的三种边」这一轮的接缝断言。

前几轮的教训又重演了一遍：按文件所有权分派不出冲突，**缺陷长在接缝上**。
每个 agent 在自己的文件里做对了，然后在报告里请别人开口子，谁也不能改谁的文件，
于是那个口子谁都没开。这一轮真实抓到的六个，全都是这个形状：

  · `decision:` 写下去、底下还没有候选时，**它在每一个门面上都消失了**——
    MCP 的 `trace_read` 单步视图和缩进树里一个字都没有，回执也不提，网页详情面板里
    只剩孤零零一行没有任何解释。而它是整套东西里唯一推导不出来、只能人写的一句话，
    写的人回头一读发现它不见了，最合理的结论是「刚才没保存上」。
  · `decision:` 和候选说明**搜不到**。`grep -rn 类别不平衡 projects/` 一秒答得出
    「当年是在哪个岔路口纠结这件事」，而站内搜索、`trace_search`、网页搜索框
    三处都答不出——工具比 grep 弱的地方，恰好是 agent 唯一够得到的地方。
  · `bad_branch`（`branch:` 拼错）没有 i18n 文案，英文界面上原样漏出一整句中文。
    上一轮 strings 那位明说「没敢加，怕加了没人接」，web 那位明说「没建条目，
    怕指向一个不存在的 key」——两边都等对方先动，于是谁都没动。
  · 「都不行」这一档在网页上显示的是「全部作废」，而 README 的视觉编码表、
    FORMAT.md 15.6、MCP 的 `fork_label` 三处承诺的都是「都不行」。**图例说谎比
    没有图例更糟**，而「作废」听着像这几条记录出了问题被撤销了——它们没有。
  · 不透明度被 `.f-abandoned` 借走了（`opacity: .72` / `.75`）。那个通道已经归
    「和选中有没有关系」，借出去之后两者会**相乘**；而 P4 明说过「变灰的语义是
    不相关，不是结论为否」。
  · `GET/POST /api/p/{项目}/forks` 挂着 `include_in_schema=False` 等 README 的
    API 表，而 README 那一轮结束时也没等到——两条真实存在的端点，文档里查不到。
  · 网页移动一个候选之后什么都不说，而 CLI 的 `mv` 会说出原岔路口和新岔路口
    各自现在剩下谁。同一个信号只有一半的人看得见。

所以这个文件按**接缝**组织，不按模块：每一节的标题是「谁和谁之间」。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import trace_cli as C
import trace_core as core
import trace_mcp as M
import trace_write as W

REPO = Path(__file__).resolve().parent.parent
APP = (REPO / "web" / "app.js").read_text(encoding="utf-8")
CSS = (REPO / "web" / "style.css").read_text(encoding="utf-8")
I18N = (REPO / "web" / "i18n.js").read_text(encoding="utf-8")
README = (REPO / "README.md").read_text(encoding="utf-8")
FORMAT = (REPO / "FORMAT.md").read_text(encoding="utf-8")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="这台机器没有 node，跳过 JS 断言")

BODY = "## 为什么\n因为。\n\n## 做了什么\n跑了 `a.py`。\n\n## 结论\n成立。\n"


@pytest.fixture()
def sd(tmp_path: Path) -> Path:
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    return core.steps_dir_of(tmp_path, "课题")


@pytest.fixture()
def forked(sd: Path) -> Path:
    """一个岔路口（011 → 012 / 012b）、一条汇回、一个「问题写了没候选」。"""
    W.create_step(sd, title="基线", status="done", body=BODY,
                  decision="类别不平衡怎么处理？只能选一条走下去")
    W.create_step(sd, parent="001", title="只调采样权重", body=BODY,
                  branch="alternative | 先试最便宜的")
    W.create_step(sd, parent="001", title="换 focal loss", body=BODY,
                  branch="alternative | 改动大但可能更稳")
    W.create_step(sd, parent="002b", title="产出分数", status="done", body=BODY)
    W.create_step(sd, parent="002", title="用了另一支的分数", status="done", body=BODY,
                  inputs=["003 | scores.csv"])
    W.create_step(sd, parent="002b", title="还没标候选的问题", body=BODY,
                  decision="要不要再加一路数据增强？")
    return sd


def cli_out(fn, **kw) -> str:
    class A:
        project = "课题"
        strict = False
        all = False
    for k, v in kw.items():
        setattr(A, k, v)
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(A())
    return buf.getvalue()


# ============================================================ 接缝 ①
# core（写下 `decision:` 之后什么都不发生）× 三个门面（谁负责说出来）
#
# `decision:` 是这整套东西里**唯一**推导不出来的信息：候选有谁是扫出来的、
# 选中了谁是从 dead 推出来的，只有「当时在决定什么」必须人写。而候选组是派生的
# ——没有候选就没有组，没有组就没有那一块 UI。于是写下这句话之后、标候选之前
# 的那段时间里，它在**每一个门面上都不存在**。
# 写的人回头一读发现它不见了，最省事的解释是「刚才没保存上」。


def test_a_decision_with_no_candidates_yet_is_reported_instead_of_vanishing(forked):
    """核心那一条：写了「在决定什么」却还没有候选，必须报出来。

    它是 fork_without_decision 的镜像。那一条至少还有两条并排的支线摆在图上，
    人看得见「这里有个岔路口，只是没写清」；这一条写下去之后什么都不发生。
    """
    f = core.compile_forest(forked)
    got = [w for w in f["warnings"] if w["code"] == "decision_without_candidates"]
    assert len(got) == 1, "写了 decision 却一个候选都没有，居然一声不吭"
    assert got[0]["vars"] == {"id": "003b"}, got[0]
    assert got[0]["level"] == "warn"
    # 真的是分叉点的那一步**不许**跟着一起报——它有候选，问题已经落到实处了。
    assert all(w["vars"]["id"] != "001" for w in got)


def test_a_step_that_really_is_a_fork_never_gets_the_orphan_hint(sd):
    """有候选就不报。这条防的是把提示变成「凡是写了 decision 都唠叨一句」。"""
    W.create_step(sd, title="基线", body=BODY, decision="A 还是 B？")
    W.create_step(sd, parent="001", title="A", body=BODY, branch="alternative")
    W.create_step(sd, parent="001", title="B", body=BODY, branch="alternative")
    codes = [w["code"] for w in core.compile_forest(sd)["warnings"]]
    assert "decision_without_candidates" not in codes


def test_the_orphan_hint_changes_no_level_anywhere(forked):
    """它和另外三条分叉诊断同一档：只提示，不进 L0–L4，也不进退出码。

    塞进评级只要一行，而违反之后的表现是「明明补齐了 commit 和 path 却上不了
    L2」，没人猜得到原因。
    """
    assert "decision_without_candidates" in C.HINT_CODES
    assert "decision_without_candidates" in re.search(
        r"var HINT_CODES = (\[[^\]]*\]);", APP).group(1)
    f = core.compile_forest(forked)
    orphan = [s for s in f["steps"] if s["id"] == "003b"][0]
    # 同一份正文，带不带那句 decision，自身等级必须一样
    assert orphan["trace"]["self"] == [s for s in f["steps"] if s["id"] == "003"][0]["trace"]["self"]


def test_mcp_says_the_sentence_out_loud_when_it_is_not_a_fork_yet(forked, tmp_path):
    """agent 只看得见 MCP 渲染出来的东西。单步视图、缩进树、写完的回执，三处都要说。

    三处缺任何一处，agent 写完那句话之后就再也看不到它，然后重写一遍或者放弃。
    """
    be = M.LocalBackend(tmp_path)
    one = M.t_read(be, {"project": "课题", "step": "003b"})
    assert "要不要再加一路数据增强？" in one, "单步视图把人写的那句话整个吞掉了"
    assert "还不是" in one and "alternative" in one, "只显示不解释，人只会以为界面坏了"

    tree = M.t_read(be, {"project": "课题"})
    assert "还没有候选" in tree, "缩进树上一点痕迹都没有"

    receipt = M.t_update_step(be, {"project": "课题", "step": "003b",
                                   "decision": "换个问法：要不要再加一路数据增强？"})
    assert "换个问法" in receipt and "alternative" in receipt, \
        "写完那一刻的回执是最该说清「它还不算岔路口」的地方"


def test_the_web_explains_the_lone_line_instead_of_just_printing_it(forked):
    """网页详情面板里那一行不许孤零零站着。

    人看到自己写的问题**在**页面上、周围却什么都没有，最容易得出的结论是
    「界面漏掉了候选」。所以紧跟一句说清它现在是什么状态、下一步做什么。
    """
    fn = re.search(r"function renderFork\(s\)[\s\S]*?\n  \}\n", APP).group(0)
    orphan = fn.split("} else if (s.decision) {")[1]
    assert "decision.question.orphan" in orphan, \
        "「问题写了、候选还没标」那个分支又变回只有一行了"
    for lang in ("en", "zh"):
        assert f'"decision.question.orphan"' in I18N
    assert "alternative" in I18N.split('"decision.question.orphan"')[1][:600], \
        "那句话得说出下一步该做什么（标一个 alternative），不能只说「还不是岔路口」"


# ============================================================ 接缝 ②
# core（人写的那两句散文）× 三处搜索（服务端 / MCP / 网页）
#
# G4 的底线是「删掉全部程序，grep -r 还答得了『为什么放弃了 X』」。
# `grep -rn 类别不平衡 projects/` 一秒就能答出「当年是在哪个岔路口纠结这件事」，
# 而工具答不出就等于比 grep 弱——而 agent 只够得到工具那一侧，它拿到「没搜到」
# 会读成「没记过」，然后重新纠结一遍同一个已经做过的决定。


def test_the_one_sentence_only_a_human_can_write_is_searchable(forked):
    """`decision:` 和候选说明进搜索干草堆。取值（extends/alternative）不进。"""
    f = core.compile_forest(forked)
    by = {s["id"]: s for s in f["steps"]}
    assert "类别不平衡" in core.fork_haystack(by["001"])
    assert "先试最便宜的" in core.fork_haystack(by["002"])
    # 取值不许进：收进来的话，搜 "alternative" 会命中半棵树
    assert "alternative" not in core.fork_haystack(by["002"])
    assert "extends" not in core.fork_haystack(by["003"])


def test_all_three_search_faces_agree_on_the_fork_haystack(forked, tmp_path):
    """三处必须搜到同一批东西，而且判据只有一份（core.fork_haystack）。

    以前 `path:` 就是这么分家的：MCP 搜得到、网页搜不到，同一个词两种答案。
    """
    f = core.compile_forest(forked)
    by = {s["id"]: s for s in f["steps"]}
    # MCP 那份是「被单独拷走」时的退路，输出必须逐字相同
    for s in by.values():
        assert M._fork_haystack(s) == core.fork_haystack(s)
    # 服务端的 where 里要有 fork 这一档
    src = (REPO / "trace_server.py").read_text(encoding="utf-8")
    assert 'core.fork_haystack' in src and '"fork"' in src
    # 网页那份
    assert "function forkHay" in APP
    assert "forkHay(step)" in APP.split("function hay(step)")[1][:600], \
        "网页的干草堆没把 forkHay 接进去 —— 侧栏搜不到，跨项目搜索却搜得到"

    be = M.LocalBackend(tmp_path)
    hit = M.t_search(be, {"query": "类别不平衡"})
    assert "001" in hit, "trace_search 搜不到 decision"
    # 命中落在正文之外时必须把那一行摆出来，否则读的人判不出是不是误命中
    assert "decision:" in hit and "类别不平衡" in hit


@needs_node
def test_the_browser_side_haystack_really_contains_both(tmp_path):
    """网页那一份用 node 真跑一遍，不靠 grep 源码。"""
    script = tmp_path / "t.js"
    script.write_text(
        "const U = require(%s);\n"
        "const s = { id: '002', title: 't', body: '', tags: [],\n"
        "            branch: 'alternative', branch_note: '先试最便宜的',\n"
        "            decision: '类别不平衡怎么处理' };\n"
        "if (!U.matches(s, '类别不平衡')) { console.log('MISS decision'); process.exit(1); }\n"
        "if (!U.matches(s, '最便宜')) { console.log('MISS branch_note'); process.exit(1); }\n"
        "console.log('OK');\n" % json.dumps(str(REPO / "web" / "app.js")),
        encoding="utf-8")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True)
    assert out.stdout.strip() == "OK", out.stdout + out.stderr


def test_every_search_bucket_the_server_can_emit_has_a_name_in_both_languages():
    """`where` 里多一档就得多一条文案，否则界面上原样漏出内部字段名。

    不写死清单、从服务端源码里扫：写死的话，下一个人加一档照样漏，而这条测试
    还是绿的。（扫出来的第一批里 `tr.title` / `tr.body` 就已经缺了——双语功能的
    意义是英文那侧也答得出同一个问题，结果连「命中在哪」都还说着内部名。）
    """
    src = (REPO / "trace_server.py").read_text(encoding="utf-8")
    buckets = set(re.findall(r'where\.append\("([a-z.]+)"\)', src))
    assert "fork" in buckets, "服务端不再把 decision / 候选说明算成一档命中了"
    for b in sorted(buckets):
        assert I18N.count(f'"search.where.{b}"') == 2, \
            f'search.where.{b} 在 en / zh 里各要一条（现在有 {I18N.count(chr(34) + "search.where." + b + chr(34))} 条）'


# ============================================================ 接缝 ③
# core 的 `bad_branch` × web 的 WARN_MAP × i18n
#
# 三方互相等对方先动：core 发了 code，strings 那位「没敢加 key，怕没人接」，
# web 那位「没建条目，怕指向一个不存在的 key」。结果是拼错 `branch:` 的人在
# 英文界面上收到一整句中文——而那一条恰恰是给刚打错字的人看的。


def test_a_misspelled_branch_value_says_so_in_the_readers_language(sd):
    """core 报得出 → WARN_MAP 认得出 → i18n 两种语言都有。三段缺一段就漏中文。"""
    d = sd / "002_x"
    d.mkdir()
    (d / "note.md").write_text(
        "---\nid: 002\nbranch: alterative\nstatus: wip\ntitle: T\n---\n\n" + BODY,
        encoding="utf-8")
    w = [x for x in core.compile_forest(sd)["warnings"] if x["code"] == "bad_branch"]
    assert w and w[0]["vars"] == {"branch": "alterative"}

    table = re.search(r"var WARN_MAP = \{[\s\S]*?\n  \};", APP).group(0)
    assert "bad_branch:" in table, "WARN_MAP 里没有 bad_branch —— 英文界面会漏出中文整句"
    assert 'key: "lint.branch.unknown"' in table
    assert 'take: ["branch"]' in table, "要走结构化的 vars，不许再抠中文正则"
    assert I18N.count('"lint.branch.unknown"') == 2, "en / zh 各要一条"
    # 文案要说清后果：写下的那个标记现在一点作用都没有
    for chunk in I18N.split('"lint.branch.unknown": ')[1:]:
        assert "extends" in chunk[:400], "没说清它被当成什么处理了"


def test_the_downgraded_branch_is_still_a_plain_continuation(sd):
    """报一声之后照常建树（和 bad_status 同一条路），不是让这一步从图上消失。"""
    d = sd / "002_x"
    d.mkdir()
    (d / "note.md").write_text(
        "---\nid: 002\nbranch: alterative | 打错了\nstatus: wip\ntitle: T\n---\n\n" + BODY,
        encoding="utf-8")
    s = [x for x in core.compile_forest(sd)["steps"] if x["id"] == "002"][0]
    assert s["branch"] == "extends"
    assert s["fork"] is None


# ============================================================ 接缝 ④
# README / FORMAT.md 承诺的三档标注 × web/i18n.js 真显示出来的字
#
# 文档把「N 选 1 / 已定 / 都不行」写成了对用户的承诺，而标注文字是 app.js
# 从 i18n 取的。两边一分家，图例就在说谎——那比没有图例更糟。


def test_the_three_fork_labels_say_what_the_docs_promise_they_say():
    """三档标注的中文措辞，README、FORMAT.md、i18n、MCP 四处必须一致。"""
    zh = I18N.split('const STRINGS')[-1]
    zh = zh[zh.index('"zh"') if '"zh"' in zh else 0:]
    for key, word in (("decision.pick", "选 1"), ("decision.settled", "已定"),
                      ("decision.alldead", "都不行")):
        line = [ln for ln in I18N.splitlines()
                if ln.strip().startswith(f'"{key}":') and re.search(r"[一-鿿]", ln)]
        assert line, f"{key} 没有中文文案"
        assert word in line[0], f"{key} 的中文是 {line[0].strip()}，文档承诺的是「{word}」"

    vis = README.split("### 三种边的视觉编码")[1].split("\n## ")[0]
    for word in ("N 选 1", "已定", "都不行"):
        assert word in vis, f"README 的视觉编码表里没有「{word}」"
    assert "都不行" in FORMAT, "FORMAT.md 也承诺过这三个字"
    # MCP 那一侧（agent 读到的）用的是同一个词
    assert "都不行" in M.fork_label({"state": "abandoned", "options": ["a", "b"]})


# ============================================================ 接缝 ⑤
# 「颜色可以承载信息」这条放宽 × 「线型归 status、不透明度归祖先链」两条没放宽
#
# 放宽的是手段（颜色），不是目的（不依赖颜色也要读得出来）。而另外两个通道
# 一格都没放宽——借出去一次，「这条边是虚的」就再也说不清是「还在跑」还是
# 「它是个候选」了。


def test_a_conclusion_is_never_expressed_by_dimming_it():
    """`abandoned` 不许用不透明度表达。两条理由，都是硬的：

    (a) 不透明度已经归「和选中有没有关系 / 有没有命中搜索」。借出去之后两者会
        **相乘**（.faded 的 .24 × .72 ＝ .17），于是一枚淡掉的牌子说不清是
        「这一组全废了」还是「它不在你选中的那条链上」；
    (b) P4 明说过「变灰的语义是不相关，不是结论为否」。「都不行」是这个问题的
        答案，不是一块要人回来补的窟窿。
    三档之间的差别由牌子上那几个字承担，那本来就是它的非颜色通道。
    """
    live = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    for m in re.finditer(r"([^{}]*\.f-abandoned[^{}]*)\{([^}]*)\}", live):
        assert "opacity" not in m.group(2), \
            f"{m.group(1).strip()} 又把不透明度借去表达「全废了」：{m.group(2).strip()}"


@needs_node
def test_the_roots_group_annotation_is_not_sliced_off_the_top_of_the_canvas(tmp_path):
    """两条互斥的**开局**（两个根各写一行 `branch: alternative`）也要看得见。

    根节点上面只有 PAD＝24px，而括弧要往上抬 15px、标注还要再占一行——按普通那
    一档摆，标注一半落在 y<0，被滚动容器裁掉（负坐标是滚不到的）。`side` 这个
    分支就是为了这件事存在的，可它以前只完成了一半：换到括弧右边躲开了卡片，
    却仍然居中在 y 上，于是照样被画布上沿切掉半行字。

    这一组在图上**只有**括弧和这枚牌子——根节点没有父边，连那条换了颜色的
    `b-alt` 边都没有。切掉它，两条互斥的开局在图上就完全不留痕迹了。
    """
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "开局")
    s = core.steps_dir_of(tmp_path, "开局")
    W.create_step(s, title="从头训一个", body=BODY, branch="alternative | 数据够多")
    W.create_step(s, title="微调现成的", body=BODY, branch="alternative | 省算力")
    nodes = core.compile_forest(s)["tree"]["nodes"]
    groups = core.compile_forest(s)["branch_groups"]
    assert groups and groups[0]["at"] == "", "两个互斥的根没有成组"

    script = tmp_path / "t.js"
    script.write_text(
        "const U = require(%s);\n"
        "const bk = U.forkBracket(%s, %s, { nw: 176 });\n"
        "if (!bk) { console.log('NO_BRACKET'); process.exit(1); }\n"
        "console.log(JSON.stringify({ y: bk.y, side: bk.side, top: Math.max(bk.y - 8, 0) }));\n"
        % (json.dumps(str(REPO / "web" / "app.js")),
           json.dumps(nodes), json.dumps(groups[0])),
        encoding="utf-8")
    got = json.loads(subprocess.run([NODE, str(script)], capture_output=True,
                                    text=True, check=True).stdout)
    assert got["side"], "根之间那一组没被识别成「上面没地方摆」"
    assert got["y"] >= 0, "括弧本身就画到画布外面去了"

    # 渲染那一侧：side 那一档必须钉**上沿**并夹到 0 以上，居中就等于切掉半行字
    fn = re.search(r"function renderForkLabels\(T, NW\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "Math.max(bk.y - 8, 0)" in fn, "side 那一档又没夹住上边界了"
    css = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    m = re.search(r"\.forklabel\.side \{([^}]*)\}", css)
    assert m and "translate(0, 0)" in m.group(1), \
        f".forklabel.side 又回去居中了：{m.group(1) if m else '规则不见了'}"


def test_the_relation_colours_stand_on_their_own_in_both_themes():
    """两套主题各定义一次 --alt / --join，缺一套就是「深色下看不见」。"""
    live = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    assert live.count("--alt:") >= 2 and live.count("--join:") >= 2, \
        "深浅两套主题里必须各有一份；细线对对比度比色块敏感得多"


# ============================================================ 接缝 ⑥
# trace_server 的路由表 × README 的 API 表
#
# 两条真实存在、真能打的端点挂着 include_in_schema=False 在等 README，
# 而 README 那一轮结束时也没等到。结果是：能力做好了，人和 agent 都不知道有。


def test_the_fork_endpoints_are_public_api_and_documented():
    pytest.importorskip("fastapi")
    import trace_server as S

    app = S.create_app({"data_dir": ".", "space": "", "token": "t", "git": {"enabled": False}})
    rows = {}
    for r in app.routes:
        if getattr(r, "path", "").endswith("/forks"):
            rows[tuple(sorted(m for m in r.methods if m in ("GET", "POST")))] = r
    assert rows, "/forks 两条路由不见了"
    for r in rows.values():
        assert r.include_in_schema, \
            "/forks 还挂着 include_in_schema=False —— 那是在等 README，README 已经写上了"
    tbl = README[README.index("## API（给 agent）"):]
    assert "`/api/p/{项目}/forks`" in tbl, "README 的 API 表里没有 /forks"
    assert tbl.count("`/api/p/{项目}/forks`") >= 2, "GET 和 POST 各要一行"


def test_writing_a_fork_still_needs_a_token_and_reading_it_does_not(tmp_path, monkeypatch):
    """读公开、写要令牌这条边界，新端点一条都不许破。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import trace_server as S

    monkeypatch.setattr(S, "ROOT", tmp_path)
    app = S.create_app({"data_dir": ".", "space": "", "token": "seam-token",
                        "git": {"enabled": False}, "paths": {"enabled": False}})
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    s = core.steps_dir_of(tmp_path, "课题")
    W.create_step(s, title="基线", body=BODY)
    W.create_step(s, parent="001", title="A", body=BODY)
    W.create_step(s, parent="001", title="B", body=BODY)
    with TestClient(app) as c:
        assert c.get("/api/p/课题/forks").status_code == 200
        assert c.post("/api/p/课题/forks", json={"ids": ["002", "002b"]}).status_code == 401
        r = c.post("/api/p/课题/forks", json={"ids": ["002", "002b"], "decision": "A 还是 B？"},
                   headers={"Authorization": "Bearer seam-token"})
        assert r.status_code == 200, r.text
        assert r.json()["group"]["options"] == ["002", "002b"]
    # 落盘的只有每个孩子自己那一行 branch: 和父节点那一行 decision:
    txt = (s / "001_基线" / "note.md").read_text(encoding="utf-8")
    assert "decision: A 还是 B？" in txt
    assert "options:" not in txt and "002b" not in txt, \
        "父节点上写了候选清单 —— 那是双真相源，一次 move_step 就过期"


# ============================================================ 接缝 ⑦
# move_step 的返回值 × 两个门面（CLI 说了，网页没说）
#
# 移动一个候选的直接后果只有这一刻看得见：组是派生的，事后重新拉一遍森林
# 只告诉你「现在是什么样」，不告诉你「刚才那一下改了什么」。


def test_moving_a_candidate_reports_what_happened_to_both_forks(forked):
    info = W.move_step(forked, "002b", None, "其实是独立的一条线")
    alt = info["alternatives"]
    assert alt["left"]["at"] == "001" and alt["left"]["options"] == ["002"]
    assert alt["joined"]["at"] == "" and alt["joined"]["options"] == ["002b"]


def test_both_the_cli_and_the_web_speak_that_consequence(forked, tmp_path, monkeypatch):
    """CLI 早就说了，网页把整个 info 扔掉了 —— 同一个信号只有一半的人看得见。"""
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "load_config", lambda: {"data_dir": str(tmp_path)})
    out = cli_out(C.cmd_mv, id="002b", parent="", reason="其实是独立的一条线",
                  by="human", date="")
    assert "001" in out and "只有一个候选" in out

    mv = re.search(r"function submitMove\(\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "movedForks" in mv, "网页移动成功之后又把 alternatives 扔掉了"
    fn = re.search(r"function movedForks\(info\)[\s\S]*?\n  \}\n", APP).group(0)
    assert "alternatives" in fn and "toast.moved.fork" in fn
    # 根之间那一组没有分叉点 id 可以说，得有自己的一句
    assert "toast.moved.fork.roots" in fn
    assert I18N.count('"toast.moved.fork"') == 2 and I18N.count('"toast.moved.fork.roots"') == 2


# ============================================================ 接缝 ⑧
# 端到端：MCP 写 → 落盘 → core 派生 → REST → 静态导出
#
# 历史上反复出现的断点全在这条链上：新字段在 create_step 加了但门面的白名单
# 没加、handler 收了没往下传、core 有字段但 to_dict 没输出、网页读了服务端没给。


def test_branch_and_decision_survive_the_whole_pipeline(tmp_path):
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    be = M.LocalBackend(tmp_path)
    M.t_new_step(be, {"project": "课题", "title": "基线", "status": "done", "body": BODY,
                      "decision": "类别不平衡怎么处理？只能选一条走下去"})
    M.t_new_step(be, {"project": "课题", "parent": "001", "title": "A", "body": BODY,
                      "branch": "alternative | 先试最便宜的"})
    M.t_new_step(be, {"project": "课题", "parent": "001", "title": "B", "body": BODY,
                      "branch": "alternative | 改动大但可能更稳"})
    s = core.steps_dir_of(tmp_path, "课题")

    # ① 落盘：G4 —— 删掉全部程序，grep 仍然答得出
    disk = "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(s.rglob("note.md")))
    assert "decision: 类别不平衡怎么处理？只能选一条走下去" in disk
    assert "branch: alternative | 先试最便宜的" in disk
    # 默认值不落盘：164 条老记录下一次编辑不该集体多一行 diff
    assert "branch: extends" not in disk

    # ② core：组是扫出来的，父节点上一个候选清单都没有
    f = core.compile_forest(s)
    assert f["branch_groups"] == [{
        "at": "001", "decision": "类别不平衡怎么处理？只能选一条走下去",
        "options": ["002", "002b"], "live": ["002", "002b"],
        "state": "open", "chosen": "",
    }]

    # ③ 每个 step 上那几个键恒在（静态导出要逐字节确定，键的有无不能随内容变）
    for st in f["steps"]:
        for k in ("branch", "branch_note", "decision", "fork", "merge_in", "merge_out"):
            assert k in st, f"{st['id']} 上少了 {k}"

    # ④ 逐字节确定：连编两次完全相同
    assert json.dumps(core.compile_forest(s), ensure_ascii=False) == \
        json.dumps(core.compile_forest(s), ensure_ascii=False)


def test_the_static_export_carries_the_forks_and_the_rejoins(tmp_path, monkeypatch):
    """`file://` 断网打开的那一份必须自带这些派生结果——它没有服务端可问。"""
    core.ensure_layout(tmp_path)
    W.create_project(tmp_path, "课题")
    s = core.steps_dir_of(tmp_path, "课题")
    W.create_step(s, title="基线", body=BODY, status="done", decision="A 还是 B？")
    W.create_step(s, parent="001", title="A", body=BODY, branch="alternative")
    W.create_step(s, parent="001", title="B", body=BODY, branch="alternative", status="dead")
    W.create_step(s, parent="002b", title="产出", body=BODY, status="done")
    W.create_step(s, parent="002", title="用了", body=BODY, status="done",
                  inputs=["003 | scores.csv"])
    out = tmp_path / "dist"
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "load_config", lambda: {"data_dir": str(tmp_path)})

    class A:
        out = None
        project = None
    A.out = str(out)
    with redirect_stdout(io.StringIO()):
        C.cmd_build(A())
    page = next(out.rglob("index.html"), None)
    pages = [p for p in out.rglob("index.html") if "branch_groups" in p.read_text(encoding="utf-8")]
    assert pages, "导出的页面里没有内联 branch_groups —— 断网打开就没有括弧"
    txt = pages[0].read_text(encoding="utf-8")
    assert '"merges"' in txt and '"at":"001"' in txt
    assert not re.search(r'(?:src|href)="https?://', txt), "静态导出引了外部资源"
    assert page is not None
