"""客户端用哪一份凭据，必须是能问出来的。

显式 token 优先于设备凭证，而且是静默的：TRACE_TOKEN 还留在环境里时，刚做完的
trace-login 一点作用都没有，只会在某次写入时莫名 401。这里守住「工具会说出来」。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_trace.deliver import auth_source, auth_token
from research_trace.device_login import save_device_credential

ROOT = Path(__file__).resolve().parent.parent
URL = "https://trace.example.org/trace"


def write_credential(tmp_path, url=URL, credential="rtd_devcred"):
    """用真实的写入函数来造夹具：格式以后改了，这些测试也不会悄悄失真。"""
    return save_device_credential(
        tmp_path / "credentials.json",
        url,
        {"credential": credential, "device": {"name": "box"}, "user": {"login": "someone"}},
    )


def test_device_credential_is_used_when_no_explicit_token(tmp_path):
    target = write_credential(tmp_path)
    bearer, source = auth_source(URL, "", target)
    assert bearer == "rtd_devcred"
    assert "device credential" in source


def test_explicit_token_wins_and_says_the_device_credential_is_ignored(tmp_path):
    target = write_credential(tmp_path)
    bearer, source = auth_source(URL, "legacy-token", target)
    assert bearer == "legacy-token"
    # 这句话就是这条测试存在的理由：不能只说「用了 token」，要说「凭证没被用」。
    assert "NOT being used" in source
    assert auth_token(URL, "legacy-token", target) == "legacy-token"


def test_no_credential_at_all_is_reported_as_such(tmp_path):
    bearer, source = auth_source(URL, "", tmp_path / "missing.json")
    assert bearer == ""
    assert source.startswith("none")


def test_a_credential_for_a_different_url_does_not_leak(tmp_path):
    target = write_credential(tmp_path, url="https://other.example.org")
    bearer, source = auth_source(URL, "", target)
    assert bearer == ""
    assert source.startswith("none")


def test_trace_login_warns_when_an_explicit_token_would_shadow_it(monkeypatch):
    """登录成功那一刻就得说，而不是等到某次 401。"""
    script = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
        "from research_trace.device_login import _warn_if_shadowed_by_explicit_token\n"
        "_warn_if_shadowed_by_explicit_token()\n"
    )
    noisy = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={"TRACE_TOKEN": "legacy-token", "PATH": "/usr/bin:/bin"},
    )
    assert "TRACE_TOKEN is set" in noisy.stderr
    assert "will NOT be" in noisy.stderr

    quiet = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )
    assert quiet.stderr == ""


# ------------------------------------------------------------- 网络路径
def test_every_client_refuses_redirects_with_the_real_cause():
    """一个 http:// 的 --url 被反向代理 301 到 https 时，urllib 会把 POST 改成 GET，
    上传就以 405 失败而且没人知道为什么。三个客户端共用一个拒绝重定向的 opener。"""
    import http.server
    import threading
    import urllib.error
    import urllib.request

    from research_trace import deliver, device_login, mcp

    class Redirect(http.server.BaseHTTPRequestHandler):
        def _go(self):
            self.send_response(301)
            self.send_header("Location", "https://example.invalid" + self.path)
            self.end_headers()

        do_GET = do_POST = _go

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(urllib.error.URLError, match=r"redirected POST .* final https"):
            device_login.open_url(urllib.request.Request(url + "/api/ingest", data=b"{}", method="POST"), timeout=5)
        with pytest.raises(deliver.DeliveryError, match="redirected"):
            deliver._post_json(url, "/api/ingest", {}, "", 5)
        with pytest.raises(RuntimeError, match="redirected"):
            mcp.Remote(url, "t").request("GET", "/api/health")
        with pytest.raises(device_login.DeviceLoginError, match="redirected"):
            device_login.request_json(url, "GET", "/api/health")
    finally:
        server.shutdown()


def test_a_401_from_central_tells_token_deployments_what_to_check(monkeypatch, tmp_path):
    from research_trace import deliver

    monkeypatch.setattr(deliver, "_post_json", lambda *a, **k: (401, {"error": "bad token"}))
    session = tmp_path / "outbox" / "ws" / "s1"
    (session / "pending").mkdir(parents=True)
    (session / "pending" / "e.json").write_text(
        json.dumps({"event_id": "e1", "session_id": "s1", "hook_event": "Stop", "payload": {}, "project_id": "p"}),
        encoding="utf-8",
    )
    report = {
        "delivered_batches": 0,
        "delivered_events": 0,
        "delivered_chunks": 0,
        "failed_batches": 0,
        "unreadable": 0,
        "conflicts": 0,
        "reclaimed": 0,
        "recovered": 0,
    }
    with pytest.raises(deliver.DeliveryError, match="TRACE_TOKEN/--token"):
        deliver.deliver_session(session, "http://central", "wrong", 5, report)
    assert (session / "pending" / "e.json").exists(), "a rejected batch stays pending"


def test_evidence_upload_timeout_scales_with_archive_size():
    from research_trace.run_evidence import upload_timeout

    assert upload_timeout(60, 1024) == 60
    assert upload_timeout(60, 100 * 1024 * 1024) > 400
    assert upload_timeout(600, 100 * 1024 * 1024) == 600
