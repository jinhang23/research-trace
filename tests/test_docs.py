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
    Step 必须把**每一个**可选键都填上，包括 lang。
    """
    step = core.Step(id="001", parent="000", status="done", title="t", lang="zh",
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


def test_the_untranslated_fields_named_in_the_readme_exist():
    """README 的端点表点名了 /untranslated 回哪几个字段，agent 会照着取。"""
    report = M.untranslated_report({"steps": [], "project": "p"}, None, "en")
    for field in ("missing", "translated", "native", "project_note"):
        assert field in report, f"README 说 /untranslated 回 {field}，实际没有"
        assert f"`{field}" in text(README) or field in text(README)


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
