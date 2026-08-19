"""GitHub OAuth and browser-session helpers for the v2 service.

OAuth access tokens are deliberately short lived in this process: they are used
only to read the GitHub identity (and optional organization membership) during
the callback.  Research Trace persists only its own opaque, hashed sessions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


SESSION_COOKIE = "trace_session"
OAUTH_NONCE_COOKIE = "trace_oauth_nonce"


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


def login_set(value: str | set[str] | list[str] | tuple[str, ...] | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    items = value.split(",") if isinstance(value, str) else value
    return frozenset(str(item).strip().lower() for item in items if str(item).strip())


@dataclass(frozen=True)
class GitHubOAuthConfig:
    client_id: str
    client_secret: str
    public_url: str
    session_secret: str
    admins: frozenset[str]
    allowed_users: frozenset[str]
    allowed_org: str | None = None
    allow_all: bool = False
    session_days: int = 30
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
        admin_set = login_set(admins)
        user_set = login_set(allowed_users)
        org_value = str(allowed_org or "").strip() or None
        if not (admin_set or user_set or org_value or allow_all):
            raise ValueError(
                "GitHub OAuth needs at least one admin, allowed user, allowed organization, or allow-all"
            )
        if not 1 <= int(session_days) <= 365:
            raise ValueError("GitHub OAuth session days must be between 1 and 365")
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
            secure_cookies=parsed.scheme == "https",
        )

    def permitted_role(self, login: str, *, active_org_member: bool = False) -> str | None:
        normalized = str(login or "").strip().lower()
        if normalized in self.admins:
            return "admin"
        if normalized in self.allowed_users or self.allow_all or active_org_member:
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
