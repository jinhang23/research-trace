"""Cheap drift guards between the docs and the code they describe.

这一轮之前发生过两次同一种事故：文档写着某个行为，代码早就不是那样了，而三份文档
互相引用所以读起来自洽。下面每条断言都只钉一个「文档说的名字必须真的存在」，
不试图检查语义——语义靠人读，名字靠这里。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = {
    "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
    "docs/QUICKSTART.md": (ROOT / "docs" / "QUICKSTART.md").read_text(encoding="utf-8"),
    "docs/REQUIREMENTS.md": (ROOT / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8"),
    "hooks/RECORDER_PROTOCOL.md": (ROOT / "hooks" / "RECORDER_PROTOCOL.md").read_text(encoding="utf-8"),
    "skills/research-trace/SKILL.md": (
        ROOT / "skills" / "research-trace" / "SKILL.md"
    ).read_text(encoding="utf-8"),
    # 新文档一样要被漂移守卫覆盖：命令、环境变量、REST 路径都会对着代码核。
    "docs/DESIGN.md": (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8"),
    "CHANGELOG.md": (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
}
CODE = "\n".join(
    path.read_text(encoding="utf-8")
    for path in [*(ROOT / "research_trace").glob("*.py"), ROOT / "scripts" / "trace_hook.py"]
)


def test_no_document_still_describes_the_deleted_receipt_or_archive_directory():
    """`TRACE_RECEIPT` 和 `awaiting_upload/` 都已作废。文档里只允许出现在
    「这个东西没有了」的说明句里，不允许再作为现行行为被描述。"""
    for name, text in DOCS.items():
        for line in text.splitlines():
            for dead in ("TRACE_RECEIPT", "awaiting_upload"):
                if dead not in line:
                    continue
                assert any(
                    marker in line for marker in
                    ("没有", "取消", "作废", "遗留", "旧", "gone", "earlier version", "An earlier")
                ), f"{name}: {dead} still described as current behaviour: {line.strip()}"


def test_every_command_the_docs_mention_is_a_real_console_script():
    scripts = set(
        re.findall(r"^([\w-]+)\s*=", (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    )
    for name, text in DOCS.items():
        # 前面不能是词字符或连字符：否则 `[research-trace-recorder]` 这种自有标记里会被
        # 切出一个并不存在的命令 `trace-recorder`，把一条正确的文档判成错的。
        for command in set(re.findall(r"(?<![\w-])trace-[a-z]+\b", text)):
            assert command in scripts, f"{name} mentions {command}, which pyproject does not install"


def test_every_environment_variable_the_docs_mention_exists_in_the_code():
    documented: set[str] = set()
    for text in DOCS.values():
        documented |= set(re.findall(r"\bTRACE_[A-Z0-9_]+\b", text))
    documented -= {"TRACE_"}
    for name in sorted(documented):
        assert name in CODE, f"docs mention {name}, which no module reads"


def test_the_backup_format_version_in_the_docs_matches_the_code():
    """上一轮真的发生过：代码把备份格式升到 3，README 和 QUICKSTART 还写着「版本为 2，
    更早的导出会被 verify 拒绝」。名字类的守卫抓不到这种**数值**漂移——文档里的每个
    字符串都合法存在，只是数字过时了，而这条正好是「几年前的备份还读不读得回来」。"""
    from research_trace.backup import FORMAT_VERSION, SUPPORTED_FORMAT_VERSIONS

    stated = {
        name: set(re.findall(r"备份格式版本(?:现在)?(?:为|是)\s*\**(\d+)", text))
        for name, text in DOCS.items()
    }
    assert any(stated.values()), "no document states the backup format version any more"
    for name, versions in stated.items():
        for version in versions:
            assert int(version) == FORMAT_VERSION, (
                f"{name} says the backup format version is {version}, the code writes {FORMAT_VERSION}"
            )
    # 读取端永不退役：只要代码仍然接受旧版本，文档就必须还说得出这件事
    for retired in sorted(set(SUPPORTED_FORMAT_VERSIONS) - {FORMAT_VERSION}):
        assert any(f"版本 {retired}" in text for text in DOCS.values()), (
            f"the code still verifies/restores format {retired}; no document promises it"
        )


def test_no_document_still_says_the_three_new_features_are_missing():
    """文档→代码方向的守卫挡不住「文档说某件事还没做，其实早就做完了」。
    这三句都出现过，而且低估能力和高估能力一样会误导人。"""
    stale = (
        "备份仍然是一棵全量树",
        "数据流视图尚未实现",
        "按年份/容量分卷与备份容量告警",
        "团队配置映射（第三种【未实现】）",
    )
    for name, text in DOCS.items():
        for line in text.splitlines():
            for phrase in stale:
                assert phrase not in line, f"{name} still says: {line.strip()}"


def test_every_rest_path_the_docs_mention_exists_in_the_server():
    """文档里写出来的端点必须真的挂在 server 上。团队映射和数据流这两组是这一轮新加的，
    文档先写好、路由忘了挂，读者只会拿到 404 而不知道是谁的错。"""
    routes = re.sub(
        r"\{[a-z_]+\}", "{x}",
        (ROOT / "research_trace" / "server.py").read_text(encoding="utf-8"),
    )
    documented: dict[str, str] = {}
    for name, text in DOCS.items():
        for line in text.splitlines():
            # 「`/api/v2/*` → `/api/*`」这类行是在记录一次改名，不是在描述现行路由
            if "→" in line:
                continue
            for path in re.findall(r"/api/[a-z0-9/{}_-]+", line):
                documented.setdefault(re.sub(r"\{[a-z_]+\}", "{x}", path.rstrip("/.,)`")), name)
    assert len(documented) > 5, documented
    for route, name in sorted(documented.items()):
        assert route in routes, f"{name} mentions {route}, which server.py does not route"


def test_the_plugin_manifest_does_not_promise_global_capture():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    capture = manifest["userConfig"]["capture"]["description"]
    assert "opt-in" in capture or "trace-project bind" in capture, capture


def test_the_marker_shape_is_the_same_everywhere():
    """marker 是 hook、投递器、CLI、MCP 和三份文档共用的契约。
    名字对不上就等于两套格式，谁写的另一边读不到。"""
    from research_trace.deliver import MARKER_NAME, MARKER_SCHEMA

    shown = 0
    for name, text in DOCS.items():
        # 只检查真的把 marker 内容摊开来写的地方；只提一句文件名的不算重复定义格式
        if '"workspace_key"' not in text:
            continue
        shown += 1
        assert MARKER_SCHEMA in text, f"{name} shows a marker without the schema string"
        assert '"capture"' in text, f"{name} shows a marker without the capture switch"
    assert shown >= 2, "the marker shape should be spelled out in at least two documents"
    assert MARKER_NAME in CODE and MARKER_SCHEMA in CODE


def test_every_trace_tool_the_skill_mentions_is_a_real_mcp_tool():
    """SKILL.md 是主 agent 唯一的指导，它点名的工具必须真的在工具表里。

    上一版 skill 就是这么烂掉的：v1 删掉之后它还留在用户机器上，指着
    `trace_new_step` / `trace_update_step` 这些已经不存在的工具，比没有 skill 更糟。
    """
    skill = DOCS["skills/research-trace/SKILL.md"]
    real = set(re.findall(r'"name":\s*"(trace_[a-z_]+)"', (ROOT / "research_trace" / "mcp.py").read_text(encoding="utf-8")))
    assert len(real) == 7, f"工具表变了，这条测试要跟着改：{sorted(real)}"
    mentioned = set(re.findall(r"\btrace_[a-z0-9_]+", skill))
    assert not (mentioned - real), f"SKILL.md 提到了不存在的工具：{sorted(mentioned - real)}"
    assert not (real - mentioned), f"SKILL.md 漏讲了工具：{sorted(real - mentioned)}"


def test_the_skill_has_the_frontmatter_that_makes_it_loadable():
    """没有 frontmatter 的 SKILL.md 不会被当成 skill 加载，而这个失败是静默的。"""
    skill = DOCS["skills/research-trace/SKILL.md"]
    front = re.match(r"^---\n(.*?)\n---\n", skill, re.S)
    assert front, "SKILL.md 缺 frontmatter"
    assert re.search(r"^name:\s*research-trace\s*$", front.group(1), re.M)
    description = re.search(r"^description:\s*(\S.*)$", front.group(1), re.M)
    assert description, "缺 description —— 模型靠它决定要不要加载这个 skill"
    # 触发词是这份 skill 唯一的入口，中英文都得有
    assert "之前试过什么" in description.group(1)
    assert "provenance" in description.group(1)


def test_no_document_links_to_a_file_that_does_not_exist():
    """文档之间互相指路，指错了比不指更糟 —— 读的人会以为那份东西不存在。"""
    broken = []
    for name in DOCS:
        source = ROOT / name
        for text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", DOCS[name]):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not (source.parent / target.split("#")[0]).exists():
                broken.append(f"{name}: [{text}]({target})")
    assert not broken, "断链:\n" + "\n".join(broken)
