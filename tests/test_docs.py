"""文档 ↔ 实现的机械核对。

这个项目反复出现的问题不是代码错，是**文档说的和代码做的对不上**：
FORMAT.md 承诺「check 和网页会给出等级」而两边都没接线、README 说「6 个 MCP 工具」
而实际是 9 个、SKILL.md 的端点表漏掉真实存在的端点、deploy/README 的安装命令
按原样粘贴会直接失败。这些都不是靠人再读一遍能长期守住的。

所以这里只钉**能机械核对的那部分**：文档里写的端点/子命令/参数/字段名/小节名
必须真实存在，代码里新长出来的公开端点必须被文档收录。
刻意不用脆弱的正则去校对散文——宁可少几条，每条都稳。
判据都来自可执行的真相（FastAPI 的路由表、argparse 的解析器、trace_mcp.TOOLS、
trace_core/trace_write 的常量、以及真跑一次写入产出的文件），不是照抄文档。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

import trace_core as core
import trace_mcp as M
import trace_write as W

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
FORMAT = ROOT / "FORMAT.md"
SKILL = ROOT / "skills" / "research-trace" / "SKILL.md"
DEPLOY = ROOT / "deploy" / "README.md"

DOCS = (README, FORMAT, SKILL, DEPLOY)


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def where(p: Path) -> str:
    """报错里用仓库相对路径——两个 README.md 只看文件名分不出是哪个。"""
    return p.relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- 端点
# 真相是 FastAPI 的路由表，不是文档。方向两条都要走：
#   文档 → 代码：写在表里的端点必须真的存在（否则 agent 照着调会 404）
#   代码 → 文档：公开 API 必须写进 README（否则新端点没人知道，比如跨项目搜索）

METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")
# 表格行的形状：| GET | `/api/...` | …
TABLE_ROW = re.compile(r"^\|\s*(" + "|".join(METHODS) + r")\s*\|\s*`([^`]+)`")
# 路径参数的名字是实现细节（{项目} vs {project} vs {sid:path}），比对时抹平
PARAM = re.compile(r"\{[^}]*\}")


def slot(path: str) -> str:
    """把一条路径归一成可比对的形状：去掉 base 前缀、查询串、参数名。"""
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"^.*?(/api/)", r"\1", path)      # 扔掉 https://域名/t/<space> 之类的前缀
    path = PARAM.sub("{}", path).rstrip("/")
    return path or "/"


@pytest.fixture(scope="module")
def routes() -> dict[str, set[str]]:
    """{归一化路径: {方法}}，只含 /api/ 命名空间。"""
    pytest.importorskip("fastapi")
    import trace_server as S

    app = S.create_app({"data_dir": ".", "space": "SP", "token": "t", "git": {"enabled": False}})
    out: dict[str, set[str]] = {}
    for r in app.routes:
        p, methods = getattr(r, "path", ""), getattr(r, "methods", None)
        if "/api/" not in p or not methods:
            continue
        out.setdefault(slot(p), set()).update(methods)
    assert out, "一条 /api/ 路由都没扫到，说明这个 fixture 本身坏了"
    return out


@pytest.fixture(scope="module")
def public_api(routes) -> dict[str, set[str]]:
    """include_in_schema=True 的那些——「对外承诺的 API」，必须在 README 里有。"""
    pytest.importorskip("fastapi")
    import trace_server as S

    app = S.create_app({"data_dir": ".", "space": "SP", "token": "t", "git": {"enabled": False}})
    out: dict[str, set[str]] = {}
    for r in app.routes:
        if "/api/" not in getattr(r, "path", "") or not getattr(r, "methods", None):
            continue
        if not getattr(r, "include_in_schema", True):
            continue
        out.setdefault(slot(r.path), set()).update(r.methods)
    return out


def documented_rows(doc: Path) -> list[tuple[str, str]]:
    return [(m.group(1), slot(m.group(2)))
            for line in text(doc).split("\n")
            if (m := TABLE_ROW.match(line))]


@pytest.mark.parametrize("doc", [README, SKILL], ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_endpoint_in_a_doc_table_really_exists(doc, routes):
    """防的是：文档写了一个端点，服务端根本没有——agent 照着调直接 404。"""
    rows = documented_rows(doc)
    assert len(rows) >= 10, f"{where(doc)} 的端点表没解析出几行，正则和表格形状对不上了"
    for method, path in rows:
        assert path in routes, f"{where(doc)} 写了 {method} {path}，服务端没有这条路由"
        assert method in routes[path], \
            f"{where(doc)} 写了 {method} {path}，实际只支持 {sorted(routes[path])}"


def test_every_public_api_route_is_documented_in_the_readme(public_api):
    """反方向：服务端新长出来的公开端点必须写进 README。

    跨项目搜索、git 同步状态这两个端点就是这么漏掉的——代码里加好了，
    人和 agent 都不知道有。include_in_schema=False 的（页面、静态资源、
    附件下载、SSE）不算公开 API，不在这条里要求。
    """
    documented = {(m, p) for m, p in documented_rows(README)}
    missing = {(m, p) for p, ms in public_api.items() for m in ms if (m, p) not in documented}
    assert not missing, f"README 的 API 表里少了: {sorted(missing)}"


def test_every_curl_in_the_deploy_guide_hits_a_real_endpoint(routes):
    """部署文档的验证清单是照着粘的，里面的 URL 必须真的存在。"""
    urls = []
    for line in text(DEPLOY).split("\n"):
        if "curl" not in line:
            continue
        for m in re.finditer(r"/api/[^\s\"'`]*", line):
            method = "POST" if re.search(r"-X\s+POST", line) else "GET"
            urls.append((method, slot(m.group(0))))
    assert urls, "deploy/README 里一条 curl 都没扫到"
    for method, path in urls:
        assert path in routes and method in routes[path], \
            f"deploy/README 的 curl 打的是 {method} {path}，服务端没有"


def test_the_two_unauthenticated_probes_in_the_deploy_guide_exist(routes):
    """/healthz 和 /robots.txt 不在 /api/ 命名空间下，单独钉一次。"""
    pytest.importorskip("fastapi")
    import trace_server as S

    app = S.create_app({"data_dir": ".", "space": "SP", "token": "t", "git": {"enabled": False}})
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/healthz" in paths and "/healthz" in text(DEPLOY)
    assert "/robots.txt" in paths


# ---------------------------------------------------------------- CLI
# 真相是 argparse 的解析器。文档里出现的每个 `trace_cli.py <子命令> <参数>`
# 都必须能被它接住 —— 否则用户照着粘一条命令，拿到的是 "unrecognized arguments"。


def cli_parsers() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """把 trace_cli.main 里那个 parser 抓出来（它是在函数内部现建的）。"""
    import trace_cli

    grabbed: list[argparse.ArgumentParser] = []
    real = argparse.ArgumentParser.parse_args

    class Stop(Exception):
        pass

    def spy(self, args=None, namespace=None):
        grabbed.append(self)
        raise Stop

    argparse.ArgumentParser.parse_args = spy
    try:
        trace_cli.main([])
    except Stop:
        pass
    finally:
        argparse.ArgumentParser.parse_args = real

    assert grabbed, "没抓到 trace_cli 的 parser（main 的结构变了？）"
    top = grabbed[0]
    subs: dict[str, argparse.ArgumentParser] = {}
    for a in top._actions:
        if isinstance(a, argparse._SubParsersAction):
            subs.update(a.choices)
    assert subs, "trace_cli 没有子命令了？"
    return top, subs


def cli_invocations() -> list[tuple[Path, str, list[str]]]:
    """扫出文档里所有 `trace_cli.py <子命令> …`，返回 (文件, 子命令, 该行后面的开关)。"""
    out = []
    for doc in DOCS:
        raw = text(doc)
        # shell 的续行：先把 `\` 结尾的行接起来，否则 deploy/README 里那条
        # 分了四行的 init 命令会被看成「init 没带任何参数」。
        joined = re.sub(r"\\\n\s*", " ", raw)
        for line in joined.split("\n"):
            m = re.search(r"trace_cli\.py\s+([a-z][a-z-]*)", line)
            if not m:
                continue
            flags = re.findall(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9-]*)", line[m.end():])
            out.append((doc, m.group(1), flags))
    return out


def test_every_cli_subcommand_named_in_the_docs_exists():
    """防的是：文档教了一条子命令，argparse 里根本没有。"""
    _top, subs = cli_parsers()
    calls = cli_invocations()
    assert len(calls) >= 8, "文档里的 trace_cli.py 命令没扫到几条，扫描器本身可疑"
    for doc, sub, _flags in calls:
        assert sub in subs, f"{where(doc)} 教了 `trace_cli.py {sub}`，但它不是子命令"


def test_every_cli_flag_named_in_the_docs_exists():
    """防的是：文档里的开关拼错或者已经被改名——照着粘会 unrecognized arguments。"""
    _top, subs = cli_parsers()
    for doc, sub, flags in cli_invocations():
        allowed = {o for a in subs[sub]._actions for o in a.option_strings}
        for f in flags:
            assert f in allowed, \
                f"{where(doc)} 的 `trace_cli.py {sub}` 用了 {f}，它接受的是 {sorted(allowed)}"


def test_the_node_test_command_in_the_readme_actually_runs_the_js_tests():
    """README 里那条 `node --test …` 原来写的是 `tests/`，在这台机器上直接
    「Cannot find module …\\tests」——照着粘的人会以为前端测试全挂了。
    这条命令是文档里唯一一条能当场执行验证的，所以就执行它。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("没装 node")
    m = re.search(r"^(node --test [^\n#]+?)\s{2,}#", text(README), re.M)
    assert m, "README 里找不到 node --test 那条命令"
    p = subprocess.run(m.group(1), shell=True, cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    assert p.returncode == 0, f"README 的 `{m.group(1)}` 跑不通:\n{(p.stdout + p.stderr)[-1500:]}"
    assert re.search(r"^# pass (\d+)", p.stdout, re.M), "没跑出任何断言，命令选不中测试文件"


def test_the_selfcheck_switches_in_the_readme_exist():
    """README 教用户把 /plugin 里的值抄进 --selfcheck 重跑，那几个开关必须真的认。"""
    allowed = {"--selfcheck", "--version"} | set(M.SELFCHECK_FLAGS)
    for line in re.sub(r"\\\n\s*", " ", text(README)).split("\n"):
        m = re.search(r"trace_mcp\.py\s", line)
        if not m:
            continue
        for f in re.findall(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]*)", line[m.end():]):
            assert f in allowed, f"README 的 trace_mcp.py 用了 {f}，它只认 {sorted(allowed)}"


# ---------------------------------------------------------------- MCP 工具
# 工具数和工具名都被插件清单对外宣称过，文档漏一个 agent 就永远不会调它。


def tool_names_in(doc: Path) -> set[str]:
    """文档里那张工具表的第一列。"""
    return {m.group(1) for line in text(doc).split("\n")
            if (m := re.match(r"^\|\s*`(trace_[a-z_]+)`\s*\|", line))}


@pytest.mark.parametrize("doc", [README, SKILL], ids=lambda p: p.relative_to(ROOT).as_posix())
def test_the_tool_table_lists_exactly_the_tools_that_exist(doc):
    """SKILL.md 原来只列了 6 个，trace_insight / trace_new_project / trace_delete_step
    这三个 agent 从没被告知过 —— 于是「项目洞察」这个需求在 agent 侧等于不存在。"""
    actual = {t["name"] for t in M.TOOLS}
    assert tool_names_in(doc) == actual, f"{where(doc)} 的工具表和 trace_mcp.TOOLS 对不上"


def test_the_readme_states_the_real_number_of_tools():
    n = len(M.TOOLS)
    assert f"{n} 个 MCP 工具" in text(README), f"README 该说 {n} 个 MCP 工具"


# 反引号里的 `trace_xxx`。模块名（trace_core / trace_mcp.py …）不在此列——
# 它们在文档里一律带 `.py` 或者出现在目录树的代码块里，不会被这条正则捞到。
TOOL_MENTION = re.compile(r"`(trace_[a-z_]+)`")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_doc_names_an_mcp_tool_that_does_not_exist(doc):
    """比工具表那条更宽：正文散文里点名的工具也必须真的存在。

    表里的名字有 test_the_tool_table_lists_exactly_the_tools_that_exist 盯着，
    但「补翻译走 `trace_translate`」这种话是写在散文里的——工具还没落地时
    先把用法写进 SKILL.md，agent 会照着调一个不存在的工具，拿到的是
    「unknown tool」，而它没有任何理由怀疑文档。
    """
    actual = {t["name"] for t in M.TOOLS} | {"trace_core", "trace_write", "trace_mcp",
                                             "trace_server", "trace_git", "trace_cli"}
    for m in TOOL_MENTION.finditer(text(doc)):
        assert m.group(1) in actual, \
            f"{where(doc)} 点名了 `{m.group(1)}`，trace_mcp.TOOLS 里没有这个工具"


# ---------------------------------------------------------------- 小节名与字段名
# 这些是**精确匹配**的字符串：_append_under 按标题找小节，sections() 按标题切正文。
# 文档写错一个字，手写出来的文件就和工具写的对不上，而且不会报任何错。


def headings_in_fence(doc: Path, marker: str) -> set[str]:
    """取出文档里某个示例代码块内的所有 `## 标题`。marker 是块里必然出现的一行。"""
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text(doc), re.S)
    for b in blocks:
        if marker in b:
            return {m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", b, re.M)}
    raise AssertionError(f"{where(doc)} 里找不到含 {marker!r} 的示例块")


def test_format_md_shows_exactly_the_five_body_sections():
    """FORMAT.md 第 2 节那个骨架必须和 trace_core.SECTIONS 逐字一致——
    traceability() 就是按这几个名字去正文里找内容的，差一个字就判成「没写」。"""
    assert headings_in_fence(FORMAT, "id: 007") == set(core.SECTIONS), \
        f"FORMAT.md 第 2 节的骨架和 trace_core.SECTIONS={list(core.SECTIONS)} 对不上"


def test_format_md_shows_exactly_the_project_note_sections(tmp_path):
    """project.md 的小节名不是约定俗成，是 _append_under 的精确匹配键。

    判据不照抄常量，而是**真跑一遍**：建项目 → 四种洞察各记一条 → 删一步，
    磁盘上产出什么标题，FORMAT.md 的示例就必须是什么标题。
    """
    p = W.create_project(tmp_path, "机械核对用的项目")
    sd = core.steps_dir_of(tmp_path, p.slug)
    for kind in W.INSIGHT_SECTIONS:
        W.update_project(tmp_path, p.slug, add=(kind, f"{kind} 的一条"))
    step, _ = W.create_step(sd, title="待删的", body="x")
    W.delete_step(sd, step.id, "机械核对", by="test")

    produced = set(re.findall(
        r"^##\s+(.+?)\s*$",
        (core.project_dir(tmp_path, p.slug) / core.PROJECT_NOTE).read_text(encoding="utf-8"),
        re.M))
    assert produced == headings_in_fence(FORMAT, "name: 我的课题"), \
        "FORMAT.md 第 11 节的 project.md 示例和实际写出来的小节对不上"


def insight_rows(doc: Path) -> dict[str, str]:
    """文档里「小节标题 → trace_insight 的 kind」那张表。"""
    out = {}
    for line in text(doc).split("\n"):
        m = re.match(r"^\|\s*`(?:##\s*)?([^`]+)`\s*\|\s*`([a-z]+)`\s*\|", line)
        if m:
            out[m.group(1).strip()] = m.group(2)
    return out


@pytest.mark.parametrize("doc", [FORMAT, SKILL], ids=lambda p: p.relative_to(ROOT).as_posix())
def test_the_insight_kind_table_matches_the_code(doc):
    """kind 写错（works/work、fails/fail）→ trace_insight 直接报错。"""
    assert insight_rows(doc) == {v: k for k, v in W.INSIGHT_SECTIONS.items()}, \
        f"{where(doc)} 的洞察小节 ↔ kind 对照表和 trace_write.INSIGHT_SECTIONS 对不上"


def test_format_md_lists_every_front_matter_key_render_note_can_emit(tmp_path):
    """判据是 render_note 真写出来的键，不是我照着代码抄的一份清单。

    `lang` 是这么漏掉的：双语上线时 render_note 学会了回写它，而第 2 节的键表
    还是十一行——照文档写的人不知道自己可以声明正文是什么语言。所以构造的
    Step 必须把**每一个**可选键都填上，包括 lang、branch、decision。
    """
    step = core.Step(id="001", parent="000", status="done", title="t", lang="zh",
                     branch="alternative", branch_note="先试最便宜的那条",
                     decision="类别不平衡怎么处理？",
                     date="2026-01-01",
                     commit="c", author="a", key="k", tags=["x"],
                     paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                     repro=[{"state": "verified", "date": "d", "by": "b", "note": "n"}],
                     body="", dirname="001_t")
    emitted = {line.split(":", 1)[0]
               for line in W.render_note(step).split("\n---", 1)[0].split("\n")
               if ":" in line}
    documented = {m.group(1) for m in re.finditer(r"^\|\s*`([a-z_]+)`\s*\|", text(FORMAT), re.M)}
    assert emitted <= documented, f"FORMAT.md 的键表少了: {sorted(emitted - documented)}"


def note_examples(doc: Path) -> list[str]:
    """文档里那些「这就是一份 note.md」的示例块。"""
    return [b for b in re.findall(r"```[a-z]*\n(.*?)```", text(doc), re.S)
            if b.startswith("---\nid:")]


@pytest.mark.parametrize("doc", [README, FORMAT], ids=lambda p: p.relative_to(ROOT).as_posix())
def test_the_note_examples_in_the_docs_parse_clean(doc):
    """示例是拿来抄的，所以直接喂给真正的解析器，一条警告都不许有。

    比「文档里别出现不存在的状态名」这种词表检查扎实得多：键名拼错、
    status 写成旧的 `success`、少一个闭合的 `---`、front-matter 里塞了
    没有冒号的行，全都会当场变成 warning 被抓住。
    """
    examples = note_examples(doc)
    assert examples, f"{where(doc)} 里找不到 note.md 的示例块"
    for raw in examples:
        meta, body, warns = core.parse_note(raw)
        step, more = core.build_step(f"{meta.get('id', '')}_例子", meta, body)
        assert not warns + more, f"{where(doc)} 的 note.md 示例解析出警告: {warns + more}"
        assert step.status in core.STATUSES
        assert set(core.sections(step.body)) <= set(core.SECTIONS), \
            f"{where(doc)} 的示例正文里有 SECTIONS 之外的小节"


REPRO_IN_DOC = (
    re.compile(r"^\s*repro:\s*(\S.*?)\s*$", re.M),          # front-matter 里的原样写法
    re.compile(r'"add_repro":\s*"([^"]+)"'),                # REST 的请求体
    re.compile(r'repro="([^"]+)"'),                         # MCP 的参数
)


def test_every_repro_example_in_the_docs_is_accepted_by_the_parser():
    """`repro:` 的状态是闭集合（failed / runnable / verified），别的词一律 WriteError。
    文档里写一个不存在的状态，照抄的人拿到的是 400。"""
    seen = 0
    for doc in DOCS:
        body = text(doc)
        for pat in REPRO_IN_DOC:
            for m in pat.finditer(body):
                seen += 1
                W.norm_repro(m.group(1))       # 不合法直接抛 WriteError
    assert seen >= 4, f"只扫到 {seen} 条 repro 示例，扫描器可疑"


# ------------------------------------------------- front-matter 的四个结构化键
# `path` / `input` / `code` / `moved` 的值都是**竖线分段**的行式格式，判定规则细到
# 「整段全是 k=v 才算属性」。文档里每一条示例都是拿来整块复制的，所以直接喂给真解析器，
# 而不是靠人再读一遍规则。判据全部来自 trace_core 的解析器和 trace_write 的校验器。


def front_matter_lines(key: str) -> list[tuple[Path, str]]:
    """扫出所有文档里 `<key>: …` 的原样写法。

    以 `<` 开头的跳过：那是**语法模板**（`code: <kind> | <位置> | <k=v …>`），
    不是可以照抄的示例。模板和示例混在一起检查，只会逼着文档不敢写语法说明。
    """
    pat = re.compile(r"^\s*%s:\s*(\S.*?)\s*$" % key, re.M)
    return [(doc, m.group(1)) for doc in DOCS for m in pat.finditer(text(doc))
            if not m.group(1).startswith("<")]


def test_every_path_example_in_the_docs_round_trips_through_the_parser():
    """`path:` 的示例必须：解析得出位置、被写入侧的校验器接受、并且**回写后逐字不变**。

    回写这一步是要紧的：任何一次无关编辑（在网页上改个标题）都会让写入侧用
    format_path 重新拼这一行。拼出来和原来不一样，就意味着文档教的写法一过工具
    就会被改写——role 掉了、校验和掉了，而且一声不吭。
    """
    seen = 0
    for doc, raw in front_matter_lines("path"):
        got = core.parse_paths(raw)
        assert got, f"{where(doc)} 的 `path: {raw}` 解析不出任何东西"
        p = got[0]
        assert p["location"], f"{where(doc)} 的 `path: {raw}` 没有位置"
        assert core.format_path(p) == raw, (
            f"{where(doc)} 的 path 回写后变了样:\n  原文: {raw}\n  回写: {core.format_path(p)}")
        # 写入侧的值域校验（size/n 要整数、日期要 YYYY-MM-DD、属性值不含空白与竖线）
        assert W.norm_paths(raw)[0]["location"] == p["location"]
        seen += 1
    assert seen >= 8, f"只扫到 {seen} 条 path 示例，扫描器可疑"


def test_the_documented_path_roles_are_exactly_the_ones_the_parser_accepts():
    """判定规则那句话里的四个词就是 core.PATH_ROLES 本身。

    多写一个（比如 `log`），照着写的人得到的是一段被并进「说明」的文字——一个字没丢，
    但它永远不会是 role，而且不会有任何警告。少写一个则相反：有效的写法没人知道。
    顺序也要对上，那张表就是照着常量列的。
    """
    pat = re.compile(r"恰好\*\*是\s*((?:`[a-z]+`\s*/?\s*)+)之一", re.S)
    for doc in (FORMAT, README, SKILL):
        runs = pat.findall(text(doc))
        assert runs, f"{where(doc)} 里找不到 path 的 role 判定规则那句话"
        for run in runs:
            assert tuple(re.findall(r"`([a-z]+)`", run)) == core.PATH_ROLES, \
                f"{where(doc)} 列的 role 和 core.PATH_ROLES={list(core.PATH_ROLES)} 对不上"


def test_the_known_path_attributes_in_format_md_are_the_ones_the_writer_knows():
    """已知属性表的第一列 = trace_write.PATH_ATTR_ORDER。

    文档多列一个（`bytes`）→ 照着写的属性谁也不认识，只会被原样留着；
    少列一个 → 那个属性没人会用。两个方向都只有逐字比对挡得住。
    """
    listed = table_first_column(format_numbered_section("东西在哪"), "| 属性 |")
    assert set(listed) == set(W.PATH_ATTR_ORDER), \
        f"FORMAT.md 列的属性是 {sorted(listed)}，trace_write.PATH_ATTR_ORDER 是 {sorted(W.PATH_ATTR_ORDER)}"


def test_every_code_example_in_the_docs_parses_and_actually_locates_the_code():
    """`code:` 是 L2 的新判据，所以文档里的每条示例都必须真的**定位得到代码**。

    一条 `code: snapshot | | manifest=…`（漏了目录）看着像模像样，照抄的人却拿不到 L2
    ——而 L2 恰恰是这个键存在的全部理由。
    """
    seen = 0
    for doc, raw in front_matter_lines("code"):
        got = core.parse_code(raw)
        assert got, f"{where(doc)} 的 `code: {raw}` 解析不出任何东西"
        rec = got[0]
        assert rec["kind"] in core.CODE_KINDS, \
            f"{where(doc)} 的 code kind {rec['kind']!r} 不在 {list(core.CODE_KINDS)} 里"
        assert core.format_code(rec) == raw, \
            f"{where(doc)} 的 code 回写后变了样: {raw!r} → {core.format_code(rec)!r}"
        step = core.Step(id="001", code=[rec], dirname="001_x")
        assert core.code_located(step), \
            f"{where(doc)} 的 `code: {raw}` 定位不到代码，照抄的人到不了 L2"
        seen += 1
    assert seen >= 3, f"只扫到 {seen} 条 code 示例，三种 kind 至少各要有一条"


def table_first_column(section: str, header: str) -> list[str]:
    """取出某张表第一列里的反引号词。`header` 是那张表表头的开头（如 `| kind |`）。"""
    start = section.find(header)
    assert start != -1, f"找不到表头 {header!r} 的那张表"
    out: list[str] = []
    for line in section[start:].split("\n")[2:]:      # 跳过表头和分隔行
        if not line.startswith("|"):
            break
        out += re.findall(r"`([a-z0-9]+)`", line.split("|")[1])
    return out


def test_the_documented_code_kinds_are_exactly_the_ones_the_parser_knows():
    """`kind` 是闭词表：写一个表外的词，`code_located` 只会退回「记了位置就算」，
    于是文档教出来的那种写法在 L2 判据上和作者以为的不是一回事。"""
    listed = table_first_column(format_numbered_section("东西在哪"), "| kind |")
    assert tuple(listed) == core.CODE_KINDS, \
        f"FORMAT.md 第 7 节的 kind 表是 {listed}，core.CODE_KINDS 是 {list(core.CODE_KINDS)}"


def test_the_documented_path_roles_table_matches_the_constant():
    """判定规则那句话之外，第 7.2 节还有一张逐个解释 role 的表——两处都得对。"""
    listed = table_first_column(format_numbered_section("东西在哪"), "| role |")
    assert tuple(listed) == core.PATH_ROLES, \
        f"FORMAT.md 第 7.2 节的 role 表是 {listed}，core.PATH_ROLES 是 {list(core.PATH_ROLES)}"


def test_every_input_example_in_the_docs_parses_into_a_step_and_a_note():
    """`input:` 的右半边（消费的是哪份产物）是这个键一半的价值——写成
    `input: 013` 而不写文件名，半年后还是得靠猜。所以文档的示例必须两边都有。"""
    seen = 0
    for doc, raw in front_matter_lines("input"):
        got = core.parse_inputs(raw)
        assert got and got[0]["step"], f"{where(doc)} 的 `input: {raw}` 没解析出步骤 id"
        assert got[0]["note"], \
            f"{where(doc)} 的 `input: {raw}` 没写清消费的是哪份产物，示例不该这么教"
        assert core.format_input(got[0]) == raw
        seen += 1
    assert seen >= 3, f"只扫到 {seen} 条 input 示例，扫描器可疑"


def test_every_moved_example_in_the_docs_is_a_complete_audit_record():
    """`moved:` 的五段里，**原因**是唯一无法自动生成的那一段（reason 空的话
    move_step 直接拒绝）。文档示例漏写原因，等于在教一种服务端会 400 的写法。"""
    seen = 0
    for doc, raw in front_matter_lines("moved"):
        got = core.parse_moved(raw)
        assert got, f"{where(doc)} 的 `moved: {raw}` 解析不出来"
        m = got[0]
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", m["date"]), \
            f"{where(doc)} 的 moved 日期不是 YYYY-MM-DD: {m['date']!r}"
        assert m["reason"], f"{where(doc)} 的 moved 示例没写原因，而写入侧要求必填"
        assert m["from"] != m["to"], f"{where(doc)} 的 moved 示例是一次空操作"
        assert core.format_moved(m) == raw
        seen += 1
    assert seen >= 2, f"只扫到 {seen} 条 moved 示例，扫描器可疑"


def test_the_docs_list_exactly_the_repeatable_front_matter_keys():
    """「可以重复多行」的键是闭集合（core.MULTI_KEYS）。文档漏一个，那个键写两行
    就会被后写的覆盖掉前一行——而且不报错，只是安静地丢一半。"""
    m = re.search(r"可以重复的键恰好是这五个：((?:\s*`[a-z]+`)+)", text(FORMAT))
    assert m, "FORMAT.md 第 2 节里找不到「可以重复的键」那句话"
    assert tuple(re.findall(r"`([a-z]+)`", m.group(1))) == core.MULTI_KEYS, \
        f"FORMAT.md 列的是 {re.findall(r'`([a-z]+)`', m.group(1))}，core.MULTI_KEYS 是 {list(core.MULTI_KEYS)}"


def test_the_front_matter_key_order_in_format_md_is_the_order_render_note_emits():
    """FORMAT.md 第 2 节承诺「写回文件时键的顺序是固定的」并把顺序印了出来。

    这句话是有代价的承诺：静态导出要逐字节确定，而人照着这个顺序手写的文件
    过一次工具之后不该被重排得面目全非。所以判据是 render_note 真写出来的顺序，
    不是照着代码抄的一份清单。

    `branch` / `decision` 必须**在这里也填上**：它们不是每条记录都有的键，构造的 Step
    漏填哪一个，这条测试就对那一个键的位置一言不发——`lang` 当年就是这么在键表里
    漏了一年的。这两个尤其容易错位：`branch` 修饰上面那行 `parent`、`decision` 修饰
    上面那行 `title`，写反了文件仍然合法，只是读起来不再是一句话。
    """
    step = core.Step(id="001", parent="000", status="done", title="t", lang="zh",
                     date="2026-01-01", commit="c", author="a", key="k", tags=["x"],
                     branch="alternative", branch_note="先试最便宜的那条",
                     decision="类别不平衡怎么处理？",
                     # `pipeline` 和 branch / decision 同一个理由要在这里填上：
                     # 断言是**相等**不是包含，漏填哪个键，这条测试就对那个键的
                     # 位置一言不发，而它恰好夹在 decision 和 lang 之间——排错了
                     # 文件仍然合法，只是「人的判断」和「机器记录」两区混在一起。
                     pipeline="exclude", pipeline_note="探索性的，没进最终流程",
                     moved=[{"date": "2026-01-02", "from": "000", "to": "001b",
                             "by": "human", "reason": "r"}],
                     inputs=[{"step": "000", "note": "a.csv"}],
                     paths=[{"location": "/blue/x", "note": "n", "kind": "hpc",
                             "role": "output", "attrs": {}}],
                     code=[{"kind": "snapshot", "location": "/o/s", "attrs": {}, "note": ""}],
                     repro=[{"state": "verified", "date": "d", "by": "b", "note": "n"}],
                     body="", dirname="001_t")
    emitted = [line.split(":", 1)[0]
               for line in W.render_note(step).split("\n---", 1)[0].split("\n")
               if ":" in line]
    m = re.search(r"写回文件时键的顺序是固定的（((?:[^）]|\n)+)）", text(FORMAT))
    assert m, "FORMAT.md 第 2 节里找不到那句「键的顺序是固定的」"
    assert re.findall(r"`([a-z]+)`", m.group(1)) == emitted, \
        f"FORMAT.md 写的顺序和 render_note 实际写出来的 {emitted} 对不上"


# ------------------------------------------------- 洞察的 id 与取代


def test_the_project_note_example_in_format_md_carries_a_real_supersede_edge():
    """第 11 节讲 id 与取代，示例里那条 `· 取代 pN` 必须真能被解析器读回来。

    这一段最容易写成一句读不回来的话：取代词、`·`、id 的形状任何一样对不上，
    解析出来的就只是一行普通文字——文档看着讲得很清楚，照着写的人却得不到任何效果。
    「被取代」还必须是**派生**的：磁盘上只有取代者身上写着那半句。
    """
    block = next(b for b in re.findall(r"```markdown\n(.*?)```", text(FORMAT), re.S)
                 if b.startswith("---\nname:"))
    insights = core.parse_insights(block)
    items = [it for rows in insights.values() for it in rows]
    assert items, "FORMAT.md 第 11 节的示例里一条洞察都没解析出来"
    sup = [it for it in items if it["supersedes"]]
    assert sup, "示例里没有一条带 `· 取代 pN` —— 那一段规矩就没有可抄的样例"
    target = sup[0]["supersedes"][0]
    old = next((it for it in items if it["id"] == target), None)
    assert old is not None, f"示例里的 `取代 {target}` 指向一条不存在的洞察"
    assert old["superseded_by"] == [sup[0]["id"]], \
        "「被谁取代」没有被派生出来（它只能从取代者身上反推，磁盘上不该有第二份）"
    assert core.SUPERSEDE_NAMES["zh"] not in old["raw"], \
        "被取代的那一行上也写了取代词 —— 双真相源回来了"
    ids = [it["id"] for it in items if it["id"]]
    assert len(ids) == len(set(ids)), "示例里的洞察 id 有重复（id 在整个 project.md 内唯一）"
    for iid in ids:
        assert re.fullmatch(core.INSIGHT_ID_RE, iid), f"示例里的 id {iid!r} 形状不合法"


def test_the_insight_id_shape_in_format_md_is_the_real_one():
    """和翻译文件名那条同一个理由：文档里抄一份「差不多的」正则，照着造出来的 id
    写得进去、读不回来，而这条洞察从此没有把手。"""
    assert core.INSIGHT_ID_RE in text(FORMAT), \
        f"FORMAT.md 里没有 core.INSIGHT_ID_RE 的原文: {core.INSIGHT_ID_RE}"


@pytest.mark.parametrize("lang", sorted(core.SUPERSEDE_NAMES))
def test_both_supersede_words_are_spelled_out_in_format_md(lang):
    """中英两套取代词都要写出来——译文里写错一个词，那条取代关系就不存在了。"""
    assert core.SUPERSEDE_NAMES[lang] in text(FORMAT), \
        f"FORMAT.md 没写出 {lang} 的取代词 {core.SUPERSEDE_NAMES[lang]!r}"


# ------------------------------------------------- 两处解析器修正的承诺


def test_the_subheading_example_in_format_md_is_not_judged_empty():
    """第 3 节承诺「一节的内容包含它下面所有更深的标题」，并把那段
    `## 做了什么` + `### 1 · …` 原样印在文档里。

    这条承诺兑现不了的后果不是排版难看：照着写的记录会被判成「什么都没写」→ L0，
    作者只能靠猜发现问题，然后补一句废话引言把评级骗上去。**评级一旦逼着人写废话，
    它就开始撒谎了。**所以直接把文档那一段喂给真评级器。
    """
    blocks = [b for b in re.findall(r"```markdown\n(.*?)```", text(FORMAT), re.S)
              if b.lstrip().startswith("## " + core.SECTION_NAMES["what"]["zh"]) and "\n### " in b]
    assert blocks, "FORMAT.md 第 3 节的子标题示例不见了"
    body = ("## %s\n因为要验证一个假设。\n\n" % core.SECTION_NAMES["why"]["zh"]
            + blocks[0]
            + "\n## %s\n成立。\n" % core.SECTION_NAMES["conclusion"]["zh"])
    step = core.Step(id="001", status="done", title="t", body=body, dirname="001_t")
    t = core.traceability(step)
    assert t["checks"]["what"], \
        "「做了什么」下面只有子标题就被判成没写 —— 第 3 节的承诺没兑现"
    assert t["level"] != "L0", f"照文档写出来的记录被判成 {t['level']}"
    # 而且这一段**不该**触发那条只提示的诊断：子标题下面是有正文的。
    assert not [w for w in core.lint_body(step) if w["code"] == "section_without_prose"], \
        "文档示例自己触发了 section_without_prose，那条诊断的判据和文档说的不一致"


def test_the_hint_only_diagnostics_named_in_format_md_really_do_not_change_the_level():
    """第 3 / 6 / 10 节反复承诺这六条「只提示、不影响 L0–L4」。

    这是一条**很容易在实现里被顺手违反**的承诺（把新检查塞进 traceability 只要一行），
    而违反之后的表现是「明明补齐了 commit 和 path 却上不了 L2」，没人猜得到原因。
    所以拿一份**故意犯全部六条**的记录去跑评级：等级必须和干净版本一模一样。
    """
    codes = set(re.findall(r"`([a-z]+_[a-z_]+)`", format_numbered_section("五个小节"))) \
        | set(re.findall(r"`([a-z]+_[a-z_]+)`", format_numbered_section("三种关系")))
    assert {"section_without_prose", "table_without_explanation", "code_without_explanation",
            "dangling_input", "self_input", "input_cycle"} <= codes, \
        f"FORMAT.md 第 3 / 6 节没列全那六条提示级诊断，只找到 {sorted(codes)}"

    clean = core.Step(id="002", status="done", title="t", commit="c1d2e3f",
                      paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                      body="## 为什么\n因为。\n\n## 做了什么\n跑了 `a.py`。\n\n## 结论\n成立。\n",
                      dirname="002_t")
    dirty = core.Step(id="002", status="done", title="t", commit="c1d2e3f",
                      paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                      inputs=[{"step": "002", "note": "自指"},
                              {"step": "999", "note": "不存在的一步"}],
                      body="## 为什么\n因为。\n\n## 做了什么\n### 子标题\n跑了。\n\n"
                           "## 结果\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                           "```bash\necho hi\n```\n\n## 结论\n成立。\n",
                      dirname="002_t")
    assert core.traceability(dirty)["level"] == core.traceability(clean)["level"] == "L2", \
        "六条提示级诊断里有人把等级拉下来了"

    got = {w["code"] for w in core.lint_body(dirty)}
    assert {"table_without_explanation", "code_without_explanation"} <= got, \
        f"表格/代码块缺说明没有被报出来: {sorted(got)}"
    warns = {w["code"] for w in core.validate_inputs({"002": dirty})}
    assert {"self_input", "dangling_input"} <= warns, f"input 的检查没报出来: {sorted(warns)}"
    assert all(w["level"] == "warn" for w in core.lint_body(dirty)), \
        "写法诊断里出现了 error 级 —— 文档说它们只是提示"


def test_format_md_promises_l2_for_a_snapshot_and_the_code_agrees():
    """第 7 / 10 节的核心主张：代码不在 git 里时，「快照目录 + 逐文件校验和」
    也能上 L2。这是 `code:` 存在的全部理由，所以直接验一遍——

    顺带钉住反面：**只有 manifest 没有目录位置不算数**（「东西在哪」没被回答），
    以及有没有 manifest **不额外分级**（那是 L3/L4 的事，硬塞进 L2 会造出一个
    机械判不清的半级）。
    """
    def step(code):
        return core.Step(id="001", status="done", title="t", code=code,
                         paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                         body="## 为什么\n因为。\n\n## 做了什么\n跑了。\n\n## 结论\n成立。\n",
                         dirname="001_t")

    bare = {"kind": "snapshot", "location": "/orange/lab/snap", "attrs": {}, "note": ""}
    with_manifest = {**bare, "attrs": {"manifest": "MANIFEST.md5", "n": "43"}}
    homeless = {"kind": "snapshot", "location": "", "attrs": {"manifest": "MANIFEST.md5"},
                "note": ""}

    assert core.traceability(step([bare]))["level"] == "L2"
    assert core.traceability(step([with_manifest]))["level"] == "L2", \
        "有 manifest 反而不是 L2？"
    assert core.traceability(step([homeless]))["level"] == "L1", \
        "没记快照目录也给了 L2 —— 「可定位」这一级就名不副实了"
    assert core.traceability(step([]))["level"] == "L1"


def test_the_commit_shorthand_is_derived_and_never_written_twice():
    """第 7 节承诺：`commit:` 等价于一条 `code: git`，但**文件里只存一份**。

    这是这个仓库最贵的一条不变量（上一代系统死于双真相源）。所以两个方向都钉：
    读侧派生得出那一条，写侧回写时绝不把它落盘。
    """
    step = core.Step(id="001", commit="c1d2e3f", dirname="001_t")
    derived = [c for c in core.code_records(step) if c.get("from") == "commit"]
    assert len(derived) == 1 and derived[0]["kind"] == "git", "commit 没有被折算成一条 code"
    assert core.code_located(step), "只写了 commit 就定位不到代码了"
    assert "code:" not in W.render_note(step), \
        "render_note 把派生出来的那条 code: git 也写进了文件 —— 双真相源回来了"


def test_the_metric_table_example_gets_what_format_md_promises(tmp_path):
    """FORMAT.md §4 一边要求「有方差就写 `0.943 ± 0.004`」，一边承诺「整列是数字
    就自动右对齐 + 底纹条」。这两句曾经互相打脸：渲染器的数值正则只认前缀 `±`，
    于是**照标准写出来的表**恰好拿不到标准承诺的那两个效果。
    所以直接拿文档自己那张示例表喂给真渲染器，看承诺兑不兑现。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("没装 node")
    blocks = [b for b in re.findall(r"```markdown\n(.*?)```", text(FORMAT), re.S) if "| 模型 |" in b]
    assert blocks, "FORMAT.md 第 4 节的示例指标表不见了"
    src = tmp_path / "table.md"
    src.write_text(blocks[0], encoding="utf-8")
    script = tmp_path / "render.js"
    script.write_text(
        "require(%s);\n"
        "const fs=require('fs');\n"
        "process.stdout.write(globalThis.md.render(fs.readFileSync(%s,'utf8'),{}));\n"
        % (repr((ROOT / "web" / "md.js").as_posix()), repr(src.as_posix())),
        encoding="utf-8")
    p = subprocess.run([node, str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr[-800:]
    assert 'class="ta-right"' in p.stdout, "示例表的数值列没有右对齐——§4 的承诺没兑现"
    assert "data-num=" in p.stdout, "示例表的数值列没有底纹条要用的主值"
    assert "data-num=\"0.943\"" in p.stdout, "底纹条取的不是主值（误差项不该参与）"


def test_the_level_table_matches_trace_core_levels():
    levels = set(re.findall(r"\*\*(L\d)\*\*", text(FORMAT)))
    assert levels == set(core.LEVELS), f"FORMAT.md 的等级表是 {sorted(levels)}，代码是 {list(core.LEVELS)}"


def test_the_docs_never_teach_the_old_logbook_status_names():
    """上一代系统的 `draft` / `ongoing` / `success` / `failed`（作为 status）一律 400。
    只有 SKILL.md 的「不要做的事」允许点名它们——那是在教人别用。"""
    for doc in DOCS:
        for line in text(doc).split("\n"):
            for word in re.findall(r"`(draft|ongoing|success)`", line):
                assert "旧状态名" in line, \
                    f"{where(doc)} 把旧状态名 {word} 当成可用值在教: {line.strip()[:60]}"


def test_the_skill_documents_every_field_update_step_accepts():
    """漏掉一个可写字段，agent 就永远不会用它（add_repro 原来就没人提过）。"""
    body = text(SKILL)
    for field in W.MUTABLE:
        assert f"`{field}`" in body, f"SKILL.md 没提过 PATCH 可以带 {field}"


# ---------------------------------------------------------------- 双语
# FORMAT.md 第 13 节是 agent 写翻译时**唯一**的规范来源，而翻译文件里的每一样
# 东西都是精确匹配的字符串：文件名的形状、front-matter 里唯一允许的键、
# 小节标题。对照表错一个字，照着它写出来的译文就评不了级、check 也读不出内容——
# 而且不会报任何错，只是静静地被当成「什么都没写」。所以这一段全部机械核对。


def format_numbered_section(keyword: str) -> str:
    """取出 FORMAT.md 里标题含 keyword 的那一个编号小节（到下一个编号小节为止）。

    按 `^## <数字>. ` 定位而不是按 `^## `：正文的示例代码块里满是 `## 为什么`
    这样的行，按后者切会在第一个示例处就截断。
    """
    body = text(FORMAT)
    heads = list(re.finditer(r"^##\s+\d+\.\s.*$", body, re.M))
    assert heads, "FORMAT.md 没有编号小节了？"
    for i, m in enumerate(heads):
        if keyword in m.group(0):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            return body[m.start():end]
    raise AssertionError(f"FORMAT.md 里找不到标题含 {keyword!r} 的编号小节")


# 三列的对照表：| `语义键` | `## 中文标题` | `## 英文标题` |
TR_NAME_ROW = re.compile(r"^\|\s*`([a-z]+)`\s*\|\s*`##\s+(.+?)`\s*\|\s*`##\s+(.+?)`\s*\|", re.M)


def test_the_bilingual_section_name_table_matches_the_code():
    """**这条是双语这一摊里最要紧的一条。**

    翻译文件能被同样地解析、同样地评级，靠的是 trace_core 里那张封闭词表：
    `_pick()` 拿 SECTION_NAMES 的值去正文里找标题，找不到就判成「这一节没写」。
    FORMAT.md 的对照表是 agent 唯一会照抄的东西——它写成 `## Why not`，
    产出的译文就整篇评不了级，而且一条警告都不会有。

    所以逐字核对，不留任何模糊：步骤的五个小节、项目笔记的四个洞察、
    以及系统自己写的删除审计小节，三组一次比完。
    """
    parsed = {m.group(1): {"zh": m.group(2), "en": m.group(3)}
              for m in TR_NAME_ROW.finditer(format_numbered_section("双语"))}
    expected = {**core.SECTION_NAMES, **core.INSIGHT_NAMES, "deleted": core.DELETED_NAME}
    assert parsed == expected, (
        "FORMAT.md 第 13 节的小节名对照表和 trace_core 对不上。\n"
        f"文档: {parsed}\n代码: {expected}")


def test_the_skill_spells_out_every_section_name_in_both_languages():
    """SKILL.md 是 agent 日常记录时读的东西，对照表在那里存了**第二份**。

    第二份就会漂：FORMAT.md 改了、SKILL.md 没跟上，agent 照着 SKILL 写出来的
    译文评不了级。所以两个方向都要求——词表里的每个名字都得在 SKILL.md 里出现过，
    而 SKILL.md 里出现的每个 `## 小节名` 也都必须在词表里。

    反例（`## Why not` 这种「写成这样等于没写」的示范）一律只放 FORMAT.md，
    不要放进 SKILL.md，否则这条会失败。
    """
    vocab = {n for names in (*core.SECTION_NAMES.values(), *core.INSIGHT_NAMES.values(),
                             core.DELETED_NAME) for n in names.values()}
    spelled = set(re.findall(r"`##\s+([^`]+)`", text(SKILL)))
    assert vocab - spelled == set(), f"SKILL.md 没写出这些小节名: {sorted(vocab - spelled)}"
    assert spelled - vocab == set(), f"SKILL.md 写了词表之外的小节名: {sorted(spelled - vocab)}"


def test_the_translation_filename_regex_in_format_md_is_the_real_one():
    """文件名的形状是 scan 的判据（TR_RE），文档里那条正则必须就是它本身。

    抄一份「差不多的」正则进文档，最先出事的是长度上限和首字符规则——
    照文档造出来的 `note.2en.md` 在磁盘上躺着，页面上永远看不见它。
    """
    assert core.TR_RE.pattern in text(FORMAT), \
        f"FORMAT.md 里没有 core.TR_RE 的原文: {core.TR_RE.pattern}"


TR_FILENAME = re.compile(r"\b(note|project)\.([A-Za-z][A-Za-z0-9-]*)\.md\b")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_translation_filename_in_the_docs_is_one_the_scanner_would_pick_up(doc):
    """文档里出现的每个 `note.<lang>.md` / `project.<lang>.md` 都得是真能被扫到的名字。

    还要求语言码**已经是归一化形式**（norm_lang 的不动点）：文档写 `note.EN.md`，
    照抄的人在 Linux 上得到的是和 `note.en.md` 并存的第二个文件，
    同一种语言分裂成两条记录；在 NTFS 上则是一次静默的别名覆盖。
    """
    for m in TR_FILENAME.finditer(text(doc)):
        stem, lang, name = m.group(1), m.group(2), m.group(0)
        pattern = core.TR_RE if stem == "note" else core.PROJECT_TR_RE
        assert pattern.match(name), f"{where(doc)} 里的 {name} 不会被 scan 当成译文"
        assert W.norm_lang(lang) == lang, \
            f"{where(doc)} 里的 {name} 语言码不是归一化形式（应为 {W.norm_lang(lang)}）"


# 「这些键一律忽略」的那一行：`id` · `parent` · … 用 · 连起来的一串反引号词。
STRUCT_KEY_RUN = re.compile(r"^`[a-z]+`(?:\s*·\s*`[a-z]+`)+\s*$", re.M)


@pytest.mark.parametrize("doc", [FORMAT, SKILL], ids=lambda p: p.relative_to(ROOT).as_posix())
def test_the_ignored_structural_keys_listed_in_the_docs_are_exactly_the_real_ones(doc):
    """漏列一个键，就等于告诉 agent「这个键写进译文是有效的」——而它其实被丢掉，
    于是译文里的 `status: done` 看着生效、实际什么也没发生。多列一个则相反，
    会让人以为 note.md 里也不能写它。两个方向都只有逐字比对能挡住。
    """
    runs = STRUCT_KEY_RUN.findall(text(doc))
    assert len(runs) == 1, f"{where(doc)} 里应当恰好有一行「被忽略的结构键」清单，找到 {len(runs)} 行"
    listed = tuple(re.findall(r"`([a-z]+)`", runs[0]))
    assert listed == core.TR_STRUCT_KEYS, \
        f"{where(doc)} 列的是 {listed}，core.TR_STRUCT_KEYS 是 {core.TR_STRUCT_KEYS}"


def bilingual_examples() -> list[tuple[str, str]]:
    """FORMAT.md 第 13 节里的翻译示例块，返回 [(唯一允许的键, 块原文)]。"""
    out = []
    for b in re.findall(r"```[a-z]*\n(.*?)```", format_numbered_section("双语"), re.S):
        if b.startswith("---\ntitle:"):
            out.append((core.TR_ONLY_KEYS[0], b))
        elif b.startswith("---\nname:"):
            out.append((core.PROJECT_TR_ONLY_KEYS[0], b))
    return out


def test_the_translation_examples_in_format_md_are_clean():
    """示例是拿来抄的，所以直接喂给真正的翻译解析器：一条警告都不许有
    （有就说明示例里混进了结构键），而且小节标题必须全部在封闭词表里。

    这比「文档里别写 id:」的词表检查扎实：示例是整块被复制走的，里面任何一行
    出错都会被原样传播到磁盘上。
    """
    examples = bilingual_examples()
    assert len(examples) >= 2, "FORMAT.md 第 13 节里的翻译示例不见了（步骤和项目笔记各要有一份）"
    known = set(core.SECTION_KEY_BY_NAME) | set(core.INSIGHT_KEY_BY_NAME) \
        | set(core.DELETED_NAME.values())
    for only_key, raw in examples:
        data, warns = core.parse_translation(raw, (only_key,), "示例")
        assert not warns, f"FORMAT.md 的翻译示例解析出警告: {warns}"
        assert data[only_key], f"示例的 front-matter 里没有 `{only_key}:`"
        for name in core.sections(data["body"]):
            assert name in known, f"FORMAT.md 的翻译示例用了词表之外的小节名: {name!r}"


def test_the_docs_promise_that_any_language_counts_and_the_code_agrees(tmp_path):
    """FORMAT.md 第 13 节承诺：小节「note.md 或任一译文里写了就算写了」。

    直接把文档里那两个示例的形状摆到磁盘上验一遍——note.md 只写了中文的
    「为什么/做了什么」，结论只在英文版里。承诺兑现的话这一步不该因为
    「没写结论」被判 L0，也不该报 missing_conclusion。
    """
    d = tmp_path / "steps" / "007_加入标题字段"
    d.mkdir(parents=True)
    (d / core.NOTE_NAME).write_text(
        "---\nid: 007\nstatus: done\ntitle: 加入标题字段\nlang: zh\n"
        "commit: c1d2e3f\npath: /blue/x | 数据\n---\n\n"
        "## 为什么\n基线丢掉词序。\n\n## 做了什么\n换成 DistilBERT。\n", encoding="utf-8")
    (d / "note.en.md").write_text(
        "---\ntitle: Add title field\n---\n\n## Conclusion\nThe hypothesis holds.\n",
        encoding="utf-8")

    steps, _files, _warns = core.scan(tmp_path / "steps")
    assert len(steps) == 1, "译文被当成步骤扫进来了"
    step = steps[0]
    assert step.lang == "zh" and "en" in step.tr
    assert step.tr["en"]["title"] == "Add title field"
    assert core.traceability(step)["checks"]["conclusion"], \
        "FORMAT.md 第 13 节承诺「任一语言写了就算写了」，代码没兑现"
    assert not [w for w in core.lint_body(step) if w["code"] == "missing_conclusion"]
    assert not [f for f in core.list_files(d) if f["path"] == "note.en.md"], \
        "FORMAT.md 说译文不是附件，list_files 却把它列进去了"


def test_the_docs_promise_that_captions_are_judged_per_file_and_the_code_agrees(tmp_path):
    """同一节里那条**例外**：图注逐份文件独立判，警告的 where 指到具体文件。

    这条和上一条方向相反，写反了就是把一整份译文里的图变成对读者的黑洞，
    所以两条都要钉住。
    """
    d = tmp_path / "steps" / "007_x"
    d.mkdir(parents=True)
    (d / core.NOTE_NAME).write_text(
        "---\nid: 007\nstatus: done\ntitle: t\n---\n\n"
        '## 为什么\n因为\n\n## 做了什么\n跑了\n\n## 结论\n成立\n\n'
        '![](loss_curve.png "第 12 轮之后验证集回升")\n', encoding="utf-8")
    (d / "note.en.md").write_text(
        "---\ntitle: T\n---\n\n## Conclusion\nHolds.\n\n![](loss_curve.png)\n",
        encoding="utf-8")

    steps, _f, _w = core.scan(tmp_path / "steps")
    figs = [w for w in core.lint_body(steps[0]) if w["code"] == "figure_without_caption"]
    assert len(figs) == 1, "中文版写了图注就把英文版那张也算过关了——那不是逐份文件判"
    assert figs[0]["where"] == "007_x/note.en.md", \
        f"警告的 where 该指到具体文件，实际是 {figs[0]['where']!r}"


def test_the_structural_keys_the_docs_call_out_really_are_ignored_and_warned(tmp_path):
    """FORMAT.md 和 SKILL.md 都说这些键「一律忽略并产出一条警告」。挨个真写一遍。

    这是双语最贵的一条不变量（上一代系统就死在双真相源上），承诺和实现之间
    不能只靠人读一遍代码来保证。
    """
    d = tmp_path / "steps" / "007_x"
    d.mkdir(parents=True)
    (d / core.NOTE_NAME).write_text(
        "---\nid: 007\nparent: 005\nstatus: done\ntitle: 原文\n---\n\n## 为什么\n因为\n",
        encoding="utf-8")
    for key, bad in (("id", "999"), ("parent", "001"), ("status", "wip"),
                     ("date", "2026-01-01"), ("commit", "deadbee"), ("author", "x"),
                     ("tags", "a, b"), ("path", "/blue/x | y"),
                     ("repro", "verified | d | b | n"), ("key", "k")):
        (d / "note.en.md").write_text(
            f"---\ntitle: T\n{key}: {bad}\n---\n\n## Why\nbecause.\n", encoding="utf-8")
        steps, _f, warns = core.scan(tmp_path / "steps")
        s = steps[0]
        codes = [w["code"] for w in warns]
        assert "translation_structural_key" in codes, f"译文里的 {key}: 没有报警告"
        assert s.id == "007" and s.parent == "005" and s.status == "done", \
            f"译文里的 {key}: 影响到了 note.md 的结构 —— 双真相源回来了"
        # digest 是算出来的（sha256 of raw bytes，给 expect 用），不是文件里读到的键。
        assert set(s.tr["en"]) - {"digest"} == {core.TR_ONLY_KEYS[0], "body"}, \
            f"译文里的 {key}: 被读进了 Step.tr"


def test_the_asymmetry_format_md_warns_about_is_real(tmp_path):
    """FORMAT.md 第 13 节点名了一个不对称：项目译文里的结构键**也**被忽略，
    但那一侧没有警告通道，一声不吭。

    写进文档是因为手写 project.en.md 的人不会得到任何提示。钉住它有两个方向：
    真的静默了（文档没骗人），以及真的被忽略了（`name` 之外一个键都没读进来）。
    哪天 scan_projects 长出警告通道，这条会失败并逼着文档改口。
    """
    p = W.create_project(tmp_path, "沉默的一侧")
    (core.project_dir(tmp_path, p.slug) / "project.en.md").write_text(
        "---\nname: The quiet side\nid: 999\nstatus: dead\n---\n\n## Ideas\n- a\n",
        encoding="utf-8")
    projects = core.scan_projects(tmp_path)
    tr = projects[0].tr["en"]
    assert set(tr) - {"digest"} == {core.PROJECT_TR_ONLY_KEYS[0], "body"}, \
        f"项目译文里的结构键被读进来了: {sorted(tr)}"
    assert projects[0].slug == p.slug, "项目译文里的键影响到了 project.md —— 双真相源回来了"


def test_the_inlined_standard_carries_the_translation_rule_too():
    """README 说 initialize 的 instructions 内联了格式标准的可执行摘要，
    「那是唯一无论怎么装都一定送达的通道」。

    `pip install git+…` 只装三个 `.py`，那台机器上根本不存在 FORMAT.md——
    双语这一段要是没进内联摘要，那台机器上的 agent 只知道有 trace_translate，
    不知道译文里不能写结构键、也不知道小节名要换成目标语言那一套。
    """
    ins = M.INSTRUCTIONS
    assert "trace_translate" in ins and "trace_untranslated" in ins
    assert core.TR_ONLY_KEYS[0] in ins, "内联摘要没说译文的 front-matter 里只准有 title"
    for key in ("why", "conclusion"):
        assert core.SECTION_NAMES[key]["en"] in ins, \
            f"内联摘要没给出英文小节名 {core.SECTION_NAMES[key]['en']}"


# ------------------------------------------------- 分叉与汇回（FORMAT.md 第 15 节）
# 这一节是 agent 写 `branch:` / `decision:` 时**唯一**的规范来源，而它讲的三样东西
# 全是精确匹配的字符串：两个取值、三种结局、三条诊断的 code。任何一个和代码对不上，
# 照着写的人得到的是「写下去了、什么都没发生」——`branch:` 拼错会静静退回 extends，
# 不会有人来告诉他。所以这里不校对散文，只把文档里的**示例**整块喂给真代码。


def fork_section() -> str:
    return format_numbered_section("三种边")


def section_note_examples(section: str) -> list[str]:
    """某一节里那些「这就是一份 note.md」的示例块。"""
    return [b for b in re.findall(r"```[a-z]*\n(.*?)```", section, re.S)
            if b.startswith("---\nid:")]


def build_forest(metas: dict[str, dict[str, str]]):
    """把一组 front-matter 直接喂给真解析器，返回 (by_id, children, order)。"""
    by_id = {}
    for sid, meta in metas.items():
        step, warns = core.build_step(f"{sid}_例子", {"id": sid, **meta}, "")
        assert not warns, f"{sid} 的 front-matter 解析出警告: {warns}"
        by_id[sid] = step
    children = core.build_children(by_id)
    return by_id, children, core.compute_order(by_id, children)


def test_the_branch_values_in_format_md_are_exactly_the_ones_the_parser_knows():
    """`branch:` 是闭词表（core.BRANCH_KINDS）。文档多写一个取值，照着写的人得到的是
    一条 `bad_branch` 警告加一次**静默降级**——那一步从候选变回普通延伸，
    括弧不画了、「N 选 1」不显示了，而记录本身看着一切正常。少写一个则相反。
    顺序也要对上，那张表就是照着常量列的。
    """
    listed = table_first_column(fork_section(), "| 取值 |")
    assert tuple(listed) == core.BRANCH_KINDS, \
        f"FORMAT.md 第 15 节的取值表是 {listed}，core.BRANCH_KINDS 是 {list(core.BRANCH_KINDS)}"
    assert f"`{core.DEFAULT_BRANCH}` | 我接着 parent 往下做。**默认" in fork_section(), \
        f"第 15 节没说清默认值是 {core.DEFAULT_BRANCH}（不写就是它）"


def test_the_fork_example_in_format_md_really_forms_one_group_the_code_recognises():
    """第 15 节的三个示例块是整块拿来抄的：一个分叉点 + 两个候选。

    所以直接喂给 compute_branch_groups——它们必须真的成为**同一组**候选，
    而不是三份各自合法、凑在一起什么都不发生的 front-matter。同时钉住这一节
    最核心的两条主张：「这一组有谁」是扫出来的（父节点上没有任何清单），
    「选了哪个」是 `status: dead` 派生出来的（磁盘上没有 chosen 这种字段）。
    """
    blocks = section_note_examples(fork_section())
    assert len(blocks) >= 3, f"第 15 节的分叉示例只找到 {len(blocks)} 块（要 1 个分叉点 + 2 个候选）"

    metas = {}
    for raw in blocks:
        meta, body, warns = core.parse_note(raw)
        assert not warns, f"第 15 节的示例解析出警告: {warns}"
        metas[meta["id"]] = {k: v for k, v in meta.items() if k != "id"}
    by_id, children, _ = build_forest(metas)

    groups = core.compute_branch_groups(by_id, children)
    assert len(groups) == 1, f"第 15 节的示例没有凑成恰好一组候选: {groups}"
    g = groups[0]
    fork = g["at"]
    assert len(g["options"]) == 2, f"两个候选没被算进同一组: {g}"
    assert fork and by_id[fork].decision, "分叉点上的 `decision:` 没被读出来"
    assert g["decision"] == by_id[fork].decision, \
        "候选组的 decision 不是分叉点那一行 —— 它只能从那里来"
    # 「选了哪个」完全由 status 决定：示例里一个 dead 一个不是 ⇒ decided。
    assert g["state"] == "decided" and g["chosen"] in g["options"], \
        f"示例里恰好一个候选没标 dead，应当算 decided: {g}"
    assert by_id[g["chosen"]].status != "dead"
    # 父节点身上不许有任何「候选清单」，兄弟之间也不许互相指名（那就是双真相源）。
    for raw in blocks:
        meta, _, _ = core.parse_note(raw)
        for banned in ("options", "alt", "rivals", "chosen"):
            assert banned not in meta, \
                f"第 15 节的示例里出现了 `{banned}:` —— 那是把双真相源请回来"


def test_the_fork_states_in_format_md_are_the_three_the_code_derives():
    """三种结局全部**从 status 派生**。文档漏掉 `abandoned` 那一档，读的人就会把
    「一组候选全标了 dead」当成数据坏了，而它是这套系统里最该被保留的一种结论（P4）。
    """
    sec = fork_section()
    for state in core.BRANCH_STATES:
        assert f"`{state}`" in sec, f"第 15 节没写出 {state} 这一档"

    # 三档各造一个，判据是真函数算出来的，不是照着表抄的。
    def state_of(statuses: list[str]) -> str:
        metas = {"001": {"status": "done", "decision": "选哪条"}}
        for i, st in enumerate(statuses):
            metas["002" + "b" * i] = {"parent": "001", "branch": "alternative", "status": st}
        by_id, children, _ = build_forest(metas)
        return core.compute_branch_groups(by_id, children)[0]["state"]

    assert state_of(["done", "dead"]) == "decided"
    assert state_of(["dead", "dead"]) == "abandoned"
    assert state_of(["wip", "wip"]) == "open"


def test_the_fork_diagnostics_in_format_md_are_the_real_codes_and_change_no_level():
    """第 15 节承诺这三条「只提示、不进 L0–L4」——和第 3 / 6 节那六条同一档。

    两个方向都盯着：文档列的 code 必须是 validate_branches 真报得出来的（列错一个，
    照着 grep 的人永远搜不到），validate_branches 报得出来的也必须都被列上
    （漏一条，界面上冒出来一句没人解释得了的提示）。

    「不降级」这条尤其容易被顺手违反：把「这个岔路口还没决定」塞进评级只要一行，
    而违反之后的表现是「明明补齐了 commit 和 path 却上不了 L2」，没人猜得到原因。
    更坏的是它会教人**为了消掉警告随手标一个 `dead`**——拿假结论换绿色。
    """
    sec = fork_section()
    # 只取带下划线的那些：诊断 code 长这样，而同一节里还有 `extends` / `alternative`
    # 这种取值表，它们不是 code。
    documented = set(re.findall(r"^\|\s*`([a-z]+_[a-z_]+)`\s*\|", sec, re.M))
    assert "bad_branch" in sec, "第 15 节没提 `bad_branch`（拼错取值时的静默降级提醒）"

    # 一次造齐四条：001 底下两个都活着且没写 decision，003 底下只有一个候选，
    # 005 写了 decision 却一个候选都没有（那一行现在什么都不做）。
    by_id, children, _ = build_forest({
        "001": {"status": "done"},
        "002": {"parent": "001", "branch": "alternative", "status": "wip"},
        "002b": {"parent": "001", "branch": "alternative", "status": "wip"},
        "003": {"status": "done", "decision": "只标了一条的那个岔路口"},
        "004": {"parent": "003", "branch": "alternative", "status": "wip"},
        "005": {"status": "done", "decision": "问题写了，候选还一个都没标"},
        "006": {"parent": "005", "status": "wip"},
    })
    warns = core.validate_branches(by_id, core.compute_branch_groups(by_id, children))
    emitted = {w["code"] for w in warns}
    assert emitted == {"lone_alternative", "fork_without_decision", "undecided_fork",
                       "decision_without_candidates"}, \
        f"validate_branches 报出来的是 {sorted(emitted)}，造用例的场景该更新了"
    assert emitted <= documented, f"第 15 节没列全这几条: {sorted(emitted - documented)}"
    assert documented <= emitted | {"bad_branch"}, \
        f"第 15 节列了 validate_branches 报不出来的 code: {sorted(documented - emitted)}"
    assert all(w["level"] == "warn" for w in warns), "分叉诊断里出现了 error 级"

    # 同一份记录，加不加分叉语义，等级必须一模一样。
    body = "## 为什么\n因为。\n\n## 做了什么\n跑了 `a.py`。\n\n## 结论\n成立。\n"
    plain = core.Step(id="002", status="done", title="t", commit="c1d2e3f", body=body,
                      paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                      dirname="002_t")
    forked = core.Step(id="002", status="done", title="t", commit="c1d2e3f", body=body,
                       paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                       branch="alternative", branch_note="先试最便宜的那条",
                       decision="下一个岔路口在决定什么", dirname="002_t")
    assert core.traceability(forked)["level"] == core.traceability(plain)["level"] == "L2", \
        "分叉语义把等级动了 —— 第 15 节说它一条都不降级"


def test_the_rejoin_criterion_in_the_docs_is_the_one_compute_merges_uses():
    """第 6 / 15 节把汇回的判据写成一句话：**两端在同一棵树里，且谁都不是谁的祖先**。

    这句话是有代价的承诺，两个反面尤其要兑现：祖先链上的那条 `input:`（树边已经画过
    这条路）和跨树的那条（从来没在同一条线上过）都**不是**汇回，老实算普通数据依赖。
    把它们也画成曲线，图上会凭空多出一堆「支线汇回」，而那正是第 6 节说的
    「把主干描粗一遍」。所以直接构造这三种，喂给真函数。
    """
    for doc, sec in ((FORMAT, format_numbered_section("三种关系")), (FORMAT, fork_section())):
        assert "谁都不是谁的祖先" in sec, \
            f"{where(doc)} 的汇回判据那句话不见了（第 6 / 15 节都要有）"

    by_id, _, order = build_forest({
        "011": {"status": "done", "decision": "两条路只能选一条走下去"},
        "012": {"parent": "011", "branch": "alternative", "status": "dead"},
        "012b": {"parent": "011", "branch": "alternative", "status": "done"},
        "013": {"parent": "012b", "status": "done"},
        # 014 读了另一支的 013：这是汇回。同时也读了自己的祖先 011：那不是。
        "014": {"parent": "012", "status": "done",
                "input": "013 | scores.csv\n011 | split.json"},
        # 另一棵树，读的还是 013 —— 没有共同祖先，谈不上「汇回」。
        "020": {"status": "done", "input": "013 | scores.csv"},
    })
    merges = core.compute_merges(by_id, order)
    assert merges == [{"from": "013", "to": "014", "at": "011", "notes": ["scores.csv"]}], \
        f"汇回判出来的是 {merges} —— 和第 6 / 15 节写的判据对不上"


def test_the_skill_is_honest_about_which_front_doors_carry_the_fork_fields():
    """和 inputs/code 那条同一个理由：**静默丢字段是最坏的一类缺陷**。

    `branch` / `decision` 已经进了 W.MUTABLE，REST 的 PATCH 把请求体整个透传，
    所以那条路现在就能用；但 MCP 的 `trace_update_step` 是**白名单**转发的，
    `POST …/steps` 也不读这两个键——从那两个门进来的字段会一声不吭地消失。
    在门面补齐之前 SKILL.md 必须明说；补齐之后那段注意事项必须删掉，
    否则 agent 会一直多发一个 PATCH。两个方向都盯着。
    """
    mcp_src = (ROOT / "trace_mcp.py").read_text(encoding="utf-8")
    m = re.search(r"def t_update_step\(.*?\n\ndef ", mcp_src, re.S)
    assert m, "trace_mcp.py 里找不到 t_update_step 了（函数名变了？）"
    mcp_ok = all(f'"{k}"' in m.group(0) for k in ("branch", "decision"))

    server_src = (ROOT / "trace_server.py").read_text(encoding="utf-8")
    c = re.search(r"async def api_create\(.*?\n\n", server_src, re.S)
    assert c, "trace_server.py 里找不到建步骤那条路由了（函数名变了？）"
    server_ok = all(f'payload.get("{k}"' in c.group(0) for k in ("branch", "decision"))

    wired = mcp_ok and server_ok
    warned = "尚未透传" in text(SKILL)
    assert wired != warned, (
        "两个门面都收 branch/decision 了，请删掉 SKILL.md 里那段「尚未透传」的注意事项"
        if wired else
        "MCP 的 trace_update_step / POST …/steps 仍然丢掉 branch/decision，"
        "SKILL.md 必须明说这件事（agent 会以为记上了）")


def test_the_readme_and_format_md_name_the_three_edges_the_same_way():
    """两份文档各讲一遍三种边（README 讲怎么看，FORMAT 讲怎么写），名字必须是同一套。

    这三个词是**新造的术语**，没有别处可查：README 叫「汇回」而 FORMAT 叫「合并」，
    读的人不会意识到说的是同一条边，只会以为漏了一种关系。
    （刻意避开 git 的词汇也是这个原因——「合并」「分支」会让人自动套上一个
    可 rebase、有唯一主干的模型，那个模型在这里全是错的。）
    """
    fork = fork_section()
    readme = text(README)
    for name in ("普通延伸", "互斥候选", "汇回"):
        assert name in fork, f"FORMAT.md 第 15 节没有「{name}」这个说法"
        assert name in readme, f"README 没有「{name}」这个说法"
    # README 那张视觉表必须给每一种关系都配一个非颜色的通道，否则打印/色觉障碍下读不出。
    vis = readme.split("### 三种边的视觉编码", 1)
    assert len(vis) == 2, "README 里找不到「三种边的视觉编码」那一节"
    for channel in ("折线", "括弧", "曲线"):
        assert channel in vis[1][:1200], \
            f"README 的视觉编码表里没写出「{channel}」这个非颜色通道"


def test_the_skill_tells_the_truth_about_what_the_create_endpoint_accepts():
    """`trace_new_step` 的 schema 上有 `inputs` / `code`，而 REST 的建步骤端点
    （`POST …/steps`）目前**不读**它们——远端模式下这两个字段会被静默丢掉。

    静默丢字段是最坏的一类缺陷：agent 以为记上了，磁盘上什么都没有，而且没有报错。
    在服务端补齐之前，SKILL.md 必须明说「建完再 PATCH 补一次」。
    这条测试两个方向都盯着：端点补上了却没删那段注意事项，同样失败——
    留着一句过时的警告会让 agent 一直多发一个请求。
    """
    src = (ROOT / "trace_server.py").read_text(encoding="utf-8")
    m = re.search(r"async def api_create\(.*?\n\n", src, re.S)
    assert m, "trace_server.py 里找不到建步骤那条路由了（函数名变了？）"
    accepts = all(f'payload.get("{k}")' in m.group(0) for k in ("inputs", "code"))
    warned = "还没接" in text(SKILL)
    assert accepts != warned, (
        "POST …/steps 已经收 inputs/code 了，请删掉 SKILL.md 里那段「还没接」的注意事项"
        if accepts else
        "POST …/steps 仍然丢掉 inputs/code，SKILL.md 必须明说这件事（agent 会以为记上了）")


def test_the_inlined_standard_carries_the_four_structured_keys():
    """同一条通道上的第二件事：`input` / `code` / `moved` / 结构化的 `path`。

    `pip install git+…` 装出来的机器上没有 FORMAT.md，那里的 agent 只认得 instructions
    里写了的东西。这四个键要是没进内联摘要，那台机器上的 agent 会：把数据依赖写进
    正文（读不回来）、代码不在 git 里时干脆不记（永远停在 L1）、改 parent 时不知道
    要写原因（直接被拒），以及把「12 GB」写进 `size=`（写入侧 400）。
    """
    ins = M.INSTRUCTIONS
    for key in ("input", "code", "moved"):
        assert f"{key}:" in ins, f"内联摘要没提过 `{key}:` 这个键"
    assert any(k in ins for k in core.CODE_KINDS if k != "git"), \
        "内联摘要只说了 git —— 代码不在 git 里的那条路没送到 agent 手上"
    assert "checked" in ins and "missing" in ins, \
        "内联摘要没说 path 的 checked= / missing="


def test_the_untranslated_fields_named_in_the_readme_exist():
    """README 的端点表点名了 /untranslated 回哪几个字段，agent 会照着取。"""
    report = M.untranslated_report({"steps": [], "project": "p"}, None, "en")
    for field in ("missing", "translated", "native", "project_note"):
        assert field in report, f"README 说 /untranslated 回 {field}，实际没有"
        assert f"`{field}" in text(README) or field in text(README)


# ------------------------------------------------- 两条路径（FORMAT.md 第 16 节）
# 这一节讲的是「记录」和「方法」的分家，而它整节的价值压在一句承诺上：**成员一个字
# 都不存，全是算出来的**。所以这里不校对散文，只做两件事：把文档里那张「在脑子里
# 算一遍」的表整块喂给 compute_pipeline（读者信不信那张图，取决于他照着算出来的
# 和程序算出来的是不是同一份），以及把两个键的取值、七条诊断的 code 逐字对上代码。


def pipeline_section() -> str:
    return format_numbered_section("两条路径")


def worked_example_rows() -> list[dict[str, str]]:
    """第 16.2 节那张「在脑子里算一遍」的表，每行一个步骤。

    按表头定位而不是按行号：这张表是读者唯一能自己验算的东西，它和代码一旦
    分家，整节就变成一段听起来很有道理的散文。
    """
    sec = pipeline_section()
    head = "| 步骤 | `parent` | `input` | `status` | `pipeline` | 在定稿流程里吗 |"
    start = sec.find(head)
    assert start != -1, "FORMAT.md 第 16 节里找不到那张「在脑子里算一遍」的表"
    out: list[dict[str, str]] = []
    for line in sec[start:].split("\n")[2:]:            # 跳过表头和分隔行
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 6, f"表格这一行不是六列: {line}"
        ids = re.findall(r"`([0-9a-z]+)`", cells[0])
        assert ids, f"表格这一行第一列没有步骤 id: {line}"
        out.append({
            "id": ids[0],
            "parent": (re.findall(r"`([0-9a-z]+)`", cells[1]) or [""])[0],
            "input": (re.findall(r"`([0-9a-z]+)`", cells[2]) or [""])[0],
            "status": (re.findall(r"`([a-z]+)`", cells[3]) or [""])[0],
            "pipeline": (re.findall(r"`([a-z]+)`", cells[4]) or [""])[0],
            "member": cells[5].startswith("✓"),
            "why": cells[5],
        })
    assert len(out) >= 5, f"那张表只扫到 {len(out)} 行，扫描器可疑"
    return out


def test_the_pipeline_rules_in_format_md_are_exactly_the_ones_the_parser_knows():
    """`pipeline:` 是闭词表（core.PIPELINE_RULES）。文档多写一个取值，照着写的人拿到的
    是一条 `bad_pipeline` 加一次**静默无效**——那一行什么都不做，而记录看着一切正常。
    少写一个则相反：有效的写法没人知道。顺序也要对上，那张表就是照着常量列的。"""
    listed = table_first_column(pipeline_section(), "| 取值 |")
    assert tuple(listed) == core.PIPELINE_RULES, \
        f"FORMAT.md 第 16 节的取值表是 {listed}，core.PIPELINE_RULES 是 {list(core.PIPELINE_RULES)}"


def test_every_pipeline_example_in_the_docs_carries_a_known_rule_and_a_reason():
    """`pipeline:` 的示例必须：取值认得出、**写了理由**、并且回写后逐字不变。

    理由那一条是写入侧的硬校验（`norm_pipeline` 不给理由直接拒绝），所以文档里
    出现一条没理由的示例，等于在教一种服务端会 400 的写法。而它偏偏是这个键
    最容易被省掉的一半——`pipeline:` 除了改变一份导出之外不留任何痕迹，
    没有那半句话，半年后分不清是想清楚的决定还是一次误点。
    """
    seen = 0
    for doc, raw in front_matter_lines("pipeline"):
        got = core.parse_pipeline(raw)
        assert got["rule"] in core.PIPELINE_RULES, \
            f"{where(doc)} 的 `pipeline: {raw}` 取值 {got['rule']!r} 不在 {list(core.PIPELINE_RULES)} 里"
        assert got["note"], f"{where(doc)} 的 `pipeline: {raw}` 没写理由，而写入侧要求必填"
        assert core.format_pipeline(got) == raw, \
            f"{where(doc)} 的 pipeline 回写后变了样: {raw!r} → {core.format_pipeline(got)!r}"
        W.norm_pipeline(raw)                        # 写入侧的校验器：不合法直接抛
        seen += 1
    assert seen >= 2, f"只扫到 {seen} 条 pipeline 示例，include / exclude 至少各要有一条"


def test_every_result_example_in_the_docs_names_a_step_and_says_what_it_is():
    """`result:` 是全项目**唯一**要人写的一行，右半边（这是什么成果）是它一半的价值：
    导出的 Methods 里每一节的标题就是它。示例只写 `result: 023` 的话，照着写的人
    得到的是一条自己半年后也看不懂的声明。

    判据走真解析器 `core.parse_results`（它自己扫 front-matter 的原始行），
    不是照着「和 input 语法一样」抄一份——两处各写一遍迟早在「说明里再有竖线
    怎么办」上分家。
    """
    seen = 0
    for doc, raw in front_matter_lines("result"):
        got = core.parse_results(f"---\nname: x\nresult: {raw}\n---\n")
        assert got, f"{where(doc)} 的 `result: {raw}` 解析不出任何东西"
        assert got[0]["step"], f"{where(doc)} 的 `result: {raw}` 没解析出步骤 id"
        assert got[0]["note"], \
            f"{where(doc)} 的 `result: {raw}` 没写清这是什么成果，示例不该这么教"
        assert core.format_result(got[0]) == raw
        seen += 1
    assert seen >= 3, f"只扫到 {seen} 条 result 示例，扫描器可疑"


def test_the_worked_example_in_format_md_derives_exactly_the_flow_it_prints():
    """第 16.2 节把一个六步的项目和它算出来的流程**都印了出来**，读者会照着验算。

    所以直接把那张表喂给 `compute_pipeline`：算出来的顺序必须和文档印的那一行
    逐字一致，✓ / ✗ 那一列也必须和真实的成员判定逐个对上。这一节的全部说服力
    都压在这件事上——表和代码一旦分家，读者会先信文档，然后照着一条不存在的
    流程去写 Methods。

    顺带钉住三件这一节明确承诺过的事：被剔掉的步骤上游**接过去**（`via` 非空）、
    「凭什么在流程里」是四选一的枚举、以及**磁盘上没有任何成员清单**。
    """
    rows = worked_example_rows()
    metas = {}
    for r in rows:
        meta: dict[str, str] = {"status": r["status"], "title": "t"}
        if r["parent"]:
            meta["parent"] = r["parent"]
        if r["input"]:
            # 表格里只写了来源步骤（竖线在 markdown 表格里要转义，写进去只会
            # 让这张表更难读）。竖线右边那半句在表格下面那句话里，是真示例。
            meta["input"] = f"{r['input']} | pairs.csv"
        if r["pipeline"]:
            meta["pipeline"] = f"{r['pipeline']} | 文档第 16.3 节那两个示例块里的理由"
        metas[r["id"]] = meta
    by_id, _children, _order = build_forest(metas)

    # 成果不从表里猜：那一节明说了「`project.md` 里只有一行 `result: …`」，
    # 就用那一行本身（它也被上面那条 result 示例测试逐字校验过）。
    decl = re.search(r"只有一行 `result:\s*([^`]+)`", pipeline_section())
    assert decl, "第 16.2 节没写清这个例子声明的是哪一个成果"
    declared = core.parse_results(f"---\nname: x\nresult: {decl.group(1)}\n---\n")
    assert declared and declared[0]["step"] in by_id, \
        f"第 16.2 节声明的成果 {declared} 不在那张表里"
    assert declared[0]["step"] == next(r["id"] for r in rows if "成果" in r["why"]), \
        "表格里标成「就是那个成果」的那一行和上面声明的 result 对不上"
    p = core.compute_pipeline(by_id, declared)

    printed = re.search(r"^顺序((?:\s*`[0-9a-z]+`)+)", pipeline_section(), re.M)
    assert printed, "第 16.2 节没有印出算完之后的顺序"
    assert re.findall(r"`([0-9a-z]+)`", printed.group(1)) == p["order"], \
        f"文档印的顺序和 compute_pipeline 算出来的 {p['order']} 对不上"
    assert [r["id"] for r in rows if r["member"]] == p["order"], \
        "表格 ✓ / ✗ 那一列和真实的成员判定对不上"

    # 被剔掉的两步各是一种理由，而且上游被接了过去（不留断口）。
    assert {d["step"]: d["why"] for d in p["excluded"]} == {"018": "declared", "020": "dead"}
    assert any(e["via"] for e in p["edges"]), \
        "剔掉中间那两步之后没有一条边带 via —— 「上游接过去」这条承诺没兑现"

    # 「凭什么在流程里」四选一，文档把四种都点了名。
    assert {w["kind"] for w in p["why"].values()} == {"result", "include", "input", "parent"}

    # 磁盘上没有任何成员清单：front-matter 里不许出现这类键。
    for meta in metas.values():
        for banned in ("pipeline_members", "members", "flow", "steps"):
            assert banned not in meta


def test_the_pipeline_diagnostics_in_the_docs_are_the_real_codes():
    """七条诊断的 `code` 是精确匹配的字符串（人要拿它 grep、前端要拿它选文案）。

    两个方向都盯着：文档列的必须是 `compute_pipeline` 真报得出来的，
    报得出来的也必须都被列上——界面上冒出来一句文档里查不到的提示，
    读的人只能当它是 bug。

    另外钉住这一节的两条边界：这些诊断**不进 `forest["warnings"]`**（现存项目
    一个 result 都没声明，挂进全局警告栏等于每次打开都被念一遍），
    以及它们**一条都不改 L0–L4**（和第 3 / 6 / 15 节那些提示同一档）。
    """
    sec = pipeline_section()
    documented = set(re.findall(r"^\|\s*`([a-z]+_[a-z_]+)`\s*\|", sec, re.M))

    emitted: set[str] = set()
    # ① 一个 result 都没声明 → 只有那条 info。
    emitted |= {d["code"] for d in core.compute_pipeline({}, [])["diagnostics"]}
    # ② 悬空的 result。
    lone, _w = core.build_step("001_x", {"id": "001", "status": "done"}, "")
    emitted |= {d["code"] for d in
                core.compute_pipeline({"001": lone}, [{"step": "099", "note": "n"}])["diagnostics"]}
    # ③ 一次造齐其余五条：dead 在闭包里、L0 的成员、exclude 却被吃、
    #    成果自己写着 exclude、以及一个数据依赖的环。
    by_id, _c, _o = build_forest({
        "001": {"status": "done"},
        "002": {"parent": "001", "status": "dead"},
        "003": {"parent": "002", "status": "done", "pipeline": "exclude | 不算流程"},
        "004": {"parent": "001", "status": "done", "input": "003 | a.csv"},
        "005": {"status": "done", "input": "006 | b.csv", "pipeline": "exclude | 我也不算"},
        "006": {"status": "done", "input": "005 | c.csv"},
    })
    p = core.compute_pipeline(by_id, [{"step": "004", "note": "主结果"},
                                      {"step": "005", "note": "自己写了 exclude 的成果"}])
    emitted |= {d["code"] for d in p["diagnostics"]}

    assert emitted == {"pipeline_no_result", "dangling_result", "pipeline_dead_step",
                       "pipeline_weak_step", "pipeline_excluded_consumed",
                       "pipeline_excluded_result", "pipeline_cycle"}, \
        f"compute_pipeline 报出来的是 {sorted(emitted)} —— 造用例的场景该更新了"
    assert emitted == documented, (
        f"第 16 节没列全: {sorted(emitted - documented)}；"
        f"列了报不出来的: {sorted(documented - emitted)}")
    assert "bad_pipeline" in sec, "第 16 节没提 `bad_pipeline`（取值拼错时的静默无效）"

    # 同一份记录，加不加 pipeline 语义，等级必须一模一样。
    body = "## 为什么\n因为。\n\n## 做了什么\n跑了 `a.py`。\n\n## 结论\n成立。\n"
    kw = dict(id="002", status="done", title="t", commit="c1d2e3f", body=body,
              paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}], dirname="002_t")
    plain = core.Step(**kw)
    marked = core.Step(pipeline="exclude", pipeline_note="不算流程", **kw)
    assert core.traceability(marked)["level"] == core.traceability(plain)["level"] == "L2", \
        "`pipeline:` 把等级动了 —— 第 16 节说这七条一条都不降级"


def test_a_project_that_declares_no_result_notices_nothing(tmp_path):
    """第 16.1 节承诺「一个 `result:` 都没声明是常态，不是缺陷」，README 也照这句话写。

    这句话是有代价的承诺：现存的每一个项目都是这个状态，所以 `compile_forest`
    的输出里**不能因此多出任何东西**——多一个键，前端要多一条判空；多一条警告，
    每个项目每次打开都被念一遍，而那正是让人从此不看警告的做法。
    """
    p = W.create_project(tmp_path, "还没声明成果的项目")
    sd = core.steps_dir_of(tmp_path, p.slug)
    W.create_step(sd, title="第一步", body="## 为什么\n因为。\n")
    forest = core.compile_forest(sd)
    assert "pipeline" not in forest, "没声明成果的项目也长出了 pipeline 键"
    assert "pipeline" not in forest["steps"][0], "没声明成果的项目在步骤上多了字段"
    assert not [w for w in forest["warnings"] if "pipeline" in w["code"]
                or "result" in w["code"]], "没声明成果却多出了警告"

    # 那句「教怎么办」的话只在**主动问起**时才说，而且是 info 级不是 warn。
    alone = core.compute_pipeline({}, [])["diagnostics"]
    assert [d["level"] for d in alone] == ["info"], \
        f"空态那条诊断不是 info 级: {alone}"


def test_the_pipeline_level_is_the_weakest_member_not_the_weakest_chain():
    """第 10 / 16.4 节都写着：流程的等级取成员**自己**的等级，不是它的整链等级。

    差别是实打实的：被剔掉的 `dead` / `exclude` 祖先会把整链等级压下去，而那些
    步骤按定义就不是方法的一部分。照整链算的话，一条方法齐全的流程会因为它
    路过了一段已经放弃的路而被报成 L1——那个数会让人去补一份根本不该写进
    Methods 的记录。
    """
    full = ("## 为什么\n因为。\n\n## 做了什么\n跑了 `a.py`。\n\n"
            "## 结果\n0.9。\n\n## 结论\n成立。\n")
    by_id = {
        # 001 什么都没写 ⇒ L0，但它是 dead，按第 16.2 节的规则会被剔出流程。
        "001": core.Step(id="001", status="dead", title="走不通的那条", body="",
                         dirname="001_x"),
        "002": core.Step(id="002", parent="001", status="done", title="主结果",
                         commit="c1d2e3f", body=full,
                         paths=[{"location": "/blue/x", "note": "n", "kind": "hpc"}],
                         dirname="002_x"),
    }
    assert core.traceability(by_id["002"])["level"] == "L2"
    assert core.traceability(by_id["001"])["level"] == "L0"
    p = core.compute_pipeline(by_id, [{"step": "002", "note": "主结果"}])
    assert p["order"] == ["002"] and p["level"] == "L2", \
        f"流程等级被剔掉的那一步压下去了: {p['level']}（成员 {p['order']}）"


def test_both_docs_name_the_two_paths_the_same_way():
    """「开发路径」和「定稿流程」是这一版新造的一对术语，没有别处可查。

    README 叫「开发路径」而 SKILL 叫「完整树」、agent 叫「全量视图」的话，
    读的人不会意识到说的是同一件事，只会以为漏了一种东西。而这一对术语的
    全部作用就是让人在「我现在该看哪一条」上不用犹豫。
    """
    docs = [README, FORMAT, SKILL,
            ROOT / "agents" / "trace-auditor.md",
            ROOT / "agents" / "trace-reproducer.md"]
    for doc in docs:
        body = text(doc)
        for name in ("开发路径", "定稿流程"):
            assert name in body, f"{where(doc)} 里没有「{name}」这个说法"
    # 最容易被读错的一句：两者不是详略两版。README 和 FORMAT 都要挡住它。
    for doc in (README, FORMAT):
        assert "不是**开发路径的精简版**" in text(doc) or "**不是**开发路径的精简版" in text(doc), \
            f"{where(doc)} 没挡住「定稿流程 = 开发路径的精简版」这个误解"


def test_the_docs_are_honest_about_which_front_doors_carry_the_pipeline_fields():
    """和 inputs/code、branch/decision 那两条同一个理由：**静默丢字段是最坏的一类缺陷**。

    `pipeline` 已经在 `W.MUTABLE` 里，REST 的 PATCH 把请求体整个透传，所以那条路
    现在就能用；但 MCP 的 `t_update_step` 是白名单转发的，`POST …/steps` 也不读它。
    在门面补齐之前 SKILL.md 必须明说；补齐之后那段注意事项必须删掉，
    否则 agent 会一直多发一个 PATCH。两个方向都盯着。
    """
    mcp_src = (ROOT / "trace_mcp.py").read_text(encoding="utf-8")
    m = re.search(r"def t_update_step\(.*?\n\ndef ", mcp_src, re.S)
    assert m, "trace_mcp.py 里找不到 t_update_step 了（函数名变了？）"
    server_src = (ROOT / "trace_server.py").read_text(encoding="utf-8")
    c = re.search(r"async def api_create\(.*?\n\n", server_src, re.S)
    assert c, "trace_server.py 里找不到建步骤那条路由了（函数名变了？）"

    wired = '"pipeline"' in m.group(0) and 'payload.get("pipeline"' in c.group(0)
    warned = "还没透传" in text(SKILL)
    assert wired != warned, (
        "两个门面都收 pipeline 了，请删掉 SKILL.md 里那段「还没透传」的注意事项"
        if wired else
        "MCP 的 trace_update_step / POST …/steps 仍然丢掉 pipeline，"
        "SKILL.md 必须明说这件事（agent 会以为记上了）")


# ---------------------------------------------------------------- 前端资源
# README 承诺「静态导出 file:// 可直接打开、断网可用」，而页面要的脚本是靠
# STATIC_ASSETS 逐个拷过去的。i18n.js 上线时差点就漏在这里：页面引了它、
# 导出没拷它，于是导出的页面 window.i18n 是 undefined，第一次 t() 就整页白屏。


def test_the_readme_directory_tree_lists_every_file_under_web():
    """目录结构那段是唯一告诉人「前端由哪几个文件组成」的地方。
    漏一个的后果不是排版难看，是没人知道界面文案在 i18n.js 里。"""
    body = text(README)
    start = body.find("├── web/")
    assert start != -1, "README 的目录结构里找不到 web/ 那一行"
    listed = set()
    for line in body[start:].split("\n")[1:]:
        m = re.match(r"^│\s+[├└]──\s+(\S+)", line)
        if not m:
            break
        listed.add(m.group(1))
    actual = {p.name for p in (ROOT / "web").iterdir() if p.is_file()}
    assert listed == actual, f"README 的 web/ 清单是 {sorted(listed)}，实际是 {sorted(actual)}"


def test_the_static_export_ships_every_asset_the_page_loads():
    """页面 `__ASSET__x` 引什么，build 就必须拷什么，否则断网打开是一片白。"""
    import trace_cli

    referenced = set(re.findall(r"__ASSET__([A-Za-z0-9_.-]+)", (ROOT / "web" / "index.html").read_text(encoding="utf-8")))
    assert referenced, "index.html 里一个 __ASSET__ 都没有？"
    missing = referenced - set(trace_cli.STATIC_ASSETS)
    assert not missing, f"index.html 引了 {sorted(missing)}，trace_cli.STATIC_ASSETS 没有它们"
    for name in trace_cli.STATIC_ASSETS:
        assert (ROOT / "web" / name).is_file(), f"STATIC_ASSETS 里的 {name} 在 web/ 下不存在"


# ---------------------------------------------------------------- 交叉引用


def test_every_relative_link_in_the_docs_points_at_a_real_file():
    """只查指向 .md 的交叉引用。示例里的 `![](loss_curve.png)` 是**给人看的样例**，
    那些文件本来就不该存在于仓库里，别把它们也当成断链。"""
    for doc in DOCS:
        for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)\s]+\.md)\)", text(doc)):
            target = m.group(1).split("#", 1)[0]
            if "://" in target:
                continue
            assert (doc.parent / target).exists(), f"{where(doc)} 指向不存在的 {target}"


def test_the_skill_points_at_the_format_standard():
    """SKILL.md 是 agent 日常记录时读的东西，而它以前从头到尾没提过 FORMAT.md——
    于是「一份格式标准」这个需求在日常记录这条路上等于不存在。"""
    body = text(SKILL)
    assert "FORMAT.md" in body, "SKILL.md 一次都没提过格式标准在哪"
    assert "instructions" in body or "initialize" in body, \
        "还得说清 pip 装的机器上没有 FORMAT.md 时从哪拿到标准"


def test_the_readme_states_the_real_default_data_dir():
    """默认值改过一次（"." → ../trace-data），文档没跟上就等于教人往公开仓里写笔记。"""
    import trace_cli

    for doc in (README, DEPLOY):
        assert trace_cli.DEFAULT_DATA_DIR in text(doc), \
            f"{where(doc)} 没写出真实的默认数据仓路径 {trace_cli.DEFAULT_DATA_DIR}"


# ---------------------------------------------------------------- 部署命令
# 这三条是真踩过的坑，粘贴即失败。散文校对不了，但这三个形状可以。


def deploy_lines() -> list[str]:
    """部署文档里的命令行，续行接好、`#` 注释剥掉。

    注释必须剥：下面几条检查的是「粘进 shell 会不会炸」，而解释**为什么不能那样写**
    的注释里当然会出现那个错误写法本身。
    """
    joined = re.sub(r"\\\n\s*", " ", text(DEPLOY))
    return [re.sub(r"(^|\s)#.*$", "", line) for line in joined.split("\n")]


def test_the_deploy_guide_does_not_create_a_home_it_then_clones_into():
    """`useradd -m` 会用 /etc/skel 往 /srv/trace 里塞 .bashrc，随后
    `git clone … /srv/trace` 直接 fatal（目标目录已存在且非空）。
    主流发行版的 skel 都非空，所以这是必然失败，不是偶发。"""
    for line in deploy_lines():
        if "useradd" not in line:
            continue
        assert not re.search(r"(?<![\w-])-m(?![\w-])", line), \
            f"deploy/README 的 useradd 带了 -m，随后的 git clone 会失败: {line.strip()}"


def test_the_deploy_guide_never_redirects_into_a_trace_owned_path():
    """`sudo -u trace cmd >> /srv/trace/.ssh/known_hosts` 里的 `>>` 是**调用者**的
    shell 执行的，写不进 trace 的 0700 目录 —— 普通 sudoer 会拿到 Permission denied。
    正确写法是让写那一端也 sudo（`| sudo -u trace tee -a`）。"""
    for line in deploy_lines():
        if "sudo -u trace" not in line:
            continue
        assert not re.search(r">>?\s*/srv/", line), \
            f"deploy/README 用调用者的 shell 往 trace 的目录里重定向: {line.strip()}"


def test_the_ssh_clone_in_the_deploy_guide_gets_the_right_home():
    """`sudo -u trace git clone git@…` 用的 HOME 仍然是**调用者**的（sudo 默认不换 HOME，
    除非 -H / -i / always_set_home）。于是 ssh 去翻你自己的 ~/.ssh，找不到刚建的
    deploy key，clone 报 Permission denied (publickey) —— 而这一步是拿数据仓的唯一途径。"""
    for line in deploy_lines():
        if "git clone git@" not in line:
            continue
        assert re.search(r"sudo\s+-u\s+trace\s+-H\b", line) or "sudo" not in line, \
            f"deploy/README 的 SSH clone 没有 -H，HOME 会是调用者的: {line.strip()}"


def test_the_deploy_guide_sets_up_ssh_before_cloning_the_private_data_repo():
    """私有数据仓走 SSH。密钥要是排在 clone 后面，第一步就 Permission denied (publickey)。"""
    body = text(DEPLOY)
    keygen = body.find("ssh-keygen")
    deploy_key = body.find("Deploy Key")
    clone = body.find("trace-data.git")
    assert -1 not in (keygen, deploy_key, clone), \
        "deploy/README 里找不到 ssh-keygen / Deploy Key / trace-data.git 三者之一"
    assert keygen < clone and deploy_key < clone, \
        "deploy/README 把私有数据仓的 clone 排在了生成密钥 / 加 Deploy Key 之前"


def test_the_deploy_guide_asks_for_git_sync_explicitly():
    """init 的默认值已经改成「不开自动同步」。部署文档不显式 --git 的话，
    照着装完的服务器根本没有备份，而它把 git push 当成灾难恢复的唯一依据。"""
    inits = [line for line in deploy_lines() if "trace_cli.py init" in line]
    assert inits, "deploy/README 里没有 init 命令了？"
    assert any("--git" in line for line in inits), "deploy/README 的 init 没有显式 --git"
