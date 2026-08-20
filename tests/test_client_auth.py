"""客户端用哪一份凭据，必须是能问出来的。

显式 token 优先于设备凭证，而且是静默的：TRACE_TOKEN 还留在环境里时，刚做完的
trace-login 一点作用都没有，只会在某次写入时莫名 401。这里守住「工具会说出来」。
"""
import subprocess
import sys
from pathlib import Path

from research_trace.deliver import auth_source, auth_token
from research_trace.device_login import save_device_credential

ROOT = Path(__file__).resolve().parent.parent
URL = "https://trace.example.org/trace"


def write_credential(tmp_path, url=URL, credential="rtd_devcred"):
    """用真实的写入函数来造夹具：格式以后改了，这些测试也不会悄悄失真。"""
    return save_device_credential(
        tmp_path / "credentials.json", url,
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
        "import sys; sys.path.insert(0, %r)\n"
        "from research_trace.device_login import _warn_if_shadowed_by_explicit_token\n"
        "_warn_if_shadowed_by_explicit_token()\n" % str(ROOT)
    )
    noisy = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                           env={"TRACE_TOKEN": "legacy-token", "PATH": "/usr/bin:/bin"})
    assert "TRACE_TOKEN is set" in noisy.stderr
    assert "will NOT be" in noisy.stderr

    quiet = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin"})
    assert quiet.stderr == ""
