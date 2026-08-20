"""正文按 markdown 渲染，而不是把 ## 原样显示给人看。

渲染器本身的断言在 tests/md.test.js（要 node）。这里守两件 Python 侧能守的事：
① 那份 JS 测试确实能跑起来；② 页面确实调了渲染器 —— 渲染器再对，接线断了也白搭。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEBAPP = (ROOT / "research_trace" / "webapp.py").read_text(encoding="utf-8")
BEGIN = "/* === markdown renderer (begin) === */"
END = "/* === markdown renderer (end) === */"


def test_the_renderer_is_embedded_between_markers_the_js_tests_slice_on():
    """标记被改掉时必须是「测试挂了」，而不是「测试悄悄测了个空气」。"""
    assert BEGIN in WEBAPP and END in WEBAPP
    body = WEBAPP.split(BEGIN, 1)[1].split(END, 1)[0]
    assert "globalThis" in body or "global.md" in body
    assert len(body) > 5000, "抠出来的渲染器太短，标记多半错位了"


def test_prose_goes_through_the_renderer():
    """节点正文、Overview、章节摘要、评论 —— 人读的散文都要渲染。"""
    for needle in (
        "md.render(node.body",
        "md.render(project.overview)",
        "md.render(chapter.summary)",
        "md.render(comment.body",
    ):
        assert needle in WEBAPP, needle


def test_search_snippets_stay_plain_text():
    """搜索结果是按字符截断的片段，渲染一个被切断的 markdown 片段只会更难读。"""
    snippet = [line for line in WEBAPP.splitlines() if "slice(0, 260)" in line]
    assert snippet, "找不到搜索摘要那一行"
    assert "esc(" in snippet[0] and "md.render" not in snippet[0]


def test_rendered_containers_turn_off_pre_wrap():
    """.body 默认 pre-wrap 是给纯文本保换行用的；渲染之后再叠一层会把标签间的缩进画出来。"""
    assert re.search(r"\.md \{[^}]*white-space:\s*normal", WEBAPP)


def test_empty_state_copy_is_not_run_through_the_renderer():
    """空态文案是我们自己的字符串，不是用户内容，没必要过渲染器。"""
    assert "md.render(project.overview) : '尚未形成项目 Overview。'" in WEBAPP


@pytest.mark.skipif(shutil.which("node") is None, reason="没装 node")
def test_markdown_renderer_assertions_pass_under_node():
    result = subprocess.run(
        [shutil.which("node"), "--test", str(ROOT / "tests" / "md.test.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "# fail 0" in result.stdout
