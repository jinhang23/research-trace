"""SQLite-backed central store for Research Trace v2.

The online database is intentionally private to one service process.  Raw events
are append-only; every editable semantic object is versioned before it changes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


SCHEMA_VERSION = 4
REVIEW_STATES = {"unreviewed", "confirmed", "corrected"}
COMMENT_KINDS = {"comment", "confirmation", "correction"}
TARGET_TYPES = {"overview", "chapter", "node"}
ARTIFACT_DIRECTIONS = {"input", "output", "reference"}
ATTRIBUTIONS = {"exact", "reported", "ambiguous", "unknown"}
AUTH_ROLES = {"reader", "member", "admin"}


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    pass


class NotFound(StoreError):
    pass


class ValidationError(StoreError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | bytes | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _expected_version(value: Any) -> int:
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("expect_version must be a positive integer") from exc
    if version < 1:
        raise ValidationError("expect_version must be a positive integer")
    return version


def slugify(value: str, fallback: str = "project") -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "-", value, flags=re.UNICODE).strip("-_")
    return (value or fallback)[:80]


def normalize_workspace_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError("workspace key cannot be empty")
    if text.startswith("git@") and ":" in text:
        host_path = text[4:].split(":", 1)
        text = f"https://{host_path[0]}/{host_path[1]}"
    text = re.sub(r"\.git$", "", text.rstrip("/\\"), flags=re.I)
    if re.match(r"https?://", text, re.I):
        text = text.lower()
    return text


def normalize_user_code(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    if len(compact) != 8:
        raise ValidationError("device user code must contain 8 characters")
    return f"{compact[:4]}-{compact[4:]}"


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _like_escape(value: str) -> str:
    """LIKE 里 % 和 _ 是通配符。搜 "50%" 或 "batch_id" 时用户要的是字面量，
    不转义会让这两个查询退化成"匹配任何东西"，命中越多越像正常结果，最难被发现。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _hit_time(item: dict[str, Any]) -> str:
    return (
        item.get("occurred_at")
        or item.get("captured_at")
        or item.get("created_at")
        or item.get("updated_at")
        or ""
    )


# 每个搜索来源的取数说明。project_column 单列出来是因为 projects 表的项目主键叫 id，
# 其余表叫 project_id。
_SEARCH_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "scope": "node", "layer": "semantic", "table": "nodes",
        "columns": "id,project_id,chapter_id,title,body,occurred_at",
        "where": "(lower(title) LIKE ? ESCAPE '\\' OR lower(body) LIKE ? ESCAPE '\\')",
        "patterns": 2, "project_column": "project_id", "time_column": "occurred_at",
    },
    {
        "scope": "comment", "layer": "semantic", "table": "comments",
        "columns": "id,project_id,target_type,target_id,kind,body,created_at",
        "where": "lower(body) LIKE ? ESCAPE '\\'",
        "patterns": 1, "project_column": "project_id", "time_column": "created_at",
    },
    {
        "scope": "overview", "layer": "semantic", "table": "projects",
        "columns": "id project_id,id,name,overview,updated_at",
        "where": "lower(overview) LIKE ? ESCAPE '\\'",
        "patterns": 1, "project_column": "id", "time_column": "updated_at",
    },
    {
        "scope": "event", "layer": "raw", "table": "events",
        "columns": "event_id id,project_id,session_id,agent_id,event_type,payload_json body,captured_at",
        "where": "lower(payload_json) LIKE ? ESCAPE '\\'",
        "patterns": 1, "project_column": "project_id", "time_column": "captured_at",
    },
    {
        "scope": "transcript", "layer": "raw", "table": "transcript_chunks",
        "columns": "chunk_id id,project_id,session_id,agent_id,search_text body,created_at",
        "where": "lower(search_text) LIKE ? ESCAPE '\\'",
        "patterns": 1, "project_column": "project_id", "time_column": "created_at",
    },
)

SEMANTIC_SEARCH_SCOPES = tuple(s["scope"] for s in _SEARCH_SOURCES if s["layer"] == "semantic")
RAW_SEARCH_SCOPES = tuple(s["scope"] for s in _SEARCH_SOURCES if s["layer"] == "raw")


class SearchResult(list):
    """搜索结果既是 hits 列表（旧调用方原样可用），又带着"还有多少没给你"的说明。

    做成 list 子类而不是 dict，是为了让已经在消费数组的 REST/MCP/网页调用方
    不需要同步改动；想说清截断的调用方读 `.truncated` / `.totals` 或 `as_dict()`。
    """

    def __init__(
        self,
        hits: Sequence[dict[str, Any]],
        *,
        totals: dict[str, int],
        limit: int,
        scope: str,
    ) -> None:
        super().__init__(hits)
        self.totals = dict(totals)
        self.limit = int(limit)
        self.scope = scope

    @property
    def returned(self) -> dict[str, int]:
        counts = {key: 0 for key in self.totals}
        for hit in self:
            counts[hit["scope"]] = counts.get(hit["scope"], 0) + 1
        return counts

    @property
    def total(self) -> int:
        return sum(self.totals.values())

    @property
    def truncated(self) -> bool:
        return self.total > len(self)

    @property
    def omitted(self) -> dict[str, int]:
        returned = self.returned
        return {key: value - returned.get(key, 0) for key, value in self.totals.items()}

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": list(self),
            "scope": self.scope,
            "limit": self.limit,
            "totals": self.totals,
            "returned": self.returned,
            "omitted": self.omitted,
            "total": self.total,
            "truncated": self.truncated,
        }


def _code_signature(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet")
    diff = item.get("diff")
    content_hash = item.get("content_sha256")
    if not content_hash and (snippet or diff):
        content_hash = hashlib.sha256(str(snippet or diff).encode("utf-8")).hexdigest()
    return {
        "repo_url": item.get("repo_url"),
        "commit_hash": item.get("commit_hash"),
        "file_path": str(item.get("file_path") or "").strip(),
        "symbol": item.get("symbol"),
        "start_line": item.get("start_line"),
        "end_line": item.get("end_line"),
        "snippet": snippet,
        "diff": diff,
        "annotation": item.get("annotation"),
        "content_sha256": content_hash,
        "attribution": str(item.get("attribution") or "unknown"),
        "contributor_agent_ids": sorted({str(x) for x in item.get("contributor_agent_ids") or []}),
    }


class Store:
    def __init__(self, data_dir: str | os.PathLike[str], attachment_limit: int = 10 * 1024 * 1024):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir = self.data_dir / "objects"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "trace.sqlite3"
        self.attachment_limit = int(attachment_limit)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except Exception:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def _init_schema(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    overview TEXT NOT NULL DEFAULT '',
                    overview_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_keys (
                    workspace_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL DEFAULT 'unknown',
                    confirmed INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chapters (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    summary_version INTEGER NOT NULL DEFAULT 1,
                    is_inbox INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, slug)
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE RESTRICT,
                    parent_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    review_state TEXT NOT NULL DEFAULT 'unreviewed',
                    occurred_at TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'recorder',
                    idempotency_key TEXT,
                    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS semantic_revisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
                    milestone INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(target_type, target_id, version)
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    anchor_json TEXT NOT NULL DEFAULT '{}',
                    kind TEXT NOT NULL DEFAULT 'comment',
                    body TEXT NOT NULL,
                    author_type TEXT NOT NULL DEFAULT 'human',
                    author_id TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    -- Recorder 的「我读过并已并入」与人的「这条了结了」是两件事。
                    -- 合并成 resolved_at 会让机器一次 curate 就把人的纠正从界面和
                    -- 后续 Recorder 上下文里抹掉（§3.4 / §4）。
                    acknowledged_at TEXT,
                    acknowledged_by TEXT
                );

                CREATE TABLE IF NOT EXISTS code_evidence (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                    repo_url TEXT,
                    commit_hash TEXT,
                    file_path TEXT NOT NULL,
                    symbol TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    snippet TEXT,
                    diff TEXT,
                    annotation TEXT,
                    content_sha256 TEXT,
                    attribution TEXT NOT NULL DEFAULT 'unknown',
                    contributor_agent_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'reference',
                    name TEXT NOT NULL,
                    mime_type TEXT,
                    size INTEGER,
                    sha256 TEXT,
                    object_path TEXT,
                    uri TEXT,
                    machine TEXT,
                    external_path TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    host TEXT,
                    cwd TEXT,
                    parent_session_id TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    parent_agent_id TEXT,
                    agent_type TEXT,
                    name TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    session_id TEXT,
                    agent_id TEXT,
                    event_type TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transcript_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    session_id TEXT,
                    agent_id TEXT,
                    source_path TEXT,
                    start_offset INTEGER,
                    end_offset INTEGER,
                    sha256 TEXT NOT NULL,
                    compressed_content BLOB NOT NULL,
                    search_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ingest_batches (
                    batch_id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    event_count INTEGER NOT NULL,
                    transcript_chunk_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_by TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    github_id INTEGER NOT NULL UNIQUE,
                    login TEXT NOT NULL,
                    display_name TEXT,
                    avatar_url TEXT,
                    role TEXT NOT NULL DEFAULT 'reader' CHECK(role IN ('reader','member','admin')),
                    disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS web_sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS device_credentials (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    expires_at TEXT
                );

                CREATE TABLE IF NOT EXISTS device_authorizations (
                    device_code_hash TEXT PRIMARY KEY,
                    user_code TEXT NOT NULL UNIQUE,
                    device_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved')),
                    user_id TEXT REFERENCES auth_users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS purge_audit (
                    id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    selector_json TEXT NOT NULL,
                    removed_json TEXT NOT NULL,
                    objects_removed INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chapters_project ON chapters(project_id);
                CREATE INDEX IF NOT EXISTS idx_nodes_chapter_time ON nodes(chapter_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_nodes_project_time ON nodes(project_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_comments_target ON comments(target_type, target_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_project_time ON events(project_id, captured_at);
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, captured_at);
                CREATE INDEX IF NOT EXISTS idx_transcript_project ON transcript_chunks(project_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_auth_users_login ON auth_users(login);
                CREATE INDEX IF NOT EXISTS idx_web_sessions_expiry ON web_sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_device_credentials_user ON device_credentials(user_id,revoked_at);
                CREATE INDEX IF NOT EXISTS idx_device_authorizations_expiry ON device_authorizations(expires_at);
                """
            )
            # CREATE TABLE IF NOT EXISTS 对已经存在的表什么也不做，所以新增列必须显式补。
            # 每一条都是可空的追加列，老库补上之后语义与新库一致，不需要数据迁移。
            for table, column, definition in (
                ("comments", "acknowledged_at", "TEXT"),
                ("comments", "acknowledged_by", "TEXT"),
                ("device_credentials", "expires_at", "TEXT"),
                ("ingest_batches", "delivered_by", "TEXT"),
            ):
                self._add_column_locked(table, column, definition)
            self._db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    def _add_column_locked(self, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            return
        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _unique_slug(self, db: sqlite3.Connection, table: str, base: str, project_id: str | None = None) -> str:
        slug = slugify(base)
        candidate = slug
        number = 2
        while True:
            if project_id is None:
                row = db.execute(f"SELECT 1 FROM {table} WHERE slug=?", (candidate,)).fetchone()
            else:
                row = db.execute(
                    f"SELECT 1 FROM {table} WHERE project_id=? AND slug=?",
                    (project_id, candidate),
                ).fetchone()
            if not row:
                return candidate
            candidate = f"{slug}-{number}"
            number += 1

    def create_project(
        self,
        name: str,
        *,
        workspace_keys: Sequence[str] = (),
        project_id: str | None = None,
        overview: str = "",
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValidationError("project name is required")
        timestamp = now_utc()
        project_id = project_id or _id("prj")
        normalized_keys = [normalize_workspace_key(key) for key in workspace_keys]
        with self.transaction() as db:
            for key in normalized_keys:
                existing = db.execute("SELECT project_id FROM workspace_keys WHERE workspace_key=?", (key,)).fetchone()
                if existing:
                    raise Conflict(f"workspace key is already mapped to {existing['project_id']}: {key}")
            slug = self._unique_slug(db, "projects", name)
            db.execute(
                "INSERT INTO projects(id,slug,name,overview,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (project_id, slug, name, str(overview or ""), timestamp, timestamp),
            )
            chapter_id = _id("ch")
            db.execute(
                "INSERT INTO chapters(id,project_id,slug,name,is_inbox,created_at,updated_at) "
                "VALUES(?,?,?,?,1,?,?)",
                (chapter_id, project_id, "inbox", "Inbox", timestamp, timestamp),
            )
            for key in normalized_keys:
                db.execute(
                    "INSERT INTO workspace_keys(workspace_key,project_id,kind,created_at) VALUES(?,?,?,?)",
                    (key, project_id, "explicit", timestamp),
                )
            self._save_revision_locked(
                db, project_id, "overview", project_id, 1,
                {"body": str(overview or "")}, "human", None, [], False,
            )
        return self.get_project(project_id, include_nodes=False)

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM chapters c WHERE c.project_id=p.id) chapter_count, "
                "(SELECT COUNT(*) FROM nodes n WHERE n.project_id=p.id) node_count "
                "FROM projects p ORDER BY lower(p.name), p.id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _project_row(self, db: sqlite3.Connection, project_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM projects WHERE id=? OR slug=?", (project_id, project_id)).fetchone()
        if not row:
            raise NotFound(f"project not found: {project_id}")
        return row

    def get_project(self, project_id: str, *, include_nodes: bool = True) -> dict[str, Any]:
        with self._lock:
            project = self._project_row(self._db, project_id)
            pid = project["id"]
            chapters = self._db.execute(
                "SELECT * FROM chapters WHERE project_id=? ORDER BY is_inbox DESC, lower(name), id",
                (pid,),
            ).fetchall()
            result = dict(project)
            result["workspace_keys"] = [
                dict(row) for row in self._db.execute(
                    "SELECT workspace_key,kind,confirmed FROM workspace_keys WHERE project_id=? ORDER BY workspace_key",
                    (pid,),
                ).fetchall()
            ]
            result["chapters"] = [dict(row) for row in chapters]
            result["comments"] = self._comments_locked(self._db, pid)
            if include_nodes:
                nodes = self._db.execute(
                    "SELECT * FROM nodes WHERE project_id=? ORDER BY occurred_at,id", (pid,)
                ).fetchall()
                result["nodes"] = [self._expand_node_locked(self._db, row) for row in nodes]
            return result

    def context(
        self,
        *,
        project_id: str | None = None,
        workspace_keys: Sequence[str] = (),
        create_if_missing: bool = False,
        project_name: str | None = None,
        recent_limit: int = 20,
    ) -> dict[str, Any]:
        keys = [normalize_workspace_key(key) for key in workspace_keys if str(key).strip()]
        resolved: set[str] = set()
        with self._lock:
            if project_id:
                resolved.add(self._project_row(self._db, project_id)["id"])
            for key in keys:
                row = self._db.execute(
                    "SELECT project_id FROM workspace_keys WHERE workspace_key=?", (key,)
                ).fetchone()
                if row:
                    resolved.add(row["project_id"])
        if len(resolved) > 1:
            raise Conflict("workspace keys resolve to different projects; explicit confirmation is required")
        if not resolved:
            if not create_if_missing:
                return {"matched": False, "workspace_keys": keys, "projects": self.list_projects()}
            created = self.create_project(project_name or "Untitled project", workspace_keys=keys)
            pid = created["id"]
        else:
            pid = next(iter(resolved))
            if keys:
                self.add_workspace_keys(pid, keys)
        detail = self.get_project(pid, include_nodes=False)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM nodes WHERE project_id=? ORDER BY occurred_at DESC,id DESC LIMIT ?",
                (pid, max(1, min(int(recent_limit), 100))),
            ).fetchall()
            detail["recent_nodes"] = [self._expand_node_locked(self._db, row) for row in rows]
            detail["unresolved_corrections"] = [
                item for item in detail["comments"]
                if item["kind"] == "correction" and not item["resolved_at"]
            ]
            cursor = self._db.execute(
                "SELECT COUNT(*) event_count, MAX(captured_at) last_event_at FROM events WHERE project_id=?",
                (pid,),
            ).fetchone()
            detail["raw_cursor"] = dict(cursor)
        return {"matched": True, "project": detail}

    def add_workspace_keys(self, project_id: str, keys: Sequence[str]) -> None:
        normalized = [normalize_workspace_key(key) for key in keys]
        timestamp = now_utc()
        with self.transaction() as db:
            pid = self._project_row(db, project_id)["id"]
            for key in normalized:
                row = db.execute("SELECT project_id FROM workspace_keys WHERE workspace_key=?", (key,)).fetchone()
                if row and row["project_id"] != pid:
                    raise Conflict(f"workspace key belongs to another project: {key}")
                db.execute(
                    "INSERT OR IGNORE INTO workspace_keys(workspace_key,project_id,kind,created_at) VALUES(?,?,?,?)",
                    (key, pid, "observed", timestamp),
                )

    def create_chapter(self, project_id: str, name: str, summary: str = "") -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValidationError("chapter name is required")
        timestamp = now_utc()
        with self.transaction() as db:
            pid = self._project_row(db, project_id)["id"]
            existing = db.execute(
                "SELECT * FROM chapters WHERE project_id=? AND lower(name)=lower(?)", (pid, name)
            ).fetchone()
            if existing:
                return dict(existing)
            chapter_id = _id("ch")
            slug = self._unique_slug(db, "chapters", name, pid)
            db.execute(
                "INSERT INTO chapters(id,project_id,slug,name,summary,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (chapter_id, pid, slug, name, str(summary or ""), timestamp, timestamp),
            )
            self._save_revision_locked(
                db, pid, "chapter", chapter_id, 1,
                {"name": name, "summary": str(summary or "")}, "human", None, [], False,
            )
            return dict(db.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone())

    def _chapter_locked(
        self, db: sqlite3.Connection, project_id: str, chapter_id: str | None, chapter_name: str | None
    ) -> sqlite3.Row:
        if chapter_id:
            row = db.execute(
                "SELECT * FROM chapters WHERE id=? AND project_id=?", (chapter_id, project_id)
            ).fetchone()
            if not row:
                raise NotFound(f"chapter not found: {chapter_id}")
            return row
        if chapter_name:
            row = db.execute(
                "SELECT * FROM chapters WHERE project_id=? AND lower(name)=lower(?)",
                (project_id, chapter_name.strip()),
            ).fetchone()
            if row:
                return row
            raise ValidationError(
                "chapter_name must match an existing human-created Chapter; omit it to use Inbox"
            )
        return db.execute(
            "SELECT * FROM chapters WHERE project_id=? AND is_inbox=1", (project_id,)
        ).fetchone()

    def record_node(
        self,
        project_id: str,
        *,
        idempotency_key: str,
        title: str,
        body: str = "",
        chapter_id: str | None = None,
        chapter_name: str | None = None,
        parent_id: str | None = None,
        labels: Sequence[str] = (),
        occurred_at: str | None = None,
        created_by: str = "recorder",
        review_state: str = "unreviewed",
        source_event_ids: Sequence[str] = (),
        code_evidence: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        if not str(idempotency_key or "").strip():
            raise ValidationError("idempotency_key is required")
        if not str(title or "").strip():
            raise ValidationError("node title is required")
        if review_state not in REVIEW_STATES:
            raise ValidationError(f"invalid review_state: {review_state}")
        # Recorder output is a proposal. Only a human update may confirm or correct it.
        if created_by == "recorder":
            review_state = "unreviewed"
        timestamp = now_utc()
        clean_labels = sorted({str(item).strip() for item in labels if str(item).strip()})
        source_ids = sorted({str(item) for item in source_event_ids if str(item)})
        with self.transaction() as db:
            pid = self._project_row(db, project_id)["id"]
            existing = db.execute(
                "SELECT * FROM nodes WHERE project_id=? AND idempotency_key=?",
                (pid, idempotency_key),
            ).fetchone()
            node_id = existing["id"] if existing else _id("node")
            if existing and not chapter_id and not chapter_name:
                chapter = db.execute("SELECT * FROM chapters WHERE id=?", (existing["chapter_id"],)).fetchone()
            else:
                chapter = self._chapter_locked(db, pid, chapter_id, chapter_name)
            occurred_at = occurred_at or (existing["occurred_at"] if existing else timestamp)
            if parent_id:
                self._assert_parent_locked(db, node_id, pid, chapter["id"], parent_id)
            if existing:
                existing_signature = self._node_snapshot(existing)
                desired_signature = {
                    **existing_signature,
                    "chapter_id": chapter["id"],
                    "parent_id": parent_id,
                    "title": title.strip(),
                    "body": str(body or ""),
                    "labels": clean_labels,
                    "review_state": review_state,
                    "occurred_at": occurred_at,
                    "created_by": created_by,
                    "source_event_ids": source_ids,
                }
                compare_keys = {
                    "chapter_id", "parent_id", "title", "body", "labels", "review_state",
                    "occurred_at", "created_by", "source_event_ids",
                }
                current_codes = [_code_signature(item) for item in self._code_evidence_locked(db, node_id)]
                desired_codes = [_code_signature(item) for item in code_evidence]
                if (
                    all(existing_signature.get(key) == desired_signature.get(key) for key in compare_keys)
                    and current_codes == desired_codes
                ):
                    return self._expand_node_locked(db, existing)
                if created_by == "recorder":
                    latest = db.execute(
                        "SELECT actor_type FROM semantic_revisions "
                        "WHERE target_type='node' AND target_id=? ORDER BY version DESC LIMIT 1",
                        (node_id,),
                    ).fetchone()
                    if latest and latest["actor_type"] != "recorder":
                        raise Conflict(
                            "node has a newer human revision; recorder cannot overwrite it"
                        )
                version = int(existing["version"]) + 1
                db.execute(
                    "UPDATE nodes SET chapter_id=?,parent_id=?,title=?,body=?,labels_json=?,review_state=?,"
                    "occurred_at=?,created_by=?,source_event_ids_json=?,version=?,updated_at=? WHERE id=?",
                    (
                        chapter["id"], parent_id, title.strip(), str(body or ""), _json(clean_labels),
                        review_state, occurred_at, created_by, _json(source_ids), version, timestamp, node_id,
                    ),
                )
                db.execute("DELETE FROM code_evidence WHERE node_id=?", (node_id,))
            else:
                version = 1
                db.execute(
                    "INSERT INTO nodes(id,project_id,chapter_id,parent_id,title,body,labels_json,review_state,"
                    "occurred_at,created_by,idempotency_key,source_event_ids_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        node_id, pid, chapter["id"], parent_id, title.strip(), str(body or ""),
                        _json(clean_labels), review_state, occurred_at, created_by, idempotency_key,
                        _json(source_ids), timestamp, timestamp,
                    ),
                )
            self._insert_code_evidence_locked(db, node_id, code_evidence, timestamp)
            row = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            snapshot = self._node_snapshot(row)
            snapshot["code_evidence"] = self._code_evidence_locked(db, node_id)
            self._save_revision_locked(
                db, pid, "node", node_id, version, snapshot, created_by, None, source_ids, False,
            )
            return self._expand_node_locked(db, row)

    def update_node(
        self,
        node_id: str,
        patch: dict[str, Any],
        *,
        expect_version: int,
        actor_type: str = "human",
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        allowed = {"title", "body", "parent_id", "labels", "review_state", "occurred_at", "chapter_id"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(f"unknown node fields: {', '.join(sorted(unknown))}")
        expected = _expected_version(expect_version)
        with self.transaction() as db:
            current = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not current:
                raise NotFound(f"node not found: {node_id}")
            if int(current["version"]) != expected:
                raise Conflict(f"node version changed: expected {expect_version}, current {current['version']}")
            if actor_type != "human":
                # record_node 早就有这道闸，PATCH 没有——于是任何持有设备凭证的机器
                # 只要给出正确的 expect_version，就能覆盖人刚改过的 Node（§15）。
                # 版本号只防"并发丢更新"，防不住"机器有权改人的定稿"。
                latest = db.execute(
                    "SELECT actor_type FROM semantic_revisions "
                    "WHERE target_type='node' AND target_id=? ORDER BY version DESC LIMIT 1",
                    (node_id,),
                ).fetchone()
                if latest and latest["actor_type"] == "human":
                    raise Conflict(
                        "node has a newer human revision; a machine credential cannot overwrite it"
                    )
            values = dict(current)
            for key, value in patch.items():
                if key == "labels":
                    values["labels_json"] = _json(sorted({str(x).strip() for x in value if str(x).strip()}))
                else:
                    values[key] = value
            if values["review_state"] not in REVIEW_STATES:
                raise ValidationError("invalid review_state")
            if not str(values["title"] or "").strip():
                raise ValidationError("node title is required")
            chapter = db.execute(
                "SELECT * FROM chapters WHERE id=? AND project_id=?", (values["chapter_id"], current["project_id"])
            ).fetchone()
            if not chapter:
                raise ValidationError("chapter must belong to the same project")
            if values.get("parent_id"):
                self._assert_parent_locked(
                    db, node_id, current["project_id"], chapter["id"], values["parent_id"]
                )
            version = int(current["version"]) + 1
            timestamp = now_utc()
            db.execute(
                "UPDATE nodes SET chapter_id=?,parent_id=?,title=?,body=?,labels_json=?,review_state=?,"
                "occurred_at=?,version=?,updated_at=? WHERE id=?",
                (
                    values["chapter_id"], values.get("parent_id"), str(values["title"]).strip(),
                    str(values["body"] or ""), values["labels_json"], values["review_state"],
                    values["occurred_at"], version, timestamp, node_id,
                ),
            )
            row = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            snapshot = self._node_snapshot(row)
            snapshot["code_evidence"] = self._code_evidence_locked(db, node_id)
            self._save_revision_locked(
                db, current["project_id"], "node", node_id, version, snapshot,
                actor_type, actor_id, [], False,
            )
            return self._expand_node_locked(db, row)

    def _assert_parent_locked(
        self,
        db: sqlite3.Connection,
        node_id: str,
        project_id: str,
        chapter_id: str,
        parent_id: str,
    ) -> None:
        parent = db.execute(
            "SELECT project_id,chapter_id FROM nodes WHERE id=?", (parent_id,)
        ).fetchone()
        if not parent or parent["project_id"] != project_id or parent["chapter_id"] != chapter_id:
            raise ValidationError("parent must belong to the same project and chapter")
        if parent_id == node_id:
            raise ValidationError("node cannot parent itself")
        cycle = db.execute(
            "WITH RECURSIVE ancestors(id,parent_id) AS ("
            "SELECT id,parent_id FROM nodes WHERE id=? "
            "UNION ALL SELECT n.id,n.parent_id FROM nodes n JOIN ancestors a ON n.id=a.parent_id"
            ") SELECT 1 FROM ancestors WHERE id=? LIMIT 1",
            (parent_id, node_id),
        ).fetchone()
        if cycle:
            raise ValidationError("parent relationship would create a cycle")

    def _node_snapshot(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["labels"] = _loads(value.pop("labels_json", "[]"), [])
        value["source_event_ids"] = _loads(value.pop("source_event_ids_json", "[]"), [])
        return value

    def _expand_node_locked(self, db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        value = self._node_snapshot(row)
        value["code_evidence"] = self._code_evidence_locked(db, row["id"])
        value["attachments"] = [
            self._expand_attachment(item) for item in db.execute(
                "SELECT * FROM attachments WHERE target_type='node' AND target_id=? ORDER BY created_at,id",
                (row["id"],),
            ).fetchall()
        ]
        value["comments"] = self._comments_locked(db, row["project_id"], "node", row["id"])
        return value

    def _insert_code_evidence_locked(
        self, db: sqlite3.Connection, node_id: str, items: Sequence[dict[str, Any]], timestamp: str
    ) -> None:
        for item in items:
            normalized = _code_signature(item)
            path = normalized["file_path"]
            if not path:
                raise ValidationError("code evidence requires file_path")
            attribution = normalized["attribution"]
            if attribution not in ATTRIBUTIONS:
                raise ValidationError(f"invalid code attribution: {attribution}")
            db.execute(
                "INSERT INTO code_evidence(id,node_id,repo_url,commit_hash,file_path,symbol,start_line,end_line,"
                "snippet,diff,annotation,content_sha256,attribution,contributor_agent_ids_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _id("code"), node_id, normalized["repo_url"], normalized["commit_hash"], path,
                    normalized["symbol"], normalized["start_line"], normalized["end_line"],
                    normalized["snippet"], normalized["diff"], normalized["annotation"],
                    normalized["content_sha256"], attribution,
                    _json(normalized["contributor_agent_ids"]), timestamp,
                ),
            )

    def _code_evidence_locked(self, db: sqlite3.Connection, node_id: str) -> list[dict[str, Any]]:
        rows = db.execute("SELECT * FROM code_evidence WHERE node_id=? ORDER BY created_at,id", (node_id,)).fetchall()
        out = []
        for row in rows:
            value = dict(row)
            value["contributor_agent_ids"] = _loads(value.pop("contributor_agent_ids_json"), [])
            out.append(value)
        return out

    def _save_revision_locked(
        self,
        db: sqlite3.Connection,
        project_id: str,
        target_type: str,
        target_id: str,
        version: int,
        snapshot: dict[str, Any],
        actor_type: str,
        actor_id: str | None,
        source_event_ids: Sequence[str],
        milestone: bool,
    ) -> None:
        db.execute(
            "INSERT INTO semantic_revisions(id,project_id,target_type,target_id,version,snapshot_json,actor_type,"
            "actor_id,source_event_ids_json,milestone,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                _id("rev"), project_id, target_type, target_id, int(version), _json(snapshot), actor_type,
                actor_id, _json(list(source_event_ids)), 1 if milestone else 0, now_utc(),
            ),
        )

    def curate(
        self,
        project_id: str,
        *,
        target_type: str,
        body: str,
        target_id: str | None = None,
        expect_version: int,
        actor_type: str = "recorder",
        actor_id: str | None = None,
        source_event_ids: Sequence[str] = (),
        milestone: bool = False,
        resolve_comment_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        if target_type not in {"overview", "chapter"}:
            raise ValidationError("curation target_type must be overview or chapter")
        expected = _expected_version(expect_version)
        with self.transaction() as db:
            pid = self._project_row(db, project_id)["id"]
            actual_target = pid if target_type == "overview" else str(target_id or "")
            if target_type == "overview":
                current = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
                current_version = int(current["overview_version"])
            else:
                current = db.execute(
                    "SELECT * FROM chapters WHERE id=? AND project_id=?", (actual_target, pid)
                ).fetchone()
                if not current:
                    raise NotFound(f"chapter not found: {actual_target}")
                current_version = int(current["summary_version"])
            if current_version != expected:
                raise Conflict(f"curation version changed: expected {expect_version}, current {current_version}")
            unresolved = db.execute(
                "SELECT id FROM comments WHERE project_id=? AND target_type=? AND target_id=? "
                "AND kind='correction' AND resolved_at IS NULL AND acknowledged_at IS NULL",
                (pid, target_type, actual_target),
            ).fetchall()
            unresolved_ids = {row["id"] for row in unresolved}
            acknowledged = set(resolve_comment_ids)
            if actor_type == "recorder" and unresolved_ids - acknowledged:
                raise Conflict("unresolved human corrections must be acknowledged before recorder curation")
            version = current_version + 1
            timestamp = now_utc()
            if target_type == "overview":
                db.execute(
                    "UPDATE projects SET overview=?,overview_version=?,updated_at=? WHERE id=?",
                    (str(body or ""), version, timestamp, pid),
                )
                snapshot = {"body": str(body or "")}
            else:
                db.execute(
                    "UPDATE chapters SET summary=?,summary_version=?,updated_at=? WHERE id=?",
                    (str(body or ""), version, timestamp, actual_target),
                )
                snapshot = {"name": current["name"], "summary": str(body or "")}
            if acknowledged:
                placeholders = ",".join("?" for _ in acknowledged)
                # 只有真人的 curate 才能了结一条纠正。机器说「我读过了」只记 acknowledged_*：
                # 它足以解开这道闸（不至于每轮都被同一条挡住），但纠正在界面和后续 Recorder
                # 上下文里仍然是未处理的，直到有人在网页上按下 resolve。
                # 旧实现让机器直接写 resolved_at，人的纠正一次 curate 之后就再也看不见了。
                if actor_type == "human":
                    db.execute(
                        f"UPDATE comments SET resolved_at=?,resolved_by=? WHERE id IN ({placeholders}) "
                        "AND project_id=? AND kind='correction'",
                        (timestamp, actor_id or actor_type, *sorted(acknowledged), pid),
                    )
                else:
                    db.execute(
                        f"UPDATE comments SET acknowledged_at=?,acknowledged_by=? "
                        f"WHERE id IN ({placeholders}) AND project_id=? AND kind='correction'",
                        (timestamp, actor_id or actor_type, *sorted(acknowledged), pid),
                    )
            self._save_revision_locked(
                db, pid, target_type, actual_target, version, snapshot, actor_type, actor_id,
                source_event_ids, milestone,
            )
            return {
                "project_id": pid,
                "target_type": target_type,
                "target_id": actual_target,
                "body": str(body or ""),
                "version": version,
            }

    def revisions(self, target_type: str, target_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM semantic_revisions WHERE target_type=? AND target_id=? ORDER BY version DESC",
                (target_type, target_id),
            ).fetchall()
        out = []
        for row in rows:
            value = dict(row)
            value["snapshot"] = _loads(value.pop("snapshot_json"), {})
            value["source_event_ids"] = _loads(value.pop("source_event_ids_json"), [])
            out.append(value)
        return out

    def add_comment(
        self,
        project_id: str,
        *,
        target_type: str,
        target_id: str | None,
        body: str,
        kind: str = "comment",
        anchor: dict[str, Any] | None = None,
        author_type: str = "human",
        author_id: str | None = None,
    ) -> dict[str, Any]:
        if target_type not in TARGET_TYPES:
            raise ValidationError("invalid comment target_type")
        if kind not in COMMENT_KINDS:
            raise ValidationError("invalid comment kind")
        if not str(body or "").strip():
            raise ValidationError("comment body is required")
        timestamp = now_utc()
        with self.transaction() as db:
            pid = self._project_row(db, project_id)["id"]
            actual_target = pid if target_type == "overview" else str(target_id or "")
            if target_type == "chapter":
                exists = db.execute("SELECT 1 FROM chapters WHERE id=? AND project_id=?", (actual_target, pid)).fetchone()
            elif target_type == "node":
                exists = db.execute("SELECT 1 FROM nodes WHERE id=? AND project_id=?", (actual_target, pid)).fetchone()
            else:
                exists = True
            if not exists:
                raise NotFound(f"comment target not found: {actual_target}")
            comment_id = _id("comment")
            db.execute(
                "INSERT INTO comments(id,project_id,target_type,target_id,anchor_json,kind,body,author_type,"
                "author_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    comment_id, pid, target_type, actual_target, _json(anchor or {}), kind,
                    body.strip(), author_type, author_id, timestamp,
                ),
            )
            if target_type == "node" and kind in {"correction", "confirmation"}:
                node = db.execute("SELECT * FROM nodes WHERE id=?", (actual_target,)).fetchone()
                version = int(node["version"]) + 1
                review_state = "corrected" if kind == "correction" else "confirmed"
                db.execute(
                    "UPDATE nodes SET review_state=?,version=?,updated_at=? WHERE id=?",
                    (review_state, version, timestamp, actual_target),
                )
                updated = db.execute("SELECT * FROM nodes WHERE id=?", (actual_target,)).fetchone()
                snapshot = self._node_snapshot(updated)
                snapshot["code_evidence"] = self._code_evidence_locked(db, actual_target)
                self._save_revision_locked(
                    db, pid, "node", actual_target, version, snapshot,
                    author_type, author_id, [], False,
                )
            return self._comment_row(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def resolve_comment(self, comment_id: str, resolved_by: str) -> dict[str, Any]:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
            if not row:
                raise NotFound(f"comment not found: {comment_id}")
            db.execute(
                "UPDATE comments SET resolved_at=?,resolved_by=? WHERE id=?",
                (now_utc(), resolved_by, comment_id),
            )
            return self._comment_row(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def _comment_row(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["anchor"] = _loads(value.pop("anchor_json"), {})
        return value

    def _comments_locked(
        self,
        db: sqlite3.Connection,
        project_id: str,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM comments WHERE project_id=?"
        args: list[Any] = [project_id]
        if target_type:
            sql += " AND target_type=?"
            args.append(target_type)
        if target_id:
            sql += " AND target_id=?"
            args.append(target_id)
        sql += " ORDER BY created_at,id"
        return [self._comment_row(row) for row in db.execute(sql, args).fetchall()]

    def ingest(
        self,
        *,
        batch_id: str,
        project_id: str | None,
        session: dict[str, Any] | None,
        agents: Sequence[dict[str, Any]],
        events: Sequence[dict[str, Any]],
        transcript_chunks: Sequence[dict[str, Any]] = (),
        delivered_by: str | None = None,
    ) -> dict[str, Any]:
        batch_id = str(batch_id or "").strip()
        if not batch_id:
            raise ValidationError("batch_id is required")
        timestamp = now_utc()
        with self.transaction() as db:
            duplicate = db.execute("SELECT * FROM ingest_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if duplicate:
                return {"batch_id": batch_id, "duplicate": True, **dict(duplicate)}
            pid = None
            if project_id:
                pid = self._project_row(db, project_id)["id"]
            session_id = None
            if session:
                session_id = str(session.get("id") or session.get("session_id") or "").strip()
                if not session_id:
                    raise ValidationError("session.id is required")
                db.execute(
                    "INSERT INTO sessions(id,project_id,source,host,cwd,parent_session_id,started_at,ended_at,"
                    "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET project_id=COALESCE(excluded.project_id,sessions.project_id),"
                    "ended_at=COALESCE(excluded.ended_at,sessions.ended_at),metadata_json=excluded.metadata_json,"
                    "updated_at=excluded.updated_at",
                    (
                        session_id, pid, session.get("source") or "unknown", session.get("host"),
                        session.get("cwd"), session.get("parent_session_id"), session.get("started_at"),
                        session.get("ended_at"), _json(session.get("metadata") or {}), timestamp, timestamp,
                    ),
                )
            for agent in agents:
                agent_id = str(agent.get("id") or agent.get("agent_id") or "").strip()
                agent_session = str(agent.get("session_id") or session_id or "").strip()
                if not agent_id or not agent_session:
                    raise ValidationError("agent id and session_id are required")
                if not db.execute("SELECT 1 FROM sessions WHERE id=?", (agent_session,)).fetchone():
                    db.execute(
                        "INSERT INTO sessions(id,project_id,source,created_at,updated_at) VALUES(?,?,?,?,?)",
                        (agent_session, pid, "unknown", timestamp, timestamp),
                    )
                db.execute(
                    "INSERT OR IGNORE INTO agents(id,session_id,parent_agent_id,agent_type,name,metadata_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        agent_id, agent_session, agent.get("parent_agent_id"), agent.get("agent_type"),
                        agent.get("name"), _json(agent.get("metadata") or {}), timestamp,
                    ),
                )
            inserted_events = 0
            duplicate_events = 0
            conflicting_event_ids: list[str] = []
            for event in events:
                event_id = str(event.get("event_id") or "").strip()
                if not event_id:
                    raise ValidationError("every event requires event_id")
                payload = event.get("payload", event)
                cursor = db.execute(
                    "INSERT OR IGNORE INTO events(event_id,batch_id,project_id,session_id,agent_id,event_type,"
                    "captured_at,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        event_id, batch_id, pid, event.get("session_id") or session_id,
                        event.get("agent_id"), event.get("event_type") or event.get("hook_event") or "unknown",
                        event.get("captured_at") or timestamp, _json(payload), timestamp,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_events += 1
                    continue
                # 同一个 event_id 再来一次是 at-least-once 的正常重放，静默去重即可；
                # 但内容变了说明发送端在复用 id，那是数据丢失而不是去重——必须报出来。
                duplicate_events += 1
                stored = db.execute(
                    "SELECT payload_json FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if stored and stored["payload_json"] != _json(payload):
                    conflicting_event_ids.append(event_id)
            inserted_chunks = 0
            duplicate_chunks = 0
            conflicting_chunk_ids: list[str] = []
            for chunk in transcript_chunks:
                content = chunk.get("content", "")
                if not isinstance(content, str):
                    raise ValidationError("transcript chunk content must be text")
                raw = content.encode("utf-8")
                digest = hashlib.sha256(raw).hexdigest()
                chunk_id = str(chunk.get("chunk_id") or f"chunk_{digest}")
                cursor = db.execute(
                    "INSERT OR IGNORE INTO transcript_chunks(chunk_id,batch_id,project_id,session_id,agent_id,"
                    "source_path,start_offset,end_offset,sha256,compressed_content,search_text,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        chunk_id, batch_id, pid, chunk.get("session_id") or session_id, chunk.get("agent_id"),
                        chunk.get("source_path"), chunk.get("start_offset"), chunk.get("end_offset"), digest,
                        sqlite3.Binary(zlib.compress(raw, level=9)), content, timestamp,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_chunks += 1
                else:
                    duplicate_chunks += 1
                    stored = db.execute(
                        "SELECT sha256 FROM transcript_chunks WHERE chunk_id=?", (chunk_id,)
                    ).fetchone()
                    if stored and stored["sha256"] != digest:
                        conflicting_chunk_ids.append(chunk_id)
            db.execute(
                "INSERT INTO ingest_batches"
                "(batch_id,project_id,event_count,transcript_chunk_count,created_at,delivered_by) "
                "VALUES(?,?,?,?,?,?)",
                (batch_id, pid, inserted_events, inserted_chunks, timestamp,
                 str(delivered_by)[:200] if delivered_by else None),
            )
            return {
                "batch_id": batch_id,
                "duplicate": False,
                "project_id": pid,
                "event_count": inserted_events,
                "transcript_chunk_count": inserted_chunks,
                "duplicate_event_count": duplicate_events,
                "duplicate_transcript_chunk_count": duplicate_chunks,
                "conflicting_event_ids": conflicting_event_ids,
                "conflicting_transcript_chunk_ids": conflicting_chunk_ids,
                "created_at": timestamp,
            }

    def attach(
        self,
        project_id: str,
        *,
        target_type: str,
        target_id: str,
        name: str,
        direction: str = "reference",
        mime_type: str | None = None,
        data_base64: str | None = None,
        uri: str | None = None,
        machine: str | None = None,
        external_path: str | None = None,
        size: int | None = None,
        sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if target_type not in TARGET_TYPES:
            raise ValidationError("invalid attachment target_type")
        if direction not in ARTIFACT_DIRECTIONS:
            raise ValidationError("invalid artifact direction")
        if not str(name or "").strip():
            raise ValidationError("attachment name is required")
        raw: bytes | None = None
        object_path = None
        if data_base64 is not None:
            try:
                raw = base64.b64decode(data_base64, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValidationError("invalid base64 attachment") from exc
            if len(raw) > self.attachment_limit:
                raise ValidationError(f"attachment exceeds {self.attachment_limit} bytes")
            sha256 = hashlib.sha256(raw).hexdigest()
            size = len(raw)
            relative = Path(sha256[:2]) / sha256[2:4] / sha256
            destination = self.objects_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                temp = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
                temp.write_bytes(raw)
                os.replace(temp, destination)
            object_path = relative.as_posix()
        elif not any((uri, external_path)):
            raise ValidationError("provide data_base64, uri, or external_path")
        timestamp = now_utc()
        with self.transaction() as db:
            pid = self._project_row(db, project_id)["id"]
            actual_target = pid if target_type == "overview" else target_id
            if target_type == "chapter":
                exists = db.execute("SELECT 1 FROM chapters WHERE id=? AND project_id=?", (actual_target, pid)).fetchone()
            elif target_type == "node":
                exists = db.execute("SELECT 1 FROM nodes WHERE id=? AND project_id=?", (actual_target, pid)).fetchone()
            else:
                exists = True
            if not exists:
                raise NotFound(f"attachment target not found: {actual_target}")
            attachment_id = _id("att")
            db.execute(
                "INSERT INTO attachments(id,project_id,target_type,target_id,direction,name,mime_type,size,sha256,"
                "object_path,uri,machine,external_path,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attachment_id, pid, target_type, actual_target, direction, name.strip(), mime_type, size,
                    sha256, object_path, uri, machine, external_path, _json(metadata or {}), timestamp,
                ),
            )
            return self._expand_attachment(
                db.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            )

    def _expand_attachment(self, row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _loads(value.pop("metadata_json"), {})
        return value

    def attachment_content(self, attachment_id: str) -> tuple[Path, str | None, str]:
        with self._lock:
            row = self._db.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if not row:
                raise NotFound(f"attachment not found: {attachment_id}")
            if not row["object_path"]:
                raise NotFound("attachment is an external reference")
            path = (self.objects_dir / row["object_path"]).resolve()
            if self.objects_dir not in path.parents:
                raise StoreError("invalid object path")
            return path, row["mime_type"], row["name"]

    def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        scope: str = "all",
        limit: int = 50,
    ) -> SearchResult:
        """跨层搜索。语义层有独立配额，原始事件再多也挤不掉它。

        原来的做法是"每个来源各取 limit 条、合并、按时间倒序、截到 limit"。
        原始事件的时间戳总是最新、条数比 Node 高几个数量级，于是几周前的结论
        永远排在几万条 event 后面——接口还什么都不说。现在语义层先拿走一半名额，
        用不完的名额才让给原始层，反之亦然；截断多少条如实记在 totals/omitted 里。
        """
        query = str(query or "").strip()
        if not query:
            raise ValidationError("search query is required")
        if scope not in {"all", "semantic", "raw"}:
            raise ValidationError("scope must be all, semantic, or raw")
        limit = max(1, min(int(limit), 200))
        pattern = f"%{_like_escape(query.lower())}%"
        semantic: list[dict[str, Any]] = []
        raw: list[dict[str, Any]] = []
        totals: dict[str, int] = {}
        with self._lock:
            pid = self._project_row(self._db, project_id)["id"] if project_id else None
            for source in _SEARCH_SOURCES:
                if scope != "all" and source["layer"] != scope:
                    continue
                where = source["where"]
                args: list[Any] = [pattern] * source["patterns"]
                if pid:
                    where += f" AND {source['project_column']}=?"
                    args.append(pid)
                totals[source["scope"]] = self._db.execute(
                    f"SELECT COUNT(*) FROM {source['table']} WHERE {where}", args
                ).fetchone()[0]
                rows = self._db.execute(
                    f"SELECT {source['columns']} FROM {source['table']} WHERE {where} "
                    f"ORDER BY {source['time_column']} DESC LIMIT ?",
                    (*args, limit),
                ).fetchall()
                bucket = semantic if source["layer"] == "semantic" else raw
                bucket.extend({"scope": source["scope"], **dict(row)} for row in rows)

        order = lambda item: (_hit_time(item), str(item.get("id") or ""))  # noqa: E731
        semantic.sort(key=order, reverse=True)
        raw.sort(key=order, reverse=True)

        if not semantic or not raw:
            selected = (semantic or raw)[:limit]
        else:
            semantic_quota = (limit + 1) // 2
            take_semantic = min(len(semantic), semantic_quota)
            take_raw = min(len(raw), limit - semantic_quota)
            spare = limit - take_semantic - take_raw
            if spare > 0:  # 一边没用满的名额让给另一边，总条数不因分区变少
                extra = min(len(semantic) - take_semantic, spare)
                take_semantic += extra
                take_raw += min(len(raw) - take_raw, spare - extra)
            selected = semantic[:take_semantic] + raw[:take_raw]
        selected.sort(key=order, reverse=True)
        return SearchResult(selected, totals=totals, limit=limit, scope=scope)

    def raw_timeline(self, project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return a bounded, newest-first session/agent timeline without inflating full transcripts."""
        limit = max(1, min(int(limit), 500))
        with self._lock:
            pid = self._project_row(self._db, project_id)["id"]
            events = self._db.execute(
                "SELECT event_id id,session_id,agent_id,event_type,captured_at,payload_json "
                "FROM events WHERE project_id=? ORDER BY captured_at DESC,event_id DESC LIMIT ?",
                (pid, limit),
            ).fetchall()
            chunks = self._db.execute(
                "SELECT chunk_id id,session_id,agent_id,source_path,start_offset,end_offset,created_at,search_text "
                "FROM transcript_chunks WHERE project_id=? ORDER BY created_at DESC,chunk_id DESC LIMIT ?",
                (pid, limit),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in events:
            value = dict(row)
            value["kind"] = "event"
            value["payload"] = _loads(value.pop("payload_json"), {})
            value["at"] = value["captured_at"]
            items.append(value)
        for row in chunks:
            value = dict(row)
            content = value.pop("search_text")
            value["kind"] = "transcript"
            value["preview"] = content[:1000]
            value["truncated"] = len(content) > 1000
            value["at"] = value["created_at"]
            items.append(value)
        items.sort(key=lambda item: (item.get("at") or "", item["id"]), reverse=True)
        return items[:limit]

    @staticmethod
    def _auth_user_value(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["disabled"] = bool(value.get("disabled"))
        return value

    def auth_user_by_github_id(self, github_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM auth_users WHERE github_id=?", (int(github_id),)
            ).fetchone()
        return self._auth_user_value(row)

    def upsert_github_user(
        self,
        profile: dict[str, Any],
        *,
        default_role: str,
        force_admin: bool = False,
    ) -> dict[str, Any]:
        """Create/update a GitHub identity without ever persisting its OAuth token."""
        try:
            github_id = int(profile.get("id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("GitHub profile id must be an integer") from exc
        login = str(profile.get("login") or "").strip()
        if github_id < 1 or not login:
            raise ValidationError("GitHub profile requires id and login")
        if default_role not in AUTH_ROLES:
            raise ValidationError("invalid auth role")
        timestamp = now_utc()
        with self.transaction() as db:
            existing = db.execute(
                "SELECT * FROM auth_users WHERE github_id=?", (github_id,)
            ).fetchone()
            if existing:
                role = "admin" if force_admin else existing["role"]
                db.execute(
                    "UPDATE auth_users SET login=?,display_name=?,avatar_url=?,role=?,"
                    "updated_at=?,last_login_at=? WHERE id=?",
                    (
                        login,
                        profile.get("name"),
                        profile.get("avatar_url"),
                        role,
                        timestamp,
                        timestamp,
                        existing["id"],
                    ),
                )
                user_id = existing["id"]
            else:
                user_id = _id("usr")
                role = "admin" if force_admin else default_role
                db.execute(
                    "INSERT INTO auth_users(id,github_id,login,display_name,avatar_url,role,disabled,"
                    "created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,0,?,?,?)",
                    (
                        user_id,
                        github_id,
                        login,
                        profile.get("name"),
                        profile.get("avatar_url"),
                        role,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
            row = db.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        return self._auth_user_value(row) or {}

    def create_web_session(self, user_id: str, raw_session: str, expires_at: str) -> None:
        if not raw_session or len(raw_session) < 32:
            raise ValidationError("web session token is invalid")
        timestamp = now_utc()
        session_hash = hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
        with self.transaction() as db:
            user = db.execute("SELECT disabled FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise NotFound("auth user not found")
            if user["disabled"]:
                raise ValidationError("auth user is disabled")
            db.execute("DELETE FROM web_sessions WHERE expires_at<=?", (timestamp,))
            db.execute(
                "INSERT INTO web_sessions(session_hash,user_id,expires_at,created_at,last_seen_at) "
                "VALUES(?,?,?,?,?)",
                (session_hash, user_id, expires_at, timestamp, timestamp),
            )

    def web_session_user(self, raw_session: str | None) -> dict[str, Any] | None:
        if not raw_session:
            return None
        session_hash = hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
        timestamp = now_utc()
        with self.transaction() as db:
            row = db.execute(
                "SELECT u.*,s.expires_at session_expires_at FROM web_sessions s "
                "JOIN auth_users u ON u.id=s.user_id "
                "WHERE s.session_hash=? AND s.expires_at>? AND u.disabled=0",
                (session_hash, timestamp),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE web_sessions SET last_seen_at=? WHERE session_hash=?",
                    (timestamp, session_hash),
                )
            else:
                db.execute("DELETE FROM web_sessions WHERE session_hash=?", (session_hash,))
        return self._auth_user_value(row)

    def delete_web_session(self, raw_session: str | None) -> None:
        if not raw_session:
            return
        session_hash = hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
        with self.transaction() as db:
            db.execute("DELETE FROM web_sessions WHERE session_hash=?", (session_hash,))

    def start_device_authorization(
        self, device_name: str, *, lifetime_seconds: int = 600
    ) -> dict[str, Any]:
        name = str(device_name or "").strip()[:120]
        if not name or any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise ValidationError("device name is required")
        lifetime = min(max(int(lifetime_seconds), 120), 900)
        timestamp = now_utc()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lifetime)).isoformat(
            timespec="milliseconds"
        )
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw_code = secrets.token_urlsafe(48)
        code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        with self.transaction() as db:
            db.execute("DELETE FROM device_authorizations WHERE expires_at<=?", (timestamp,))
            pending = db.execute("SELECT COUNT(*) FROM device_authorizations").fetchone()[0]
            if pending >= 5000:
                raise ValidationError("too many pending device authorizations; retry later")
            for _attempt in range(20):
                compact = "".join(secrets.choice(alphabet) for _ in range(8))
                user_code = f"{compact[:4]}-{compact[4:]}"
                try:
                    db.execute(
                        "INSERT INTO device_authorizations(device_code_hash,user_code,device_name,"
                        "expires_at,created_at) VALUES(?,?,?,?,?)",
                        (code_hash, user_code, name, expires, timestamp),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:  # pragma: no cover - collision probability is negligible
                raise Conflict("could not allocate a unique device user code")
        return {
            "device_code": raw_code,
            "user_code": user_code,
            "device_name": name,
            "expires_at": expires,
            "expires_in": lifetime,
            "interval": 3,
        }

    def device_authorization(self, user_code: str) -> dict[str, Any] | None:
        normalized = normalize_user_code(user_code)
        timestamp = now_utc()
        with self._lock:
            row = self._db.execute(
                "SELECT user_code,device_name,status,expires_at,created_at,approved_at "
                "FROM device_authorizations WHERE user_code=? AND expires_at>?",
                (normalized, timestamp),
            ).fetchone()
        return _row(row)

    def approve_device_authorization(self, user_code: str, user_id: str) -> dict[str, Any]:
        normalized = normalize_user_code(user_code)
        timestamp = now_utc()
        with self.transaction() as db:
            user = db.execute("SELECT disabled FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if not user or user["disabled"]:
                raise NotFound("active auth user not found")
            row = db.execute(
                "SELECT * FROM device_authorizations WHERE user_code=? AND expires_at>?",
                (normalized, timestamp),
            ).fetchone()
            if not row:
                raise NotFound("device authorization is invalid or expired")
            if row["status"] == "approved" and row["user_id"] != user_id:
                raise Conflict("device authorization was already approved by another user")
            db.execute(
                "UPDATE device_authorizations SET status='approved',user_id=?,approved_at=? "
                "WHERE user_code=?",
                (user_id, timestamp, normalized),
            )
            value = db.execute(
                "SELECT user_code,device_name,status,expires_at,created_at,approved_at "
                "FROM device_authorizations WHERE user_code=?", (normalized,),
            ).fetchone()
        return _row(value) or {}

    def exchange_device_authorization(
        self, raw_device_code: str, *, credential_days: int = 90
    ) -> dict[str, Any]:
        code = str(raw_device_code or "")
        if len(code) < 32:
            return {"status": "invalid"}
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        timestamp = now_utc()
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM device_authorizations WHERE device_code_hash=?", (code_hash,)
            ).fetchone()
            if not row:
                return {"status": "invalid"}
            if row["expires_at"] <= timestamp:
                db.execute("DELETE FROM device_authorizations WHERE device_code_hash=?", (code_hash,))
                return {"status": "expired"}
            if row["status"] != "approved" or not row["user_id"]:
                return {"status": "pending", "interval": 3}
            user = db.execute(
                "SELECT * FROM auth_users WHERE id=? AND disabled=0", (row["user_id"],)
            ).fetchone()
            if not user:
                db.execute("DELETE FROM device_authorizations WHERE device_code_hash=?", (code_hash,))
                return {"status": "denied"}
            raw_credential = "rtd_" + secrets.token_urlsafe(48)
            token_hash = hashlib.sha256(raw_credential.encode("utf-8")).hexdigest()
            device_id = _id("dev")
            # 到期时间在铸造时钉死并落库。之前是服务端每次请求用 created_at + 环境变量
            # 现算的，于是运维把 TRACE_DEVICE_CREDENTIAL_DAYS 调小再调回去，
            # 已经"过期"的凭证会集体复活。
            days = max(1, min(int(credential_days or 90), 3650))
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=days)
            ).isoformat(timespec="milliseconds")
            db.execute(
                "INSERT INTO device_credentials(id,user_id,name,token_hash,created_at,expires_at) "
                "VALUES(?,?,?,?,?,?)",
                (device_id, row["user_id"], row["device_name"], token_hash, timestamp, expires_at),
            )
            db.execute("DELETE FROM device_authorizations WHERE device_code_hash=?", (code_hash,))
        public_user = self._auth_user_value(user) or {}
        return {
            "status": "authorized",
            "credential": raw_credential,
            "device": {
                "id": device_id, "name": row["device_name"], "created_at": timestamp,
                "expires_at": expires_at,
            },
            "expires_at": expires_at,
            "user": {key: public_user.get(key) for key in ("id", "github_id", "login", "role")},
        }

    def device_credential_identity(self, raw_credential: str | None) -> dict[str, Any] | None:
        value = str(raw_credential or "")
        if not value.startswith("rtd_") or len(value) < 40:
            return None
        token_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
        timestamp = now_utc()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="milliseconds")
        with self.transaction() as db:
            row = db.execute(
                "SELECT d.id device_id,d.user_id,d.name device_name,d.created_at device_created_at,"
                "d.last_used_at,d.revoked_at,d.expires_at device_expires_at,u.* "
                "FROM device_credentials d "
                "JOIN auth_users u ON u.id=d.user_id "
                "WHERE d.token_hash=? AND d.revoked_at IS NULL AND u.disabled=0 "
                "AND (d.expires_at IS NULL OR d.expires_at > ?)",
                (token_hash, timestamp),
            ).fetchone()
            if row and (not row["last_used_at"] or row["last_used_at"] < cutoff):
                db.execute(
                    "UPDATE device_credentials SET last_used_at=? WHERE id=?",
                    (timestamp, row["device_id"]),
                )
        if not row:
            return None
        user = {key: row[key] for key in (
            "id", "github_id", "login", "display_name", "avatar_url", "role", "disabled",
            "created_at", "updated_at", "last_login_at",
        )}
        user["disabled"] = bool(user["disabled"])
        return {
            "kind": "device",
            "user": user,
            "device": {
                "id": row["device_id"], "user_id": row["user_id"], "name": row["device_name"],
                "created_at": row["device_created_at"], "last_used_at": row["last_used_at"],
                "expires_at": row["device_expires_at"],
            },
        }

    def list_device_credentials(
        self, *, user_id: str | None = None, include_all: bool = False
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT d.id,d.user_id,d.name,d.created_at,d.last_used_at,d.revoked_at,"
            "d.expires_at,u.login "
            "FROM device_credentials d JOIN auth_users u ON u.id=d.user_id"
        )
        parameters: tuple[Any, ...] = ()
        if not include_all:
            if not user_id:
                raise ValidationError("user id is required")
            query += " WHERE d.user_id=?"
            parameters = (user_id,)
        query += " ORDER BY d.revoked_at IS NOT NULL,d.created_at DESC,d.id"
        with self._lock:
            rows = self._db.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def revoke_device_credential(
        self, device_id: str, *, requester_user_id: str, is_admin: bool = False
    ) -> dict[str, Any]:
        timestamp = now_utc()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM device_credentials WHERE id=?", (device_id,)).fetchone()
            if not row or (row["user_id"] != requester_user_id and not is_admin):
                raise NotFound("device credential not found")
            if not row["revoked_at"]:
                db.execute(
                    "UPDATE device_credentials SET revoked_at=? WHERE id=?", (timestamp, device_id)
                )
            value = db.execute(
                "SELECT id,user_id,name,created_at,last_used_at,revoked_at,expires_at "
                "FROM device_credentials WHERE id=?", (device_id,),
            ).fetchone()
        return _row(value) or {}

    def revoke_user_credentials(self, user_id: str) -> dict[str, Any]:
        """撤掉一个人的全部会话与设备凭证，但不动 `disabled`。

        以前唯一的全撤手段是 update_auth_user(disabled=True)，它同时翻转禁用标志，
        并且对最后一个活跃管理员会抛 Conflict。移出白名单的人在请求时已经被
        server.still_whitelisted 挡住了，但数据库里的行会一直躺到自然过期为止。
        """
        timestamp = now_utc()
        with self.transaction() as db:
            sessions = db.execute(
                "DELETE FROM web_sessions WHERE user_id=?", (str(user_id),)
            ).rowcount
            devices = db.execute(
                "UPDATE device_credentials SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE user_id=? AND revoked_at IS NULL",
                (timestamp, str(user_id)),
            ).rowcount
        return {"user_id": str(user_id), "sessions_removed": max(0, sessions),
                "devices_revoked": max(0, devices)}

    def list_auth_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM auth_users ORDER BY disabled,"
                "CASE role WHEN 'admin' THEN 0 WHEN 'member' THEN 1 ELSE 2 END,lower(login),id"
            ).fetchall()
        return [self._auth_user_value(row) or {} for row in rows]

    def update_auth_user(
        self, user_id: str, *, role: str | None = None, disabled: bool | None = None
    ) -> dict[str, Any]:
        if role is not None and role not in AUTH_ROLES:
            raise ValidationError("role must be reader, member, or admin")
        if role is None and disabled is None:
            raise ValidationError("role or disabled is required")
        timestamp = now_utc()
        with self.transaction() as db:
            current = db.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if not current:
                raise NotFound("auth user not found")
            next_role = role if role is not None else current["role"]
            next_disabled = int(bool(disabled)) if disabled is not None else current["disabled"]
            removes_active_admin = (
                current["role"] == "admin" and not current["disabled"]
                and (next_role != "admin" or next_disabled)
            )
            if removes_active_admin:
                active_admins = db.execute(
                    "SELECT COUNT(*) FROM auth_users WHERE role='admin' AND disabled=0"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise Conflict("cannot disable or demote the last active admin")
            db.execute(
                "UPDATE auth_users SET role=?,disabled=?,updated_at=? WHERE id=?",
                (next_role, next_disabled, timestamp, user_id),
            )
            if next_disabled:
                db.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
                db.execute(
                    "UPDATE device_credentials SET revoked_at=COALESCE(revoked_at,?) WHERE user_id=?",
                    (timestamp, user_id),
                )
            row = db.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        return self._auth_user_value(row) or {}

    def purge_generation(self) -> int:
        """每次紧急 purge +1。备份用它判断"这份导出是在第几次清除之后做的"。"""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM schema_meta WHERE key='purge_generation'"
            ).fetchone()
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError):
            return 0

    def purge_log(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """审计记录只有"谁、为什么、删了哪些 id、各表几行"，永远不含被删内容原文。"""
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM purge_audit ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)
            ).fetchall()
        entries = []
        for row in rows:
            value = dict(row)
            value["selector"] = _loads(value.pop("selector_json"), {})
            value["removed"] = _loads(value.pop("removed_json"), {})
            entries.append(value)
        return entries

    def purge(
        self,
        *,
        actor_id: str,
        reason: str,
        project_ids: Sequence[str] = (),
        session_ids: Sequence[str] = (),
        node_ids: Sequence[str] = (),
        event_ids: Sequence[str] = (),
        transcript_chunk_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """管理员紧急清除（REQUIREMENTS §13）。

        默认永久保存的前提是"进去的东西也能被拿出来"：令牌、密钥、病人数据一旦被
        transcript 抄进来，没有 purge 就永远删不掉。这里做真删除而不是打标记，
        并且连内容寻址的附件文件一起回收；只留下不含原文的审计行。
        备份仓库里的历史副本要另外用 backup.rewrite_backup_history 处理。
        """
        actor_id = str(actor_id or "").strip()
        reason = str(reason or "").strip()
        if not actor_id:
            raise ValidationError("purge requires actor_id")
        if len(reason) < 4:
            raise ValidationError("purge requires a written reason")
        selector = {
            "project_ids": [str(v) for v in project_ids if str(v or "").strip()],
            "session_ids": [str(v) for v in session_ids if str(v or "").strip()],
            "node_ids": [str(v) for v in node_ids if str(v or "").strip()],
            "event_ids": [str(v) for v in event_ids if str(v or "").strip()],
            "transcript_chunk_ids": [str(v) for v in transcript_chunk_ids if str(v or "").strip()],
        }
        if not any(selector.values()):
            raise ValidationError("purge requires at least one selector")

        removed: dict[str, int] = {}
        objects: set[str] = set()
        timestamp = now_utc()

        def marks(values: Sequence[str]) -> str:
            return ",".join("?" for _ in values)

        with self.transaction() as db:
            def drop(table: str, where: str, args: Sequence[Any]) -> None:
                cursor = db.execute(f"DELETE FROM {table} WHERE {where}", list(args))
                if cursor.rowcount > 0:
                    removed[table] = removed.get(table, 0) + cursor.rowcount

            def collect_objects(where: str, args: Sequence[Any]) -> None:
                for row in db.execute(
                    f"SELECT object_path FROM attachments WHERE object_path IS NOT NULL AND {where}",
                    list(args),
                ).fetchall():
                    objects.add(row["object_path"])

            projects = selector["project_ids"]
            nodes = list(selector["node_ids"])
            sessions = list(selector["session_ids"])
            if projects:
                placeholders = marks(projects)
                nodes += [
                    row["id"] for row in db.execute(
                        f"SELECT id FROM nodes WHERE project_id IN ({placeholders})", projects
                    ).fetchall()
                ]
                sessions += [
                    row["id"] for row in db.execute(
                        f"SELECT id FROM sessions WHERE project_id IN ({placeholders})", projects
                    ).fetchall()
                ]
                collect_objects(f"project_id IN ({placeholders})", projects)
            nodes = list(dict.fromkeys(nodes))
            sessions = list(dict.fromkeys(sessions))

            if nodes:
                placeholders = marks(nodes)
                collect_objects(f"target_type='node' AND target_id IN ({placeholders})", nodes)
                drop("code_evidence", f"node_id IN ({placeholders})", nodes)
                drop("attachments", f"target_type='node' AND target_id IN ({placeholders})", nodes)
                drop("comments", f"target_type='node' AND target_id IN ({placeholders})", nodes)
                drop("semantic_revisions", f"target_type='node' AND target_id IN ({placeholders})", nodes)
                # 子节点的 parent_id 由外键 SET NULL 处理，不能因为删父节点连坐子节点。
                drop("nodes", f"id IN ({placeholders})", nodes)
            if projects:
                placeholders = marks(projects)
                drop("attachments", f"project_id IN ({placeholders})", projects)
                drop("comments", f"project_id IN ({placeholders})", projects)
                drop("semantic_revisions", f"project_id IN ({placeholders})", projects)
                drop("chapters", f"project_id IN ({placeholders})", projects)
                drop("workspace_keys", f"project_id IN ({placeholders})", projects)
                drop("events", f"project_id IN ({placeholders})", projects)
                drop("transcript_chunks", f"project_id IN ({placeholders})", projects)
                drop("ingest_batches", f"project_id IN ({placeholders})", projects)
            if sessions:
                placeholders = marks(sessions)
                drop("events", f"session_id IN ({placeholders})", sessions)
                drop("transcript_chunks", f"session_id IN ({placeholders})", sessions)
                drop("agents", f"session_id IN ({placeholders})", sessions)
                drop("sessions", f"id IN ({placeholders})", sessions)
            if selector["event_ids"]:
                drop("events", f"event_id IN ({marks(selector['event_ids'])})", selector["event_ids"])
            if selector["transcript_chunk_ids"]:
                chunks = selector["transcript_chunk_ids"]
                drop("transcript_chunks", f"chunk_id IN ({marks(chunks)})", chunks)
            if projects:
                drop("projects", f"id IN ({marks(projects)})", projects)

            orphaned = []
            for path in sorted(objects):
                still_used = db.execute(
                    "SELECT 1 FROM attachments WHERE object_path=? LIMIT 1", (path,)
                ).fetchone()
                if not still_used:
                    orphaned.append(path)

            generation = 0
            row = db.execute("SELECT value FROM schema_meta WHERE key='purge_generation'").fetchone()
            if row:
                try:
                    generation = int(row["value"])
                except (TypeError, ValueError):
                    generation = 0
            generation += 1
            db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('purge_generation',?)",
                (str(generation),),
            )
            purge_id = _id("purge")
            db.execute(
                "INSERT INTO purge_audit(id,actor_id,reason,selector_json,removed_json,objects_removed,"
                "generation,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    purge_id, actor_id, reason, _json(selector), _json(removed),
                    len(orphaned), generation, timestamp,
                ),
            )

        # 文件在事务提交后才删：事务回滚了还能重放，文件删了就回不来。
        objects_removed = 0
        for path in orphaned:
            target = (self.objects_dir / path).resolve()
            try:
                target.relative_to(self.objects_dir.resolve())
            except ValueError:
                continue
            if target.is_file():
                target.unlink()
                objects_removed += 1
        return {
            "purge_id": purge_id,
            "actor_id": actor_id,
            "reason": reason,
            "selector": selector,
            "removed": removed,
            "objects_removed": objects_removed,
            "purge_generation": generation,
            "created_at": timestamp,
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            counts = {}
            for table in (
                "projects", "chapters", "nodes", "comments", "events", "transcript_chunks",
                "attachments", "auth_users", "device_credentials",
            ):
                counts[table] = self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            last_batch = _row(self._db.execute(
                "SELECT * FROM ingest_batches ORDER BY created_at DESC LIMIT 1"
            ).fetchone())
        purges = self.purge_log(limit=1)
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "data_dir": str(self.data_dir),
            "counts": counts,
            "last_batch": last_batch,
            "purge_generation": self.purge_generation(),
            "last_purge": purges[0] if purges else None,
        }
