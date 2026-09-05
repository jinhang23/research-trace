"""版本只有一个来源，其余地方必须与它一致 —— 而且必须真的会变。

`claude plugin update` 是按版本号判断要不要重新拷贝的：版本没动，它就打印
「already at the latest version」然后什么都不做。所以插件包里的东西（hooks.json、
scripts/trace_hook.py、skills/）改了却忘了 bump，等于改了个寂寞：
marketplace 那份是新的，真正在跑的插件缓存还是旧的，谁都拿不到。

来源是 research_trace/__init__.py 的 __version__（PEP 440）。pyproject 用 dynamic version 读它，
server.py / mcp.py 引用 PLUGIN_VERSION；只有两个 JSON 清单必须手写 semver 形式，这里守着它们。
"""

import json
import re
from pathlib import Path

import research_trace

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_reads_the_version_from_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert 'version = { attr = "research_trace.__version__" }' in text
    assert not re.search(r'^version = "', text, re.M), "pyproject must not carry a second literal version"


def test_server_and_mcp_do_not_carry_their_own_version_literal():
    for name in ("server.py", "mcp.py"):
        text = (ROOT / "research_trace" / name).read_text(encoding="utf-8")
        assert "PLUGIN_VERSION" in text, f"{name} should import PLUGIN_VERSION"
        assert not re.search(r'version["=: ]+"\d+\.\d+\.\d+', text), f"{name} still has a literal version"


def test_the_plugin_manifest_and_marketplace_state_the_same_version_as_the_package():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entries = market.get("plugins") or market.get("entries") or []
    listed = next(e for e in entries if e.get("name") == "research-trace")["version"]
    assert plugin == listed == research_trace.PLUGIN_VERSION, (plugin, listed, research_trace.PLUGIN_VERSION)


def test_plugin_version_conversion_covers_the_pre_release_forms():
    convert = research_trace.plugin_version
    assert convert("2.0.0a29") == "2.0.0-alpha.29"
    assert convert("2.0.0b1") == "2.0.0-beta.1"
    assert convert("2.0.0rc2") == "2.0.0-rc.2"
    assert convert("2.0.0") == "2.0.0"
