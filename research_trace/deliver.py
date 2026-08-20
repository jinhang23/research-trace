"""Independent Research Trace outbox deliverer and project binding.

投递权威在这里，不在 Recorder。Hook 只负责把事件原子地落进 `pending/`，
这个进程负责把它们送到中央服务，并且**只有中央返回 2xx 才**把文件搬进 `sent/`。
这样正确性就不再依赖「模型是否记得调用工具」「fork 是否成功」「prompt cache 是否命中」
（REQUIREMENTS §6 最后一句），也不再需要任何由 LLM 自己写出来的回执。

它扫的是整个 outbox 根：所有 workspace-hash 目录下的所有 session 目录。被 kill 掉、
或正常退出的 session 留在磁盘上的 `pending/` 因此终于有了重放路径（§15 第 1、2 条）。

同一个文件里还放着「项目绑定」这套东西（marker 的读写与 `trace-project` CLI），
因为 marker 同时是 hook 的采集开关和投递时的项目身份来源，两边必须用同一份解析逻辑，
拆成两个模块只会让它们各自漂移。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from .device_login import (
    default_credential_file,
    load_device_credential,
    normalize_server_url,
)


MARKER_NAME = ".research-trace.json"
MARKER_SCHEMA = "research-trace.project.v1"

# 一次 POST 的上限。transcript chunk 单个最大 512 KiB，事件通常几 KB；
# 这个上限只是为了避免一次把几百 MB 的积压塞进一个请求体。
MAX_BATCH_BYTES = 6 * 1024 * 1024
MAX_BATCH_FILES = 400

# 投递锁的过期时间。一次投递可能要传几百 MB，所以给得比 hook 的 state 锁宽得多；
# 负数 age（系统时钟回拨）同样按过期处理，否则这台机器会永久拒绝投递。
DELIVER_LOCK_STALE = 900.0

_CHUNK_NAME_RE = re.compile(
    r"^(?P<key>[0-9a-f]+)_(?P<start>\d{16})_(?P<end>\d{16})_(?P<digest>[0-9a-f]{8,})\.jsonl$"
)

# 路径形态的 workspace key。§7 的第一句就是「绝对 cwd 不能作为中央项目身份」，
# 而在此之前没有任何一层做过形态校验：`/home/alice/proj` 和 `C:\Users\bob\proj`
# 是两个不同的字符串，于是同一个仓库在两台机器上各自 create 一个中央项目——
# 正是 §7 明令禁止的「静默创建多个重复项目」。
_PATH_SHAPED_KEY_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|~[\\/]|\.{1,2}[\\/]|file://)")


class DeliveryError(RuntimeError):
    """无法继续本轮投递（网络不可达、鉴权失败）。文件全部留在 pending/。"""


# --------------------------------------------------------------------------------------
# 项目 marker：采集是 opt-in 的，没有 marker 的目录一个字节都不写
# --------------------------------------------------------------------------------------


def long_path(path: str | os.PathLike[str]) -> Path:
    """Windows 上给 outbox 根加 `\\\\?\\` 前缀，绕开 260 字符的 MAX_PATH。

    只在这一个地方加：所有 session/pending/sent 路径都是从 outbox 派生出来的，
    前缀会跟着传下去，于是 glob、os.replace、unlink 全都能看见超长路径。
    hook 已经能写这些文件了（scripts/trace_hook.py::_long），投递器如果看不见，
    结果是同一件事：那段历史永远上不去，而且没有任何报错。
    """
    target = Path(path)
    if os.name != "nt":
        return target
    text = str(target)
    if text.startswith("\\\\?\\"):
        return target
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC" + absolute[1:])
    return Path("\\\\?\\" + absolute)


def workspace_key_problem(value: str) -> str | None:
    """workspace key 的形态校验。合格返回 None，否则返回一句可以直接打给人看的原因。

    这一条规则同时被客户端（marker / `trace-project bind`）和中央服务
    （`/api/context`、`/api/projects`）用，所以只能有一份实现。它住在这里而不是
    storage 里，是因为写 marker 的人是它第一个把关的对象，而 marker 的读写就在
    这个文件；服务端 import 它，不反过来（server 依赖 fastapi，客户端不能依赖）。
    """
    text = str(value or "").strip()
    if not text:
        return "cannot be empty"
    if _PATH_SHAPED_KEY_RE.match(text):
        return (
            "looks like a filesystem path; an absolute cwd is machine-specific and cannot be a "
            "project identity (REQUIREMENTS §7). Use the marker's rt-ws-… key or a Git remote URL"
        )
    return None


def marker_path_for(directory: str | os.PathLike[str]) -> Path:
    return Path(directory).expanduser() / MARKER_NAME


def find_marker(start: str | os.PathLike[str] | None = None, *, limit: int = 64) -> Path | None:
    """从 start 向上找 marker。找不到返回 None —— 调用方必须据此完全跳过采集。"""
    try:
        current = Path(start or os.getcwd()).expanduser().resolve()
    except OSError:
        return None
    for _ in range(limit):
        candidate = current / MARKER_NAME
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            return None
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def read_marker(path: str | os.PathLike[str]) -> dict[str, Any]:
    return _read_json(path)


def _marker_keys(value: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    candidates: list[Any] = [value.get("workspace_key")]
    extra = value.get("workspace_keys")
    if isinstance(extra, list):
        candidates.extend(extra)
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in keys:
            keys.append(text)
    return keys


def project_binding(cwd: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    """解析当前目录的项目绑定。

    返回 None 有两种含义，对采集来说是同一件事：这个目录没有被显式绑定过
    （§13 的「项目排除」默认态），或者 marker 里写了 `"capture": false`（显式排除）。
    marker 存在但内容坏掉时仍然按「已绑定」处理：文件在那儿就是用户表达过意愿，
    身份字段可以稍后补，历史不该因为一个 JSON 打错字而丢掉。
    """
    marker = find_marker(cwd)
    if marker is None:
        return None
    value = read_marker(marker)
    if value.get("capture") is False:
        return None
    keys = _marker_keys(value)
    project_id = str(value.get("project_id") or "").strip() or None
    return {
        "marker_path": str(marker),
        "project_dir": str(marker.parent),
        "workspace_keys": keys,
        "workspace_key": keys[0] if keys else None,
        "project_id": project_id,
        "project_name": str(value.get("project_name") or "").strip() or None,
    }


def new_workspace_key() -> str:
    """随机但稳定的 workspace key。

    marker 文件跟着项目目录走（建议提交进仓库），所以随机 key 天然满足 §7：
    不同机器上的不同绝对路径、以及 Git worktree，读到的是同一个 key，
    因此映射到同一个中央 project_id，而绝对 cwd 不参与身份。
    """
    return "rt-ws-" + uuid.uuid4().hex


def git_remote_key(directory: str | os.PathLike[str]) -> str | None:
    """规范化的 Git remote，作为第二个 workspace key（只读操作）。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = (result.stdout or "").strip()
    if result.returncode != 0 or not url:
        return None
    if url.startswith("git@") and ":" in url:
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    url = re.sub(r"\.git$", "", url.rstrip("/\\"), flags=re.I)
    url = url.lower() if re.match(r"https?://", url, re.I) else url
    # `git clone /srv/mirrors/repo` 的 remote 是一条本机路径，它和 cwd 一样是
    # 机器局部的，拿它当第二个 workspace key 只会在每台机器上长出一个新项目。
    return None if workspace_key_problem(url) else url


def write_marker(
    directory: str | os.PathLike[str],
    *,
    workspace_key: str | None = None,
    workspace_keys: Iterable[str] = (),
    project_id: str | None = None,
    project_name: str | None = None,
    capture: bool | None = None,
) -> Path:
    """创建/更新 marker。已有字段只在显式传入时覆盖，绝不丢掉别人写的键。"""
    target = marker_path_for(directory)
    value = read_marker(target)
    value["schema"] = MARKER_SCHEMA
    if workspace_key:
        value["workspace_key"] = workspace_key
    value.setdefault("workspace_key", new_workspace_key())
    merged = [key for key in _marker_keys(value) if key != value["workspace_key"]]
    for key in workspace_keys:
        text = str(key or "").strip()
        if text and text != value["workspace_key"] and text not in merged:
            merged.append(text)
    if merged:
        value["workspace_keys"] = merged
    else:
        value.pop("workspace_keys", None)
    if project_id:
        value["project_id"] = project_id
    if project_name:
        value["project_name"] = project_name
    if capture is not None:
        value["capture"] = bool(capture)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temp, target)
    return target


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


def _post_json(
    url: str, path: str, value: dict[str, Any], token: str, timeout: float
) -> tuple[int, dict[str, Any]]:
    """返回 (status, body)。只有 2xx 算送达；其余都让调用方保留 pending/。"""
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"raw": raw}
            return int(response.status), body if isinstance(body, dict) else {"result": body}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"error": raw or str(exc.reason)}
        return int(exc.code), body if isinstance(body, dict) else {"error": raw}
    except urllib.error.URLError as exc:
        # DNS 挂掉、连接被拒、TLS 失败都在这里。整轮投递中止，内容原样留在 pending/。
        raise DeliveryError(f"Research Trace unavailable at {url}: {exc.reason}") from exc
    except OSError as exc:
        raise DeliveryError(f"Research Trace unavailable at {url}: {exc}") from exc


def auth_token(url: str, token: str = "", credential_file: str | os.PathLike[str] | None = None) -> str:
    if token:
        return token
    target = Path(credential_file).expanduser() if credential_file else default_credential_file()
    try:
        value = load_device_credential(target, url)
    except Exception:
        return ""
    return str(value.get("credential") or "") if value else ""


# --------------------------------------------------------------------------------------
# outbox 扫描与投递
# --------------------------------------------------------------------------------------


def iter_session_dirs(outbox: Path) -> Iterator[Path]:
    """outbox/<workspace-hash>/<session>/ —— 全部，不只是当前活着的那个。"""
    try:
        workspaces = sorted(p for p in outbox.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return
    for workspace in workspaces:
        try:
            sessions = sorted(p for p in workspace.iterdir() if p.is_dir())
        except OSError:
            continue
        for session in sessions:
            yield session


def recover_awaiting_upload(session_dir: Path) -> int:
    """把历史遗留的 `awaiting_upload/` 搬回 `pending/`。

    这个目录是「hook 负责投递、失败就归档」时代的产物，全仓库没有任何代码读它，
    进去的历史永远上不去。现在投递器是唯一权威，所以它必须先把这些捡回来，
    否则老数据永远丢在磁盘上（§12「永不自动删除」不等于「永远送不到」）。
    """
    moved = 0
    for legacy, target, meta in (
        (session_dir / "awaiting_upload", session_dir / "pending", None),
        (
            session_dir / "transcripts" / "awaiting_upload",
            session_dir / "transcripts" / "pending",
            session_dir / "transcripts" / "meta",
        ),
    ):
        if not legacy.is_dir():
            continue
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(legacy.iterdir()):
            if not path.is_file():
                continue
            destination = target / path.name
            if destination.exists():
                continue
            try:
                os.replace(path, destination)
            except OSError:
                continue
            moved += 1
            if meta is not None:
                _ensure_chunk_meta(destination, meta, session_dir.name)
        try:
            legacy.rmdir()
        except OSError:
            pass
    return moved


def _ensure_chunk_meta(chunk: Path, meta_dir: Path, session_id: str) -> dict[str, Any]:
    """meta/ 里没有这份 chunk 的元数据时，从文件名和内容重建。

    归档流程当年会 unlink 掉 meta；文件名里带着 key/start/end/digest，内容 hash 可以现算，
    所以元数据是可重建的，不需要那份被删掉的副本。
    """
    meta_path = meta_dir / f"{chunk.name}.json"
    existing = _read_json(meta_path)
    if existing:
        return existing
    match = _CHUNK_NAME_RE.match(chunk.name)
    try:
        raw = chunk.read_bytes()
    except OSError:
        return {}
    digest = hashlib.sha256(raw).hexdigest()
    value = {
        "path": f"transcripts/pending/{chunk.name}",
        "chunk_id": f"claude-transcript-{digest}",
        "session_id": session_id,
        "agent_id": None,
        "sha256": digest,
        "recovered": True,
    }
    if match:
        value["start_offset"] = int(match.group("start"))
        value["end_offset"] = int(match.group("end"))
    meta_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return value


def _group_files(paths: list[Path]) -> Iterator[list[Path]]:
    group: list[Path] = []
    total = 0
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if group and (total + size > MAX_BATCH_BYTES or len(group) >= MAX_BATCH_FILES):
            yield group
            group, total = [], 0
        group.append(path)
        total += size
    if group:
        yield group


def _load_events(paths: list[Path]) -> tuple[list[dict[str, Any]], list[Path], list[Path]]:
    events: list[dict[str, Any]] = []
    good: list[Path] = []
    bad: list[Path] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bad.append(path)
            continue
        if not isinstance(value, dict) or not value.get("event_id"):
            bad.append(path)
            continue
        events.append(value)
        good.append(path)
    return events, good, bad


def _load_chunks(
    session_dir: Path, paths: list[Path]
) -> tuple[list[dict[str, Any]], list[Path], list[Path]]:
    meta_dir = session_dir / "transcripts" / "meta"
    chunks: list[dict[str, Any]] = []
    good: list[Path] = []
    bad: list[Path] = []
    for path in paths:
        metadata = _ensure_chunk_meta(path, meta_dir, session_dir.name)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            bad.append(path)
            continue
        value = dict(metadata)
        value.pop("path", None)
        value["content"] = content
        chunks.append(value)
        good.append(path)
    return chunks, good, bad


def session_identity(session_dir: Path) -> dict[str, Any]:
    """从任意一个事件文件里取 session/项目身份。

    只有 transcript chunk 的 batch（比如某个 session 只剩下 chunk 没投出去）本身不带
    project_id，不能因此变成无归属的原始历史；身份是 session 级别的，从这里补。
    """
    for directory in ("pending", "sent"):
        try:
            candidates = (session_dir / directory).glob("*.json")
            path = next(iter(candidates), None)
        except OSError:
            path = None
        if path is None:
            continue
        value = _read_json(path)
        if value:
            identity = {
                "session_id": value.get("session_id"),
                "cwd": value.get("project_dir"),
                "project_id": value.get("project_id"),
            }
            # 事件文件里的 project_id 是它被写下那一刻的快照，marker 才是当前真相。
            # §7 明说 `project_id` 要等 workspace key 在中央映射完成之后才写进 marker，
            # 而 hook 从 marker 存在的那一刻起就开始采集：这中间产生的 pending 事件
            # 全都带着 None。按快照投出去，它们会以未归属状态永久留在中央——
            # storage.ingest 只对 sessions 行做 COALESCE 回填，events / transcript_chunks
            # 是 INSERT OR IGNORE，写进去时是 NULL 就永远是 NULL。
            #
            # 只补空，不改写：事件已经带着一个 project_id 时那是当时确实成立的归属，
            # 项目后来被重新绑定到别处不应该追溯性地搬走旧历史。
            if not identity.get("project_id") and identity.get("cwd"):
                binding = project_binding(identity["cwd"])
                if binding and binding.get("project_id"):
                    identity["project_id"] = binding["project_id"]
            return identity
    return {}


def _batch_payload(
    session_dir: Path, events: list[dict[str, Any]], chunks: list[dict[str, Any]],
    names: list[str], identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # batch_id 由内容集合决定：同一批文件重投得到同一个 batch_id，中央的 batch 去重
    # 因此对「已存但响应丢了」这种情况立即命中，不会重复插入。
    digest = hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:24]
    identity = identity or {}
    session_id = identity.get("session_id")
    cwd = identity.get("cwd")
    project_id = identity.get("project_id")
    agents: dict[str, dict[str, Any]] = {}
    for event in events:
        session_id = session_id or event.get("session_id")
        cwd = cwd or event.get("project_dir")
        project_id = project_id or event.get("project_id")
        agent_id = event.get("agent_id")
        if agent_id:
            agents[str(agent_id)] = {
                "id": str(agent_id),
                "session_id": event.get("session_id") or session_id,
                "agent_type": event.get("agent_type"),
            }
    for chunk in chunks:
        session_id = session_id or chunk.get("session_id")
    for agent in agents.values():
        agent["session_id"] = agent.get("session_id") or session_id
    return {
        "batch_id": f"deliver-{digest}",
        "project_id": project_id,
        "session": {
            "id": session_id,
            "source": "claude-code",
            "cwd": cwd,
            "metadata": {"outbox": str(session_dir)},
        } if session_id else None,
        "agents": [agent for agent in agents.values() if agent.get("session_id")],
        "events": events,
        "transcript_chunks": chunks,
    }


def _archive(paths: list[Path], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in paths:
        target = destination / path.name
        try:
            os.replace(path, target)
            # 保留期从「中央确认」这一刻算起，不是从事件产生那一刻。os.replace 保留
            # mtime，所以积压了两个月的 pending 一投出去就会在同一轮被回收掉。
            os.utime(target, None)
        except OSError as exc:
            print(f"research-trace deliver: cannot archive {path}: {exc}", file=sys.stderr)


def deliver_session(
    session_dir: Path, url: str, token: str, timeout: float, report: dict[str, Any]
) -> None:
    recovered = recover_awaiting_upload(session_dir)
    report["recovered"] += recovered

    def listing(directory: Path, suffix: str) -> list[Path]:
        try:
            return sorted(p for p in directory.glob(f"*{suffix}") if p.is_file())
        except OSError:
            return []

    events = listing(session_dir / "pending", ".json")
    chunks = listing(session_dir / "transcripts" / "pending", ".jsonl")
    if not events and not chunks:
        return
    identity = session_identity(session_dir)
    groups: list[tuple[list[Path], list[Path]]] = [
        (group, []) for group in _group_files(events)
    ] + [([], group) for group in _group_files(chunks)]
    for event_paths, chunk_paths in groups:
        loaded_events, good_events, bad_events = _load_events(event_paths)
        loaded_chunks, good_chunks, bad_chunks = _load_chunks(session_dir, chunk_paths)
        report["unreadable"] += len(bad_events) + len(bad_chunks)
        if not loaded_events and not loaded_chunks:
            continue
        names = [p.name for p in good_events] + [p.name for p in good_chunks]
        payload = _batch_payload(session_dir, loaded_events, loaded_chunks, names, identity)
        status, body = _post_json(url, "/api/ingest", payload, token, timeout)
        if status in {401, 403}:
            raise DeliveryError(
                f"Research Trace rejected this device ({status}); run trace-login and retry"
            )
        if status >= 400 and payload.get("project_id"):
            # marker 里的 project_id 可能已经在中央被删除/改名。原始历史不能因此永久卡住，
            # 所以退一步以未归属的形式送达；归属可以事后由人或 Recorder 修。
            payload = dict(payload, project_id=None)
            status, body = _post_json(url, "/api/ingest", payload, token, timeout)
        if not (200 <= status < 300):
            report["failed_batches"] += 1
            report["last_error"] = f"HTTP {status}: {str(body)[:200]}"
            print(
                f"research-trace deliver: {session_dir.name} batch kept in pending/ "
                f"({report['last_error']})",
                file=sys.stderr,
            )
            continue
        _archive(good_events, session_dir / "sent")
        _archive(good_chunks, session_dir / "transcripts" / "sent")
        report["delivered_batches"] += 1
        report["delivered_events"] += len(good_events)
        report["delivered_chunks"] += len(good_chunks)
        # 2xx 不等于「干净地存下了」：中央按 event_id / chunk_id 去重，如果我们
        # 复用了一个 id 但内容不同，第二份会被丢弃并在这里报回来。文件照样进
        # sent/（那一条确实已在中央），但这不是正常重放，必须说出来。
        conflicts = (
            list(body.get("conflicting_event_ids") or [])
            + list(body.get("conflicting_transcript_chunk_ids") or [])
        )
        if conflicts:
            report["conflicts"] += len(conflicts)
            report["last_error"] = (
                f"central kept its stored copy for {len(conflicts)} reused id(s): "
                + ", ".join(str(item) for item in conflicts[:5])
            )
            print(f"research-trace deliver: {report['last_error']}", file=sys.stderr)


class _DeliverLock:
    """整个 outbox 一把锁：多个 session 的 SessionStart 可能同时拉起投递器。"""

    def __init__(self, outbox: Path):
        self.path = outbox / ".deliver-lock"
        self.acquired = False

    def __enter__(self) -> bool:
        try:
            self.path.mkdir(parents=True)
            self.acquired = True
        except FileExistsError:
            if _lock_is_stale(self.path, DELIVER_LOCK_STALE):
                _remove_lock(self.path)
                try:
                    self.path.mkdir(parents=True)
                    self.acquired = True
                except OSError:
                    self.acquired = False
        except OSError:
            self.acquired = False
        if self.acquired:
            try:
                (self.path / "owner").write_text(str(os.getpid()), encoding="utf-8")
            except OSError:
                pass
        return self.acquired

    def __exit__(self, *_exc: Any) -> None:
        if self.acquired:
            _remove_lock(self.path)


def _lock_is_stale(path: Path, stale: float) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    # 时钟回拨会让 age 变成很大的负数；把它也当过期，否则这台机器永远拆不掉这把锁。
    return age > stale or age < -stale


def _remove_lock(path: Path) -> None:
    try:
        for child in path.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        path.rmdir()
    except OSError:
        pass


def reclaim_sent(session_dir: Path, retain_days: float) -> int:
    """按 mtime 回收 `sent/` 与 `transcripts/sent/`（§12 的 30 天保留）。

    `sent/` 是唯一允许回收的目录：里面每一条都已经被中央 2xx 确认存下来了，
    中央才是长期真相源。`pending/` 永远不动——那才是唯一一份还没被确认的副本。
    保留期 <= 0 表示不回收。
    """
    if retain_days <= 0:
        return 0
    cutoff = time.time() - retain_days * 86400
    removed = 0
    for directory in (session_dir / "sent", session_dir / "transcripts" / "sent"):
        if not directory.is_dir():
            continue
        for path in list(directory.iterdir()):
            try:
                if not path.is_file() or path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
            except OSError:
                continue
            removed += 1
            # transcript chunk 的元数据跟着它一起走，否则 meta/ 单调增长
            meta = session_dir / "transcripts" / "meta" / f"{path.name}.json"
            try:
                meta.unlink()
            except OSError:
                pass
    return removed


def outbox_stats(outbox: Path) -> dict[str, Any]:
    """本机 outbox 的计数与最老一条 pending 的时间。给 --status 和上报用。"""
    pending = sent = 0
    oldest: float | None = None
    for session in iter_session_dirs(outbox):
        for directory, suffix in (
            (session / "pending", "*.json"),
            (session / "transcripts" / "pending", "*.jsonl"),
        ):
            try:
                entries = list(directory.glob(suffix))
            except OSError:
                continue
            pending += len(entries)
            for path in entries:
                try:
                    stamp = path.stat().st_mtime
                except OSError:
                    continue
                oldest = stamp if oldest is None else min(oldest, stamp)
        for directory, suffix in (
            (session / "sent", "*.json"), (session / "transcripts" / "sent", "*.jsonl")
        ):
            try:
                sent += len(list(directory.glob(suffix)))
            except OSError:
                pass
    return {
        "pending": pending,
        "sent": sent,
        "oldest_pending_at": (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(oldest)) if oldest else None
        ),
        # 语义层的游标：`batches/` 里还开着的 manifest 就是 Recorder 还没处理的批次
        # （处理完会被归档进 `batches/done`）。§11 要求界面能看见这个数。
        "recorder_pending_batches": recorder_backlog(outbox),
    }


def recorder_backlog(outbox: Path) -> int:
    total = 0
    for session in iter_session_dirs(outbox):
        try:
            total += len([p for p in (session / "batches").glob("*.json") if p.is_file()])
        except OSError:
            continue
    return total


def disk_warning(outbox: Path, free_ratio: float = 0.05, free_bytes: int = 512 * 1024 * 1024):
    """接近磁盘阈值时告警（§12）。**绝不删除未确认内容腾空间。**"""
    try:
        usage = shutil.disk_usage(outbox)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    if usage.free >= free_bytes and usage.free / usage.total >= free_ratio:
        return None
    return (
        f"low disk on {outbox}: {usage.free // (1024 * 1024)} MiB free of "
        f"{usage.total // (1024 * 1024)} MiB. Research Trace will keep every unconfirmed "
        "event in pending/ regardless; free space yourself or lower --retain-sent-days."
    )


def report_outbox_status(
    url: str, token: str, stats: dict[str, Any], report: dict[str, Any], timeout: float
) -> bool:
    """把本机 outbox 健康报给中央（§10 的 outbox 面板）。

    纯遥测：失败不影响投递，也不改变任何文件的去向。
    """
    payload = {
        "machine": socket.gethostname(),
        "pending": stats.get("pending"),
        "sent": stats.get("sent"),
        "oldest_pending_at": stats.get("oldest_pending_at"),
        "last_delivered_at": report.get("finished_at") if report.get("delivered_batches") else None,
        "last_error": report.get("last_error"),
        "recorder_pending_batches": stats.get("recorder_pending_batches"),
    }
    try:
        status, _ = _post_json(url, "/api/telemetry/outbox", payload, token, timeout)
    except DeliveryError:
        return False
    return 200 <= status < 300


def deliver_once(
    data_dir: str | os.PathLike[str],
    url: str,
    *,
    token: str = "",
    credential_file: str | os.PathLike[str] | None = None,
    timeout: float = 60.0,
    retain_sent_days: float = 30.0,
) -> dict[str, Any]:
    outbox = long_path(Path(data_dir).expanduser() / "outbox")
    report: dict[str, Any] = {
        "outbox": str(Path(data_dir).expanduser() / "outbox"), "url": url,
        "sessions": 0, "recovered": 0,
        "delivered_batches": 0, "delivered_events": 0, "delivered_chunks": 0,
        "failed_batches": 0, "unreadable": 0, "conflicts": 0, "reclaimed": 0,
        "last_error": None, "finished_at": None, "skipped": False, "ok": True,
    }
    if not outbox.is_dir():
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return report
    resolved = normalize_server_url(url)
    bearer = auth_token(resolved, token, credential_file)
    with _DeliverLock(outbox) as acquired:
        if not acquired:
            # 被跳过必须**出声**。这里以前只往 JSON 里塞一个 skipped 就返回，
            # 于是人手动跑 `trace-deliver` 会看到 sessions=0 / delivered=0 /
            # last_error=null —— 和「本来就没东西要发」逐字一样，然后据此认为
            # 数据已经传完了。而 hook 在 SessionStart 分离启动的那个投递器
            # 恰好握着锁好几秒，所以这不是罕见路径，是日常路径。
            report["skipped"] = True
            report["last_error"] = (
                "another deliver run holds the outbox lock; nothing was attempted. "
                "This is usually the one the SessionStart hook just launched — "
                "wait a few seconds and run it again."
            )
            report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            report["ok"] = False
            print("research-trace deliver: " + report["last_error"], file=sys.stderr)
            _write_status(outbox, report)
            return report
        try:
            for session_dir in iter_session_dirs(outbox):
                report["sessions"] += 1
                deliver_session(session_dir, resolved, bearer, timeout, report)
        except DeliveryError as exc:
            report["last_error"] = str(exc)
        for session_dir in iter_session_dirs(outbox):
            report["reclaimed"] += reclaim_sent(session_dir, retain_sent_days)
        warning = disk_warning(outbox)
        if warning:
            report["disk_warning"] = warning
            print(f"research-trace deliver: {warning}", file=sys.stderr)
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stats = outbox_stats(outbox)
        report.update(stats)
        report["reported"] = report_outbox_status(resolved, bearer, stats, report, timeout)
        report["ok"] = not (report.get("last_error") or report.get("failed_batches"))
        _write_status(outbox, report)
    return report


def _write_status(outbox: Path, report: dict[str, Any]) -> None:
    """把最近一次投递结果留在 outbox 根上。

    中央有没有收到上报都不影响它：这是本机唯一不依赖网络就能回答
    「我这台机器还有多少没传上去」的地方（§12 的卸载前未同步计数）。
    """
    try:
        value = dict(report)
        value.setdefault("pending_events", value.get("pending", 0))
        temp = outbox / f".delivery-status.{uuid.uuid4().hex}.tmp"
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, outbox / "delivery-status.json")
    except OSError:
        pass


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def default_data_dir() -> str:
    return os.environ.get("TRACE_DATA_DIR") or os.environ.get("CLAUDE_PLUGIN_DATA") or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deliver every staged Research Trace outbox batch to the central service"
    )
    parser.add_argument("--data-dir", default=default_data_dir())
    parser.add_argument("--url", default=os.environ.get("TRACE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("TRACE_TOKEN", ""))
    parser.add_argument("--credential-file", default=os.environ.get("TRACE_CREDENTIAL_FILE"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--watch", action="store_true", help="keep running and retry on an interval")
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument(
        "--retain-sent-days", type=float,
        default=float(os.environ.get("TRACE_RETAIN_SENT_DAYS", "30")),
        help="delete central-confirmed sent/ files older than this; 0 keeps them forever",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="print how much this machine has not delivered yet and exit (no network)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if not args.data_dir:
        print(
            "no outbox: pass --data-dir or set TRACE_DATA_DIR/CLAUDE_PLUGIN_DATA", file=sys.stderr
        )
        return 2
    if args.status:
        # 卸载前 / 出门前的那个问题：「还有多少没传上去」。不联网，所以中央挂着
        # 也答得出来（§12）。
        plain = Path(args.data_dir).expanduser() / "outbox"
        outbox = long_path(plain)
        value = outbox_stats(outbox) if outbox.is_dir() else {"pending": 0, "sent": 0}
        value["outbox"] = str(plain)  # 长路径前缀是给系统调用看的，不是给人看的
        value["last_delivery"] = _read_json(outbox / "delivery-status.json") or None
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 1 if value.get("pending") else 0
    outbox_root = long_path(Path(args.data_dir).expanduser() / "outbox")
    while True:
        try:
            report = deliver_once(
                args.data_dir, args.url, token=args.token,
                credential_file=args.credential_file, timeout=args.timeout,
                retain_sent_days=args.retain_sent_days,
            )
        except Exception as exc:  # 投递器自身崩溃不能变成用户可见的失败循环
            # 但它必须留下痕迹。hook 拉起的那个进程 stdout/stderr 都是 DEVNULL 且带
            # --quiet，所以「投递从来没成功过」和「投递一启动就死」在 --status 里
            # 长得一模一样（last_delivery 都是 null）。排查会因此卡住很久。
            report = {
                "ok": False, "last_error": str(exc), "failed_batches": 1, "url": args.url,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            print(f"research-trace deliver failed: {exc}", file=sys.stderr)
            _write_status(outbox_root, report)
        if not args.quiet:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.watch:
            return 1 if report.get("last_error") or report.get("failed_batches") else 0
        time.sleep(max(5.0, float(args.interval)))


def _resolve_project(
    url: str, keys: list[str], name: str | None, create: bool, token: str,
    credential_file: str | os.PathLike[str] | None, timeout: float,
) -> dict[str, Any]:
    bearer = auth_token(url, token, credential_file)
    status, body = _post_json(
        url, "/api/context",
        {"workspace_keys": keys, "create_if_missing": create, "project_name": name},
        bearer, timeout,
    )
    if not (200 <= status < 300):
        raise DeliveryError(f"HTTP {status}: {str(body)[:300]}")
    return body


def project_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bind a directory to a Research Trace project. Capture is opt-in: "
        "without a marker the Claude Code hooks record nothing at all."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bind = sub.add_parser("bind", help="create or update the project marker in a directory")
    bind.add_argument("path", nargs="?", default=".")
    bind.add_argument("--url", default=os.environ.get("TRACE_URL", "http://127.0.0.1:8765"))
    bind.add_argument("--token", default=os.environ.get("TRACE_TOKEN", ""))
    bind.add_argument("--credential-file", default=os.environ.get("TRACE_CREDENTIAL_FILE"))
    bind.add_argument("--project-id", default="")
    bind.add_argument("--name", default="")
    bind.add_argument("--workspace-key", default="")
    bind.add_argument("--create", action="store_true", help="allow creating a new central project")
    bind.add_argument("--offline", action="store_true", help="write the marker without contacting central")
    bind.add_argument("--no-git", action="store_true", help="do not add the Git remote as a second key")

    status = sub.add_parser("status", help="show the binding that applies to a directory")
    status.add_argument("path", nargs="?", default=".")

    disable = sub.add_parser("disable", help="keep the marker but exclude the project from capture")
    disable.add_argument("path", nargs="?", default=".")

    args = parser.parse_args(argv)
    directory = Path(args.path).expanduser().resolve()

    if args.command == "status":
        binding = project_binding(directory)
        if binding:
            print(json.dumps(binding, ensure_ascii=False, indent=2))
            return 0
        marker = find_marker(directory)
        if marker is not None:  # marker 在，只是被显式排除了；别让它看起来像「没绑定过」
            print(f'excluded: {marker} sets "capture": false, so nothing is recorded')
            print(f"re-enable with: trace-project bind {marker.parent}")
            return 1
        print(f"not bound: no {MARKER_NAME} at or above {directory}")
        print(f"capture is opt-in; run: trace-project bind {directory}")
        return 1

    if args.command == "disable":
        target = write_marker(directory, capture=False)
        print(f"capture disabled for {directory} (marker: {target})")
        return 0

    existing = read_marker(marker_path_for(directory))
    keys = _marker_keys(existing)
    requested_key = args.workspace_key.strip()
    if requested_key:
        problem = workspace_key_problem(requested_key)
        if problem:
            print(f"--workspace-key {requested_key!r} {problem}", file=sys.stderr)
            return 2
    key = requested_key or (keys[0] if keys else new_workspace_key())
    extra = [item for item in keys if item != key]
    if not args.no_git:
        remote = git_remote_key(directory)
        if remote and remote != key and remote not in extra:
            extra.append(remote)
    project_id = args.project_id.strip() or str(existing.get("project_id") or "").strip()
    name = args.name.strip() or str(existing.get("project_name") or "").strip() or directory.name
    if not project_id and not args.offline:
        try:
            url = normalize_server_url(args.url)
            body = _resolve_project(
                url, [key, *extra], name, args.create, args.token, args.credential_file, 30.0
            )
            for dropped in body.get("rejected_workspace_keys") or []:
                print(
                    f"ignored workspace key {dropped.get('workspace_key')!r}: {dropped.get('reason')}",
                    file=sys.stderr,
                )
            if body.get("pending_confirmation"):
                # §7：「映射不确定时进入待确认状态，不能静默创建多个重复项目。」
                # 团队映射命中了不止一个项目，谁也不知道是哪个——这时候唯一正确的
                # 行为是把候选摊开让人选，而不是挑一个或者新建一个。
                print(
                    f"the team mapping matches more than one central project for {key}; "
                    "nothing was created or bound.", file=sys.stderr,
                )
                for item in body.get("candidates") or []:
                    print(
                        f"  {item.get('project_id')}  {item.get('project_name')}"
                        f"   <- rule {item.get('pattern')!r} added by {item.get('created_by')}"
                        f" on {item.get('created_at')}",
                        file=sys.stderr,
                    )
                print("Pick one: trace-project bind --project-id <id>", file=sys.stderr)
                return 2
            if body.get("matched") is False:
                print(f"no central project matches {key}.", file=sys.stderr)
                print(
                    "Re-run with --project-id <existing id> or --create to make a new one "
                    "(refusing to create silently: REQUIREMENTS §7).",
                    file=sys.stderr,
                )
                for item in body.get("projects") or []:
                    print(f"  {item.get('id')}  {item.get('name')}", file=sys.stderr)
                return 2
            # /api/context 的成功响应是 {"matched": true, "project": {...}}，id 在里层。
            # 这里以前读的是外层的 body["id"]，永远是 None，所以 `trace-project bind`
            # 从来没有把 project_id 写进过 marker：每台机器都停在「未归属」，
            # 而那正是 §7 要靠 marker 解决的问题本身。
            resolved = body.get("project") if isinstance(body.get("project"), dict) else body
            project_id = str(resolved.get("id") or "") or None
            if body.get("resolved_by") == "team_mapping":
                rule = (body.get("matched_rules") or [{}])[0]
                print(
                    f"resolved through the team mapping: rule {rule.get('pattern')!r} "
                    f"added by {rule.get('created_by')} on {rule.get('created_at')}"
                )
        except DeliveryError as exc:
            print(f"central not reachable ({exc}); writing an offline marker", file=sys.stderr)
    target = write_marker(
        directory, workspace_key=key, workspace_keys=extra,
        project_id=project_id or None, project_name=name, capture=True,
    )
    print(f"bound {directory} -> {target}")
    print(json.dumps(read_marker(target), ensure_ascii=False, indent=2))
    if not project_id:
        print(
            "no central project_id yet: raw history will upload unassigned until you re-run "
            "`trace-project bind --create` or bind through the trace_context MCP tool.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
