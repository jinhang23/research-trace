"""The semantic Recorder is isolated from the main Claude Code session."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = (ROOT / "scripts" / "trace_hook.py").read_text(encoding="utf-8")
WORKER = (ROOT / "research_trace" / "recorder.py").read_text(encoding="utf-8")


def test_stop_path_never_blocks_or_asks_the_main_agent_to_fork():
    handle = HOOK[HOOK.index("def handle("):]
    assert "_nudge(" not in handle
    assert '"decision": "block"' not in handle
    assert "SendMessage" not in handle
    assert "subagent_type='fork'" not in handle


def test_independent_cli_has_no_project_tools_or_settings():
    assert '"--tools", ""' in WORKER
    assert '"--setting-sources", ""' in WORKER
    assert '"--strict-mcp-config"' in WORKER
    assert '"--permission-mode", "dontAsk"' in WORKER