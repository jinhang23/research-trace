"""GitHub OAuth and browser-session helpers for the Research Trace service.

OAuth access tokens are deliberately short lived in this process: they are used
only to read the GitHub identity (and optional organization membership) during
the callback.  Research Trace persists only its own opaque, hashed sessions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SESSION_COOKIE = "trace_session"
OAUTH_NONCE_COOKIE = "trace_oauth_nonce"
ADMIN_BUCKET = "admins"
MEMBER_BUCKET = "allowed_users"


class OAuthError(RuntimeError):
    """A safe-to-display OAuth failure without secrets or upstream bodies."""


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def csrf_token(session_secret: str, raw_session: str) -> str:
    return _b64url(hmac.new(
        session_secret.encode("utf-8"), raw_session.encode("utf-8"), hashlib.sha256
    ).digest())


def safe_return_to(value: str | None) -> str:
    value = str(value or "/").strip()
    if (
        not value.startswith("/") or value.startswith("//") or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return "/"
    return value


@dataclass(frozen=True)
class Principals:
    """一条白名单（admins 或 allowed_users）解析后的形状。

    GitHub 用户名在账号改名或注销后会被释放，任何人都能抢注同一个名字。
    所以用户名只被当作"还没解析出数字 id 的占位符"：真正的锚是不可变的
    `github_id`。配置里可以直接写 `id:12345` 一步到位，也可以写用户名，
    由 IdentityPins 在首次登录时把 id 钉下来。
    """

    logins: frozenset[str]
    github_ids: frozenset[int]

    def __bool__(self) -> bool:
        return bool(self.logins or self.github_ids)


def parse_principals(
    value: str | set[str] | list[str] | tuple[str, ...] | None,
) -> Principals:
    if value is None:
        return Principals(frozenset(), frozenset())
    items = value.split(",") if isinstance(value, str) else value
    logins: set[str] = set()
    ids: set[int] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        # 只有显式的 `id:` 前缀才当数字 id：GitHub 允许纯数字用户名，
        # 裸数字如果被猜成 id，会把一个普通用户静默提权成别人。
        if text.lower().startswith("id:"):
            digits = text[3:].strip()
            if digits.isdigit() and int(digits) > 0:
                ids.add(int(digits))
                continue
            raise ValueError(f"invalid GitHub id entry: {text}")
        logins.add(text.lower())
    return Principals(frozenset(logins), frozenset(ids))


class IdentityPins:
    """把配置里写的 GitHub 用户名，在首次成功解析后钉到不可变的数字 id 上。

    迁移路径：现有部署的 `TRACE_GITHUB_ADMINS=jinhang23` 不用改。第一次
    jinhang23 登录成功时把 `jinhang23 -> 4711` 写进这个文件；此后同一条配置
    只认 4711，用户名被释放并被抢注也拿不到权限；而本人改名后仍然认得出来。
    """

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._pins: dict[str, dict[str, int]] = self._load()

    def _load(self) -> dict[str, dict[str, int]]:
        if not self.path or not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 钉的记录坏了不能让服务起不来；退化成"还没钉过"，
            # 下一次成功登录会重新写。
            return {}
        pins: dict[str, dict[str, int]] = {}
        for bucket, mapping in (raw.get("pins") or {}).items():
            if not isinstance(mapping, dict):
                continue
            pins[str(bucket)] = {
                str(login).lower(): int(value)
                for login, value in mapping.items()
                if str(value).lstrip("-").isdigit() and int(value) > 0
            }
        return pins

    def _write_locked(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps({"version": 1, "pins": self._pins}, indent=2, sort_keys=True) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.path)

    def pinned_id(self, bucket: str, login: str) -> int | None:
        with self._lock:
            return self._pins.get(bucket, {}).get(str(login).lower())

    def pin(self, bucket: str, login: str, github_id: int) -> None:
        normalized = str(login).lower()
        with self._lock:
            current = self._pins.setdefault(bucket, {})
            if current.get(normalized) == int(github_id):
                return
            current[normalized] = int(github_id)
            self._write_locked()

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {bucket: dict(mapping) for bucket, mapping in self._pins.items()}


class RateLimiter:
    """按 key 的滑动窗口限流，全部在进程内存里。

    只用于挡住"一个客户端 15 秒打满全局配额"这种粗暴洪水；单实例服务
    （SQLite WAL 只允许一个实例）下够用，不需要外部状态。
    """

    def __init__(self, limit: int, window_seconds: float, max_keys: int = 4096):
        self.limit = max(int(limit), 1)
        self.window_seconds = max(float(window_seconds), 1.0)
        self.max_keys = max(int(max_keys), 16)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """记一次尝试；返回 0 表示放行，否则返回建议的 Retry-After 秒数。"""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            for existing in [name for name, times in self._hits.items() if not times or times[-1] <= cutoff]:
                self._hits.pop(existing, None)
            if len(self._hits) >= self.max_keys and key not in self._hits:
                # 攻击者可以换 IP 撑爆字典；宁可对新 key 直接退让，
                # 也不要让限流器本身变成内存耗尽的入口。
                return self.window_seconds
            times = self._hits.setdefault(key, deque())
            while times and times[0] <= cutoff:
                times.popleft()
            if len(times) >= self.limit:
                return max(times[0] + self.window_seconds - now, 1.0)
            times.append(now)
            return 0.0


@dataclass(frozen=True)
class GitHubOAuthConfig:
    client_id: str
    client_secret: str
    public_url: str
    session_secret: str
    admins: Principals
    allowed_users: Principals
    allowed_org: str | None = None
    allow_all: bool = False
    session_days: int = 30
    device_credential_days: int = 90
    secure_cookies: bool = True

    @property
    def callback_url(self) -> str:
        return f"{self.public_url}/auth/github/callback"

    @property
    def scopes(self) -> str:
        return "read:user read:org" if self.allowed_org else "read:user"

    @classmethod
    def build(
        cls,
        *,
        client_id: str | None,
        client_secret: str | None,
        public_url: str | None,
        session_secret: str | None,
        admins: str | set[str] | list[str] | tuple[str, ...] | None = None,
        allowed_users: str | set[str] | list[str] | tuple[str, ...] | None = None,
        allowed_org: str | None = None,
        allow_all: bool = False,
        session_days: int = 30,
        device_credential_days: int = 90,
        insecure_cookies: bool = False,
    ) -> "GitHubOAuthConfig | None":
        pieces = [str(client_id or "").strip(), str(client_secret or "").strip(),
                  str(public_url or "").strip(), str(session_secret or "").strip()]
        if not any(pieces):
            return None
        if not all(pieces):
            raise ValueError(
                "GitHub OAuth requires client id, client secret, public URL, and session secret"
            )
        client_id_value, client_secret_value, public_url_value, session_secret_value = pieces
        public_url_value = public_url_value.rstrip("/")
        parsed = urllib.parse.urlparse(public_url_value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("GitHub OAuth public URL must be an absolute http(s) origin")
        if parsed.path not in {"", "/"}:
            raise ValueError("GitHub OAuth public URL must not include a path")
        is_loopback = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (insecure_cookies and is_loopback):
            raise ValueError("GitHub OAuth requires HTTPS (HTTP is allowed only for loopback development)")
        if len(session_secret_value) < 32:
            raise ValueError("GitHub OAuth session secret must be at least 32 characters")
        admin_set = parse_principals(admins)
        user_set = parse_principals(allowed_users)
        org_value = str(allowed_org or "").strip() or None
        if not (admin_set or user_set or org_value or allow_all):
            raise ValueError(
                "GitHub OAuth needs at least one admin, allowed user, allowed organization, or allow-all"
            )
        if not 1 <= int(session_days) <= 365:
            raise ValueError("GitHub OAuth session days must be between 1 and 365")
        if not 1 <= int(device_credential_days) <= 3650:
            raise ValueError("device credential days must be between 1 and 3650")
        return cls(
            client_id=client_id_value,
            client_secret=client_secret_value,
            public_url=public_url_value,
            session_secret=session_secret_value,
            admins=admin_set,
            allowed_users=user_set,
            allowed_org=org_value,
            allow_all=bool(allow_all),
            session_days=int(session_days),
            device_credential_days=int(device_credential_days),
            secure_cookies=parsed.scheme == "https",
        )

    @staticmethod
    def _matches(
        principals: Principals,
        bucket: str,
        login: str,
        github_id: int | None,
        pins: IdentityPins | None,
    ) -> bool:
        if github_id is not None and int(github_id) in principals.github_ids:
            return True
        if github_id is not None and pins is not None:
            # 本人改名之后，配置里写的仍是旧用户名，但钉住的 id 没变，
            # 所以先按 id 反查一遍，别把改过名的管理员锁在门外。
            for candidate in principals.logins:
                if pins.pinned_id(bucket, candidate) == int(github_id):
                    return True
        normalized = str(login or "").strip().lower()
        if not normalized or normalized not in principals.logins:
            return False
        if github_id is None or pins is None:
            return True
        pinned = pins.pinned_id(bucket, normalized)
        if pinned is None:
            pins.pin(bucket, normalized, int(github_id))
            return True
        # 名字对上了但 id 对不上 —— 这正是"用户名被释放后被抢注"的形状。
        return pinned == int(github_id)

    def resolve_role(
        self,
        *,
        login: str,
        github_id: int | None = None,
        active_org_member: bool = False,
        pins: IdentityPins | None = None,
    ) -> str | None:
        if self._matches(self.admins, ADMIN_BUCKET, login, github_id, pins):
            return "admin"
        if self._matches(self.allowed_users, MEMBER_BUCKET, login, github_id, pins):
            return "member"
        if self.allow_all or active_org_member:
            return "member"
        return None


@dataclass(frozen=True)
class PendingOAuth:
    verifier: str
    nonce_hash: str
    return_to: str
    expires_at: float


class PendingOAuthStore:
    """One-process, one-use state store; deliberately absent from backups."""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PendingOAuth] = {}
        self._lock = threading.Lock()

    def create(self, return_to: str | None) -> tuple[str, str, str, str]:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        attempt = PendingOAuth(
            verifier=verifier,
            nonce_hash=hash_secret(nonce),
            return_to=safe_return_to(return_to),
            expires_at=time.time() + self.ttl_seconds,
        )
        with self._lock:
            now = time.time()
            self._items = {key: value for key, value in self._items.items() if value.expires_at > now}
            self._items[hash_secret(state)] = attempt
        return state, nonce, verifier, pkce_challenge(verifier)

    def consume(self, state: str, nonce: str | None) -> PendingOAuth:
        with self._lock:
            attempt = self._items.pop(hash_secret(str(state or "")), None)
        if attempt is None or attempt.expires_at <= time.time():
            raise OAuthError("OAuth state is invalid, expired, or already used")
        if not nonce or not secrets.compare_digest(hash_secret(nonce), attempt.nonce_hash):
            raise OAuthError("OAuth browser binding is invalid")
        return attempt


class GitHubOAuthClient:
    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    API_URL = "https://api.github.com"

    def __init__(self, config: GitHubOAuthConfig, timeout: float = 15):
        self.config = config
        self.timeout = timeout

    def authorize_url(self, *, state: str, challenge: str) -> str:
        query = urllib.parse.urlencode({
            "client_id": self.config.client_id,
            "redirect_uri": self.config.callback_url,
            "scope": self.config.scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{self.AUTHORIZE_URL}?{query}"

    def _json_request(
        self, url: str, *, data: dict[str, str] | None = None, token: str | None = None
    ) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "research-trace",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=encoded, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise OAuthError(f"GitHub OAuth request failed ({type(exc).__name__})") from exc

    def exchange_code(self, *, code: str, verifier: str) -> str:
        value = self._json_request(self.TOKEN_URL, data={
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": self.config.callback_url,
            "code_verifier": verifier,
        })
        token = str(value.get("access_token") or "")
        if not token:
            raise OAuthError("GitHub did not return an access token")
        return token

    def fetch_user(self, access_token: str) -> dict[str, Any]:
        value = self._json_request(f"{self.API_URL}/user", token=access_token)
        if not value.get("id") or not value.get("login"):
            raise OAuthError("GitHub identity is missing an id or login")
        return value

    def active_org_member(self, access_token: str, organization: str) -> bool:
        quoted = urllib.parse.quote(organization, safe="")
        try:
            value = self._json_request(
                f"{self.API_URL}/user/memberships/orgs/{quoted}", token=access_token
            )
        except OAuthError:
            return False
        return value.get("state") == "active"
