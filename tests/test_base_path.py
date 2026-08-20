"""服务挂在路径前缀下（--base-path）。

这里守两件事：① 根部署的行为一个字节都不能变；② 挂在前缀下时，前缀之外必须
真的进不去 —— 「站点只存在于某个不可猜路径之下」这种部署，靠的就是这一条。
"""
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from research_trace.auth import GitHubOAuthConfig, normalize_base_path, safe_return_to
from research_trace.server import create_app
from research_trace.webapp import INDEX_HTML, render_index


@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("", ""), ("/", ""),
    ("/trace", "/trace"), ("trace", "/trace"), ("/trace/", "/trace"),
    ("/t/abc/def/", "/t/abc/def"),
])
def test_normalize_base_path(raw, expected):
    assert normalize_base_path(raw) == expected


@pytest.mark.parametrize("raw", ["https://example.org/trace", "//evil", "/trace?x=1", "/trace#f"])
def test_normalize_base_path_rejects_non_paths(raw):
    with pytest.raises(ValueError):
        normalize_base_path(raw)


def test_safe_return_to_falls_back_to_the_site_home_not_the_domain_root():
    # 挂在前缀下时 "/" 是**别的**应用，把人踢过去是个 bug 而不是兜底。
    assert safe_return_to(None, "/trace") == "/trace/"
    assert safe_return_to("/trace/x", "/trace") == "/trace/x"
    assert safe_return_to("/app/other/", "/trace") == "/trace/"
    assert safe_return_to("//evil.example", "/trace") == "/trace/"
    assert safe_return_to(None, "") == "/"


def test_render_index_at_root_is_byte_identical_to_the_template():
    assert render_index("") == INDEX_HTML.replace("__TRACE_BASE__", "")
    assert "__TRACE_BASE__" not in render_index("")
    assert "const BASE = '';" in render_index("")


def test_render_index_under_a_prefix_carries_it_into_every_self_reference():
    page = render_index("/t/abc")
    assert "const BASE = '/t/abc';" in page
    assert '<a class="brand" href="/t/abc/"' in page
    assert "__TRACE_BASE__" not in page


def test_root_deployment_is_unchanged(tmp_path):
    app = create_app(tmp_path, token="secret")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 200


def test_prefix_owns_the_site_and_everything_else_is_404(tmp_path):
    app = create_app(tmp_path, token="secret", base_path="/t/abc")
    with TestClient(app) as client:
        assert client.get("/t/abc/api/health").status_code == 200
        assert client.get("/t/abc/").status_code == 200
        # 只设 root_path 的话这两条会照常匹配到路由 —— 那等于门没锁。
        assert client.get("/api/health").status_code == 404
        assert client.get("/").status_code == 404
        assert client.get("/t/wrong/api/health").status_code == 404
        # 前缀本身不带尾斜杠时把人送进去，而不是 404
        response = client.get("/t/abc", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].endswith("/t/abc/")


def test_writes_still_need_the_token_under_a_prefix(tmp_path):
    app = create_app(tmp_path, token="secret", base_path="/t/abc")
    with TestClient(app) as client:
        assert client.post("/t/abc/api/projects", json={"name": "x"}).status_code == 401
        created = client.post("/t/abc/api/projects", json={"name": "x"},
                              headers={"Authorization": "Bearer secret"})
        assert created.status_code == 200


def _oauth_kwargs(base_path, public_url):
    return dict(
        client_id="cid", client_secret="secret",
        public_url=public_url, session_secret="s" * 32,
        admins="someone", base_path=base_path, insecure_cookies=True,
    )


def test_oauth_public_url_must_carry_the_same_prefix():
    config = GitHubOAuthConfig.build(**_oauth_kwargs("/t/abc", "http://127.0.0.1:8765/t/abc"))
    assert config.callback_url == "http://127.0.0.1:8765/t/abc/auth/github/callback"

    # 少了前缀：GitHub 会把人回调到域名根，那里通常是别的应用。
    with pytest.raises(ValueError):
        GitHubOAuthConfig.build(**_oauth_kwargs("/t/abc", "http://127.0.0.1:8765"))
    # 多了前缀：根部署却给了带路径的 public URL。
    with pytest.raises(ValueError):
        GitHubOAuthConfig.build(**_oauth_kwargs("", "http://127.0.0.1:8765/t/abc"))


def test_session_cookies_are_scoped_to_the_prefix(tmp_path):
    """cookie 写死 path=/ 时，会被发给同一域名上的每一个应用。"""
    app = create_app(
        tmp_path, base_path="/t/abc",
        github_client_id="cid", github_client_secret="secret",
        public_url="http://127.0.0.1:8765/t/abc", session_secret="s" * 32,
        github_admins="someone", insecure_cookies=True,
    )
    with TestClient(app) as client:
        response = client.get("/t/abc/auth/github/login", follow_redirects=False)
        assert response.status_code == 302
        assert "Path=/t/abc" in response.headers["set-cookie"]

        config = client.get("/t/abc/api/auth/config").json()
        assert config["login_url"] == "/t/abc/auth/github/login"


def test_device_page_redirects_into_the_prefix_when_signed_out(tmp_path):
    app = create_app(
        tmp_path, base_path="/t/abc",
        github_client_id="cid", github_client_secret="secret",
        public_url="http://127.0.0.1:8765/t/abc", session_secret="s" * 32,
        github_admins="someone", insecure_cookies=True,
    )
    with TestClient(app) as client:
        response = client.get("/t/abc/device", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/t/abc/auth/github/login")
        assert "return_to=%2Ft%2Fabc%2Fdevice" in response.headers["location"]
