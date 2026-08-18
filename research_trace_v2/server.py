"""FastAPI application for the Research Trace v2 central service."""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import secrets
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import (
    OAUTH_NONCE_COOKIE,
    SESSION_COOKIE,
    GitHubOAuthClient,
    GitHubOAuthConfig,
    OAuthError,
    PendingOAuthStore,
    csrf_token,
)
from .backup import sync_git_backup
from .storage import Conflict, NotFound, Store, StoreError, ValidationError, now_utc

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
except ImportError:  # pragma: no cover
    Depends = FastAPI = HTTPException = Request = None  # type: ignore[assignment]
    FileResponse = HTMLResponse = JSONResponse = RedirectResponse = None  # type: ignore[assignment]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app(
    data_dir: str | os.PathLike[str] | None = None,
    *,
    token: str | None = None,
    attachment_limit: int = 10 * 1024 * 1024,
    backup_repo: str | os.PathLike[str] | None = None,
    backup_interval_hours: float = 24,
    backup_subdirectory: str = "research-trace-backup",
    backup_remote: str = "origin",
    backup_branch: str = "main",
    github_client_id: str | None = None,
    github_client_secret: str | None = None,
    public_url: str | None = None,
    session_secret: str | None = None,
    github_admins: str | set[str] | list[str] | tuple[str, ...] | None = None,
    github_allowed_users: str | set[str] | list[str] | tuple[str, ...] | None = None,
    github_allowed_org: str | None = None,
    github_allow_all: bool | None = None,
    session_days: int | None = None,
    insecure_cookies: bool | None = None,
    oauth_client: Any | None = None,
):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("server requires fastapi; install research-trace[server]")

    oauth_config = GitHubOAuthConfig.build(
        client_id=github_client_id if github_client_id is not None else os.environ.get("TRACE_V2_GITHUB_CLIENT_ID"),
        client_secret=(github_client_secret if github_client_secret is not None
                       else os.environ.get("TRACE_V2_GITHUB_CLIENT_SECRET")),
        public_url=public_url if public_url is not None else os.environ.get("TRACE_V2_PUBLIC_URL"),
        session_secret=session_secret if session_secret is not None else os.environ.get("TRACE_V2_SESSION_SECRET"),
        admins=github_admins if github_admins is not None else os.environ.get("TRACE_V2_GITHUB_ADMINS"),
        allowed_users=(github_allowed_users if github_allowed_users is not None
                       else os.environ.get("TRACE_V2_GITHUB_ALLOWED_USERS")),
        allowed_org=(github_allowed_org if github_allowed_org is not None
                     else os.environ.get("TRACE_V2_GITHUB_ALLOWED_ORG")),
        allow_all=(github_allow_all if github_allow_all is not None
                   else _env_bool("TRACE_V2_GITHUB_ALLOW_ALL")),
        session_days=(session_days if session_days is not None
                      else int(os.environ.get("TRACE_V2_SESSION_DAYS", "30"))),
        insecure_cookies=(insecure_cookies if insecure_cookies is not None
                          else _env_bool("TRACE_V2_INSECURE_COOKIES")),
    )
    root = Path(data_dir or os.environ.get("TRACE_V2_DATA") or ".trace-v2-data")
    store = Store(root, attachment_limit=attachment_limit)
    write_token = token if token is not None else os.environ.get("TRACE_V2_TOKEN", "")
    pending_oauth = PendingOAuthStore()
    github = oauth_client or (GitHubOAuthClient(oauth_config) if oauth_config else None)
    backup_state: dict[str, Any] = {
        "enabled": bool(backup_repo), "running": False, "last_attempt_at": None,
        "last_success_at": None, "error": None, "changed": None, "pushed": None,
    }
    stop_backup = asyncio.Event()

    async def backup_loop() -> None:
        interval = max(float(backup_interval_hours), 1 / 60) * 3600
        while not stop_backup.is_set():
            backup_state.update(running=True, last_attempt_at=now_utc(), error=None)
            try:
                result = await asyncio.to_thread(
                    sync_git_backup, store, backup_repo,
                    subdirectory=backup_subdirectory, remote=backup_remote, branch=backup_branch,
                )
                backup_state.update(
                    last_success_at=now_utc(), changed=result["changed"], pushed=result["pushed"]
                )
            except Exception as exc:
                backup_state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                backup_state["running"] = False
            try:
                await asyncio.wait_for(stop_backup.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(backup_loop()) if backup_repo else None
        try:
            yield
        finally:
            stop_backup.set()
            if task:
                await task
            store.close()

    app = FastAPI(title="Research Trace v2", version="2.0.0-alpha.4", lifespan=lifespan)
    app.state.store = store
    app.state.write_token = write_token
    app.state.backup_status = backup_state
    app.state.oauth_config = oauth_config

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data: https://avatars.githubusercontent.com; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if (
            request.url.path.startswith("/auth/") or request.url.path.startswith("/api/v2/auth/")
            or request.url.path.startswith("/api/v2/device/")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StoreError)
    async def store_error(_request: Request, exc: StoreError):
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 400 if isinstance(exc, ValidationError) else 500
        return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=status)

    def bearer_identity(request: Request) -> dict[str, Any] | None:
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not supplied:
            return None
        if write_token and secrets.compare_digest(supplied, write_token):
            return {"kind": "legacy_machine"}
        return store.device_credential_identity(supplied)

    def browser_user(request: Request) -> dict[str, Any] | None:
        return store.web_session_user(request.cookies.get(SESSION_COOKIE)) if oauth_config else None

    def require_read(request: Request) -> dict[str, Any]:
        machine = bearer_identity(request)
        if machine:
            return machine
        if not oauth_config:
            return {"kind": "public"}
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login required")
        return {"kind": "user", "user": user}

    def _check_csrf(request: Request) -> None:
        assert oauth_config is not None
        raw_session = request.cookies.get(SESSION_COOKIE, "")
        supplied = request.headers.get("X-CSRF-Token", "")
        expected = csrf_token(oauth_config.session_secret, raw_session)
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="valid CSRF token required")

    def require_write(request: Request) -> dict[str, Any]:
        machine = bearer_identity(request)
        if machine:
            if machine["kind"] == "device" and machine["user"]["role"] not in {"member", "admin"}:
                raise HTTPException(status_code=403, detail="member role required")
            return machine
        if not oauth_config:
            if not write_token:
                return {"kind": "unprotected"}
            raise HTTPException(status_code=401, detail="valid Bearer token required")
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login or valid Bearer token required")
        if user["role"] not in {"member", "admin"}:
            raise HTTPException(status_code=403, detail="member role required")
        _check_csrf(request)
        return {"kind": "user", "user": user}

    def require_admin(request: Request) -> dict[str, Any]:
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login required")
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="admin role required")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            _check_csrf(request)
        return {"kind": "user", "user": user}

    def require_user_csrf(request: Request) -> dict[str, Any]:
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login required")
        _check_csrf(request)
        return {"kind": "user", "user": user}

    def require_device(request: Request) -> dict[str, Any]:
        identity = bearer_identity(request)
        if not identity or identity.get("kind") != "device":
            raise HTTPException(status_code=401, detail="valid device credential required")
        return identity

    def actor(request: Request) -> str:
        machine = bearer_identity(request)
        if machine and machine.get("kind") == "device":
            return f"{machine['user']['login']}@{machine['device']['name']}"[:200]
        user = browser_user(request)
        if user:
            return str(user["login"])[:200]
        return (request.headers.get("X-Trace-Actor") or "human").strip()[:200]

    @app.get("/api/v2/auth/config")
    def auth_config():
        return {"enabled": bool(oauth_config), "login_url": "/auth/github/login" if oauth_config else None}

    @app.get("/auth/github/login")
    def github_login(return_to: str = "/"):
        if not oauth_config or not github:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        state, nonce, _verifier, challenge = pending_oauth.create(return_to)
        response = RedirectResponse(github.authorize_url(state=state, challenge=challenge), status_code=302)
        response.set_cookie(
            OAUTH_NONCE_COOKIE, nonce, max_age=pending_oauth.ttl_seconds, httponly=True,
            secure=oauth_config.secure_cookies, samesite="lax", path="/",
        )
        return response

    @app.get("/auth/github/callback")
    def github_callback(request: Request, code: str | None = None, state: str | None = None,
                        error: str | None = None):
        if not oauth_config or not github:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        if error:
            raise HTTPException(status_code=400, detail="GitHub login was cancelled or denied")
        if not code or not state:
            raise HTTPException(status_code=400, detail="GitHub callback requires code and state")
        try:
            attempt = pending_oauth.consume(state, request.cookies.get(OAUTH_NONCE_COOKIE))
            access_token = github.exchange_code(code=code, verifier=attempt.verifier)
            profile = github.fetch_user(access_token)
            org_member = bool(
                oauth_config.allowed_org and github.active_org_member(access_token, oauth_config.allowed_org)
            )
            role_hint = oauth_config.permitted_role(profile.get("login"), active_org_member=org_member)
            if not role_hint:
                raise OAuthError("this GitHub account is not allowed to access Research Trace")
            is_admin = str(profile.get("login") or "").lower() in oauth_config.admins
            user = store.upsert_github_user(profile, default_role=role_hint, force_admin=is_admin)
            if user["disabled"]:
                raise OAuthError("this Research Trace account is disabled")
            raw_session = secrets.token_urlsafe(48)
            expires = datetime.now(timezone.utc) + timedelta(days=oauth_config.session_days)
            store.create_web_session(user["id"], raw_session, expires.isoformat(timespec="milliseconds"))
        except OAuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        response = RedirectResponse(attempt.return_to, status_code=303)
        response.set_cookie(
            SESSION_COOKIE, raw_session, max_age=oauth_config.session_days * 86400, httponly=True,
            secure=oauth_config.secure_cookies, samesite="lax", path="/",
        )
        response.delete_cookie(OAUTH_NONCE_COOKIE, path="/", secure=oauth_config.secure_cookies, samesite="lax")
        return response

    @app.get("/api/v2/auth/me")
    def auth_me(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login required")
        public_user = {key: user.get(key) for key in (
            "id", "github_id", "login", "display_name", "avatar_url", "role", "disabled"
        )}
        return {"user": public_user,
                "csrf_token": csrf_token(oauth_config.session_secret, request.cookies[SESSION_COOKIE])}

    @app.post("/api/v2/auth/logout")
    def auth_logout(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        raw_session = request.cookies.get(SESSION_COOKIE, "")
        if not browser_user(request):
            raise HTTPException(status_code=401, detail="GitHub login required")
        _check_csrf(request)
        store.delete_web_session(raw_session)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=oauth_config.secure_cookies, samesite="lax")
        return response

    @app.post("/api/v2/device/start")
    async def device_start(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        body = await request.json()
        value = store.start_device_authorization(body.get("device_name"))
        user_code = value["user_code"]
        return {
            **value,
            "verification_uri": f"{oauth_config.public_url}/device",
            "verification_uri_complete": (
                f"{oauth_config.public_url}/device?"
                + urllib.parse.urlencode({"code": user_code})
            ),
        }

    @app.post("/api/v2/device/token")
    async def device_token(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        body = await request.json()
        value = store.exchange_device_authorization(body.get("device_code"))
        if value["status"] == "pending":
            return JSONResponse(value, status_code=202)
        if value["status"] != "authorized":
            return JSONResponse(value, status_code=400)
        return value

    @app.get("/device", response_class=HTMLResponse)
    def device_page(request: Request, code: str):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        authorization = store.device_authorization(code)
        if not authorization:
            raise HTTPException(status_code=404, detail="device authorization is invalid or expired")
        user = browser_user(request)
        normalized_code = authorization["user_code"]
        if not user:
            return_to = "/device?" + urllib.parse.urlencode({"code": normalized_code})
            login_url = "/auth/github/login?" + urllib.parse.urlencode({"return_to": return_to})
            return RedirectResponse(login_url, status_code=303)
        safe_name = html.escape(authorization["device_name"])
        safe_code = html.escape(normalized_code)
        safe_login = html.escape(str(user["login"]))
        page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>批准设备 · Research Trace</title>
<meta name="theme-color" content="#edf5f3"><style>
:root{{--ink:#142421;--soft:#536a64;--accent:#147765;--line:rgba(54,83,76,.13)}}
*{{box-sizing:border-box}}body{{min-height:100vh;margin:0;display:grid;place-items:center;padding:24px;color:var(--ink);
background:radial-gradient(circle at 10% 0,rgba(105,207,174,.27),transparent 31rem),
radial-gradient(circle at 96% 8%,rgba(116,145,224,.2),transparent 32rem),
linear-gradient(145deg,#f2f7f6,#e7eff0);font:15px/1.6 Inter,ui-sans-serif,system-ui,"PingFang SC","Microsoft YaHei",sans-serif}}
.box{{width:min(560px,100%);padding:clamp(24px,6vw,42px);border:1px solid rgba(255,255,255,.86);border-radius:26px;
background:rgba(255,255,255,.74);box-shadow:0 28px 72px rgba(25,47,42,.15);backdrop-filter:blur(22px) saturate(145%)}}
.brand{{display:flex;align-items:center;gap:10px;margin-bottom:30px;font-weight:750}}.mark{{display:grid;width:40px;height:40px;
place-items:center;border-radius:13px;color:white;background:linear-gradient(145deg,#20947e,#0e6255);box-shadow:0 8px 18px rgba(14,98,85,.24)}}
.mark svg{{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}}
.eyebrow{{margin:0 0 8px;color:#0d5d50;font-size:10px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}}
h1{{margin:0;font-size:clamp(28px,7vw,42px);line-height:1.12;letter-spacing:-.04em}}.lead{{margin:14px 0 22px;color:var(--soft)}}
.device{{display:grid;gap:13px;padding:17px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.62)}}
.row{{display:flex;align-items:center;justify-content:space-between;gap:14px}}.label{{color:var(--soft);font-size:12px}}
code{{padding:5px 9px;border-radius:9px;color:#0d5d50;background:rgba(20,119,101,.1);font-size:18px;font-weight:750;letter-spacing:.08em}}
.notice{{margin:20px 0;color:var(--soft);font-size:13px}}button{{display:inline-flex;min-height:48px;align-items:center;justify-content:center;
gap:8px;border:0;border-radius:13px;padding:11px 17px;color:white;background:linear-gradient(145deg,#1a8a74,#0f6758);
box-shadow:0 10px 22px rgba(15,103,88,.2);font:inherit;font-weight:650;cursor:pointer;touch-action:manipulation}}
button:hover{{background:linear-gradient(145deg,#18806d,#0d5b4e)}}button:disabled{{cursor:wait;opacity:.62;box-shadow:none}}
button:focus-visible{{outline:3px solid rgba(20,119,101,.35);outline-offset:3px}}.meta{{color:var(--soft)}}#status{{min-height:24px;margin:13px 0 0}}
.danger{{color:#a23f4c}}@media(prefers-reduced-motion:reduce){{*{{transition-duration:.01ms!important}}}}
</style></head>
<body><main class="box"><div class="brand"><span class="mark" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="6" cy="6" r="2.2"/>
<circle cx="18" cy="7" r="2.2"/><circle cx="9" cy="18" r="2.2"/><path d="M8 7.2l7.8-.2M7.2 8l1.2 7.7M16.6 8.8l-5.8 7.4"/></svg></span>
Research Trace</div><p class="eyebrow">Secure device authorization</p><h1>批准设备登录</h1>
<p class="lead">确认这是你正在连接的机器。批准后，你可以随时在账户页面撤销它。</p>
<div class="device"><div class="row"><span class="label">设备</span><strong>{safe_name}</strong></div>
<div class="row"><span class="label">验证码</span><code>{safe_code}</code></div>
<div class="row"><span class="label">登录账号</span><strong>@{safe_login}</strong></div></div>
<p class="notice">这台设备只会获得 Research Trace 的独立凭证，不会获得你的 GitHub access token。</p>
<button id="approve">批准此设备</button><p id="status" class="meta" role="status" aria-live="polite"></p></main>
<script>document.querySelector('#approve').onclick=async()=>{{const out=document.querySelector('#status');
const button=document.querySelector('#approve');button.disabled=true;button.textContent='批准中…';out.className='meta';
try{{const me=await fetch('/api/v2/auth/me').then(r=>r.json());const r=await fetch('/api/v2/device/approve',{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':me.csrf_token}},body:JSON.stringify({{user_code:{normalized_code!r}}})}});const v=await r.json();if(!r.ok)throw Error(v.error||v.detail||r.statusText);out.textContent='已批准。可以返回终端，登录会自动完成。';button.textContent='已批准'}}catch(e){{button.disabled=false;button.textContent='批准此设备';out.className='danger';out.textContent=e.message}}}};</script></body></html>"""
        return HTMLResponse(page)

    @app.post("/api/v2/device/approve")
    async def device_approve(request: Request, identity: dict[str, Any] = Depends(require_user_csrf)):
        body = await request.json()
        return store.approve_device_authorization(body.get("user_code"), identity["user"]["id"])

    @app.get("/api/v2/auth/devices")
    def auth_devices(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login required")
        return {"devices": store.list_device_credentials(user_id=user["id"])}

    @app.delete("/api/v2/auth/devices/{device_id}")
    def auth_revoke_device(device_id: str, identity: dict[str, Any] = Depends(require_user_csrf)):
        user = identity["user"]
        return store.revoke_device_credential(
            device_id, requester_user_id=user["id"], is_admin=user["role"] == "admin"
        )

    @app.delete("/api/v2/device/self")
    def device_revoke_self(identity: dict[str, Any] = Depends(require_device)):
        return store.revoke_device_credential(
            identity["device"]["id"], requester_user_id=identity["user"]["id"]
        )

    @app.get("/api/v2/admin/users")
    def admin_users(_identity: dict[str, Any] = Depends(require_admin)):
        return {"users": store.list_auth_users()}

    @app.patch("/api/v2/admin/users/{user_id}")
    async def admin_update_user(user_id: str, request: Request,
                                _identity: dict[str, Any] = Depends(require_admin)):
        body = await request.json()
        return store.update_auth_user(user_id, role=body.get("role"), disabled=body.get("disabled"))

    @app.get("/api/v2/health")
    def health(request: Request):
        if oauth_config and not (bearer_identity(request) or browser_user(request)):
            return {
                "ok": True, "schema_version": 3, "oauth_enabled": True,
                "authentication_required": True,
            }
        value = store.health()
        value["write_protected"] = bool(write_token or oauth_config)
        value["oauth_enabled"] = bool(oauth_config)
        value["backup"] = dict(backup_state)
        return value

    @app.get("/api/v2/projects", dependencies=[Depends(require_read)])
    def projects():
        return {"projects": store.list_projects()}

    @app.post("/api/v2/projects", dependencies=[Depends(require_write)])
    async def create_project(request: Request):
        body = await request.json()
        return store.create_project(body.get("name"), workspace_keys=body.get("workspace_keys") or [],
                                    overview=body.get("overview") or "")

    @app.get("/api/v2/projects/{project_id}", dependencies=[Depends(require_read)])
    def project(project_id: str):
        return store.get_project(project_id)

    @app.get("/api/v2/projects/{project_id}/raw", dependencies=[Depends(require_read)])
    def raw_timeline(project_id: str, limit: int = 100):
        return {"items": store.raw_timeline(project_id, limit=limit)}

    @app.post("/api/v2/context", dependencies=[Depends(require_write)])
    async def context(request: Request):
        body = await request.json()
        return store.context(
            project_id=body.get("project_id"), workspace_keys=body.get("workspace_keys") or [],
            create_if_missing=bool(body.get("create_if_missing")), project_name=body.get("project_name"),
            recent_limit=body.get("recent_limit", 20),
        )

    @app.post("/api/v2/projects/{project_id}/chapters", dependencies=[Depends(require_write)])
    async def create_chapter(project_id: str, request: Request):
        body = await request.json()
        return store.create_chapter(project_id, body.get("name"), body.get("summary") or "")

    @app.post("/api/v2/record", dependencies=[Depends(require_write)])
    async def record(request: Request):
        body = await request.json()
        return store.record_node(
            body.get("project_id"), idempotency_key=body.get("idempotency_key"), title=body.get("title"),
            body=body.get("body") or "", chapter_id=body.get("chapter_id"),
            chapter_name=body.get("chapter_name"), parent_id=body.get("parent_id"),
            labels=body.get("labels") or [], occurred_at=body.get("occurred_at"),
            created_by=body.get("created_by") or "recorder",
            review_state=body.get("review_state") or "unreviewed",
            source_event_ids=body.get("source_event_ids") or [], code_evidence=body.get("code_evidence") or [],
        )

    @app.patch("/api/v2/nodes/{node_id}", dependencies=[Depends(require_write)])
    async def update_node(node_id: str, request: Request):
        body = await request.json()
        return store.update_node(
            node_id, body.get("patch") or {}, expect_version=body.get("expect_version"),
            actor_type=body.get("actor_type") or "human", actor_id=actor(request),
        )

    @app.post("/api/v2/curate", dependencies=[Depends(require_write)])
    async def curate(request: Request):
        body = await request.json()
        return store.curate(
            body.get("project_id"), target_type=body.get("target_type"), target_id=body.get("target_id"),
            body=body.get("body") or "", expect_version=body.get("expect_version"),
            actor_type=body.get("actor_type") or "recorder",
            actor_id=body.get("actor_id") or actor(request), source_event_ids=body.get("source_event_ids") or [],
            milestone=bool(body.get("milestone")), resolve_comment_ids=body.get("resolve_comment_ids") or [],
        )

    @app.get("/api/v2/revisions/{target_type}/{target_id}", dependencies=[Depends(require_read)])
    def revisions(target_type: str, target_id: str):
        return {"revisions": store.revisions(target_type, target_id)}

    @app.post("/api/v2/comments", dependencies=[Depends(require_write)])
    async def add_comment(request: Request):
        body = await request.json()
        return store.add_comment(
            body.get("project_id"), target_type=body.get("target_type"), target_id=body.get("target_id"),
            body=body.get("body"), kind=body.get("kind") or "comment", anchor=body.get("anchor") or {},
            author_type=body.get("author_type") or "human",
            author_id=body.get("author_id") or actor(request),
        )

    @app.post("/api/v2/comments/{comment_id}/resolve", dependencies=[Depends(require_write)])
    def resolve_comment(comment_id: str, request: Request):
        return store.resolve_comment(comment_id, actor(request))

    @app.post("/api/v2/ingest", dependencies=[Depends(require_write)])
    async def ingest(request: Request):
        body = await request.json()
        return store.ingest(
            batch_id=body.get("batch_id"), project_id=body.get("project_id"), session=body.get("session"),
            agents=body.get("agents") or [], events=body.get("events") or [],
            transcript_chunks=body.get("transcript_chunks") or [],
        )

    @app.post("/api/v2/attach", dependencies=[Depends(require_write)])
    async def attach(request: Request):
        body = await request.json()
        return store.attach(
            body.get("project_id"), target_type=body.get("target_type"), target_id=body.get("target_id"),
            name=body.get("name"), direction=body.get("direction") or "reference",
            mime_type=body.get("mime_type"), data_base64=body.get("data_base64"), uri=body.get("uri"),
            machine=body.get("machine"), external_path=body.get("external_path"), size=body.get("size"),
            sha256=body.get("sha256"), metadata=body.get("metadata") or {},
        )

    @app.get("/api/v2/attachments/{attachment_id}/content", dependencies=[Depends(require_read)])
    def attachment_content(attachment_id: str):
        path, mime, name = store.attachment_content(attachment_id)
        return FileResponse(path, media_type=mime or "application/octet-stream", filename=name)

    @app.get("/api/v2/search", dependencies=[Depends(require_read)])
    def search(q: str, project_id: str | None = None, scope: str = "all", limit: int = 50):
        return {"hits": store.search(q, project_id=project_id, scope=scope, limit=limit)}

    @app.get("/", response_class=HTMLResponse)
    def index():
        from .webapp import INDEX_HTML
        return HTMLResponse(INDEX_HTML)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Trace v2 central service")
    parser.add_argument("--data-dir", default=os.environ.get("TRACE_V2_DATA", ".trace-v2-data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("TRACE_V2_TOKEN", ""))
    parser.add_argument("--backup-repo", default=os.environ.get("TRACE_V2_BACKUP_REPO"))
    parser.add_argument("--backup-interval-hours", type=float,
                        default=float(os.environ.get("TRACE_V2_BACKUP_INTERVAL_HOURS", "24")))
    parser.add_argument("--backup-subdirectory",
                        default=os.environ.get("TRACE_V2_BACKUP_SUBDIRECTORY", "research-trace-backup"))
    parser.add_argument("--backup-remote", default=os.environ.get("TRACE_V2_BACKUP_REMOTE", "origin"))
    parser.add_argument("--backup-branch", default=os.environ.get("TRACE_V2_BACKUP_BRANCH", "main"))
    parser.add_argument("--public-url", default=os.environ.get("TRACE_V2_PUBLIC_URL"))
    parser.add_argument("--github-client-id", default=os.environ.get("TRACE_V2_GITHUB_CLIENT_ID"))
    parser.add_argument("--github-client-secret", default=os.environ.get("TRACE_V2_GITHUB_CLIENT_SECRET"))
    parser.add_argument("--session-secret", default=os.environ.get("TRACE_V2_SESSION_SECRET"))
    parser.add_argument("--github-admins", default=os.environ.get("TRACE_V2_GITHUB_ADMINS"))
    parser.add_argument("--github-allowed-users", default=os.environ.get("TRACE_V2_GITHUB_ALLOWED_USERS"))
    parser.add_argument("--github-allowed-org", default=os.environ.get("TRACE_V2_GITHUB_ALLOWED_ORG"))
    parser.add_argument("--github-allow-all", action="store_true",
                        default=_env_bool("TRACE_V2_GITHUB_ALLOW_ALL"))
    parser.add_argument("--session-days", type=int,
                        default=int(os.environ.get("TRACE_V2_SESSION_DAYS", "30")))
    parser.add_argument("--insecure-cookies", action="store_true",
                        default=_env_bool("TRACE_V2_INSECURE_COOKIES"))
    args = parser.parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required: pip install 'research-trace[server]'", file=os.sys.stderr)
        return 2
    uvicorn.run(
        create_app(
            args.data_dir, token=args.token, backup_repo=args.backup_repo,
            backup_interval_hours=args.backup_interval_hours,
            backup_subdirectory=args.backup_subdirectory, backup_remote=args.backup_remote,
            backup_branch=args.backup_branch, public_url=args.public_url,
            github_client_id=args.github_client_id, github_client_secret=args.github_client_secret,
            session_secret=args.session_secret, github_admins=args.github_admins,
            github_allowed_users=args.github_allowed_users, github_allowed_org=args.github_allowed_org,
            github_allow_all=args.github_allow_all, session_days=args.session_days,
            insecure_cookies=args.insecure_cookies,
        ),
        host=args.host, port=args.port, workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
