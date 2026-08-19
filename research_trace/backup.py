"""Deterministic, verifiable Git-friendly backups for Research Trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .storage import SCHEMA_VERSION, Store, ValidationError


FORMAT_VERSION = 3
# 3 之前是「一棵全量树 + 根 manifest.json」。写入端已经退役，但读取端**永远不退役**：
# 备份的全部意义是「几年后还能读回来」，一次不兼容的升级就把之前所有备份变成废纸。
# 所以 verify/restore 认这里列出的每一个版本，export 只写 FORMAT_VERSION。
SUPPORTED_FORMAT_VERSIONS = (2, 3)
INDEX_NAME = "index.json"
VOLUMES_DIR = "volumes"
BASE_VOLUME = "base"
INDEX_FORMAT = "research-trace-backup"
VOLUME_FORMAT = "research-trace-backup-volume"

# 分卷策略：**先按年，年内再按容量**（REQUIREMENTS §13）。
#
# 按年切是主轴，因为 transcript 与 events 是逐年只增的：一行的 created_at 永远不会变，
# 所以「哪一年」是一个稳定的分区键——去年的卷一旦写定就再也不会被重写，Git 不必每天
# 重新打包几百 MB 的历史，而且哪一年太大时可以整卷搬去别的仓库。仅按容量切做不到这点：
# 中间插入一行会把它后面所有分片的边界全部推移，等于每天重写整棵树。
#
# 年内再按容量切，是因为 GitHub 的限制有两个量级：单文件 50 MiB 警告 / 100 MiB 直接拒绝
# push，单仓库建议 1 GiB、5 GiB 附近开始被拦。按年切只能压住仓库总量的增速，压不住
# 「某一年 events 表本身就 300 MB」这种单文件超限，所以每张表在卷内按字节切成分片。
DEFAULT_PART_BYTES = 32 * 1024 * 1024

# 容量告警阈值。看三样东西，因为它们对应三种不同的失败：
#   * 单文件 —— GitHub 对 >50 MiB 的文件发警告、>100 MiB 直接拒绝 push。留 10% 余量。
#   * 仓库总量 —— GitHub 建议 private repo 保持在 1 GiB 以下，5 GiB 附近开始受限；
#     这里量的是 `git count-objects -v`（含历史），不是工作树，因为撑爆仓库的是历史。
#   * 导出树总量 —— 工作树本身的大小，即使还没 commit 也能提前几天看到趋势。
# 阈值可用环境变量覆盖，因为不同托管方（GitHub Enterprise、自建 Gitea）数字不一样。
FILE_WARN_BYTES = 50 * 1024 * 1024
FILE_CRITICAL_BYTES = 90 * 1024 * 1024
REPO_WARN_BYTES = 1024 * 1024 * 1024
REPO_CRITICAL_BYTES = 4 * 1024 * 1024 * 1024

# 备份树里的每个文件都按字节和 manifest 对校验和。Git 只要做任何转换
# （core.autocrlf 把 LF 换成 CRLF、smudge/clean filter、$Id$ 展开），
# 校验和立刻对不上：verify 报损坏，restore 拿到的是被改写过的 transcript。
# Git for Windows 的默认就是 autocrlf=true，所以这个文件必须跟着导出树走。
# 分卷之后内容都在 volumes/<id>/ 下面，含斜杠的模式是锚定的，必须用 **/ 前缀。
GITATTRIBUTES = (
    "# Research Trace backup content is verified byte-for-byte against its manifest.\n"
    "# Git must not rewrite line endings or run any filter over it.\n"
    "* -text -ident -filter\n"
    "*.zlib binary\n"
    "**/objects/** binary\n"
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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


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


def _volume_name(row: dict[str, Any]) -> str:
    """一行属于哪个卷。只看 created_at 的年份，因为它写下之后永不改变。

    用 updated_at 会让一行在被编辑时从一个卷跳到另一个卷，去年的卷就又活了过来，
    分卷想解决的「旧卷写定不再重打包」立刻失效。没有时间戳的行（schema_meta）
    进 base 卷。
    """
    stamp = str(row.get("created_at") or "")
    year = stamp[:4]
    return year if len(year) == 4 and year.isdigit() else BASE_VOLUME


class _TableParts:
    """把一张表在一个卷里按字节切成 `<table>.NNNN.jsonl` 分片。

    分片只在追加的末尾滚动，所以已经写满的分片在下一次导出里字节不变，Git 只需要
    重传最后一个分片。
    """

    def __init__(self, directory: Path, table: str, budget: int) -> None:
        self._directory = directory
        self._table = table
        self._budget = max(int(budget), 4096)
        self._stream: Any = None
        self._current: Path | None = None
        self._temporary: Path | None = None
        self._written = 0
        self.parts: list[str] = []
        self.rows = 0

    def _open(self) -> None:
        name = f"{self._table}.{len(self.parts) + 1:04d}.jsonl"
        self.parts.append(f"tables/{name}")
        self._directory.mkdir(parents=True, exist_ok=True)
        self._current = self._directory / name
        self._temporary = self._directory / f".{name}.tmp"
        self._stream = self._temporary.open("wb")
        self._written = 0

    def _close_part(self) -> None:
        self._stream.close()
        self._temporary.replace(self._current)
        self._stream = None

    def write(self, line: bytes) -> None:
        if self._stream is None:
            self._open()
        elif self._written and self._written + len(line) > self._budget:
            self._close_part()
            self._open()
        self._stream.write(line)
        self._written += len(line)
        self.rows += 1

    def close(self) -> list[str]:
        if self._stream is not None:
            self._close_part()
        return self.parts


class _Volume:
    def __init__(self, root: Path, name: str) -> None:
        self.name = name
        self.rel = f"{VOLUMES_DIR}/{name}"
        self.root = _inside(root, self.rel)
        self.expected: set[str] = set()
        self.table_files: dict[str, list[str]] = {}
        self.table_counts: dict[str, int] = {}
        self.missing_objects: list[str] = []


def _capacity(
    volume_entries: list[dict[str, Any]],
    root_files: dict[str, dict[str, Any]],
    repository_bytes: int | None = None,
) -> dict[str, Any]:
    """把「离撞墙还有多远」算成一个能直接显示的结构。

    这里不抛异常也不阻止备份：容量到顶时最不该做的事就是停止备份。它只负责让人看见。
    """
    limits = {
        "file_warn": _env_int("TRACE_BACKUP_FILE_WARN_BYTES", FILE_WARN_BYTES),
        "file_critical": _env_int("TRACE_BACKUP_FILE_CRITICAL_BYTES", FILE_CRITICAL_BYTES),
        "repository_warn": _env_int("TRACE_BACKUP_REPO_WARN_BYTES", REPO_WARN_BYTES),
        "repository_critical": _env_int(
            "TRACE_BACKUP_REPO_CRITICAL_BYTES", REPO_CRITICAL_BYTES
        ),
    }
    export_bytes = sum(int(entry.get("bytes") or 0) for entry in volume_entries)
    export_bytes += sum(int(item.get("size") or 0) for item in root_files.values())
    largest_name, largest_bytes = "", 0
    for entry in volume_entries:
        size = int(entry.get("largest_file_bytes") or 0)
        if size > largest_bytes:
            largest_name, largest_bytes = str(entry.get("largest_file") or ""), size
    for rel, item in root_files.items():
        size = int(item.get("size") or 0)
        if size > largest_bytes:
            largest_name, largest_bytes = rel, size

    warnings: list[str] = []
    level = "ok"

    def raise_level(candidate: str) -> None:
        nonlocal level
        if candidate == "critical" or (candidate == "warn" and level == "ok"):
            level = candidate

    if largest_bytes >= limits["file_critical"]:
        raise_level("critical")
        warnings.append(
            f"largest backup file {largest_name} is {largest_bytes} bytes, "
            f"at or above the critical file threshold {limits['file_critical']}"
        )
    elif largest_bytes >= limits["file_warn"]:
        raise_level("warn")
        warnings.append(
            f"largest backup file {largest_name} is {largest_bytes} bytes, "
            f"above the file warning threshold {limits['file_warn']}"
        )
    if repository_bytes is not None:
        if repository_bytes >= limits["repository_critical"]:
            raise_level("critical")
            warnings.append(
                f"backup repository is {repository_bytes} bytes, at or above the critical "
                f"repository threshold {limits['repository_critical']}"
            )
        elif repository_bytes >= limits["repository_warn"]:
            raise_level("warn")
            warnings.append(
                f"backup repository is {repository_bytes} bytes, above the repository "
                f"warning threshold {limits['repository_warn']}"
            )
    elif export_bytes >= limits["repository_critical"]:
        # 没有仓库尺寸（纯 export）时用导出树兜底，否则最大的那个数字没人看得见。
        raise_level("critical")
        warnings.append(f"backup export tree is {export_bytes} bytes")
    elif export_bytes >= limits["repository_warn"]:
        raise_level("warn")
        warnings.append(f"backup export tree is {export_bytes} bytes")

    report = {
        "level": level,
        "export_bytes": export_bytes,
        "largest_file": largest_name,
        "largest_file_bytes": largest_bytes,
        "volumes": len(volume_entries),
        "limits": limits,
        "warnings": warnings,
    }
    if repository_bytes is not None:
        report["repository_bytes"] = repository_bytes
    return report


def _previous_paths(root: Path) -> set[str]:
    """上一次导出写过哪些文件（相对备份根）。stale 清理只删这些，不敢扫整棵树：
    `--target` 被指到一个有别的东西的目录时，扫树式清理等于删用户的文件。"""
    paths: set[str] = set()
    index = _read_json(root / INDEX_NAME)
    if index:
        paths |= set(index.get("files") or {})
        paths.add(INDEX_NAME)
        for entry in index.get("volumes") or []:
            base = str(entry.get("path") or "").strip("/")
            manifest_rel = str(entry.get("manifest") or "")
            if manifest_rel:
                paths.add(manifest_rel)
            volume_manifest = _read_json(root / manifest_rel) if manifest_rel else None
            for rel in (volume_manifest or {}).get("files") or {}:
                paths.add(f"{base}/{rel}" if base else rel)
    legacy = _read_json(root / "manifest.json")
    if legacy and legacy.get("format") in (INDEX_FORMAT, VOLUME_FORMAT):
        paths |= set(legacy.get("files") or {})
        # 从全量单树升级到分卷时根 manifest 必须消失，否则 verify 会挑中它、
        # 按旧格式校验一棵已经被搬空的树。
        paths.add("manifest.json")
    return paths


def _prune_empty_dirs(root: Path) -> None:
    for path in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts), reverse=True,
    ):
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:
            pass


def export_backup(
    store: Store, target: str | Path, *, part_bytes: int | None = None
) -> dict[str, Any]:
    """Export one logical snapshot into per-year volumes.

    Repeated exports without data changes are byte-identical.
    """
    root = Path(target).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    budget = int(part_bytes or _env_int("TRACE_BACKUP_PART_BYTES", DEFAULT_PART_BYTES))
    volumes: dict[str, _Volume] = {}

    def volume(name: str) -> _Volume:
        if name not in volumes:
            volumes[name] = _Volume(root, name)
        return volumes[name]

    volume(BASE_VOLUME)  # base 永远存在：schema_meta 和没有时间戳的行都在里面
    table_totals: dict[str, int] = {}
    attachment_objects: list[tuple[str, str]] = []

    with _reader(store) as db:
        for table in TABLES:
            order = _primary_order(db, table)
            writers: dict[str, _TableParts] = {}
            total = 0
            for original in db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                row = dict(original)
                name = _volume_name(row)
                current = volume(name)
                if table == "transcript_chunks":
                    raw = row.pop("compressed_content")
                    # search_text 是解压内容的逐字副本。把它也写进 JSONL，
                    # 等于把"压缩 transcript"重新展开成明文，体积翻几十倍。
                    # restore 时从 .zlib 还原即可。
                    row.pop("search_text", None)
                    rel = f"transcripts/{row['chunk_id']}.zlib"
                    _write(_inside(current.root, rel), bytes(raw))
                    current.expected.add(rel)
                    row["compressed_file"] = rel
                if table == "attachments" and row.get("object_path"):
                    attachment_objects.append((name, str(row["object_path"])))
                writer = writers.get(name)
                if writer is None:
                    writer = writers[name] = _TableParts(current.root / "tables", table, budget)
                writer.write((_json(row) + "\n").encode("utf-8"))
                total += 1
            for name, writer in writers.items():
                parts = writer.close()
                current = volume(name)
                current.table_files[table] = parts
                current.table_counts[table] = writer.rows
                current.expected.update(parts)
            table_totals[table] = total

        purge_generation = 0
        row = db.execute("SELECT value FROM schema_meta WHERE key='purge_generation'").fetchone()
        if row:
            try:
                purge_generation = int(row["value"])
            except (TypeError, ValueError):
                purge_generation = 0

    missing_objects: list[str] = []
    copied: set[tuple[str, str]] = set()
    for name, object_path in attachment_objects:
        backup_rel = f"objects/{object_path.replace(chr(92), '/')}"
        if (name, backup_rel) in copied:
            continue
        copied.add((name, backup_rel))
        current = volume(name)
        source = _inside(store.objects_dir, object_path)
        if not source.is_file():
            # 一个对象文件不见了就中止整次导出，等于从那天起所有历史都进不了备份，
            # 而且连 manifest 都写不出来——为了一个已经丢了的字节，把还在的全部内容
            # 一起放弃。这里改成登记缺口继续导出：缺什么写在 manifest / index 里。
            current.missing_objects.append(backup_rel)
            missing_objects.append(f"{current.rel}/{backup_rel}")
            continue
        destination = _inside(current.root, backup_rel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or _sha256(destination) != _sha256(source):
            shutil.copyfile(source, destination)
        current.expected.add(backup_rel)

    _write(_inside(root, ".gitattributes"), GITATTRIBUTES.encode("utf-8"))
    root_expected = {".gitattributes"}

    keep = set(root_expected) | {INDEX_NAME}
    for current in volumes.values():
        keep.add(f"{current.rel}/manifest.json")
        keep.update(f"{current.rel}/{rel}" for rel in current.expected)
    for rel in sorted(_previous_paths(root) - keep):
        stale = _inside(root, rel)
        if stale.is_file():
            stale.unlink()

    volume_entries: list[dict[str, Any]] = []
    for name in sorted(volumes):
        current = volumes[name]
        files = {
            rel: {
                "sha256": _sha256(_inside(current.root, rel)),
                "size": _inside(current.root, rel).stat().st_size,
            }
            for rel in sorted(current.expected)
        }
        manifest = {
            "format": VOLUME_FORMAT,
            "format_version": FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "volume": name,
            "purge_generation": purge_generation,
            "tables": current.table_counts,
            "table_files": {
                table: current.table_files[table] for table in sorted(current.table_files)
            },
            "missing_objects": sorted(current.missing_objects),
            "files": files,
        }
        manifest_rel = f"{current.rel}/manifest.json"
        _write(_inside(root, manifest_rel), (_json(manifest) + "\n").encode("utf-8"))
        # manifest.json 不在自己的 files 表里（它没法给自己算校验和），但容量统计必须
        # 算上它：manifest 每个文件一条记录，一个有几十万附件对象的卷，manifest 本身
        # 就能长到上百 MB 而被 GitHub 直接拒绝 push。漏掉它等于在最该报警的那一格里
        # 报"一切正常"。index.json 仍然不计——它内含 export_bytes，自我引用。
        manifest_bytes = _inside(root, manifest_rel).stat().st_size
        largest_name, largest_bytes = manifest_rel, manifest_bytes
        for rel, item in files.items():
            if int(item["size"]) > largest_bytes:
                largest_name, largest_bytes = f"{current.rel}/{rel}", int(item["size"])
        volume_entries.append({
            "volume": name,
            "path": current.rel,
            "manifest": manifest_rel,
            "manifest_sha256": _sha256(_inside(root, manifest_rel)),
            "files": len(files),
            "bytes": sum(int(item["size"]) for item in files.values()) + manifest_bytes,
            "largest_file": largest_name,
            "largest_file_bytes": largest_bytes,
            "tables": current.table_counts,
        })

    _prune_empty_dirs(root)
    root_files = {
        rel: {"sha256": _sha256(_inside(root, rel)), "size": _inside(root, rel).stat().st_size}
        for rel in sorted(root_expected)
    }
    index = {
        "format": INDEX_FORMAT,
        "format_version": FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "purge_generation": purge_generation,
        "tables": table_totals,
        "excluded_ephemeral_tables": ["web_sessions", "device_authorizations"],
        "missing_objects": sorted(missing_objects),
        "files": root_files,
        "volumes": volume_entries,
        # 只放导出树自己算得出的数字。仓库尺寸随每次 push 变化，写进 index 会让
        # index.json 每轮都不一样，从而每轮都产生一个「内容没变」的 commit。
        "capacity": _capacity(volume_entries, root_files),
    }
    _write(_inside(root, INDEX_NAME), (_json(index) + "\n").encode("utf-8"))
    return index


def _verify_tree(root: Path, *, expect_volume: bool = False) -> dict[str, Any]:
    """校验一棵单树：旧格式的全量备份，或新格式里的一个卷。两者的 manifest 同形。"""
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"invalid backup manifest: {exc}") from exc
    if manifest.get("format") not in (INDEX_FORMAT, VOLUME_FORMAT):
        raise ValidationError("not a Research Trace backup")
    if expect_volume and manifest.get("format") != VOLUME_FORMAT:
        raise ValidationError(f"not a backup volume: {root.name}")
    if manifest.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
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
        count = 0
        for rel in _table_files(manifest, table):
            with _inside(root, rel).open("rb") as stream:
                count += sum(1 for line in stream if line.strip())
        if count != expected_count:
            raise ValidationError(f"backup row count mismatch: {table}")
    return manifest


def _table_files(manifest: dict[str, Any], table: str) -> list[str]:
    """一张表在这棵树里对应哪些文件。旧格式没有 table_files，只有单个 jsonl。"""
    listed = (manifest.get("table_files") or {}).get(table)
    if listed is None:
        return [f"tables/{table}.jsonl"]
    return [str(rel) for rel in listed]


def _verify_index(root: Path) -> dict[str, Any]:
    index = _read_json(root / INDEX_NAME)
    if index is None:
        raise ValidationError("invalid backup index")
    if index.get("format") != INDEX_FORMAT:
        raise ValidationError("not a Research Trace backup")
    if index.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        raise ValidationError("unsupported backup format version")
    if index.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("backup schema version does not match this server")
    for rel, expected in (index.get("files") or {}).items():
        path = _inside(root, rel)
        if not path.is_file():
            raise ValidationError(f"backup file is missing: {rel}")
        if path.stat().st_size != expected.get("size") or _sha256(path) != expected.get("sha256"):
            raise ValidationError(f"backup checksum mismatch: {rel}")

    entries = list(index.get("volumes") or [])
    if not entries:
        raise ValidationError("backup index lists no volumes")
    listed = {str(entry.get("volume") or "") for entry in entries}
    present = {
        path.name for path in (root / VOLUMES_DIR).iterdir() if path.is_dir()
    } if (root / VOLUMES_DIR).is_dir() else set()
    # 一个卷被整个删掉、或者多出一个没被索引的卷，都必须响亮地失败。否则 verify
    # 会对着剩下的卷说"备份完好"——备份最坏的失败模式就是系统说它是好的。
    if listed != present:
        missing = sorted(listed - present)
        extra = sorted(present - listed)
        raise ValidationError(
            f"backup volumes do not match the index (missing: {missing}, unlisted: {extra})"
        )

    totals: dict[str, int] = {}
    for entry in entries:
        manifest_rel = str(entry.get("manifest") or "")
        manifest_path = _inside(root, manifest_rel)
        if not manifest_path.is_file():
            raise ValidationError(f"backup volume manifest is missing: {manifest_rel}")
        if _sha256(manifest_path) != entry.get("manifest_sha256"):
            raise ValidationError(f"backup volume manifest was modified: {manifest_rel}")
        manifest = _verify_tree(_inside(root, str(entry.get("path") or "")), expect_volume=True)
        for table, count in (manifest.get("tables") or {}).items():
            totals[table] = totals.get(table, 0) + int(count)
    for table, count in (index.get("tables") or {}).items():
        if totals.get(table, 0) != int(count):
            raise ValidationError(f"backup row count mismatch across volumes: {table}")
    return index


def verify_backup(source: str | Path, *, volume: str | None = None) -> dict[str, Any]:
    """Verify a whole backup, one volume of it, or a legacy single-tree backup.

    `volume="2026"` 只校验那一个卷，因为一份多年的备份可能有几十 GB，而运维通常只想
    确认刚写的那一卷。整体校验额外确认卷的集合与索引一致。
    """
    root = Path(source).expanduser().resolve()
    if volume:
        return _verify_tree(_inside(root, f"{VOLUMES_DIR}/{volume}"), expect_volume=True)
    if (root / INDEX_NAME).is_file():
        return _verify_index(root)
    return _verify_tree(root)


def _volume_trees(root: Path, manifest: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    """把一份备份摊平成 (树根, manifest) 列表。旧格式就是它自己这一棵。"""
    if manifest.get("format") != INDEX_FORMAT or not manifest.get("volumes"):
        return [(root, manifest)]
    trees = []
    for entry in manifest.get("volumes") or []:
        tree = _inside(root, str(entry.get("path") or ""))
        volume_manifest = _read_json(tree / "manifest.json")
        if volume_manifest is None:
            raise ValidationError(f"invalid backup volume manifest: {entry.get('manifest')}")
        trees.append((tree, volume_manifest))
    return trees


def _rows(root: Path, table: str, manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """按 b"\\n" 切行，绝不用 str.splitlines()。

    str.splitlines() 还会在 U+2028 / U+2029 / U+0085 / \\x0b / \\x0c 处断开。
    只要哪份 transcript 或 Node 正文里出现这些字符（网页粘贴的文本里很常见），
    一条 JSON 就被劈成两半，restore 直接失败——而 verify 是按字节数 \\n 计数的，
    照样报"备份完好"。这是备份最坏的失败模式：系统说它是好的。
    """
    rows = []
    for rel in _table_files(manifest or {}, table):
        path = _inside(root, rel)
        if manifest and not path.is_file():
            continue
        with path.open("rb") as stream:
            for number, raw in enumerate(stream, 1):
                if not raw.strip():
                    continue
                try:
                    rows.append(json.loads(raw.decode("utf-8")))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValidationError(f"invalid {rel} line {number}") from exc
    return rows


def _insert(db, table: str, row: dict[str, Any]) -> None:
    columns = list(row)
    placeholders = ",".join("?" for _ in columns)
    db.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
        [row[column] for column in columns],
    )


def restore_backup(source: str | Path, store: Store) -> dict[str, Any]:
    """Restore into an empty, freshly initialized Store and verify every referenced object.

    所有卷先被读进来合成一份整体，再按 RESTORE_ORDER 一次性写入：卷之间因此没有任何
    隐含顺序，索引里卷的排列方式换了也恢复出同一个库。反过来（一卷一卷地恢复）会让
    2027 年的 Node 指向 2026 年的 Chapter 时撞上外键，丢一个卷就全废。
    """
    root = Path(source).expanduser().resolve()
    manifest = verify_backup(root)
    trees = _volume_trees(root, manifest)
    rows: dict[str, list[tuple[Path, dict[str, Any]]]] = {table: [] for table in TABLES}
    for tree, tree_manifest in trees:
        for table in TABLES:
            for row in _rows(tree, table, tree_manifest):
                rows[table].append((tree, row))

    with store._lock:
        occupied = {
            table: store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES if table != "schema_meta"
        }
    if any(occupied.values()):
        raise ValidationError("restore destination must be an empty Store")

    missing_objects: list[str] = []
    for tree, row in rows["attachments"]:
        rel = row.get("object_path")
        if not rel:
            continue
        source_path = _inside(tree, f"objects/{str(rel).replace(chr(92), '/')}")
        if not source_path.is_file():
            # 导出时就已经丢了的对象（manifest 里记着 missing_objects）不能让整次恢复失败：
            # 那个字节早就没了，其余几年的历史还在。
            missing_objects.append(str(rel))
            continue
        destination = _inside(store.objects_dir, str(rel))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)

    parent_links: list[tuple[str, str]] = []
    with store.transaction() as db:
        db.execute("DELETE FROM schema_meta")
        for table in RESTORE_ORDER:
            for tree, original in rows[table]:
                row = dict(original)
                if table == "nodes" and row.get("parent_id"):
                    parent_links.append((row["id"], row["parent_id"]))
                    row["parent_id"] = None
                if table == "transcript_chunks":
                    rel = row.pop("compressed_file")
                    blob = _inside(tree, rel).read_bytes()
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
    return {
        "restored": True,
        "tables": manifest["tables"],
        "volumes": [str(entry.get("volume") or "") for entry in (manifest.get("volumes") or [])],
        "missing_objects": sorted(set(missing_objects)),
    }


def backup_file_paths(source: str | Path) -> set[str]:
    """备份树里应该存在的每一个文件（相对备份根，posix 分隔符）。

    sync 用它回头检查 Git 索引里到底有没有这些文件。
    """
    root = Path(source).expanduser().resolve()
    manifest = _read_json(root / INDEX_NAME)
    if manifest is None:
        manifest = _read_json(root / "manifest.json") or {}
        return set(manifest.get("files") or {}) | ({"manifest.json"} if manifest else set())
    paths = set(manifest.get("files") or {}) | {INDEX_NAME}
    for entry in manifest.get("volumes") or []:
        base = str(entry.get("path") or "").strip("/")
        manifest_rel = str(entry.get("manifest") or "")
        if manifest_rel:
            paths.add(manifest_rel)
        volume_manifest = _read_json(root / manifest_rel) if manifest_rel else None
        for rel in (volume_manifest or {}).get("files") or {}:
            paths.add(f"{base}/{rel}" if base else rel)
    return paths


# https://user:token@host/… 形式的 remote 一旦让 git 失败，命令行会原样进异常字符串，
# 再被 server 塞进 health 的 backup.error 里，于是一个部署令牌公开挂在 /api/health 上
# 直到下一轮备份。所有从这里逃出去的文本都先过这一遍。
_URL_CREDENTIALS = re.compile(r"(?i)(?<=://)[^/@\s]+(?=@)")


def _redact(text: str) -> str:
    return _URL_CREDENTIALS.sub("***", str(text))


class GitCommandError(subprocess.CalledProcessError):
    """CalledProcessError，但命令行与 stderr 都已去掉 URL 里的凭证。"""

    def __str__(self) -> str:
        # stderr 也要带出来一行：server 只把 str(exc) 写进 health，argv 本身说不清
        # 为什么失败（"push 返回 128" 对运维毫无用处）。
        detail = (self.stderr or "").strip().splitlines()
        base = super().__str__()
        return f"{base} {detail[-1]}" if detail else base


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repo), *args]
    completed = subprocess.run(
        command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        raise GitCommandError(
            completed.returncode, [_redact(item) for item in command],
            output=_redact(completed.stdout or ""), stderr=_redact(completed.stderr or ""),
        )
    return completed


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


def _repository_bytes(repo: Path) -> int | None:
    """仓库实际占多少（松散对象 + pack，含全部历史）。撑爆 GitHub 的是历史，不是工作树。"""
    counted = _run_git(repo, "count-objects", "-v", check=False)
    if counted.returncode != 0:
        return None
    total = 0
    seen = False
    for line in counted.stdout.splitlines():
        key, _, value = line.partition(":")
        if key.strip() in ("size", "size-pack"):
            try:
                total += int(value.strip()) * 1024  # git 报的是 KiB
                seen = True
            except ValueError:
                continue
    return total if seen else None


def _assert_staged(repo: Path, subdirectory: str, target: Path) -> None:
    """确认导出的每个文件真的进了 Git 索引。

    `git add -- <dir>` 对被 .gitignore 命中的文件是静默跳过的：退出码 0，sync 照样
    返回 pushed=True，而克隆下来的备份少了整个 objects/ 或全部 *.jsonl。verify 只看
    工作树，看不出这件事。这一步是唯一能把"推上去的那份是不是完整的"问出来的地方。
    """
    prefix = subdirectory.replace("\\", "/").strip("/")
    listed = _run_git(repo, "ls-files", "--cached", "-z", "--", subdirectory, check=False)
    if listed.returncode != 0:
        return
    tracked = {item for item in listed.stdout.split("\0") if item}
    missing = sorted(
        rel for rel in backup_file_paths(target) if f"{prefix}/{rel}" not in tracked
    )
    if missing:
        raise ValidationError(
            f"{len(missing)} backup files were not staged by git (check the repository "
            f".gitignore); first: {', '.join(missing[:3])}"
        )


def sync_git_backup(
    store: Store,
    repo: str | Path,
    *,
    subdirectory: str = "research-trace-backup",
    remote: str = "origin",
    branch: str = "main",
    part_bytes: int | None = None,
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
    index = export_backup(store, target, part_bytes=part_bytes)
    verify_backup(target)
    _run_git(repo_path, "add", "--", subdirectory)
    _assert_staged(repo_path, subdirectory, target)
    changed = _run_git(repo_path, "diff", "--cached", "--quiet", "--", subdirectory, check=False)
    if changed.returncode not in (0, 1):
        raise RuntimeError(_redact(changed.stderr.strip()) or "git diff failed")
    has_new_content = changed.returncode == 1
    if has_new_content:
        _run_git(repo_path, "commit", "-m", "research-trace: update verified backup", "--", subdirectory)
    capacity = _capacity(
        list(index.get("volumes") or []), dict(index.get("files") or {}),
        repository_bytes=_repository_bytes(repo_path),
    )
    pending = _unpushed_commits(repo_path, remote, branch)
    result = {
        "changed": has_new_content,
        "pushed": False,
        "retried_commits": pending or 0,
        "unpushed_commits": pending,
        "capacity": capacity,
        "missing_objects": list(index.get("missing_objects") or []),
        "manifest": index,
    }
    if not has_new_content and pending == 0:
        result["unpushed_commits"] = 0
        return result
    _run_git(repo_path, "push", remote, f"HEAD:{branch}")
    # push 之后重新数一遍：返回 push 之前的积压量会让健康卡片在刚刚补推成功的那一轮
    # 依旧显示"远端落后"，和真的落后长得一模一样。
    result["pushed"] = True
    result["unpushed_commits"] = _unpushed_commits(repo_path, remote, branch)
    return result


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
    index = export_backup(store, target)
    verify_backup(target)
    _run_git(repo_path, "checkout", "--orphan", "research-trace-purge-rebuild")
    _run_git(repo_path, "add", "-A")
    _assert_staged(repo_path, subdirectory, target)
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
        "purge_generation": index.get("purge_generation"),
        "manifest": index,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Trace deterministic backup")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--data-dir", required=True)
    export.add_argument("--target", required=True)
    export.add_argument("--part-bytes", type=int, default=None,
                        help="per-file byte budget inside a volume (default 32 MiB)")
    verify = sub.add_parser("verify")
    verify.add_argument("--source", required=True)
    verify.add_argument("--volume", default=None, help="verify one volume instead of the whole backup")
    restore = sub.add_parser("restore")
    restore.add_argument("--source", required=True)
    restore.add_argument("--data-dir", required=True)
    sync = sub.add_parser("sync-git")
    sync.add_argument("--data-dir", required=True)
    sync.add_argument("--repo", required=True)
    sync.add_argument("--subdirectory", default="research-trace-backup")
    sync.add_argument("--remote", default="origin")
    sync.add_argument("--branch", default="main")
    sync.add_argument("--part-bytes", type=int, default=None)
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
        result = verify_backup(args.source, volume=args.volume)
    else:
        store = Store(args.data_dir)
        try:
            if args.command == "export":
                result = export_backup(store, args.target, part_bytes=args.part_bytes)
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
                    remote=args.remote, branch=args.branch, part_bytes=args.part_bytes,
                )
        finally:
            store.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
