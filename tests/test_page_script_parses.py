"""整页那段内联脚本必须能被 JS 解析。

这条看着像废话，但它是**唯一**接得住「改动切断了一个函数」的网。此前所有前端测试
都是从页面里切一段函数出来单独跑，切下来的那一段自己闭合，就算它上下文早已括号不平
也照样通过——真出过一次：一次搬移把 bindWorkspace 里的一个 onclick 从中间截断，
276 条测试全绿，而浏览器里整个页面一行 JS 都没跑起来。

所以这里不切片，整段丢给 node --check。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from research_trace.server import create_app


def test_the_whole_inline_script_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    with TestClient(create_app(tmp_path, token="secret")) as client:
        page = client.get("/").text

    assert page.count("<script>") == 1, "页面不止一段脚本了，这条测试得跟着扩到每一段"
    script = page[page.index("<script>") + len("<script>"): page.rindex("</script>")]
    source = tmp_path / "page.js"
    source.write_text(script, encoding="utf-8")

    result = subprocess.run([node, "--check", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, f"内联脚本解析不了：\n{result.stderr}"
