"""内网模式：一个共享访问密钥同时守读和写，网页用它登录，机器一次 trace-login --token。

以前只有两档：完全开放的读（TRACE_TOKEN 只挡写）和 GitHub OAuth。内网小团队不想申请
OAuth App，又不能把原始 transcript 对整个网段公开——这一档就是给这种部署的。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from research_trace import device_login
from research_trace.auth import client_in_networks, parse_networks, token_session_cookie, token_session_user
from research_trace.deliver import auth_source
from research_trace.server import create_app, load_env_file, write_init_env


def test_token_sessions_are_signed_with_the_key_and_expire():
    raw = token_session_cookie("Jinhang Wei", "k1")
    user = token_session_user(raw, "k1")
    assert user["login"] == "Jinhang Wei" and user["role"] == "admin" and user["kind"] == "token"
    assert token_session_user(raw, "other-key") is None, "rotating the key invalidates every session"
    assert token_session_user(raw[:-1] + ("0" if raw[-1] != "0" else "1"), "k1") is None
    assert token_session_user("garbage", "k1") is None
    assert token_session_user(token_session_cookie("x", "k1", days=-1), "k1") is None


def test_protected_reads_require_the_key_or_a_web_login(tmp_path):
    app = create_app(tmp_path, token="k1", protect_reads=True)
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 401
        health = client.get("/api/health").json()
        assert health["authentication_required"] is True and health["access_mode"] == "token"
        assert "counts" not in health, "an unauthenticated health check reveals nothing about the data"
        assert health["oauth_enabled"] is False
        assert client.get("/api/auth/config").json() == {"enabled": True, "mode": "token", "login_url": None}

        bearer = {"Authorization": "Bearer k1"}
        assert client.get("/api/projects", headers=bearer).status_code == 200
        assert client.get("/api/health", headers=bearer).json()["authenticated"] is True

        assert client.post("/api/auth/token-login", json={"key": "wrong", "name": "x"}).status_code == 401
        login = client.post("/api/auth/token-login", json={"key": "k1", "name": "  Jinhang  Wei "})
        assert login.status_code == 200
        assert login.json()["user"]["login"] == "Jinhang Wei"
        assert "trace_session" in login.cookies

        me = client.get("/api/auth/me").json()
        assert me["user"]["login"] == "Jinhang Wei" and me["csrf_token"]
        assert client.get("/api/projects").status_code == 200, "the cookie now reads"

        project = client.post("/api/projects", json={"name": "P"})
        assert project.status_code == 403, "writes from the browser still need the CSRF token"
        csrf = {"X-CSRF-Token": me["csrf_token"]}
        project = client.post("/api/projects", headers=csrf, json={"name": "P"}).json()
        node = client.post(
            "/api/record",
            headers=csrf,
            json={"project_id": project["id"], "idempotency_key": "n1", "title": "人写的"},
        ).json()
        assert node["created_by"] == "human", "a person at a browser with the key is a human actor"

        out = client.post("/api/auth/logout", headers=csrf)
        assert out.status_code == 200
        client.cookies.clear()
        assert client.get("/api/projects").status_code == 401


def test_the_default_stays_open_reads_for_loopback_trials(tmp_path):
    app = create_app(tmp_path, token="k1")
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 200
        assert client.get("/api/auth/config").json()["mode"] == "open"
        assert client.post("/api/auth/token-login", json={"key": "k1", "name": "x"}).status_code == 404


def test_network_allowlist_uses_the_forwarded_address_only_behind_a_trusted_proxy(tmp_path):
    assert parse_networks("10.0.0.0/8, 192.168.1.0/24") and parse_networks("") == []
    nets = parse_networks("10.0.0.0/8")
    assert client_in_networks("10.4.5.6", nets) and not client_in_networks("192.168.1.1", nets)
    assert not client_in_networks("testclient", nets), "an unparsable address is never allowed"
    assert client_in_networks("anything", [])

    app = create_app(tmp_path, token="k1", allowed_networks="10.0.0.0/8", trust_proxy_headers=True)
    with TestClient(app) as client:
        assert client.get("/api/health", headers={"X-Forwarded-For": "10.1.2.3"}).status_code == 200
        assert client.get("/api/health", headers={"X-Forwarded-For": "192.168.1.1"}).status_code == 403
    app = create_app(tmp_path / "b", token="k1", allowed_networks="10.0.0.0/8", trust_proxy_headers=False)
    with TestClient(app) as client:
        # without a trusted proxy the header is ignored and the TestClient address is not an IP
        assert client.get("/api/health", headers={"X-Forwarded-For": "10.1.2.3"}).status_code == 403


def test_init_writes_a_protected_env_file_and_env_file_loads_it(tmp_path):
    target = write_init_env(tmp_path / "data", host="0.0.0.0", port=8765)
    text = target.read_text(encoding="utf-8")
    assert "TRACE_PROTECT_READS=true" in text and "TRACE_TOKEN=rt_" in text
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SystemExit):
        write_init_env(tmp_path / "data", host="0.0.0.0", port=8765)
    environ = {"TRACE_DATA": "keep-me"}  # never touch the real process environment from a test
    loaded = load_env_file(target, environ)
    assert loaded == 2 and environ["TRACE_DATA"] == "keep-me", "existing environment wins"
    assert environ["TRACE_TOKEN"].startswith("rt_") and environ["TRACE_PROTECT_READS"] == "true"


def test_trace_login_token_stores_the_key_for_every_client(tmp_path, monkeypatch):
    credential_file = tmp_path / "credentials.json"
    monkeypatch.setattr(
        device_login,
        "request_json",
        lambda url, method, path, value=None, credential=None, timeout=30: (
            (200, {"write_protected": True, "authenticated": True})
            if credential == "k1"
            else (401, {"error": "rejected"})
        ),
    )
    assert (
        device_login.main(
            ["--url", "http://central:8765", "--token", "nope", "--credential-file", str(credential_file)]
        )
        != 0
    )
    assert (
        device_login.main(["--url", "http://central:8765/", "--token", "k1", "--credential-file", str(credential_file)])
        == 0
    )
    stored = json.loads(credential_file.read_text(encoding="utf-8"))["credentials"]["http://central:8765"]
    assert stored["credential"] == "k1" and stored["kind"] == "token"
    assert device_login.load_device_credential(credential_file, "http://central:8765")["credential"] == "k1"
    bearer, source = auth_source("http://central:8765", "", credential_file)
    assert bearer == "k1" and "shared access key" in source
    # an explicit token still wins, and says so
    bearer, source = auth_source("http://central:8765", "explicit", credential_file)
    assert bearer == "explicit" and "NOT being used" in source
