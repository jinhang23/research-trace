"""Research Trace: central research memory and recorder APIs."""

import re

from .storage import Conflict, NotFound, Store

__all__ = ["PLUGIN_VERSION", "Conflict", "NotFound", "Store", "__version__"]

# 唯一的版本来源（PEP 440）。pyproject 通过 dynamic version 读这一行；
# server.py / mcp.py 引用它；插件清单里的 semver 写法由 PLUGIN_VERSION 给出，
# tests/test_version_is_consistent.py 守着 .claude-plugin/*.json 与它一致。
__version__ = "2.0.0a39"


def plugin_version(value: str = __version__) -> str:
    """PEP 440 → 插件清单的 semver 写法：2.0.0a29 → 2.0.0-alpha.29，2.0.0 → 2.0.0。"""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", value)
    if not match:
        return value
    base, tag, number = match.groups()
    names = {"a": "alpha", "b": "beta", "rc": "rc"}
    return f"{base}-{names[tag]}.{number}" if tag else base


PLUGIN_VERSION = plugin_version()
