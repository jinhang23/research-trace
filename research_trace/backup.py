"""Deterministic, verifiable Git-friendly backups for Research Trace v2."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .storage import SCHEMA_VERSION, Store, ValidationError


FORMAT_VERSION = 1
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _primary_order(store: Store, table: str) -> str:
    columns = store._db.execute(f"PRAGMA table_info({table})").fetchall()
    primary = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
    return ",".join(primary or [row["name"] for row in columns])


def _snapshot(store: Store) -> dict[str, list[dict[str, Any]]]:
    with store._lock:
        result: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            order = _primary_order(store, table)
            rows = store._db.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
            result[table] = [dict(row) for row in rows]
        return result


def export_backup(store: Store, target: str | Path) -> dict[str, Any]:
    """Export one logical snapshot. Repeated exports without data changes are byte-identical."""
    root = Path(target).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot(store)
    expected: set[str] = set()
    table_counts: dict[str, int] = {}

    for table, rows in snapshot.items():
        exported: list[dict[str, Any]] = []
        for original in rows:
            row = dict(original)
            if table == "transcript_chunks":
                raw = row.pop("compressed_content")
                rel = f"transcripts/{row['chunk_id']}.zlib"
                _write(_inside(root, rel), bytes(raw))
                expected.add(rel)
                row["compressed_file"] = rel
            exported.append(row)
        content = "".join(_json(row) + "\n" for row in exported).encode("utf-8")
        rel = f"tables/{table}.jsonl"
        _write(_inside(root, rel), content)
        expected.add(rel)
        table_counts[table] = len(rows)

    for row in snapshot["attachments"]:
        rel = row.get("object_path")
        if not rel:
            continue
        source = _inside(store.objects_dir, str(rel))
        if not source.is_file():
            raise ValidationError(f"attachment object is missing: {rel}")
        backup_rel = f"objects/{str(rel).replace(chr(92), '/')}"
        destination = _inside(root, backup_rel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or _sha256(destination) != _sha256(source):
            shutil.copyfile(source, destination)
        expected.add(backup_rel)

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
    path = _inside(root, f"tables/{table}.jsonl")
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
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
                    row["compressed_content"] = _inside(root, rel).read_bytes()
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


def sync_git_backup(
    store: Store,
    repo: str | Path,
    *,
    subdirectory: str = "research-trace-backup",
    remote: str = "origin",
    branch: str = "main",
) -> dict[str, Any]:
    """Export, verify, commit only changed backup content, and push without force."""
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
    if changed.returncode == 0:
        return {"changed": False, "pushed": False, "manifest": manifest}
    if changed.returncode != 1:
        raise RuntimeError(changed.stderr.strip() or "git diff failed")
    _run_git(repo_path, "commit", "-m", "research-trace: update verified backup", "--", subdirectory)
    _run_git(repo_path, "push", remote, f"HEAD:{branch}")
    return {"changed": True, "pushed": True, "manifest": manifest}


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
