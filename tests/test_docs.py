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
        for command in set(re.findall(r"\btrace-[a-z]+\b", text)):
            assert command in scripts, f"{name} mentions {command}, which pyproject does not install"


def test_every_environment_variable_the_docs_mention_exists_in_the_code():
    documented: set[str] = set()
    for text in DOCS.values():
        documented |= set(re.findall(r"\bTRACE_[A-Z0-9_]+\b", text))
    documented -= {"TRACE_"}
    for name in sorted(documented):
        assert name in CODE, f"docs mention {name}, which no module reads"


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
