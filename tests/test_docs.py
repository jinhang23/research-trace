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
    """判据是 render_note 真写出来的键，不是我照着代码抄的一份清单。"""
    step = core.Step(id="001", parent="000", status="done", title="t", date="2026-01-01",
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
