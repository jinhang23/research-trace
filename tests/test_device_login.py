from __future__ import annotations

import json

import research_trace.mcp as mcp
import research_trace.device_login as device_login
from research_trace.device_login import (
    load_device_credential,
    remove_device_credential,
    save_device_credential,
)


def issued(credential: str = "rtv2d_" + "x" * 64):
    return {
        "status": "authorized",
        "credential": credential,
        "device": {"id": "dev_1", "name": "hipergator",
                   "expires_at": "2026-11-16T00:00:00.000+00:00"},
        "user": {"id": "usr_1", "login": "alice", "role": "member"},
        "expires_at": "2026-11-16T00:00:00.000+00:00",
    }


def test_credential_store_is_bound_to_server_url_and_removable(tmp_path):
    path = tmp_path / "credentials.json"
    save_device_credential(path, "https://trace.example/", issued())
    assert load_device_credential(path, "https://trace.example")["user"]["login"] == "alice"
    assert load_device_credential(path, "https://other.example") is None
    assert "rtv2d_" in path.read_text(encoding="utf-8")
    remove_device_credential(path, "https://trace.example")
    assert load_device_credential(path, "https://trace.example") is None


def test_mcp_login_never_returns_the_raw_device_credential_to_model(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    remote = mcp.Remote("https://trace.example", credential_file=path)
    started = {
        "device_code": "secret-device-code-" + "d" * 40,
        "user_code": "ABCD-EFGH",
        "device_name": "workstation",
        "verification_uri": "https://trace.example/device",
        "expires_in": 600,
        "interval": 3,
    }
    monkeypatch.setattr(mcp, "start_login", lambda url, name: dict(started))
    first = remote.device_login("start", "workstation")
    assert first["status"] == "approval_required"
    assert "device_code" not in first
    # 服务端已经不返回 verification_uri_complete（可转发的一键批准链接就是钓鱼面）。
    # 这里以前是硬取该键，所以每一次真实的 trace_login action=start 都会 KeyError。
    assert "verification_uri_complete" not in first
    assert first["user_code"] == "ABCD-EFGH"
    assert "type the code" in first["next"]
    assert not remote.auth_token()

    monkeypatch.setattr(mcp, "poll_login", lambda url, code: issued())
    complete = remote.device_login("status")
    assert complete["status"] == "connected"
    assert issued()["credential"] not in json.dumps(complete)
    assert remote.auth_token().startswith("rtv2d_")


def test_login_cli_never_offers_a_one_click_approval_link(tmp_path, monkeypatch, capsys):
    """一键批准链接可以被转发给受害者，所以 CLI 只给页面地址 + 需手工输入的验证码。"""
    path = tmp_path / "credentials.json"
    started = {
        "device_code": "secret-device-code-" + "d" * 40,
        "user_code": "ABCD-EFGH",
        "device_name": "hpc",
        "verification_uri": "https://trace.example/device",
        "expires_in": 600,
        "interval": 1,
    }
    opened: list[str] = []
    monkeypatch.setattr(device_login, "start_login", lambda url, name: dict(started))
    monkeypatch.setattr(device_login, "poll_login", lambda url, code: issued())
    monkeypatch.setattr(device_login.webbrowser, "open", lambda url: opened.append(url) or True)
    assert device_login.main([
        "--url", "https://trace.example", "--device-name", "hpc",
        "--credential-file", str(path),
    ]) == 0
    output = capsys.readouterr().out
    assert "https://trace.example/device" in output
    assert "ABCD-EFGH" in output
    assert "?code=" not in output
    assert opened == ["https://trace.example/device"]
    assert "Never enter a code somebody sent you" in output
    assert issued()["credential"] not in output
    saved = load_device_credential(path, "https://trace.example")
    assert saved["expires_at"] == "2026-11-16T00:00:00.000+00:00"


def test_login_cli_renews_an_existing_credential_in_place(tmp_path, monkeypatch, capsys):
    path = tmp_path / "credentials.json"
    save_device_credential(path, "https://trace.example", issued())
    fresh = issued("rtv2d_" + "y" * 64)
    fresh["expires_at"] = "2027-01-01T00:00:00.000+00:00"
    seen: list[str] = []

    def fake_renew(url, credential):
        seen.append(credential)
        return dict(fresh)

    monkeypatch.setattr(device_login, "renew_login", fake_renew)
    assert device_login.main([
        "--url", "https://trace.example", "--renew", "--credential-file", str(path),
    ]) == 0
    assert seen == ["rtv2d_" + "x" * 64]
    assert load_device_credential(path, "https://trace.example")["credential"].endswith("y" * 64)
    assert "2027-01-01" in capsys.readouterr().out
