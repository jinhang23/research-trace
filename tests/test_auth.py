from __future__ import annotations

import json
import shutil
import subprocess
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from research_trace.auth import (
    GitHubOAuthConfig,
    OAUTH_NONCE_COOKIE,
    SESSION_COOKIE,
    RateLimiter,
)
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
        # 一键批准链接是钓鱼入口，服务端不再产出它。
        assert "verification_uri_complete" not in started
        assert started["verification_uri"] == "https://trace.example/device"
        assert client.post(
            "/api/v2/device/token", json={"device_code": started["device_code"]}
        ).status_code == 202
        approval_page = client.get("/device", params={"code": started["user_code"]})
        # 批准页不接受链接里带来的验证码，必须手工输入。
        assert "hipergator-login-01" not in approval_page.text
        assert started["user_code"] not in approval_page.text
        assert 'id="code"' in approval_page.text
        assert "trace-login" in approval_page.text
        assert "GitHub access token" in approval_page.text
        looked_up = client.get(
            "/api/v2/device/authorization", params={"user_code": started["user_code"]}
        ).json()
        assert looked_up["device_name"] == "hipergator-login-01"
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


def approved_device(client, csrf, name="worker-01"):
    started = client.post("/api/v2/device/start", json={"device_name": name}).json()
    client.post(
        "/api/v2/device/approve", json={"user_code": started["user_code"]},
        headers={"X-CSRF-Token": csrf},
    ).raise_for_status()
    return client.post(
        "/api/v2/device/token", json={"device_code": started["device_code"]}
    ).json()


def test_device_credential_write_identity_comes_from_the_credential_not_the_body(tmp_path):
    """普通写入者不能靠请求体把自己写成别人，也不能自称 human 绕开人工纠正闸门。"""
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice", "name": "Alice", "avatar_url": None})
        csrf = client.get("/api/v2/auth/me").json()["csrf_token"]
        credential = approved_device(client, csrf, "hpg-node-7")["credential"]
        machine = {"Authorization": "Bearer " + credential}
        project = client.post(
            "/api/v2/projects", headers={"X-CSRF-Token": csrf}, json={"name": "Identity"}
        ).json()

        comment = client.post(
            "/api/v2/comments", headers=machine,
            json={
                "project_id": project["id"], "target_type": "overview", "kind": "comment",
                "body": "from the machine", "author_id": "alice", "author_type": "human",
            },
        ).json()
        assert comment["author_type"] == "recorder"
        assert comment["author_id"] == "alice@hpg-node-7"

        # 人留下一条未处理的 correction，recorder 就不能再改写 Overview，
        # 哪怕它在请求体里自称 actor_type=human。
        client.post(
            "/api/v2/comments", headers={"X-CSRF-Token": csrf},
            json={
                "project_id": project["id"], "target_type": "overview",
                "kind": "correction", "body": "这里的结论不对",
            },
        ).raise_for_status()
        blocked = client.post(
            "/api/v2/curate", headers=machine,
            json={
                "project_id": project["id"], "target_type": "overview", "body": "recorder rewrite",
                "expect_version": 1, "actor_type": "human", "actor_id": "alice",
            },
        )
        assert blocked.status_code == 409
        assert "corrections" in blocked.json()["error"]


def test_signed_in_member_cannot_sign_a_write_as_somebody_else(tmp_path):
    """合法会话 + 合法 CSRF 也不能把 author_id / created_by 写成别人。"""
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 202, "login": "bob", "name": "Bob", "avatar_url": None})
        csrf = {"X-CSRF-Token": client.get("/api/v2/auth/me").json()["csrf_token"]}
        project = client.post(
            "/api/v2/projects", headers=csrf, json={"name": "Impersonation"}
        ).json()
        node = client.post(
            "/api/v2/record", headers=csrf,
            json={"project_id": project["id"], "idempotency_key": "k", "title": "t",
                  "created_by": "recorder"},
        ).json()
        assert node["created_by"] == "human"
        comment = client.post(
            "/api/v2/comments", headers=csrf,
            json={"project_id": project["id"], "target_type": "node", "target_id": node["id"],
                  "kind": "confirmation", "body": "looks right",
                  "author_id": "alice", "author_type": "human"},
        ).json()
        assert comment["author_id"] == "bob"
        revision = client.get(f"/api/v2/revisions/node/{node['id']}").json()["revisions"][0]
        assert revision["actor_id"] == "bob"


def test_device_credential_expires_and_can_be_renewed_before_it_does(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake, device_credential_days=1)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice", "name": "Alice", "avatar_url": None})
        csrf = client.get("/api/v2/auth/me").json()["csrf_token"]
        issued = approved_device(client, csrf, "laptop")
        assert issued["expires_at"] > issued["device"]["created_at"]
        machine = {"Authorization": "Bearer " + issued["credential"]}

        renewed = client.post("/api/v2/device/renew", headers=machine).json()
        assert renewed["credential"] != issued["credential"]
        client.cookies.clear()  # 只看 Bearer 凭证，别让浏览器会话兜底
        assert client.get(
            "/api/v2/projects", headers={"Authorization": "Bearer " + renewed["credential"]}
        ).status_code == 200
        # 换发之后旧凭证立刻作废，不留下第二把长期钥匙。
        assert client.get("/api/v2/projects", headers=machine).status_code == 401

        store = app.state.store
        device_id = renewed["device"]["id"]
        # 到期时间在铸造时落库，所以让它过期要改 expires_at 本身。
        assert renewed["device"]["expires_at"]
        store._db.execute(
            "UPDATE device_credentials SET expires_at=? WHERE id=?",
            ("2020-01-01T00:00:00.000+00:00", device_id),
        )
        store._db.commit()
        stale = {"Authorization": "Bearer " + renewed["credential"]}
        assert client.get("/api/v2/projects", headers=stale).status_code == 401


def test_a_lowered_then_raised_credential_lifetime_does_not_resurrect_dead_credentials(tmp_path):
    """到期时间是铸造时定死的事实，不是每次请求按环境变量现算的。

    现算的版本里，运维把 TRACE_DEVICE_CREDENTIAL_DAYS 调小、发现太严再调回去，
    所有已经过期的凭证会一起复活——包括当初就该被时间收回的那些。
    """
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake, device_credential_days=1)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice", "name": "Alice", "avatar_url": None})
        csrf = client.get("/api/v2/auth/me").json()["csrf_token"]
        issued = approved_device(client, csrf, "laptop")
    data_dir = tmp_path

    # 同一个数据目录，改成 90 天重启：旧凭证的 expires_at 不会因此往后挪。
    relaxed = oauth_app(data_dir, FakeGitHub(), device_credential_days=90)
    relaxed.state.store._db.execute(
        "UPDATE device_credentials SET expires_at=?",
        ("2020-01-01T00:00:00.000+00:00",),
    )
    relaxed.state.store._db.commit()
    with TestClient(relaxed, base_url="https://trace.example") as client:
        assert client.get(
            "/api/v2/projects", headers={"Authorization": "Bearer " + issued["credential"]}
        ).status_code == 401


def test_device_start_is_rate_limited_per_client(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake, device_start_limit=3, device_start_window_seconds=600)
    with TestClient(app, base_url="https://trace.example") as client:
        codes = [
            client.post("/api/v2/device/start", json={"device_name": f"flood-{index}"})
            for index in range(3)
        ]
        assert all(response.status_code == 200 for response in codes)
        flooded = client.post("/api/v2/device/start", json={"device_name": "flood-4"})
        assert flooded.status_code == 429
        assert int(flooded.headers["Retry-After"]) > 0


def test_rate_limiter_releases_the_window_and_bounds_its_own_memory():
    limiter = RateLimiter(limit=2, window_seconds=1)
    assert limiter.hit("a") == 0
    assert limiter.hit("a") == 0
    assert limiter.hit("a") > 0
    assert limiter.hit("b") == 0
    time.sleep(1.05)
    assert limiter.hit("a") == 0

    bounded = RateLimiter(limit=5, window_seconds=600, max_keys=16)
    for index in range(64):
        bounded.hit(f"ip-{index}")
    assert len(bounded._hits) <= 16


def test_admin_whitelist_is_anchored_on_github_id_after_first_resolution(tmp_path):
    """用户名会被释放并被抢注；第一次解析后必须只认数字 id。"""
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice", "name": "Alice", "avatar_url": None})
        assert client.get("/api/v2/auth/me").json()["user"]["role"] == "admin"
        client.cookies.clear()

        # 抢注者拿到了被释放的用户名 alice，但 github_id 不同。
        _state, squatted = login(
            client, fake, {"id": 999, "login": "alice", "name": "Squatter", "avatar_url": None}
        )
        assert squatted.status_code == 403
        assert client.cookies.get(SESSION_COOKIE) is None

        pins_file = tmp_path / "identity-pins.json"
        pins = json.loads(pins_file.read_text(encoding="utf-8"))
        assert pins["pins"]["admins"]["alice"] == 101

    # 钉子文件不在备份里；从空库 restore 之后要能从 auth_users 重新长出来。
    pins_file.unlink()
    reseeded = oauth_app(tmp_path, fake)
    assert json.loads(pins_file.read_text(encoding="utf-8"))["pins"]["admins"]["alice"] == 101
    reseeded.state.store.close()

    # 本人改名后，配置里的旧用户名仍然通过钉住的 id 认得出来。
    renamed_app = oauth_app(tmp_path, fake)
    with TestClient(renamed_app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice-new", "name": "Alice", "avatar_url": None})
        assert client.get("/api/v2/auth/me").json()["user"]["role"] == "admin"


def test_config_admins_accept_an_explicit_github_id():
    config = GitHubOAuthConfig.build(
        client_id="id", client_secret="secret", public_url="https://trace.example",
        session_secret="x" * 32, admins="id:4711,legacy-name",
    )
    assert config.admins.github_ids == frozenset({4711})
    assert config.admins.logins == frozenset({"legacy-name"})
    assert config.resolve_role(login="whatever", github_id=4711) == "admin"
    assert config.resolve_role(login="stranger", github_id=5) is None
    with pytest.raises(ValueError, match="invalid GitHub id"):
        GitHubOAuthConfig.build(
            client_id="id", client_secret="secret", public_url="https://trace.example",
            session_secret="x" * 32, admins="id:not-a-number",
        )


def test_removing_a_user_from_the_whitelist_invalidates_live_sessions_and_devices(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake, github_admins="alice", github_allowed_users="bob")
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 202, "login": "bob", "name": "Bob", "avatar_url": None})
        csrf = client.get("/api/v2/auth/me").json()["csrf_token"]
        credential = approved_device(client, csrf, "bob-laptop")["credential"]
        machine = {"Authorization": "Bearer " + credential}
        assert client.get("/api/v2/projects", headers=machine).status_code == 200
        session_cookie = client.cookies.get(SESSION_COOKIE)

    # 管理员把 bob 从白名单删掉并重启服务；他既有的 cookie 和设备凭证必须立刻失效。
    restarted = oauth_app(tmp_path, fake, github_admins="alice", github_allowed_users="")
    with TestClient(restarted, base_url="https://trace.example") as client:
        client.cookies.set(SESSION_COOKIE, session_cookie, domain="trace.example")
        assert client.get("/api/v2/auth/me").status_code == 401
        assert client.get("/api/v2/projects", headers=machine).status_code == 401


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


def test_admin_can_purge_and_read_the_audit_over_http(tmp_path):
    """§13 的紧急 purge 此前只有 CLI 入口——运维必须能登到服务器上才能用。"""
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice", "name": "Alice", "avatar_url": None})
        csrf = {"X-CSRF-Token": client.get("/api/v2/auth/me").json()["csrf_token"]}
        project = client.post("/api/v2/projects", headers=csrf, json={"name": "Leaky"}).json()
        client.post("/api/v2/ingest", headers=csrf, json={
            "batch_id": "b1", "project_id": project["id"],
            "session": {"id": "s1", "source": "claude-code"},
            "events": [{"event_id": "e1", "event_type": "PreToolUse",
                        "payload": {"command": "export TOKEN=ghp_realsecret"}}],
        }).raise_for_status()
        assert client.get("/api/v2/search", params={"q": "ghp_realsecret"}).json()["hits"]

        assert client.post("/api/v2/admin/purge", headers=csrf,
                           json={"project_ids": [project["id"]]}).status_code == 400
        done = client.post("/api/v2/admin/purge", headers=csrf, json={
            "reason": "token leaked into a tool call", "project_ids": [project["id"]],
        }).json()
        assert done["removed"]["events"] == 1
        assert client.get("/api/v2/search", params={"q": "ghp_realsecret"}).json()["hits"] == []

        audit = client.get("/api/v2/admin/purges", headers=csrf).json()["purges"][0]
        assert audit["actor_id"] == "alice"  # 操作者来自凭证，不是请求体
        assert audit["reason"] == "token leaked into a tool call"
        assert "ghp_realsecret" not in json.dumps(audit), "审计记录里不能有原文"


def test_a_member_cannot_purge(tmp_path):
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 202, "login": "bob", "name": "Bob", "avatar_url": None})
        csrf = {"X-CSRF-Token": client.get("/api/v2/auth/me").json()["csrf_token"]}
        assert client.post("/api/v2/admin/purge", headers=csrf,
                           json={"reason": "r", "project_ids": ["p"]}).status_code == 403
        assert client.get("/api/v2/admin/purges", headers=csrf).status_code == 403


def test_a_machine_credential_cannot_close_a_human_correction_by_any_route(tmp_path):
    """人工纠正只有人能了结。curate 那边把机器的 resolve_comment_ids 降级成
    acknowledgement 之后，/comments/{id}/resolve 是它剩下的唯一一条抹除路径。"""
    fake = FakeGitHub()
    app = oauth_app(tmp_path, fake)
    with TestClient(app, base_url="https://trace.example") as client:
        login(client, fake, {"id": 101, "login": "alice", "name": "A", "avatar_url": None})
        csrf = {"X-CSRF-Token": client.get("/api/v2/auth/me").json()["csrf_token"]}
        issued = approved_device(client, csrf["X-CSRF-Token"], "hpg")
        project = client.post("/api/v2/projects", headers=csrf,
                              json={"name": "P", "overview": "v1"}).json()
        correction = client.post("/api/v2/comments", headers=csrf, json={
            "project_id": project["id"], "target_type": "overview",
            "kind": "correction", "body": "这个结论不成立",
        }).json()

        machine = {"Authorization": "Bearer " + issued["credential"]}
        client.cookies.clear()
        assert client.post(
            f"/api/v2/comments/{correction['id']}/resolve", headers=machine
        ).status_code == 403

        # acknowledge 之后 curate 通得过，但纠正对人仍然是未处理的
        assert client.post("/api/v2/curate", headers=machine, json={
            "project_id": project["id"], "target_type": "overview", "body": "v2",
            "expect_version": 1, "resolve_comment_ids": [correction["id"]],
            "actor_type": "human", "actor_id": "alice",
        }).status_code == 200
        context = client.post("/api/v2/context", headers=machine,
                              json={"project_id": project["id"]}).json()
        assert [item["id"] for item in context["project"]["unresolved_corrections"]] == [
            correction["id"]
        ]
        revision = client.get(
            f"/api/v2/revisions/overview/{project['id']}", headers=machine
        ).json()["revisions"][0]
        assert revision["actor_type"] == "recorder", "请求体里的 actor_type 不算数"
