from __future__ import annotations

import json

import research_trace_v2.mcp as mcp
import research_trace_v2.device_login as device_login
from research_trace_v2.device_login import (
    load_device_credential,
    remove_device_credential,
    save_device_credential,
)


def issued(credential: str = "rtv2d_" + "x" * 64):
    return {
        "status": "authorized",
        "credential": credential,
        "device": {"id": "dev_1", "name": "hipergator"},
        "user": {"id": "usr_1", "login": "alice", "role": "member"},
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
        "verification_uri_complete": "https://trace.example/device?code=ABCD-EFGH",
        "expires_in": 600,
        "interval": 3,
    }
    monkeypatch.setattr(mcp, "start_login", lambda url, name: dict(started))
    first = remote.device_login("start", "workstation")
    assert first["status"] == "approval_required"
    assert "device_code" not in first
    assert not remote.auth_token()

    monkeypatch.setattr(mcp, "poll_login", lambda url, code: issued())
    complete = remote.device_login("status")
    assert complete["status"] == "connected"
    assert issued()["credential"] not in json.dumps(complete)
    assert remote.auth_token().startswith("rtv2d_")


def test_login_cli_opens_approval_and_saves_without_printing_secret(tmp_path, monkeypatch, capsys):
    path = tmp_path / "credentials.json"
    started = {
        "device_code": "secret-device-code-" + "d" * 40,
        "user_code": "ABCD-EFGH",
        "device_name": "hpc",
        "verification_uri": "https://trace.example/device",
        "verification_uri_complete": "https://trace.example/device?code=ABCD-EFGH",
        "expires_in": 600,
        "interval": 1,
    }
    monkeypatch.setattr(device_login, "start_login", lambda url, name: dict(started))
    monkeypatch.setattr(device_login, "poll_login", lambda url, code: issued())
    monkeypatch.setattr(device_login.webbrowser, "open", lambda url: True)
    assert device_login.main([
        "--url", "https://trace.example", "--device-name", "hpc",
        "--credential-file", str(path),
    ]) == 0
    output = capsys.readouterr().out
    assert started["verification_uri_complete"] in output
    assert issued()["credential"] not in output
    assert load_device_credential(path, "https://trace.example")
