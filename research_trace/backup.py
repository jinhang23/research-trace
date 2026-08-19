"""Deterministic, verifiable Git-friendly backups for Research Trace v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .storage import SCHEMA_VERSION, Store, ValidationError


FORMAT_VERSION = 2

# 备份树里的每个文件都按字节和 manifest 对校验和。Git 只要做任何转换
# （core.autocrlf 把 LF 换成 CRLF、smudge/clean filter、$Id$ 展开），
# 校验和立刻对不上：verify 报损坏，restore 拿到的是被改写过的 transcript。
# Git for Windows 的默认就是 autocrlf=true，所以这个文件必须跟着导出树走。
GITATTRIBUTES = (
    "# Research Trace backup content is verified byte-for-byte against manifest.json.\n"
    "# Git must not rewrite line endings or run any filter over it.\n"
    "* -text -ident -filter\n"
    "transcripts/*.zlib binary\n"
    "objects/** binary\n"
)
TABLES = (
    "schema_meta",
    "projects",
    "workspace_keys",
    "chapters",
    "nodes",
    "semantic_revisions",
    "comments",
    "code_evidence",
    "attachments",
    "sessions",
    "agents",
    "events",
    "transcript_chunks",
    "ingest_batches",
    "auth_users",
    "device_credentials",
    "purge_audit",
)
RESTORE_ORDER = (
    "schema_meta",
    "projects",
    "workspace_keys",
    "chapters",
    "nodes",
    "semantic_revisions",
    "comments",
    "code_evidence",
    "attachments",
    "sessions",
    "agents",
    "events",
    "transcript_chunks",
    "ingest_batches",
    "auth_users",
    "device_credentials",
    "purge_audit",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError(f"backup path escapes its root: {relative}") from exc
    return candidate


def _write(path: Path, content: bytes) -> None:
    _write_stream(path, [content])


def _write_stream(path: Path, blocks: Any) -> None:
    """流式落盘：整库 JSONL 不进内存，写完再原子替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as stream:
        for block in blocks:
            stream.write(block)
    temporary.replace(path)


def _primary_order(db: sqlite3.Connection, table: str) -> str:
    columns = db.execute(f"PRAGMA table_info({table})").fetchall()
    primary = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
    return ",".join(primary or [row["name"] for row in columns])


@contextmanager
def _reader(store: Store) -> Iterator[sqlite3.Connection]:
    """导出用自己的连接读，不抢 Store._lock。

    以前整个导出都攥着写锁，一份大库导出期间所有 hook 追加全部堵住；WAL 下
    读者本来就不该阻塞写者。BEGIN DEFERRED 保证这次导出看到的是同一个快照。
    """
    db = sqlite3.connect(store.db_path, timeout=60)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN DEFERRED")
        yield db
        db.rollback()
    finally:
        db.close()


def export_backup(store: Store, target: str | Path) -> dict[str, Any]:
    """Export one logical snapshot. Repeated exports without data changes are byte-identical."""
    root = Path(target).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    table_counts: dict[str, int] = {}

    with _reader(store) as db:
        for table in TABLES:
            order = _primary_order(db, table)
            counter = {"rows": 0}

            def lines(table: str = table, order: str = order, counter: dict = counter):
                for original in db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                    row = dict(original)
                    if table == "transcript_chunks":
                        raw = row.pop("compressed_content")
                        # search_text 是解压内容的逐字副本。把它也写进 JSONL，
                        # 等于把"压缩 transcript"重新展开成明文，体积翻几十倍。
                        # restore 时从 .zlib 还原即可。
                        row.pop("search_text", None)
                        rel = f"transcripts/{row['chunk_id']}.zlib"
                        _write(_inside(root, rel), bytes(raw))
                        expected.add(rel)
                        row["compressed_file"] = rel
                    counter["rows"] += 1
                    yield (_json(row) + "\n").encode("utf-8")

            rel = f"tables/{table}.jsonl"
            _write_stream(_inside(root, rel), lines())
            expected.add(rel)
            table_counts[table] = counter["rows"]

        attachment_objects = [
            row["object_path"] for row in db.execute(
                "SELECT object_path FROM attachments WHERE object_path IS NOT NULL "
                "ORDER BY object_path"
            )
        ]
        purge_generation = 0
        row = db.execute("SELECT value FROM schema_meta WHERE key='purge_generation'").fetchone()
        if row:
            try:
                purge_generation = int(row["value"])
            except (TypeError, ValueError):
                purge_generation = 0

    for rel in attachment_objects:
        source = _inside(store.objects_dir, str(rel))
        if not source.is_file():
            raise ValidationError(f"attachment object is missing: {rel}")
        backup_rel = f"objects/{str(rel).replace(chr(92), '/')}"
        destination = _inside(root, backup_rel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or _sha256(destination) != _sha256(source):
            shutil.copyfile(source, destination)
        expected.add(backup_rel)

    _write(_inside(root, ".gitattributes"), GITATTRIBUTES.encode("utf-8"))
    expected.add(".gitattributes")

    old_manifest = root / "manifest.json"
    if old_manifest.is_file():
        try:
            previous = json.loads(old_manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        for rel in (previous.get("files") or {}):
            if rel not in expected:
                stale = _inside(root, rel)
                if stale.is_file():
                    stale.unlink()

    files = {
        rel: {"sha256": _sha256(_inside(root, rel)), "size": _inside(root, rel).stat().st_size}
        for rel in sorted(expected)
    }
    manifest = {
        "format": "research-trace-backup",
        "format_version": FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "purge_generation": purge_generation,
        "tables": table_counts,
        "excluded_ephemeral_tables": ["web_sessions", "device_authorizations"],
        "files": files,
    }
    _write(old_manifest, (_json(manifest) + "\n").encode("utf-8"))
    return manifest


def verify_backup(source: str | Path) -> dict[str, Any]:
    root = Path(source).expanduser().resolve()
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"invalid backup manifest: {exc}") from exc
    if manifest.get("format") != "research-trace-backup":
        raise ValidationError("not a Research Trace v2 backup")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValidationError("unsupported backup format version")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("backup schema version does not match this server")
    for rel, expected in (manifest.get("files") or {}).items():
        path = _inside(root, rel)
        if not path.is_file():
            raise ValidationError(f"backup file is missing: {rel}")
        if path.stat().st_size != expected.get("size") or _sha256(path) != expected.get("sha256"):
            raise ValidationError(f"backup checksum mismatch: {rel}")
    for table, expected_count in (manifest.get("tables") or {}).items():
        if table not in TABLES:
            raise ValidationError(f"unknown backup table: {table}")
        path = _inside(root, f"tables/{table}.jsonl")
        with path.open("rb") as stream:
            count = sum(1 for line in stream if line.strip())
        if count != expected_count:
            raise ValidationError(f"backup row count mismatch: {table}")
    return manifest


def _rows(root: Path, table: str) -> list[dict[str, Any]]:
    """按 b"\\n" 切行，绝不用 str.splitlines()。

    str.splitlines() 还会在 U+2028 / U+2029 / U+0085 / \\x0b / \\x0c 处断开。
    只要哪份 transcript 或 Node 正文里出现这些字符（网页粘贴的文本里很常见），
    一条 JSON 就被劈成两半，restore 直接失败——而 verify 是按字节数 \\n 计数的，
    照样报"备份完好"。这是备份最坏的失败模式：系统说它是好的。
    """
    path = _inside(root, f"tables/{table}.jsonl")
    rows = []
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                rows.append(json.loads(raw.decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValidationError(f"invalid {table}.jsonl line {number}") from exc
    return rows


def _insert(db, table: str, row: dict[str, Any]) -> None:
    columns = list(row)
    placeholders = ",".join("?" for _ in columns)
    db.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
        [row[column] for column in columns],
    )


def restore_backup(source: str | Path, store: Store) -> dict[str, Any]:
    """Restore into an empty, freshly initialized Store and verify every referenced object."""
    root = Path(source).expanduser().resolve()
    manifest = verify_backup(root)
    rows = {table: _rows(root, table) for table in TABLES}
    with store._lock:
        occupied = {
            table: store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES if table != "schema_meta"
        }
    if any(occupied.values()):
        raise ValidationError("restore destination must be an empty Store")

    for row in rows["attachments"]:
        rel = row.get("object_path")
        if rel:
            source_path = _inside(root, f"objects/{str(rel).replace(chr(92), '/')}")
            destination = _inside(store.objects_dir, str(rel))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)

    parent_links: list[tuple[str, str]] = []
    with store.transaction() as db:
        db.execute("DELETE FROM schema_meta")
        for table in RESTORE_ORDER:
            for original in rows[table]:
                row = dict(original)
                if table == "nodes" and row.get("parent_id"):
                    parent_links.append((row["id"], row["parent_id"]))
                    row["parent_id"] = None
                if table == "transcript_chunks":
                    rel = row.pop("compressed_file")
                    blob = _inside(root, rel).read_bytes()
                    row["compressed_content"] = blob
                    # 导出时故意不写明文；搜索列从压缩内容还原，两边永远一致。
                    content = zlib.decompress(blob)
                    if hashlib.sha256(content).hexdigest() != row.get("sha256"):
                        raise ValidationError(f"transcript chunk content does not match its hash: {rel}")
                    row.setdefault("search_text", content.decode("utf-8"))
                _insert(db, table, row)
        for node_id, parent_id in parent_links:
            db.execute("UPDATE nodes SET parent_id=? WHERE id=?", (parent_id, node_id))
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValidationError(f"restored backup violates foreign keys: {len(violations)}")
    return {"restored": True, "tables": manifest["tables"]}


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _unpushed_commits(repo: Path, remote: str, branch: str) -> int | None:
    """本地 HEAD 比"上次真的推上去的位置"多几个 commit。

    push 成功会同时更新 refs/remotes/<remote>/<branch>，push 失败则不会。
    所以不联网也能判断上一轮是不是只 commit 成功、push 失败了。
    远端跟踪引用还不存在（从没推过）时返回 None，按"必须推"处理。
    """
    head = _run_git(repo, "rev-parse", "--verify", "HEAD", check=False)
    if head.returncode != 0:
        return 0
    tracking = f"refs/remotes/{remote}/{branch}"
    known = _run_git(repo, "rev-parse", "--verify", "--quiet", tracking, check=False)
    if known.returncode != 0:
        return None
    counted = _run_git(repo, "rev-list", "--count", f"{tracking}..HEAD", check=False)
    if counted.returncode != 0:
        return None
    try:
        return int(counted.stdout.strip())
    except ValueError:
        return None


def sync_git_backup(
    store: Store,
    repo: str | Path,
    *,
    subdirectory: str = "research-trace-backup",
    remote: str = "origin",
    branch: str = "main",
) -> dict[str, Any]:
    """Export, verify, commit only changed backup content, and push without force.

    push 失败后本地 commit 必须在下一轮被重推。以前"这轮没有新数据就直接返回成功"，
    于是远端永远停在故障那一刻，而 health 把 error 清空、刷新 last_success_at——
    备份的健康指示在说谎，等到需要恢复时才发现远端少了几周。
    """
    repo_path = Path(repo).expanduser().resolve()
    if not (repo_path / ".git").exists():
        raise ValidationError("backup repository must already be a Git checkout")
    target = _inside(repo_path, subdirectory)
    if target == repo_path:
        raise ValidationError("backup subdirectory cannot be the repository root")
    manifest = export_backup(store, target)
    verify_backup(target)
    _run_git(repo_path, "add", "--", subdirectory)
    changed = _run_git(repo_path, "diff", "--cached", "--quiet", "--", subdirectory, check=False)
    if changed.returncode not in (0, 1):
        raise RuntimeError(changed.stderr.strip() or "git diff failed")
    has_new_content = changed.returncode == 1
    if has_new_content:
        _run_git(repo_path, "commit", "-m", "research-trace: update verified backup", "--", subdirectory)
    pending = _unpushed_commits(repo_path, remote, branch)
    if not has_new_content and pending == 0:
        return {"changed": False, "pushed": False, "unpushed_commits": 0, "manifest": manifest}
    _run_git(repo_path, "push", remote, f"HEAD:{branch}")
    return {
        "changed": has_new_content,
        "pushed": True,
        "unpushed_commits": pending,
        "manifest": manifest,
    }


def rewrite_backup_history(
    store: Store,
    repo: str | Path,
    *,
    subdirectory: str = "research-trace-backup",
    remote: str = "origin",
    branch: str = "main",
    confirm: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """紧急 purge 之后重写备份仓库历史（REQUIREMENTS §13）。

    常规备份只追加、不 force-push。但 purge 掉的令牌/敏感路径仍然躺在旧 commit 里，
    只重新导出一次是删不掉的，所以这里显式地把备份分支压成一个全新的根 commit 再
    force-push。这是唯一允许 force 的路径，必须由管理员带着 confirm 和理由调用。
    注意：远端的旧对象要等托管方 GC 后才真正消失，涉及密钥时仍应轮换仓库和密钥。
    """
    if not confirm:
        raise ValidationError("rewriting backup history requires confirm=True")
    if len(str(reason or "").strip()) < 4:
        raise ValidationError("rewriting backup history requires a written reason")
    repo_path = Path(repo).expanduser().resolve()
    if not (repo_path / ".git").exists():
        raise ValidationError("backup repository must already be a Git checkout")
    target = _inside(repo_path, subdirectory)
    if target == repo_path:
        raise ValidationError("backup subdirectory cannot be the repository root")
    if target.exists():
        shutil.rmtree(target)  # 先清空，purge 掉的文件不能靠"覆盖"留在工作树里
    manifest = export_backup(store, target)
    verify_backup(target)
    _run_git(repo_path, "checkout", "--orphan", "research-trace-purge-rebuild")
    _run_git(repo_path, "add", "-A")
    _run_git(
        repo_path, "commit", "-m",
        f"research-trace: rebuild backup after emergency purge ({reason.strip()})",
    )
    _run_git(repo_path, "branch", "-M", branch)
    _run_git(repo_path, "push", "--force", remote, f"HEAD:{branch}")
    return {
        "rewritten": True,
        "forced": True,
        "branch": branch,
        "purge_generation": manifest.get("purge_generation"),
        "manifest": manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Trace v2 deterministic backup")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--data-dir", required=True)
    export.add_argument("--target", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--source", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--source", required=True)
    restore.add_argument("--data-dir", required=True)
    sync = sub.add_parser("sync-git")
    sync.add_argument("--data-dir", required=True)
    sync.add_argument("--repo", required=True)
    sync.add_argument("--subdirectory", default="research-trace-backup")
    sync.add_argument("--remote", default="origin")
    sync.add_argument("--branch", default="main")
    purge = sub.add_parser("purge", help="administrator emergency purge (permanent)")
    purge.add_argument("--data-dir", required=True)
    purge.add_argument("--actor", required=True)
    purge.add_argument("--reason", required=True)
    purge.add_argument("--project-id", action="append", default=[])
    purge.add_argument("--session-id", action="append", default=[])
    purge.add_argument("--node-id", action="append", default=[])
    purge.add_argument("--event-id", action="append", default=[])
    purge.add_argument("--transcript-chunk-id", action="append", default=[])
    rewrite = sub.add_parser("rewrite-history", help="force-rebuild the backup repo after a purge")
    rewrite.add_argument("--data-dir", required=True)
    rewrite.add_argument("--repo", required=True)
    rewrite.add_argument("--subdirectory", default="research-trace-backup")
    rewrite.add_argument("--remote", default="origin")
    rewrite.add_argument("--branch", default="main")
    rewrite.add_argument("--reason", required=True)
    rewrite.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "verify":
        result = verify_backup(args.source)
    else:
        store = Store(args.data_dir)
        try:
            if args.command == "export":
                result = export_backup(store, args.target)
            elif args.command == "restore":
                result = restore_backup(args.source, store)
            elif args.command == "purge":
                result = store.purge(
                    actor_id=args.actor, reason=args.reason,
                    project_ids=args.project_id, session_ids=args.session_id,
                    node_ids=args.node_id, event_ids=args.event_id,
                    transcript_chunk_ids=args.transcript_chunk_id,
                )
            elif args.command == "rewrite-history":
                result = rewrite_backup_history(
                    store, args.repo, subdirectory=args.subdirectory, remote=args.remote,
                    branch=args.branch, confirm=args.confirm, reason=args.reason,
                )
            else:
                result = sync_git_backup(
                    store, args.repo, subdirectory=args.subdirectory,
                    remote=args.remote, branch=args.branch,
                )
        finally:
            store.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
