"""FastAPI application for the Research Trace central service."""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import html
import json
import os
import re
import secrets
import sys
import threading
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .auth import (
    ADMIN_BUCKET,
    MEMBER_BUCKET,
    OAUTH_NONCE_COOKIE,
    SESSION_COOKIE,
    GitHubOAuthClient,
    GitHubOAuthConfig,
    IdentityPins,
    OAuthError,
    PendingOAuthStore,
    RateLimiter,
    csrf_token,
    normalize_base_path,
    safe_return_to,
)
from .backup import sync_git_backup
from .deliver import workspace_key_problem
from .storage import (
    SCHEMA_VERSION,
    Conflict,
    NotFound,
    Store,
    StoreError,
    ValidationError,
    normalize_workspace_key,
    now_utc,
)

try:
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import (
        FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
    )
except ImportError:  # pragma: no cover
    Depends = FastAPI = HTTPException = Request = None  # type: ignore[assignment]
    FileResponse = HTMLResponse = JSONResponse = RedirectResponse = None  # type: ignore[assignment]
    PlainTextResponse = None  # type: ignore[assignment]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_timestamp(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


TEAM_MAP_SCHEMA = "research-trace.team-map.v1"
TEAM_MAP_FILE = "team-project-map.json"
TEAM_MAP_HISTORY_LIMIT = 500


def usable_workspace_keys(raw: Sequence[Any]) -> tuple[list[str], list[dict[str, str]]]:
    """规范化请求里的 workspace keys，并把路径形态的那些**丢掉并回报**。

    为什么是丢掉而不是整个请求 400：一个 `git clone /srv/mirror/repo` 的仓库，
    remote 就是一条本机路径，而它同时还有 marker 的 `rt-ws-…` key。把整个请求打回去
    等于让这类仓库根本没法绑定；只把不合格的那一个剔掉，身份仍然成立。

    但如果调用方给的**每一个** key 都是路径形态，那就一个机器无关的身份都没有，
    继续往下走只会在下一台机器上再建一个重复项目（§7）——那时候才 400。
    """
    kept: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        problem = workspace_key_problem(text)
        if problem:
            rejected.append({"workspace_key": text, "reason": problem})
            continue
        normalized = normalize_workspace_key(text)
        if normalized not in kept:
            kept.append(normalized)
    if rejected and not kept:
        raise ValidationError(
            "every workspace key was a filesystem path: " + "; ".join(
                f"{item['workspace_key']} {item['reason']}" for item in rejected
            )
        )
    return kept, rejected


def normalize_team_pattern(value: str) -> str:
    """团队映射规则的 glob。和 workspace key 走同一套规范化，否则两边比不上。"""
    text = str(value or "").strip()
    problem = workspace_key_problem(text)
    if problem:
        raise ValidationError(f"team mapping pattern {problem}")
    # `*` 一条规则就能把全世界映射到一个项目上，那是「静默落进错误项目」而不是
    # 「静默创建重复项目」，同样违背 §7 的意图。要求规则里有足够的字面量。
    if len(re.sub(r"[*?\[\]]", "", text)) < 4:
        raise ValidationError(
            "team mapping pattern must contain at least 4 literal characters; "
            "a bare wildcard would map every workspace to one project"
        )
    return normalize_workspace_key(text)


class TeamProjectMap:
    """§7 的第三种 workspace key 发现方式：团队配置映射。

    形状选择：中央持有一张表，管理员用 REST 维护，成员可读（因此也可以导出成
    一份团队配置文件发下去）。为什么不是 SQLite 表——这是运维配置而不是研究数据，
    和 `identity-pins.json` 同一类：从空库 restore 之后它必须能被管理员独立重建，
    而且要能直接 diff、直接读给人看。

    每条规则记 `created_by`（来自凭证，不是请求体）和 `created_at`，增删都进 `history`，
    §7 要求映射本身可被审计。
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        # FastAPI 把同步端点丢进线程池，两个管理员同时加规则会互相盖掉对方那一行。
        self._lock = threading.Lock()
        self._value: dict[str, Any] = {"schema": TEAM_MAP_SCHEMA, "rules": [], "history": []}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            self._value["rules"] = [r for r in (loaded.get("rules") or []) if isinstance(r, dict)]
            self._value["history"] = [h for h in (loaded.get("history") or []) if isinstance(h, dict)]

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        temp.write_text(
            json.dumps(self._value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def rules(self) -> list[dict[str, Any]]:
        return [dict(rule) for rule in self._value["rules"]]

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        window = self._value["history"][-max(1, min(int(limit), TEAM_MAP_HISTORY_LIMIT)):]
        return [dict(item) for item in reversed(window)]

    def _log(self, action: str, rule: dict[str, Any], actor: str) -> None:
        self._value["history"].append({
            "action": action, "rule_id": rule.get("id"), "pattern": rule.get("pattern"),
            "project_id": rule.get("project_id"), "actor": actor, "at": now_utc(),
        })
        del self._value["history"][:-TEAM_MAP_HISTORY_LIMIT]

    def add(self, *, pattern: str, project_id: str, note: str, actor: str) -> dict[str, Any]:
        normalized = normalize_team_pattern(pattern)
        project_id = str(project_id or "").strip()
        if not project_id:
            raise ValidationError("team mapping rule requires a project_id")
        with self._lock:
            for rule in self._value["rules"]:
                if rule.get("pattern") != normalized:
                    continue
                if rule.get("project_id") == project_id:
                    return dict(rule)  # 幂等：同一条规则重复添加不产生第二行
                raise Conflict(
                    f"pattern {normalized} already maps to {rule.get('project_id')}; "
                    "delete that rule first rather than stacking an ambiguous second one"
                )
            rule = {
                "id": "map_" + secrets.token_hex(8), "pattern": normalized,
                "project_id": project_id, "note": str(note or "")[:500],
                "created_by": actor, "created_at": now_utc(),
            }
            self._value["rules"].append(rule)
            self._log("add", rule, actor)
            self._write()
            return dict(rule)

    def remove(self, rule_id: str, *, actor: str) -> dict[str, Any]:
        with self._lock:
            for index, rule in enumerate(self._value["rules"]):
                if rule.get("id") == rule_id:
                    self._value["rules"].pop(index)
                    self._log("remove", rule, actor)
                    self._write()
                    return {"removed": True, "rule": dict(rule)}
        raise NotFound(f"team mapping rule not found: {rule_id}")

    def match(self, keys: Sequence[str]) -> list[dict[str, Any]]:
        """命中的规则，带上是哪一个 key 命中的。"""
        hits: list[dict[str, Any]] = []
        for rule in self._value["rules"]:
            pattern = str(rule.get("pattern") or "").lower()
            if not pattern or not rule.get("project_id"):
                continue
            for key in keys:
                # 必须是 fnmatchcase：fnmatch.fnmatch 会先过 os.path.normcase，
                # Windows 上它把 `/` 换成 `\`，同一条规则在两个平台上匹配结果不同。
                if fnmatch.fnmatchcase(str(key).lower(), pattern):
                    hits.append({**rule, "matched_key": key})
                    break
        return hits


ANONYMOUS_READ_WARNING = (
    "!!! Research Trace: GitHub OAuth is NOT configured. Every read endpoint is open to\n"
    "!!! anyone who can reach this port, including raw transcripts, tool arguments and\n"
    "!!! attachment downloads. TRACE_TOKEN only protects writes, never reads.\n"
    "!!! Configure TRACE_GITHUB_CLIENT_ID / _SECRET / TRACE_PUBLIC_URL / TRACE_SESSION_SECRET,\n"
    "!!! or bind the service to a loopback address that nothing else can reach."
)


NO_BACKUP_WARNING = (
    "!!! Research Trace: running with --no-backup. The SQLite database and the object\n"
    "!!! directory on this machine are the ONLY copy of every record and every raw\n"
    "!!! transcript. One disk failure ends the project's history.\n"
    "!!! Configure --backup-repo <a PRIVATE git worktree> as soon as this is more than a trial."
)

MISSING_BACKUP = (
    "refusing to start without a backup destination.\n"
    "\n"
    "A provenance system whose only copy lives on one disk is not a provenance system.\n"
    "Point --backup-repo (or TRACE_BACKUP_REPO) at a git worktree whose remote is a\n"
    "PRIVATE repository -- the export carries raw transcripts:\n"
    "\n"
    "  trace-server --data-dir <dir> --backup-repo /srv/research-trace/private-backup\n"
    "\n"
    "Only the named subdirectory is staged and committed, so pointing this at a repo you\n"
    "already use for something else does not sweep up its other changes.\n"
    "\n"
    "If you really mean to run without any backup (a local trial, a throwaway instance),\n"
    "say so explicitly with --no-backup or TRACE_NO_BACKUP=true."
)


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
    base_path: str | None = None,
    github_client_id: str | None = None,
    github_client_secret: str | None = None,
    public_url: str | None = None,
    session_secret: str | None = None,
    github_admins: str | set[str] | list[str] | tuple[str, ...] | None = None,
    github_allowed_users: str | set[str] | list[str] | tuple[str, ...] | None = None,
    github_allowed_org: str | None = None,
    github_allow_all: bool | None = None,
    session_days: int | None = None,
    device_credential_days: int | None = None,
    device_start_limit: int | None = None,
    device_start_window_seconds: float | None = None,
    trust_proxy_headers: bool | None = None,
    insecure_cookies: bool | None = None,
    oauth_client: Any | None = None,
):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("server requires fastapi; install research-trace[server]")

    # 挂载前缀。服务把整个前缀据为己有：不带前缀的请求一律 404（见下面的 base_guard），
    # 所以「靠不可猜路径隐藏」在绕过反向代理直连端口时同样成立。
    base = normalize_base_path(base_path if base_path is not None
                               else os.environ.get("TRACE_BASE_PATH"))
    cookie_path = base or "/"

    oauth_config = GitHubOAuthConfig.build(
        base_path=base,
        client_id=github_client_id if github_client_id is not None else os.environ.get("TRACE_GITHUB_CLIENT_ID"),
        client_secret=(github_client_secret if github_client_secret is not None
                       else os.environ.get("TRACE_GITHUB_CLIENT_SECRET")),
        public_url=public_url if public_url is not None else os.environ.get("TRACE_PUBLIC_URL"),
        session_secret=session_secret if session_secret is not None else os.environ.get("TRACE_SESSION_SECRET"),
        admins=github_admins if github_admins is not None else os.environ.get("TRACE_GITHUB_ADMINS"),
        allowed_users=(github_allowed_users if github_allowed_users is not None
                       else os.environ.get("TRACE_GITHUB_ALLOWED_USERS")),
        allowed_org=(github_allowed_org if github_allowed_org is not None
                     else os.environ.get("TRACE_GITHUB_ALLOWED_ORG")),
        allow_all=(github_allow_all if github_allow_all is not None
                   else _env_bool("TRACE_GITHUB_ALLOW_ALL")),
        session_days=(session_days if session_days is not None
                      else int(os.environ.get("TRACE_SESSION_DAYS", "30"))),
        device_credential_days=(device_credential_days if device_credential_days is not None
                                else int(os.environ.get("TRACE_DEVICE_CREDENTIAL_DAYS", "90"))),
        insecure_cookies=(insecure_cookies if insecure_cookies is not None
                          else _env_bool("TRACE_INSECURE_COOKIES")),
    )
    root = Path(data_dir or os.environ.get("TRACE_DATA") or ".trace-data")
    store = Store(root, attachment_limit=attachment_limit)
    write_token = token if token is not None else os.environ.get("TRACE_TOKEN", "")
    pending_oauth = PendingOAuthStore()
    github = oauth_client or (GitHubOAuthClient(oauth_config) if oauth_config else None)
    # 用户名 -> GitHub 数字 id 的钉子和 SQLite 放在同一个数据目录，
    # 这样搬数据目录不会把 admin 锚点丢在原地。
    identity_pins = IdentityPins(root / "identity-pins.json") if oauth_config else None
    # §7 第三种 workspace key。和 identity-pins 一样是运维配置，放数据目录旁边。
    team_map = TeamProjectMap(root / TEAM_MAP_FILE)
    device_start_limiter = RateLimiter(
        limit=(device_start_limit if device_start_limit is not None
               else int(os.environ.get("TRACE_DEVICE_START_LIMIT", "10"))),
        window_seconds=(device_start_window_seconds if device_start_window_seconds is not None
                        else float(os.environ.get("TRACE_DEVICE_START_WINDOW_SECONDS", "600"))),
    )
    trust_proxy = (trust_proxy_headers if trust_proxy_headers is not None
                   else _env_bool("TRACE_TRUST_PROXY_HEADERS"))
    if not oauth_config:
        print(ANONYMOUS_READ_WARNING, file=sys.stderr, flush=True)

    if oauth_config and identity_pins:
        # 钉子文件不在 SQLite 备份里，从空库 restore 之后会丢。auth_users 是备份的，
        # 所以用它把钉子重新长出来：同名取最早创建的那条，也就是原始持有者。
        for account in sorted(store.list_auth_users(), key=lambda item: str(item.get("created_at") or "")):
            name = str(account.get("login") or "").lower()
            github_id = account.get("github_id")
            if not name or not github_id:
                continue
            for bucket, principals in (
                (ADMIN_BUCKET, oauth_config.admins), (MEMBER_BUCKET, oauth_config.allowed_users)
            ):
                if name in principals.logins and identity_pins.pinned_id(bucket, name) is None:
                    identity_pins.pin(bucket, name, int(github_id))
    if oauth_config and not (oauth_config.allow_all or oauth_config.allowed_org):
        # 从白名单里删掉一个人之后，still_whitelisted 会在每次请求上挡住他，但
        # web_sessions / device_credentials 里的行会一直躺到自然过期。启动时把它们
        # 真正撤掉，这样"我已经移除他了"在数据库里也是真的。
        for account in store.list_auth_users():
            if oauth_config.resolve_role(
                login=str(account.get("login") or ""),
                github_id=account.get("github_id"),
                pins=identity_pins,
            ) is None:
                store.revoke_user_credentials(str(account.get("id") or ""))
    backup_state: dict[str, Any] = {
        "enabled": bool(backup_repo), "running": False, "last_attempt_at": None,
        "last_success_at": None, "error": None, "changed": None, "pushed": None,
        # 上一轮 commit 成功但 push 失败时这里 > 0：否则"远端落后几周"在健康页上
        # 看起来和一切正常完全一样。
        "unpushed_commits": None,
        # §13 的容量阈值告警。备份撞上 GitHub 的上限是**渐进**发生的：等到 push
        # 被拒才知道，就已经有一轮备份没写进去了。sync_git_backup 每轮都算这个，
        # 服务端必须把它带到 /api/health，否则那次计算谁也看不见。
        "capacity": None,
        # 大产物只备份引用元数据，但小附件的对象文件确实可能在数据卷上丢了。
        # 这不该让整轮备份失败，可是也绝不能悄悄过去。
        "missing_objects": None,
    }
    # 每台工作站最近一次 trace-deliver 的自述。它是客户端上报的，不是中央推断的，
    # 所以只作为健康显示，不参与任何正确性判断。
    outbox_reports: dict[str, dict[str, Any]] = {}
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
                capacity = result.get("capacity") or None
                backup_state.update(
                    last_success_at=now_utc(), changed=result["changed"], pushed=result["pushed"],
                    unpushed_commits=result.get("unpushed_commits"),
                    capacity=capacity,
                    missing_objects=list(result.get("missing_objects") or []),
                )
                # 无人值守的部署没人开网页。容量告警至少要落进服务日志一次。
                if capacity and capacity.get("level") in {"warn", "critical"}:
                    print(
                        "research-trace backup capacity "
                        f"{capacity.get('level')}: " + "; ".join(
                            str(item) for item in (capacity.get("warnings") or [])
                        ),
                        file=sys.stderr, flush=True,
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

    app = FastAPI(title="Research Trace", version="2.0.0-alpha.14", lifespan=lifespan,
                  root_path=base)
    app.state.base_path = base
    app.state.store = store
    app.state.write_token = write_token
    app.state.backup_status = backup_state
    app.state.oauth_config = oauth_config
    app.state.team_map = team_map

    @app.middleware("http")
    async def base_guard(request: Request, call_next):
        """前缀之外一律 404。

        只设 root_path 是不够的：Starlette 的 get_route_path 在路径**不以** root_path
        开头时原样返回，于是 /api/health 绕过前缀照样能匹配到路由。对「站点只存在于
        某个不可猜前缀之下」这种部署来说，那等于门没锁。
        """
        if not base:
            return await call_next(request)
        path = request.scope.get("path", "")
        if path == base:
            return RedirectResponse(base + "/", status_code=307)
        if not path.startswith(base + "/"):
            return PlainTextResponse("Not Found", status_code=404)
        return await call_next(request)

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
        route_path = request.url.path
        if base and route_path.startswith(base):
            route_path = route_path[len(base):] or "/"
        if (
            route_path.startswith("/auth/") or route_path.startswith("/api/auth/")
            or route_path.startswith("/api/device/")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StoreError)
    async def store_error(_request: Request, exc: StoreError):
        status = 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, Conflict) else 400 if isinstance(exc, ValidationError) else 500
        return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=status)

    def still_whitelisted(user: dict[str, Any]) -> bool:
        """配置里删掉一个人之后，他手上的会话和设备凭证必须立刻失效。

        原来只在 OAuth 回调那一次检查白名单，所以把人从 TRACE_GITHUB_ADMINS
        删掉并重启，他既有的 cookie 和 rtd_ 凭证照常能用。这里在每次请求上
        重新判一次。org / allow_all 需要联网问 GitHub，离线判不了，那两种配置
        本来就是宽授权，跳过。
        """
        if not oauth_config or oauth_config.allow_all or oauth_config.allowed_org:
            return True
        return oauth_config.resolve_role(
            login=str(user.get("login") or ""),
            github_id=user.get("github_id"),
            pins=identity_pins,
        ) is not None

    def device_credential_expiry(identity: dict[str, Any]) -> datetime | None:
        """到期时间来自铸造时落库的那一列，不再按 created_at + 环境变量现算。

        现算的版本有一条阴的路径：运维把 TRACE_DEVICE_CREDENTIAL_DAYS 调小、
        发现太严又调回去，所有已经"过期"的凭证会集体复活。落库之后这个值
        在铸造那一刻就定死了，改环境变量只影响此后新发的凭证。
        """
        stored = _parse_timestamp(identity.get("device", {}).get("expires_at"))
        if stored:
            return stored
        if not oauth_config:
            return None
        # 本轮之前铸造的凭证没有这一列，退回旧算法，不至于把它们一刀切成无限期。
        created = _parse_timestamp(identity.get("device", {}).get("created_at"))
        if not created:
            return None
        return created + timedelta(days=oauth_config.device_credential_days)

    def bearer_identity(request: Request) -> dict[str, Any] | None:
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not supplied:
            return None
        if write_token and secrets.compare_digest(supplied, write_token):
            return {"kind": "legacy_machine"}
        identity = store.device_credential_identity(supplied)
        if not identity:
            return None
        expires_at = device_credential_expiry(identity)
        if expires_at and expires_at <= datetime.now(timezone.utc):
            return None
        if not still_whitelisted(identity["user"]):
            return None
        if expires_at:
            identity["device"]["expires_at"] = expires_at.isoformat(timespec="milliseconds")
        return identity

    def browser_user(request: Request) -> dict[str, Any] | None:
        if not oauth_config:
            return None
        user = store.web_session_user(request.cookies.get(SESSION_COOKIE))
        if user and not still_whitelisted(user):
            return None
        return user

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

    def principal(identity: dict[str, Any], request: Request) -> tuple[str, str]:
        """把 (actor_type, actor_id) 只从凭证推出来，永远不看请求体。

        actor_type 是按凭证类别定的，不是按请求体说的：
        - 浏览器会话（GitHub 登录 + CSRF）是唯一能证明"有个人在回路里"的凭证，
          所以只有它是 `human`；
        - 设备凭证由 trace-login 发给机器，用它的是 Recorder / 投递器这类 agent，
          所以是 `recorder`；
        - 旧的共享 TRACE_TOKEN 和完全未配置认证的模式都没有可认证主体，
          按更受限的 `recorder` 处理，绝不能默认成 human —— 否则
          storage 里"未处理的人工 correction 必须先被确认"那道闸就形同虚设。
        """
        kind = identity.get("kind")
        if kind == "user":
            return "human", str(identity["user"]["login"])[:200]
        if kind == "device":
            return "recorder", f"{identity['user']['login']}@{identity['device']['name']}"[:200]
        label = (request.headers.get("X-Trace-Actor") or "").strip()[:200]
        return "recorder", label or "machine"

    @app.get("/api/auth/config")
    def auth_config():
        return {"enabled": bool(oauth_config),
                "login_url": base + "/auth/github/login" if oauth_config else None}

    @app.get("/auth/github/login")
    def github_login(return_to: str | None = None):
        if not oauth_config or not github:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        state, nonce, _verifier, challenge = pending_oauth.create(
            safe_return_to(return_to, base))
        response = RedirectResponse(github.authorize_url(state=state, challenge=challenge), status_code=302)
        response.set_cookie(
            OAUTH_NONCE_COOKIE, nonce, max_age=pending_oauth.ttl_seconds, httponly=True,
            secure=oauth_config.secure_cookies, samesite="lax", path=cookie_path,
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
            try:
                profile_github_id = int(profile.get("id"))
            except (TypeError, ValueError) as exc:
                raise OAuthError("GitHub identity is missing a numeric id") from exc
            # 白名单按不可变的 github_id 判定；用户名只是首次解析用的入口，
            # 解析后由 IdentityPins 钉住（见 auth.IdentityPins）。
            role_hint = oauth_config.resolve_role(
                login=profile.get("login"), github_id=profile_github_id,
                active_org_member=org_member, pins=identity_pins,
            )
            if not role_hint:
                raise OAuthError("this GitHub account is not allowed to access Research Trace")
            user = store.upsert_github_user(
                profile, default_role=role_hint, force_admin=role_hint == "admin"
            )
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
            secure=oauth_config.secure_cookies, samesite="lax", path=cookie_path,
        )
        response.delete_cookie(OAUTH_NONCE_COOKIE, path=cookie_path,
                               secure=oauth_config.secure_cookies, samesite="lax")
        return response

    @app.get("/api/auth/me")
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

    @app.post("/api/auth/logout")
    def auth_logout(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        raw_session = request.cookies.get(SESSION_COOKIE, "")
        if not browser_user(request):
            raise HTTPException(status_code=401, detail="GitHub login required")
        _check_csrf(request)
        store.delete_web_session(raw_session)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(SESSION_COOKIE, path=cookie_path,
                               secure=oauth_config.secure_cookies, samesite="lax")
        return response

    def client_key(request: Request) -> str:
        """限流用的客户端标识。

        反向代理终止 TLS 时 request.client.host 永远是 127.0.0.1，按它限流
        等于又变回一个全局配额。只有在明确声明前面有可信代理时才读
        X-Forwarded-For，并取最右一跳（我们自己的代理追加的那个），
        因为左边的部分是客户端可以随便伪造的。
        """
        if trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For", "")
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-1][:80]
        return (request.client.host if request.client else "unknown")[:80]

    def issued_credential_payload(value: dict[str, Any]) -> dict[str, Any]:
        # storage 铸造时已经写好 expires_at；这里只在老库里补一次。
        if not value.get("expires_at"):
            created = _parse_timestamp(value.get("device", {}).get("created_at"))
            if oauth_config and created:
                expiry = created + timedelta(days=oauth_config.device_credential_days)
                value["device"]["expires_at"] = expiry.isoformat(timespec="milliseconds")
                value["expires_at"] = value["device"]["expires_at"]
        return value

    @app.post("/api/device/start")
    async def device_start(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        # 这个端点没有鉴权（终端还没有任何凭证），所以必须按来源限流：
        # 否则一个客户端几十秒就能顶满全局 pending 上限，全团队十分钟内
        # 无法给任何新机器做设备登录。
        retry_after = device_start_limiter.hit(client_key(request))
        if retry_after:
            return JSONResponse(
                {"error": "too many device login attempts from this client; retry later"},
                status_code=429, headers={"Retry-After": str(int(retry_after) + 1)},
            )
        body = await request.json()
        value = store.start_device_authorization(body.get("device_name"))
        # 刻意不返回 verification_uri_complete：一键批准链接是 RFC 8628 §5.4
        # 点名的钓鱼入口，攻击者把自己机器的链接发给受害者，受害者点一次
        # 就把自己账号的长期设备凭证交了出去。用户必须手工输入 user code。
        return {
            **value,
            "verification_uri": f"{oauth_config.public_url}/device",
            "credential_days": oauth_config.device_credential_days,
        }

    @app.post("/api/device/token")
    async def device_token(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        body = await request.json()
        value = store.exchange_device_authorization(
            body.get("device_code"), credential_days=oauth_config.device_credential_days
        )
        if value["status"] == "pending":
            return JSONResponse(value, status_code=202)
        if value["status"] != "authorized":
            return JSONResponse(value, status_code=400)
        return issued_credential_payload(value)

    @app.post("/api/device/renew")
    def device_renew(request: Request, identity: dict[str, Any] = Depends(require_device)):
        """用一份还没过期的设备凭证换一份新的，不需要人再批准一次。

        持有未过期凭证本身就证明这台机器当初被本人批准过，所以滚动更新是
        安全的；过期之后就只能重走 trace-login + 人工批准。
        """
        if not oauth_config:
            # 换发要铸造新凭证，而有效期长度来自 OAuth 配置。关掉 OAuth 之后
            # 数据库里可能还留着旧凭证行，那也不该走这条路。
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user_id = identity["user"]["id"]
        # 每次换发都会新插一行 device_credentials；按设备限流，别让一份凭证
        # 把凭证表刷满。
        retry_after = device_start_limiter.hit("renew:" + str(identity["device"]["id"]))
        if retry_after:
            return JSONResponse(
                {"error": "this device is renewing too often; retry later"},
                status_code=429, headers={"Retry-After": str(int(retry_after) + 1)},
            )
        started = store.start_device_authorization(identity["device"]["name"])
        store.approve_device_authorization(started["user_code"], user_id)
        issued = store.exchange_device_authorization(
            started["device_code"], credential_days=oauth_config.device_credential_days
        )
        if issued.get("status") != "authorized":
            raise HTTPException(status_code=409, detail="could not renew this device credential")
        store.revoke_device_credential(identity["device"]["id"], requester_user_id=user_id)
        return issued_credential_payload(issued)

    @app.get("/api/device/authorization")
    def device_authorization_lookup(user_code: str, request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        if not browser_user(request):
            raise HTTPException(status_code=401, detail="GitHub login required")
        try:
            value = store.device_authorization(user_code)
        except ValidationError:
            value = None
        if not value:
            raise HTTPException(status_code=404, detail="device authorization is invalid or expired")
        return value

    @app.get("/device", response_class=HTMLResponse)
    def device_page(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user = browser_user(request)
        if not user:
            login_url = base + "/auth/github/login?" + urllib.parse.urlencode(
                {"return_to": base + "/device"})
            return RedirectResponse(login_url, status_code=303)
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
.warn{{margin:20px 0;padding:14px 16px;border:1px solid rgba(162,63,76,.3);border-radius:14px;
background:rgba(162,63,76,.07);color:#7d2f3a;font-size:13px}}
input#code{{width:100%;min-height:48px;padding:11px 14px;border:1px solid var(--line);border-radius:13px;
background:rgba(255,255,255,.8);color:var(--ink);font:inherit;font-size:20px;font-weight:700;letter-spacing:.16em;text-transform:uppercase}}
input#code:focus-visible{{outline:3px solid rgba(20,119,101,.35);outline-offset:2px}}
label{{display:block;margin:0 0 8px;color:var(--soft);font-size:12px}}[hidden]{{display:none!important}}
</style></head>
<body><main class="box"><div class="brand"><span class="mark" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="6" cy="6" r="2.2"/>
<circle cx="18" cy="7" r="2.2"/><circle cx="9" cy="18" r="2.2"/><path d="M8 7.2l7.8-.2M7.2 8l1.2 7.7M16.6 8.8l-5.8 7.4"/></svg></span>
Research Trace</div><p class="eyebrow">Secure device authorization</p><h1>批准设备登录</h1>
<p class="lead">当前账号 <strong>@{safe_login}</strong>。请把终端里显示的验证码手工输入到下面。</p>
<p class="warn">只有你刚刚在自己的机器上运行过 <code>trace-login</code> 才批准。
任何人通过聊天、邮件或链接发给你的验证码都不要输入 —— 批准等于把你账号的长期设备凭证交给那台机器。
本页不接受链接里带来的验证码，就是为了防这一手。</p>
<form id="lookupForm"><label for="code">终端显示的验证码</label>
<input id="code" name="code" autocomplete="off" autocapitalize="characters" spellcheck="false"
inputmode="latin" maxlength="9" placeholder="ABCD-EFGH" required>
<p></p><button id="lookup" type="submit">查看这台设备</button></form>
<div id="confirm" hidden><div class="device"><div class="row"><span class="label">设备</span><strong id="deviceName"></strong></div>
<div class="row"><span class="label">验证码</span><code id="deviceCode"></code></div>
<div class="row"><span class="label">登录账号</span><strong>@{safe_login}</strong></div></div>
<p class="notice">这台设备只会获得 Research Trace 的独立凭证（有效期 {oauth_config.device_credential_days} 天），
不会获得你的 GitHub access token。你可以随时在账户页面撤销它。</p>
<button id="approve" type="button">批准此设备</button></div>
<p id="status" class="meta" role="status" aria-live="polite"></p></main>
<script>
const BASE={base!r};
const $=id=>document.getElementById(id);
const out=$('status');
const fail=message=>{{out.className='danger';out.textContent=message}};
let pendingCode='';
$('lookupForm').onsubmit=async event=>{{
  event.preventDefault();
  out.className='meta';out.textContent='';
  const typed=$('code').value.replace(/[^A-Za-z0-9]/g,'').toUpperCase();
  if(typed.length!==8){{fail('验证码是 8 位字母数字，例如 ABCD-EFGH。');return}}
  const normalized=typed.slice(0,4)+'-'+typed.slice(4);
  const button=$('lookup');button.disabled=true;
  try{{
    const response=await fetch(BASE+'/api/device/authorization?user_code='+encodeURIComponent(normalized));
    const value=await response.json();
    if(!response.ok)throw Error(value.error||value.detail||response.statusText);
    pendingCode=value.user_code;
    $('deviceName').textContent=value.device_name;
    $('deviceCode').textContent=value.user_code;
    $('lookupForm').hidden=true;$('confirm').hidden=false;$('approve').focus();
  }}catch(error){{fail(error.message)}}finally{{button.disabled=false}}
}};
$('approve').onclick=async()=>{{
  const button=$('approve');button.disabled=true;button.textContent='批准中…';out.className='meta';
  try{{
    const me=await fetch(BASE+'/api/auth/me').then(r=>r.json());
    const response=await fetch(BASE+'/api/device/approve',{{method:'POST',
      headers:{{'Content-Type':'application/json','X-CSRF-Token':me.csrf_token}},
      body:JSON.stringify({{user_code:pendingCode}})}});
    const value=await response.json();
    if(!response.ok)throw Error(value.error||value.detail||response.statusText);
    out.textContent='已批准。可以返回终端，登录会自动完成。';button.textContent='已批准';
  }}catch(error){{button.disabled=false;button.textContent='批准此设备';fail(error.message)}}
}};
</script></body></html>"""
        return HTMLResponse(page)

    @app.post("/api/device/approve")
    async def device_approve(request: Request, identity: dict[str, Any] = Depends(require_user_csrf)):
        body = await request.json()
        return store.approve_device_authorization(body.get("user_code"), identity["user"]["id"])

    @app.get("/api/auth/devices")
    def auth_devices(request: Request):
        if not oauth_config:
            raise HTTPException(status_code=404, detail="GitHub OAuth is not enabled")
        user = browser_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="GitHub login required")
        devices = store.list_device_credentials(user_id=user["id"])
        for device in devices:
            if device.get("expires_at"):
                continue  # 铸造时就写好了；只给本轮之前的老行补算
            created = _parse_timestamp(device.get("created_at"))
            if created:
                device["expires_at"] = (
                    created + timedelta(days=oauth_config.device_credential_days)
                ).isoformat(timespec="milliseconds")
        return {"devices": devices}

    @app.delete("/api/auth/devices/{device_id}")
    def auth_revoke_device(device_id: str, identity: dict[str, Any] = Depends(require_user_csrf)):
        user = identity["user"]
        return store.revoke_device_credential(
            device_id, requester_user_id=user["id"], is_admin=user["role"] == "admin"
        )

    @app.delete("/api/device/self")
    def device_revoke_self(identity: dict[str, Any] = Depends(require_device)):
        return store.revoke_device_credential(
            identity["device"]["id"], requester_user_id=identity["user"]["id"]
        )

    @app.get("/api/admin/users")
    def admin_users(_identity: dict[str, Any] = Depends(require_admin)):
        return {"users": store.list_auth_users()}

    @app.patch("/api/admin/users/{user_id}")
    async def admin_update_user(user_id: str, request: Request,
                                _identity: dict[str, Any] = Depends(require_admin)):
        body = await request.json()
        return store.update_auth_user(user_id, role=body.get("role"), disabled=body.get("disabled"))

    @app.post("/api/admin/purge")
    async def admin_purge(request: Request, identity: dict[str, Any] = Depends(require_admin)):
        """§13 要求的管理员紧急 purge。此前只有 CLI 入口，运维必须能登到服务器上。

        操作者来自凭证而不是请求体；理由是必填，审计记录里只有 id/计数/操作者/理由，
        不留原文。
        """
        body = await request.json()
        return store.purge(
            actor_id=principal(identity, request)[1],
            reason=str(body.get("reason") or ""),
            project_ids=body.get("project_ids") or (),
            session_ids=body.get("session_ids") or (),
            node_ids=body.get("node_ids") or (),
            event_ids=body.get("event_ids") or (),
            transcript_chunk_ids=body.get("transcript_chunk_ids") or (),
        )

    @app.get("/api/admin/purges")
    def admin_purges(limit: int = 50, _identity: dict[str, Any] = Depends(require_admin)):
        return {"purges": store.purge_log(limit=limit)}

    @app.post("/api/telemetry/outbox", dependencies=[Depends(require_write)])
    async def telemetry_outbox(request: Request):
        """投递器每轮上报一次本机 outbox 健康（§10、§11）。

        中央自己看不见任何一台工作站的 pending/：不上报的话，"我这台机器的东西
        到底传上去没有"在界面上只能显示"未上报"。这里只收计数与时间戳，
        不收任何 outbox 内容。
        """
        body = await request.json()
        machine = str(body.get("machine") or body.get("host") or "unknown")[:200]
        outbox_reports[machine] = {
            "machine": machine,
            "pending": int(body.get("pending") or 0),
            "sent": int(body.get("sent") or 0),
            "oldest_pending_at": str(body.get("oldest_pending_at") or "") or None,
            "last_delivered_at": str(body.get("last_delivered_at") or "") or None,
            "last_error": str(body.get("last_error") or "") or None,
            "recorder_pending_batches": int(body.get("recorder_pending_batches") or 0),
            "reported_at": now_utc(),
        }
        while len(outbox_reports) > 200:  # 有界：别让伪造的 machine 名把内存吃光
            outbox_reports.pop(next(iter(outbox_reports)))
        return {"ok": True, "machines": len(outbox_reports)}

    @app.get("/api/health")
    def health(request: Request):
        if oauth_config and not (bearer_identity(request) or browser_user(request)):
            return {
                "ok": True, "schema_version": SCHEMA_VERSION, "oauth_enabled": True,
                "authentication_required": True,
            }
        value = store.health()
        value["write_protected"] = bool(write_token or oauth_config)
        value["oauth_enabled"] = bool(oauth_config)
        value["anonymous_read"] = not bool(oauth_config)
        value["backup"] = dict(backup_state)
        # 没有任何一台机器上报过就不给这一格，网页据此显示"未上报"而不是画个假绿灯。
        if outbox_reports:
            machines = list(outbox_reports.values())
            value["outbox"] = {"machines": machines}
            # §11：Recorder 未处理的游标。中央自己看不见它——语义 batch manifest
            # 只存在于每台机器本地的 outbox/<ws>/<session>/batches/。
            latest = max(machines, key=lambda item: str(item.get("reported_at") or ""))
            value["recorder"] = {
                "pending_batches": sum(
                    int(item.get("recorder_pending_batches") or 0) for item in machines
                ),
                "last_processed_at": latest.get("reported_at"),
                "last_error": latest.get("last_error"),
            }
        return value

    @app.get("/api/projects", dependencies=[Depends(require_read)])
    def projects():
        return {"projects": store.list_projects()}

    @app.post("/api/projects", dependencies=[Depends(require_write)])
    async def create_project(request: Request):
        body = await request.json()
        keys, rejected = usable_workspace_keys(body.get("workspace_keys") or [])
        value = store.create_project(body.get("name"), workspace_keys=keys,
                                     overview=body.get("overview") or "")
        if rejected:
            value["rejected_workspace_keys"] = rejected
        return value

    @app.get("/api/projects/{project_id}", dependencies=[Depends(require_read)])
    def project(project_id: str):
        return store.get_project(project_id)

    @app.get("/api/projects/{project_id}/raw", dependencies=[Depends(require_read)])
    def raw_timeline(project_id: str, limit: int = 100):
        return {"items": store.raw_timeline(project_id, limit=limit)}

    @app.post("/api/context", dependencies=[Depends(require_write)])
    async def context(request: Request):
        """按 §7 的三种 workspace key 依次解析：显式 marker key / 规范化 Git remote
        （两者都在 `workspace_keys` 表里）→ 团队配置映射 → 最后才考虑新建。

        创建被推迟到映射之后是这条路径的关键：以前 `create_if_missing` 一路直达
        `store.context`，团队映射再准也没机会说话，第二台机器照样新建一个重复项目。
        """
        body = await request.json()
        keys, rejected = usable_workspace_keys(body.get("workspace_keys") or [])
        recent_limit = body.get("recent_limit", 20)
        # §8 的派生数据流视图是可选的，默认不算：context 是每个 batch 都要拉的热路径。
        # 这个标志必须原样透传给 storage，否则 MCP 侧的 include_dataflow 会被静默吞掉，
        # 客户端只会看到「这台服务器不会算数据流」。
        include_dataflow = bool(body.get("include_dataflow"))

        def decorate(value: dict[str, Any]) -> dict[str, Any]:
            if rejected:
                value["rejected_workspace_keys"] = rejected
            return value

        result = store.context(
            project_id=body.get("project_id"), workspace_keys=keys,
            create_if_missing=False, project_name=body.get("project_name"),
            recent_limit=recent_limit, include_dataflow=include_dataflow,
        )
        if result.get("matched") is not False:
            return decorate(result)

        # 规则指向的项目可能已经被 purge 掉了。这种规则不能算命中，否则整条
        # /api/context 会 404，这台机器就此停摆——而它其实只需要退回到「没有映射」。
        matches = [rule for rule in team_map.match(keys)
                   if _project_name_or_none(rule["project_id"]) is not None]
        targets = {str(rule["project_id"]) for rule in matches}
        if len(targets) > 1:
            # §7 硬要求：不确定时进入待确认状态，**即使调用方传了 create_if_missing**。
            # 这里返回 200 而不是 409，因为「需要人来选一个」是一个正常的中间状态，
            # 客户端要拿着候选列表继续往下走，而不是把它当成一次失败。
            return decorate({
                "matched": False, "pending_confirmation": True,
                "reason": "team_mapping_ambiguous", "workspace_keys": keys,
                "candidates": [
                    {**rule, "project_name": _project_name_or_none(rule["project_id"])}
                    for rule in matches
                ],
                "projects": result.get("projects"),
            })
        if targets:
            pid = next(iter(targets))
            try:
                # 把命中的 key 登记到这个项目上：下一次这台机器（和这个仓库的
                # 下一个 clone）直接走第一/第二种发现方式，不必再过映射。
                if keys:
                    store.add_workspace_keys(pid, keys)
            except StoreError:
                pass
            resolved = store.context(
                project_id=pid, recent_limit=recent_limit, include_dataflow=include_dataflow
            )
            resolved["resolved_by"] = "team_mapping"
            resolved["matched_rules"] = matches
            return decorate(resolved)

        if body.get("create_if_missing"):
            return decorate(store.context(
                project_id=body.get("project_id"), workspace_keys=keys,
                create_if_missing=True, project_name=body.get("project_name"),
                recent_limit=recent_limit, include_dataflow=include_dataflow,
            ))
        return decorate(result)

    @app.get("/api/projects/{project_id}/dataflow", dependencies=[Depends(require_read)])
    def project_dataflow(project_id: str, limit: int = 2000):
        """§8 的可选派生视图，给网页一个不用重拉整个 context 的入口。

        边只来自 Node 上明确登记的 input/output artifact 键；没有 artifact 关系的项目
        返回空图，那是正常状态而不是错误（§8 最后一句）。
        """
        return store.dataflow(project_id, limit=limit)

    def _project_name_or_none(project_id: str) -> str | None:
        """规则指向的项目还在不在。不在就返回 None——规则本身留着不动，
        删除是管理员的显式动作，不是一次读请求的副作用。"""
        try:
            return str(store.get_project(project_id, include_nodes=False)["name"])
        except StoreError:
            return None

    @app.get("/api/team/mapping", dependencies=[Depends(require_read)])
    def team_mapping(history: int = 50):
        """团队映射的可读视图，也是「可分发的团队配置文件」的导出口。"""
        return {"schema": TEAM_MAP_SCHEMA, "rules": team_map.rules(),
                "history": team_map.history(history)}

    @app.post("/api/team/mapping")
    async def team_mapping_add(request: Request, identity: dict[str, Any] = Depends(require_admin)):
        body = await request.json()
        # 规则必须指向一个真实存在的项目，否则第一个撞上它的人会被送进一个
        # 解析不出来的待确认状态，而他根本看不到规则是谁写错的。
        project = store.get_project(str(body.get("project_id") or ""), include_nodes=False)
        return team_map.add(
            pattern=body.get("pattern"), project_id=str(project["id"]),
            note=body.get("note") or "", actor=principal(identity, request)[1],
        )

    @app.delete("/api/team/mapping/{rule_id}")
    def team_mapping_remove(rule_id: str, request: Request,
                            identity: dict[str, Any] = Depends(require_admin)):
        return team_map.remove(rule_id, actor=principal(identity, request)[1])

    @app.post("/api/projects/{project_id}/chapters", dependencies=[Depends(require_write)])
    async def create_chapter(project_id: str, request: Request):
        body = await request.json()
        return store.create_chapter(project_id, body.get("name"), body.get("summary") or "")

    @app.post("/api/record")
    async def record(request: Request, identity: dict[str, Any] = Depends(require_write)):
        body = await request.json()
        actor_type, _actor_id = principal(identity, request)
        # created_by 决定 storage 里那条 "recorder 的旧幂等重试不得覆盖人类改动"
        # 的闸门，所以只能来自凭证；review_state 同理，非人类一律 unreviewed。
        return store.record_node(
            body.get("project_id"), idempotency_key=body.get("idempotency_key"), title=body.get("title"),
            body=body.get("body") or "", chapter_id=body.get("chapter_id"),
            chapter_name=body.get("chapter_name"), parent_id=body.get("parent_id"),
            labels=body.get("labels") or [], occurred_at=body.get("occurred_at"),
            created_by=actor_type,
            review_state=(body.get("review_state") or "unreviewed") if actor_type == "human" else "unreviewed",
            source_event_ids=body.get("source_event_ids") or [], code_evidence=body.get("code_evidence") or [],
        )

    @app.patch("/api/nodes/{node_id}")
    async def update_node(node_id: str, request: Request,
                          identity: dict[str, Any] = Depends(require_write)):
        body = await request.json()
        actor_type, actor_id = principal(identity, request)
        patch = body.get("patch") or {}
        if actor_type != "human" and isinstance(patch, dict) and "review_state" in patch:
            # 确认/纠正是人的动作。Recorder 不能自我确认（REQUIREMENTS §15），
            # 而 PATCH 里的 review_state 曾经是绕过它最直接的一条路。
            raise HTTPException(
                status_code=403, detail="only a signed-in human may change review_state"
            )
        return store.update_node(
            node_id, patch, expect_version=body.get("expect_version"),
            actor_type=actor_type, actor_id=actor_id,
        )

    @app.post("/api/curate")
    async def curate(request: Request, identity: dict[str, Any] = Depends(require_write)):
        body = await request.json()
        actor_type, actor_id = principal(identity, request)
        return store.curate(
            body.get("project_id"), target_type=body.get("target_type"), target_id=body.get("target_id"),
            body=body.get("body") or "", expect_version=body.get("expect_version"),
            actor_type=actor_type, actor_id=actor_id,
            source_event_ids=body.get("source_event_ids") or [],
            milestone=bool(body.get("milestone")), resolve_comment_ids=body.get("resolve_comment_ids") or [],
        )

    @app.get("/api/revisions/{target_type}/{target_id}", dependencies=[Depends(require_read)])
    def revisions(target_type: str, target_id: str):
        return {"revisions": store.revisions(target_type, target_id)}

    @app.post("/api/comments")
    async def add_comment(request: Request, identity: dict[str, Any] = Depends(require_write)):
        body = await request.json()
        author_type, author_id = principal(identity, request)
        kind = body.get("kind") or "comment"
        if author_type != "human" and kind in {"confirmation", "correction"}:
            # confirmation 会把 Node 直接改成 confirmed，correction 会占住
            # curate 的闸门。两者都必须是真人在网页上做的。
            raise HTTPException(
                status_code=403,
                detail="only a signed-in human may post a confirmation or correction",
            )
        return store.add_comment(
            body.get("project_id"), target_type=body.get("target_type"), target_id=body.get("target_id"),
            body=body.get("body"), kind=kind, anchor=body.get("anchor") or {},
            author_type=author_type, author_id=author_id,
        )

    @app.post("/api/comments/{comment_id}/resolve")
    def resolve_comment(comment_id: str, request: Request,
                        identity: dict[str, Any] = Depends(require_write)):
        actor_type, actor_id = principal(identity, request)
        if actor_type != "human":
            # 了结一条纠正是人的动作（§9：Comments 的人工操作走网页 REST）。
            # curate 那边已经把机器的"我读过了"降级成 acknowledgement 了，如果这条
            # 端点还敞着，Recorder 只要多调一次就能把人的纠正彻底抹掉。
            raise HTTPException(
                status_code=403, detail="only a signed-in human may resolve a comment"
            )
        return store.resolve_comment(comment_id, actor_id)

    @app.post("/api/ingest")
    async def ingest(request: Request, identity: dict[str, Any] = Depends(require_write)):
        body = await request.json()
        # 投递已经和产生事件的那个 session 解耦了，所以「哪台机器推的这批」
        # 只能从凭证记下来，请求体说了不算。
        return store.ingest(
            batch_id=body.get("batch_id"), project_id=body.get("project_id"), session=body.get("session"),
            agents=body.get("agents") or [], events=body.get("events") or [],
            transcript_chunks=body.get("transcript_chunks") or [],
            delivered_by=principal(identity, request)[1],
        )

    @app.post("/api/attach", dependencies=[Depends(require_write)])
    async def attach(request: Request):
        body = await request.json()
        return store.attach(
            body.get("project_id"), target_type=body.get("target_type"), target_id=body.get("target_id"),
            name=body.get("name"), direction=body.get("direction") or "reference",
            mime_type=body.get("mime_type"), data_base64=body.get("data_base64"), uri=body.get("uri"),
            machine=body.get("machine"), external_path=body.get("external_path"), size=body.get("size"),
            sha256=body.get("sha256"), metadata=body.get("metadata") or {},
        )

    @app.get("/api/attachments/{attachment_id}/content", dependencies=[Depends(require_read)])
    def attachment_content(attachment_id: str):
        path, mime, name = store.attachment_content(attachment_id)
        return FileResponse(path, media_type=mime or "application/octet-stream", filename=name)

    @app.get("/api/search", dependencies=[Depends(require_read)])
    def search(q: str, project_id: str | None = None, scope: str = "all", limit: int = 50):
        # as_dict() 是旧结构的超集（仍带 hits），额外带 totals / returned / omitted /
        # truncated。存储层早就算出"还有多少条没显示"，以前在这一行被丢掉，
        # 于是界面永远看不出自己只拿到了一部分。
        return store.search(q, project_id=project_id, scope=scope, limit=limit).as_dict()

    @app.get("/", response_class=HTMLResponse)
    def index():
        from .webapp import render_index
        return HTMLResponse(render_index(base))

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Trace central service")
    parser.add_argument("--data-dir", default=os.environ.get("TRACE_DATA", ".trace-data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("TRACE_TOKEN", ""))
    parser.add_argument("--base-path", default=os.environ.get("TRACE_BASE_PATH", ""),
                        help="把服务挂在一个路径前缀下，例如 /trace；前缀之外一律 404")
    parser.add_argument("--backup-repo", default=os.environ.get("TRACE_BACKUP_REPO"))
    parser.add_argument("--no-backup", action="store_true", default=_env_bool("TRACE_NO_BACKUP"),
                        help="明确表示这个实例不要备份（本地试用/一次性实例）")
    parser.add_argument("--backup-interval-hours", type=float,
                        default=float(os.environ.get("TRACE_BACKUP_INTERVAL_HOURS", "24")))
    parser.add_argument("--backup-subdirectory",
                        default=os.environ.get("TRACE_BACKUP_SUBDIRECTORY", "research-trace-backup"))
    parser.add_argument("--backup-remote", default=os.environ.get("TRACE_BACKUP_REMOTE", "origin"))
    parser.add_argument("--backup-branch", default=os.environ.get("TRACE_BACKUP_BRANCH", "main"))
    parser.add_argument("--public-url", default=os.environ.get("TRACE_PUBLIC_URL"))
    parser.add_argument("--github-client-id", default=os.environ.get("TRACE_GITHUB_CLIENT_ID"))
    parser.add_argument("--github-client-secret", default=os.environ.get("TRACE_GITHUB_CLIENT_SECRET"))
    parser.add_argument("--session-secret", default=os.environ.get("TRACE_SESSION_SECRET"))
    parser.add_argument("--github-admins", default=os.environ.get("TRACE_GITHUB_ADMINS"))
    parser.add_argument("--github-allowed-users", default=os.environ.get("TRACE_GITHUB_ALLOWED_USERS"))
    parser.add_argument("--github-allowed-org", default=os.environ.get("TRACE_GITHUB_ALLOWED_ORG"))
    parser.add_argument("--github-allow-all", action="store_true",
                        default=_env_bool("TRACE_GITHUB_ALLOW_ALL"))
    parser.add_argument("--session-days", type=int,
                        default=int(os.environ.get("TRACE_SESSION_DAYS", "30")))
    parser.add_argument("--device-credential-days", type=int,
                        default=int(os.environ.get("TRACE_DEVICE_CREDENTIAL_DAYS", "90")))
    parser.add_argument("--device-start-limit", type=int,
                        default=int(os.environ.get("TRACE_DEVICE_START_LIMIT", "10")))
    parser.add_argument("--device-start-window-seconds", type=float,
                        default=float(os.environ.get("TRACE_DEVICE_START_WINDOW_SECONDS", "600")))
    parser.add_argument("--trust-proxy-headers", action="store_true",
                        default=_env_bool("TRACE_TRUST_PROXY_HEADERS"))
    parser.add_argument("--insecure-cookies", action="store_true",
                        default=_env_bool("TRACE_INSECURE_COOKIES"))
    args = parser.parse_args(argv)
    # 备份不是可选项，而是必须做出的一个选择。默认什么都不配就跑起来，等于让每个部署
    # 都默默停在「唯一副本在一块盘上」这个状态 —— 而这件事通常要到盘坏了才被发现。
    if not str(args.backup_repo or "").strip() and not args.no_backup:
        print("trace-server: " + MISSING_BACKUP, file=os.sys.stderr)
        return 2
    if args.no_backup:
        print(NO_BACKUP_WARNING, file=os.sys.stderr)
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
            backup_branch=args.backup_branch, base_path=args.base_path,
            public_url=args.public_url,
            github_client_id=args.github_client_id, github_client_secret=args.github_client_secret,
            session_secret=args.session_secret, github_admins=args.github_admins,
            github_allowed_users=args.github_allowed_users, github_allowed_org=args.github_allowed_org,
            github_allow_all=args.github_allow_all, session_days=args.session_days,
            device_credential_days=args.device_credential_days,
            device_start_limit=args.device_start_limit,
            device_start_window_seconds=args.device_start_window_seconds,
            trust_proxy_headers=args.trust_proxy_headers,
            insecure_cookies=args.insecure_cookies,
        ),
        host=args.host, port=args.port, workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
