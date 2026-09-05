"""docs/FORMATS.md 是格式清单：代码里出现的每一种格式标识都必须在那里有一行。

格式标识散在 hook、投递器、Recorder、服务端和 trace-code 里；以前没有一处把它们放在一起，
新加一种（或升一个版本）没有任何东西提醒要写文档。这里守的是名字，不是语义。
"""

from __future__ import annotations

import re
from pathlib import Path

from research_trace.backup import FORMAT_VERSION, SUPPORTED_FORMAT_VERSIONS
from research_trace.storage import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "FORMATS.md").read_text(encoding="utf-8")
CODE_FILES = [*(ROOT / "research_trace").glob("*.py"), ROOT / "scripts" / "trace_hook.py"]


def test_every_format_identifier_in_the_code_is_listed():
    identifiers: set[str] = set()
    for path in CODE_FILES:
        identifiers |= set(re.findall(r"research-trace\.[a-z0-9\-]+\.v\d+", path.read_text(encoding="utf-8")))
    assert identifiers, "no format identifiers found — the regex or the code moved"
    missing = sorted(name for name in identifiers if f"`{name}`" not in DOC)
    assert not missing, f"docs/FORMATS.md does not list: {missing}"


def test_numeric_versions_in_the_doc_match_the_code():
    stated_schema = re.search(r"数据库 schema 版本为 (\d+)", DOC)
    stated_backup = re.search(r"备份格式版本为 (\d+)", DOC)
    assert stated_schema and int(stated_schema.group(1)) == SCHEMA_VERSION
    assert stated_backup and int(stated_backup.group(1)) == FORMAT_VERSION
    for retired in sorted(set(SUPPORTED_FORMAT_VERSIONS) - {FORMAT_VERSION}):
        assert f"版本 {retired}" in DOC, f"the code still reads backup format {retired}; the doc must say so"


def test_the_doc_lists_every_alembic_revision():
    revisions = sorted(p.stem for p in (ROOT / "research_trace" / "migrations" / "versions").glob("*.py"))
    assert revisions
    for revision in revisions:
        assert f"`{revision}`" in DOC, f"migration {revision} is not listed in docs/FORMATS.md"
