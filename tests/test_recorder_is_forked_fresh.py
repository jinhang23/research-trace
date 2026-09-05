"""The semantic Recorder is isolated from the main Claude Code session."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = (ROOT / "scripts" / "trace_hook.py").read_text(encoding="utf-8")
WORKER = (ROOT / "research_trace" / "recorder.py").read_text(encoding="utf-8")


def test_stop_path_never_blocks_or_asks_the_main_agent_to_fork():
    handle = HOOK[HOOK.index("def handle(") :]
    assert "_nudge(" not in handle
    assert "_spawn_recorder" not in HOOK
    assert '"decision": "block"' not in handle
    assert "SendMessage" not in handle
    assert "subagent_type='fork'" not in handle


def test_independent_cli_has_no_project_tools_or_settings():
    """看真正构造出来的命令，而不是源码字面量：排版换行不该让这条守卫失明。"""
    from research_trace.recorder import build_command

    command = build_command("claude", "sonnet")
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'


def test_independent_cli_is_stateless():
    """每轮都带完整 packet，--resume 只会把之前每一轮的 packet 再送一遍；固定前缀本身就能命中缓存。"""
    from research_trace.recorder import build_command

    command = build_command("claude", "sonnet")
    assert "--no-session-persistence" in command
    assert not {"--resume", "--session-id"} & set(command)
    assert '"--resume"' not in WORKER and '"--session-id"' not in WORKER
    # 官方帮助写明该参数在传 --system-prompt 时被忽略；旧版 CLI 还会因为不认识它而 rc=1
    assert "--exclude-dynamic-system-prompt-sections" not in WORKER


def test_the_hook_never_imports_a_network_module():
    """REQUIREMENTS §15 的头号不变量「hook 全程不发网络请求」此前没有任何测试守着。"""
    header = HOOK[: HOOK.index("def _now(")]
    for module in ("urllib", "http.client", "requests", "httpx", "socket", "ssl"):
        assert f"import {module}" not in header, f"hook imports {module}"
        assert f"from {module}" not in header, f"hook imports {module}"
    assert "urlopen" not in HOOK and "http.client" not in HOOK
