from __future__ import annotations

import shutil
import subprocess
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from research_trace.auth import GitHubOAuthConfig, OAUTH_NONCE_COOKIE, SESSION_COOKIE
from research_trace.backup import export_backup, restore_backup
from research_trace.server import create_app
from research_trace.storage import Conflict, Store


class FakeGitHub:
    def __init__(self):
        self.profile = {
            "id": 101,
            "login": "alice",
            "name": "Alice Researcher",
            "avatar_url": "https://avatars.githubusercontent.com/u/101",
        }
        self.exchanged: list[tuple[str, str]] = []
        self.challenge = ""

    def authorize_url(self, *, state: str, challenge: str) -> str:
        self.challenge = challenge
        return f"https://github.com/login/oauth/authorize?state={state}&code_challenge={challenge}"

    def exchange_code(self, *, code: str, verifier: str) -> str:
        self.exchanged.append((code, verifier))
        return "github-access-token-must-never-be-persisted"

    def fetch_user(self, access_token: str):
        assert access_token == "github-access-token-must-never-be-persisted"
        return dict(self.profile)

    def active_org_member(self, access_token: str, organization: str) -> bool:
        return organization == "trace-lab"


def oauth_app(tmp_path, fake: FakeGitHub, **overrides):
    values = {
        "token": "machine-secret",
        "github_client_id": "client-id",
        "github_client_secret": "client-secret",
        "public_url": "https://trace.example",
        "session_secret": "s" * 48,
        "github_admins": "alice",
        "github_allowed_users": "bob",
        "oauth_client": fake,
    }
    values.update(overrides)
    return create_app(tmp_path, **values)


def login(client: TestClient, fake: FakeGitHub, profile: dict, return_to: str = "/"):
    fake.profile = profile
    start = client.get(
        "/auth/github/login", params={"return_to": return_to}, follow_redirects=False
    )
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    state = query["state"][0]
    assert query["code_challenge"][0] == fake.challenge
    assert client.cookies.get(OAUTH_NONCE_COOKIE)
    done = client.get(
        "/auth/github/callback", params={"code": "temporary-code", "state": state},
        follow_redirects=False,
    )
    return state, done


def test_oauth_flow_roles_csrf_machine_access_and_one_use_state(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        assert client.get("/api/v2/auth/config").json()["enabled"] is True
        assert client.get("/api/v2/projects").status_code == 401
        minimal_health = client.get("/api/v2/health").json()
        assert "data_dir" not in minimal_health

        state, callback = login(
            client, fake,
            {"id": 101, "login": "alice", "name": "Alice", "avatar_url": "https://example/a"},
            return_to="/",
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/"
        assert client.cookies.get(SESSION_COOKIE)
        assert fake.exchanged and fake.exchanged[0][1]

        replay = client.get(
            "/auth/github/callback", params={"code": "again", "state": state},
            follow_redirects=False,
        )
        assert replay.status_code == 403

        me = client.get("/api/v2/auth/me").json()
        assert me["user"]["login"] == "alice"
        assert me["user"]["role"] == "admin"
        csrf = me["csrf_token"]
        assert client.post("/api/v2/projects", json={"name": "Denied"}).status_code == 403
        project = client.post(
            "/api/v2/projects", json={"name": "OAuth project"},
            headers={"X-CSRF-Token": csrf},
        )
        assert project.status_code == 200
        assert client.get("/api/v2/projects").json()["projects"][0]["name"] == "OAuth project"

        machine = {"Authorization": "Bearer machine-secret", "X-Trace-Actor": "claude-worker"}
        assert client.get("/api/v2/projects", headers=machine).status_code == 200
        assert client.post("/api/v2/projects", headers=machine, json={"name": "MCP"}).status_code == 200

        users = client.get("/api/v2/admin/users").json()["users"]
        assert users[0]["login"] == "alice"
        assert client.patch(
            f"/api/v2/admin/users/{users[0]['id']}",
            headers={"X-CSRF-Token": csrf}, json={"role": "reader"},
        ).status_code == 409

        dump = "\n".join(app.state.store._db.iterdump())
        assert "github-access-token-must-never-be-persisted" not in dump
        assert client.cookies.get(SESSION_COOKIE) not in dump
        backup = tmp_path / "backup"
        manifest = export_backup(app.state.store, backup)
        assert "auth_users" in manifest["tables"]
        assert manifest["excluded_ephemeral_tables"] == ["web_sessions", "device_authorizations"]
        backup_text = "\n".join(
            path.read_text("utf-8", errors="ignore") for path in backup.rglob("*") if path.is_file()
        )
        assert "github-access-token-must-never-be-persisted" not in backup_text
        assert client.cookies.get(SESSION_COOKIE) not in backup_text
        restored = Store(tmp_path / "restored")
        restore_backup(backup, restored)
        assert restored.list_auth_users()[0]["login"] == "alice"
        assert restored._db.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 0
        restored.close()

        assert client.post("/api/v2/auth/logout").status_code == 403
        assert client.post(
            "/api/v2/auth/logout", headers={"X-CSRF-Token": csrf}
        ).json() == {"logged_out": True}
        assert client.get("/api/v2/auth/me").status_code == 401


def test_allowed_member_and_reader_permissions(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        _state, done = login(
            client, fake,
            {"id": 202, "login": "bob", "name": "Bob", "avatar_url": None},
        )
        assert done.status_code == 303
        me = client.get("/api/v2/auth/me").json()
        assert me["user"]["role"] == "member"
        csrf = me["csrf_token"]
        assert client.post(
            "/api/v2/projects", headers={"X-CSRF-Token": csrf}, json={"name": "Bob project"}
        ).status_code == 200
        app.state.store.update_auth_user(me["user"]["id"], role="reader")
        assert client.get("/api/v2/projects").status_code == 200
        assert client.post(
            "/api/v2/projects", headers={"X-CSRF-Token": csrf}, json={"name": "No"}
        ).status_code == 403
        assert client.get("/api/v2/admin/users").status_code == 403


def test_account_approved_device_login_is_independent_and_revocable(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        _state, done = login(
            client, fake,
            {"id": 101, "login": "alice", "name": "Alice", "avatar_url": None},
        )
        assert done.status_code == 303
        me = client.get("/api/v2/auth/me").json()
        csrf = me["csrf_token"]

        started = client.post(
            "/api/v2/device/start", json={"device_name": "hipergator-login-01"}
        ).json()
        assert started["verification_uri_complete"].endswith(
            "/device?code=" + started["user_code"]
        )
        assert client.post(
            "/api/v2/device/token", json={"device_code": started["device_code"]}
        ).status_code == 202
        approval_page = client.get("/device", params={"code": started["user_code"]})
        assert "hipergator-login-01" in approval_page.text
        assert "GitHub access token" in approval_page.text
        assert "backdrop-filter:blur(22px)" in approval_page.text
        assert 'role="status" aria-live="polite"' in approval_page.text
        assert "prefers-reduced-motion" in approval_page.text
        node = shutil.which("node")
        if node:
            script = approval_page.text.split("<script>", 1)[1].split("</script>", 1)[0]
            checked = subprocess.run([node, "--check"], input=script.encode("utf-8"), capture_output=True)
            assert checked.returncode == 0, checked.stderr.decode("utf-8", errors="replace")
        assert client.post(
            "/api/v2/device/approve", json={"user_code": started["user_code"]}
        ).status_code == 403
        approved = client.post(
            "/api/v2/device/approve", json={"user_code": started["user_code"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert approved.status_code == 200

        issued = client.post(
            "/api/v2/device/token", json={"device_code": started["device_code"]}
        ).json()
        credential = issued["credential"]
        assert credential.startswith("rtv2d_")
        assert issued["user"]["login"] == "alice"
        assert client.post(
            "/api/v2/device/token", json={"device_code": started["device_code"]}
        ).status_code == 400

        device_headers = {"Authorization": "Bearer " + credential}
        assert client.get("/api/v2/projects", headers=device_headers).status_code == 200
        created = client.post(
            "/api/v2/projects", headers=device_headers, json={"name": "From HiperGator"}
        )
        assert created.status_code == 200
        devices = client.get("/api/v2/auth/devices").json()["devices"]
        assert devices[0]["name"] == "hipergator-login-01"

        dump = "\n".join(app.state.store._db.iterdump())
        assert credential not in dump
        backup = tmp_path / "device-backup"
        manifest = export_backup(app.state.store, backup)
        assert manifest["tables"]["device_credentials"] == 1
        backup_text = "\n".join(
            path.read_text("utf-8", errors="ignore") for path in backup.rglob("*") if path.is_file()
        )
        assert credential not in backup_text
        restored = Store(tmp_path / "device-restored")
        restore_backup(backup, restored)
        assert restored.device_credential_identity(credential)["user"]["login"] == "alice"
        restored.close()

        revoked = client.delete(
            f"/api/v2/auth/devices/{issued['device']['id']}",
            headers={"X-CSRF-Token": csrf},
        )
        assert revoked.status_code == 200
        client.cookies.clear()
        assert client.get("/api/v2/projects", headers=device_headers).status_code == 401


def test_oauth_config_rejects_partial_or_insecure_production_settings():
    with pytest.raises(ValueError, match="requires"):
        GitHubOAuthConfig.build(
            client_id="id", client_secret=None, public_url=None, session_secret=None,
            admins="alice",
        )
    with pytest.raises(ValueError, match="HTTPS"):
        GitHubOAuthConfig.build(
            client_id="id", client_secret="secret", public_url="http://trace.example",
            session_secret="x" * 32, admins="alice",
        )
    local = GitHubOAuthConfig.build(
        client_id="id", client_secret="secret", public_url="http://127.0.0.1:8765",
        session_secret="x" * 32, admins="alice", insecure_cookies=True,
    )
    assert local and local.secure_cookies is False


def test_store_prevents_disabling_last_active_admin(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    admin = store.upsert_github_user(
        {"id": 1, "login": "admin"}, default_role="admin", force_admin=True
    )
    with pytest.raises(Conflict, match="last active admin"):
        store.update_auth_user(admin["id"], disabled=True)
    store.close()


def test_disabling_user_revokes_every_bound_device(tmp_path):
    store = Store(tmp_path)
    store.upsert_github_user(
        {"id": 1, "login": "admin"}, default_role="admin", force_admin=True
    )
    member = store.upsert_github_user(
        {"id": 2, "login": "member"}, default_role="member"
    )
    started = store.start_device_authorization("member-laptop")
    store.approve_device_authorization(started["user_code"], member["id"])
    credential = store.exchange_device_authorization(started["device_code"])["credential"]
    assert store.device_credential_identity(credential)["user"]["login"] == "member"
    store.update_auth_user(member["id"], disabled=True)
    assert store.device_credential_identity(credential) is None
    assert store.list_device_credentials(user_id=member["id"])[0]["revoked_at"]
    store.close()
