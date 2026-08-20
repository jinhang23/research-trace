"""版本号写在六个地方，它们必须一致 —— 而且必须真的会变。

`claude plugin update` 是按版本号判断要不要重新拷贝的：版本没动，它就打印
「already at the latest version」然后什么都不做。所以插件包里的东西（hooks.json、
scripts/trace_hook.py、skills/）改了却忘了 bump，等于改了个寂寞：
marketplace 那份是新的，真正在跑的插件缓存还是旧的，谁都拿不到。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def normalize(value: str) -> str:
    """PEP 440 的 2.0.0a5 和 plugin manifest 的 2.0.0-alpha.5 是同一个版本。"""
    return re.sub(r"[-.]?alpha[-.]?", "a", value.strip()).replace("-", "")


def collect() -> dict[str, str]:
    found = {}
    found["pyproject.toml"] = re.search(
        r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    found["research_trace/__init__.py"] = re.search(
        r'__version__ = "([^"]+)"', (ROOT / "research_trace" / "__init__.py").read_text(encoding="utf-8")).group(1)
    found["research_trace/server.py"] = re.search(
        r'version="([0-9][^"]*)"', (ROOT / "research_trace" / "server.py").read_text(encoding="utf-8")).group(1)
    found["research_trace/mcp.py"] = re.search(
        r'"version": "([0-9][^"]*)"', (ROOT / "research_trace" / "mcp.py").read_text(encoding="utf-8")).group(1)
    found["plugin.json"] = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entries = market.get("plugins") or market.get("entries") or []
    found["marketplace.json"] = [e for e in entries if e.get("name") == "research-trace"][0]["version"]
    return found


def test_every_place_that_states_a_version_agrees():
    found = collect()
    normalized = {name: normalize(value) for name, value in found.items()}
    assert len(set(normalized.values())) == 1, f"版本号不一致: {found}"


def test_the_plugin_manifest_and_the_marketplace_entry_are_byte_identical():
    """两边不一致时，marketplace 说的是 A、装上去的是 B，排查会非常绕。"""
    found = collect()
    assert found["plugin.json"] == found["marketplace.json"], found
