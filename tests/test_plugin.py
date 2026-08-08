"""插件清单的断言。

清单是 JSON，写坏了插件装上就是坏的，而且坏在用户机器上、不在我这里。
所以它和代码之间的每一处耦合都要有测试盯着。
"""

import json
import re
from pathlib import Path

import pytest

import trace_mcp as M

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
MARKET = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))


def test_manifests_are_valid_and_named_consistently():
    assert PLUGIN["name"] == "research-trace"
    assert [p["name"] for p in MARKET["plugins"]] == [PLUGIN["name"]]
    assert MARKET["plugins"][0]["version"] == PLUGIN["version"], "市场条目和清单的版本要一致"


def test_declared_component_directories_exist_and_are_not_empty():
    """指着空目录会让 Claude Code 在加载时报警告。"""
    for key in ("skills", "commands"):
        d = ROOT / PLUGIN[key].lstrip("./")
        assert d.is_dir(), f"{key} 指向的 {d} 不存在"
        assert any(d.iterdir()), f"{key} 指向的 {d} 是空的"
    assert (ROOT / "skills" / "research-trace" / "SKILL.md").is_file()


def test_plugin_does_not_declare_directories_it_has_no_content_for():
    for key in ("agents", "hooks", "outputStyles", "lspServers"):
        if key in PLUGIN:
            target = ROOT / str(PLUGIN[key]).lstrip("./")
            assert target.exists(), f"清单声明了 {key} 但 {target} 不存在"


# ------------------------------------------------------------ MCP server 声明


def test_mcp_server_points_at_a_file_that_exists():
    srv = PLUGIN["mcpServers"]["trace"]
    args = srv["args"]
    assert len(args) == 1
    rel = args[0].replace("${CLAUDE_PLUGIN_ROOT}/", "")
    assert (ROOT / rel).is_file(), f"清单指向的 {rel} 不在仓库里"


def test_mcp_server_only_sets_env_vars_the_code_actually_reads():
    """清单和 make_backend() 之间的耦合：改了一边忘了另一边，插件就会静默失灵。"""
    declared = set(PLUGIN["mcpServers"]["trace"]["env"])
    src = (ROOT / "trace_mcp.py").read_text(encoding="utf-8")
    read = set(re.findall(r'os\.environ(?:\.get)?[\(\[]"(TRACE_[A-Z_]+)"', src))
    assert declared <= read, f"清单设了但代码不读: {declared - read}"


def test_every_user_config_key_is_actually_substituted_somewhere():
    """声明了却没人用的配置项＝白问用户一次。"""
    keys = set(PLUGIN["userConfig"])
    blob = json.dumps(PLUGIN["mcpServers"], ensure_ascii=False)
    used = set(re.findall(r"\$\{user_config\.([A-Za-z_][A-Za-z0-9_]*)\}", blob))
    assert keys == used, f"声明了没用: {keys - used}；用了没声明: {used - keys}"


def test_user_config_entries_have_the_required_fields():
    for key, spec in PLUGIN["userConfig"].items():
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), f"{key} 不是合法标识符"
        for field in ("type", "title", "description"):
            assert spec.get(field), f"{key} 缺 {field}"
        assert spec["type"] in ("string", "number", "boolean", "directory", "file"), spec["type"]
    assert PLUGIN["userConfig"]["token"].get("sensitive") is True, "令牌必须标 sensitive"


def test_python_default_is_python3_not_python():
    """`python` 在 Windows 上经常指向别的软件自带的 2.x。"""
    assert PLUGIN["userConfig"]["python"]["default"] == "python3"
    assert PLUGIN["mcpServers"]["trace"]["command"] == "${user_config.python}"


# ------------------------------------------------------------ 空值语义
# 用户把某一项留空时，环境变量会是空串。代码必须把空串当成"没配"。


def test_blank_user_config_values_behave_as_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "")          # 用户没填远端
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    be = M.make_backend()
    assert isinstance(be, M.LocalBackend), "留空的远端地址不该盖过本地目录"


def test_whitespace_only_values_also_count_as_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "   ")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    assert isinstance(M.make_backend(), M.LocalBackend)


def test_remote_wins_when_both_are_filled(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://例子/t/abc")
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    be = M.make_backend()
    assert isinstance(be, M.HttpBackend) and be.base == "https://例子/t/abc"


def test_all_blank_gives_an_actionable_error(monkeypatch, tmp_path):
    for k in ("TRACE_URL", "TRACE_TOKEN", "TRACE_DATA"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("TRACE_CONFIG", str(tmp_path / "没有这个文件.json"))
    with pytest.raises(M.ToolError) as e:
        M.make_backend()
    assert "TRACE_DATA" in str(e.value) and "trace.json" in str(e.value)


# ------------------------------------------------------------ 令牌不该让人手抄
# 令牌在 init 时就随机生成好了。本地模式根本用不上；服务在本机时程序能自己找到。


def _server_repo(tmp_path: Path, space: str, token: str) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"space": space, "token": token}), encoding="utf-8")
    return d


def test_local_mode_needs_no_token_at_all(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_DATA", str(tmp_path))
    for k in ("TRACE_URL", "TRACE_TOKEN"):
        monkeypatch.setenv(k, "")
    assert isinstance(M.make_backend(), M.LocalBackend)


def test_token_is_discovered_from_the_local_config_when_the_space_matches(tmp_path: Path, monkeypatch):
    """服务跑在本机时，令牌就在 config.json 里，不该再让人抄一遍。"""
    repo = _server_repo(tmp_path, "Pez39Q2KiYjpDNBHzZMfUQ", "秘密令牌")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "http://127.0.0.1:8123/t/Pez39Q2KiYjpDNBHzZMfUQ")
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(repo))
    assert M.make_backend().token == "秘密令牌"


def test_a_token_for_a_different_server_is_never_used(tmp_path: Path, monkeypatch):
    """本地留着的可能是另一台服务器的令牌，拿去用只会换来莫名其妙的 401。"""
    repo = _server_repo(tmp_path, "本机的space", "本机的令牌")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://别的域名/t/完全不同的space")
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(repo))
    assert M.make_backend().token == "", "space 对不上就不能用那个令牌"


def test_an_explicit_token_always_wins(tmp_path: Path, monkeypatch):
    repo = _server_repo(tmp_path, "space1", "自动找到的")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://x/t/space1")
    monkeypatch.setenv("TRACE_TOKEN", "手填的")
    monkeypatch.setenv("TRACE_DATA", str(repo))
    assert M.make_backend().token == "手填的"


def test_discovery_survives_a_broken_config_json(tmp_path: Path, monkeypatch):
    d = tmp_path / "repo"
    d.mkdir()
    (d / "config.json").write_text("{ 这不是 JSON", encoding="utf-8")
    monkeypatch.delenv("TRACE_CONFIG", raising=False)
    monkeypatch.setenv("TRACE_URL", "https://x/t/space1")
    monkeypatch.setenv("TRACE_TOKEN", "")
    monkeypatch.setenv("TRACE_DATA", str(d))
    assert M.make_backend().token == ""      # 不崩，只是找不到
