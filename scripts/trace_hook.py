#!/usr/bin/env python3
"""Claude Code hooks for durable Research Trace event capture.

三条硬性质，按重要性排列：

1. **只录显式绑定过的项目。** 第一件事是从 cwd 向上找 `.research-trace.json`；找不到就
   立即返回，一个字节都不写、一个目录都不建。装上插件不等于同意录下这台机器上每个项目
   （REQUIREMENTS §13 的项目排除、§7 的「不能静默创建重复项目」）。
2. **hook 不投递。** 它只把事件原子地写进 `pending/` 就返回，绝不碰网络、绝不等任何人。
   把 pending 送到中央并只在 2xx 后搬进 `sent/` 是独立进程 `trace-deliver` 的事。
   于是正确性不再依赖模型是否记得调用工具、fork 是否成功、或缓存是否命中（§6）。
3. **隐藏推理不落盘。** transcript 增量按行解析，`thinking` / `redacted_thinking` 块在
   进 outbox 之前就被丢掉（§6「隐藏 chain-of-thought 不采集」）。

所有失败都 fail-open：研究工作不能因为 Research Trace 而中断（§6.1）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = "research-trace.event.v1"
RECORDER_MARKER = "[research-trace-recorder]"
BATCH_MARKER = "[research-trace-batch:"
RECORDER_READ_TOOLS = {"Read", "Grep", "Glob"}
RECORDER_TRACE_TOOLS = {
    "trace_context", "trace_ingest", "trace_record", "trace_curate", "trace_attach", "trace_search",
}

MARKER_NAME = ".research-trace.json"

# outbox 里装着完整对话和带令牌的命令原文。在多用户 HPC 节点上默认 0755/0644 等于
# 同机任何人可读；凭证文件早就是 0600，这里照抄同一个标准。Windows 上 chmod 基本无效，
# 所以是 best-effort，不影响流程。
DIR_MODE = 0o700
FILE_MODE = 0o600

# 隐藏推理块的类型名。字节级 hint 用来在不解析 JSON 的前提下跳过绝大多数行。
THINKING_TYPES = {"thinking", "redacted_thinking"}
THINKING_HINT = b"thinking"

# 同一个 session 两次 SessionStart 之间不重复拉起投递器（/clear 会连发）。
DELIVER_SPAWN_INTERVAL = 60.0

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
try:  # marker 的权威实现在包里，hook 只是它的第一个消费者
    from research_trace.deliver import project_binding as _package_binding
except Exception:  # pragma: no cover - 未安装/被裁剪时退化成下面的内联版本
    _package_binding = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return (text or fallback)[:120]


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(value)


def _long(path: Path) -> Path:
    """Windows 上给绝对路径加 `\\\\?\\` 前缀，绕开 260 字符的 MAX_PATH。

    outbox 路径的长度我们只控制得了一半：`${CLAUDE_PLUGIN_DATA}` 有多深是宿主决定的，
    session id 是 36 字符的 UUID，事件文件名本身就有 64 字符。实测在一个稍深的
    data-dir 下，`os.replace` 会以 WinError 3 失败，而 hook 是 fail-open 的——
    退出码仍然是 0，只在没人看的 stderr 上留一行，于是这台机器上每一条事件都被
    静默丢掉。加前缀之后同一条路径可以写到约 32767 字符。

    只在真的超长时才加：`\\\\?\\` 路径不做任何规范化，短路径没必要冒这个险。
    """
    if os.name != "nt":
        return path
    text = str(path)
    if len(text) < 240 or text.startswith("\\\\?\\"):
        return path
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):  # UNC: \\server\share -> \\?\UNC\server\share
        return Path("\\\\?\\UNC" + absolute[1:])
    return Path("\\\\?\\" + absolute)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(_long(path), mode)
    except OSError:
        pass


def _mkdir(path: Path) -> None:
    _long(path).mkdir(parents=True, exist_ok=True)
    _chmod(path, DIR_MODE)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(
        path,
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"),
    )


def _atomic_bytes(path: Path, value: bytes) -> None:
    _mkdir(path.parent)
    temp = _long(path.parent / f".{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(value)
        _chmod(temp, FILE_MODE)
        os.replace(temp, _long(path))
    except OSError:
        # 失败必须把半成品带走：旧实现留下的 .tmp 会在「永不自动删除」的目录里无限堆积。
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    _chmod(path, FILE_MODE)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(_long(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


# --------------------------------------------------------------------------------------
# ② 采集 opt-in：没有 marker 的目录直接跳过
# --------------------------------------------------------------------------------------


def _inline_binding(cwd: str | None) -> dict[str, Any] | None:
    """`research_trace` 不可导入时的退化实现：只认 marker 在不在、capture 开不开。"""
    try:
        current = Path(cwd or os.getcwd()).expanduser().resolve()
    except OSError:
        return None
    for _ in range(64):
        candidate = current / MARKER_NAME
        try:
            if candidate.is_file():
                value = _read_json(candidate, {})
                if not isinstance(value, dict):
                    value = {}
                if value.get("capture") is False:
                    return None
                keys = [
                    str(item).strip()
                    for item in [value.get("workspace_key"), *(value.get("workspace_keys") or [])]
                    if str(item or "").strip()
                ]
                return {
                    "marker_path": str(candidate),
                    "project_dir": str(current),
                    "workspace_keys": keys,
                    "workspace_key": keys[0] if keys else None,
                    "project_id": str(value.get("project_id") or "").strip() or None,
                    "project_name": str(value.get("project_name") or "").strip() or None,
                }
        except OSError:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def binding_for(payload: dict[str, Any]) -> dict[str, Any] | None:
    cwd = payload.get("cwd")
    cwd = str(cwd) if isinstance(cwd, str) and cwd.strip() else None
    if _package_binding is not None:
        try:
            return _package_binding(cwd)
        except Exception:
            pass
    return _inline_binding(cwd)


def _session_root(data_dir: Path, payload: dict[str, Any], binding: dict[str, Any]) -> Path:
    # 目录用 workspace key 做 hash：同一个项目在不同路径/worktree 下打开时共用一个 outbox
    # 分支（§7：绝对 cwd 不是项目身份）。没有 key 的 marker 退回 cwd。
    identity = binding.get("workspace_key") or os.path.normcase(
        str(payload.get("cwd") or os.getcwd())
    )
    workspace = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:16]
    session = _safe(payload.get("session_id"), "unknown-session")
    root = data_dir / "outbox" / workspace / session
    for name in (
        "pending", "sent", "batches", "batches/done",
        "transcripts/pending", "transcripts/sent", "transcripts/meta",
    ):
        _mkdir(root / name)
    _chmod(root, DIR_MODE)
    _chmod(root.parent, DIR_MODE)
    _chmod(data_dir / "outbox", DIR_MODE)
    return root


@contextmanager
def _state_lock(root: Path, timeout: float = 1.0, stale: float = 10.0) -> Iterator[bool]:
    """A tiny cross-platform lock based on atomic directory creation.

    被 kill 掉的 hook 会留下这个目录。旧实现的过期判断是 `now - mtime > 30` 且只在单向上
    生效：系统时钟回拨（HPC 上 NTP 常见）会让差值永远为负，锁再也拆不掉，此后每一次
    hook 事件固定多花整个 timeout。这里改成双向判断，并且优先看持有者进程还在不在。
    """
    lock = root / ".state-lock"
    deadline = time.monotonic() + timeout
    acquired = False
    while True:
        try:
            lock.mkdir()
            acquired = True
            break
        except FileExistsError:
            # 拆完不直接 continue：万一 rmdir 也失败，这里必须仍然受 deadline 约束。
            if _lock_is_dead(lock, stale):
                _drop_lock(lock)
        except OSError:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    if acquired:
        try:
            (lock / "owner").write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
    try:
        yield acquired
    finally:
        if acquired:
            _drop_lock(lock)


def _lock_is_dead(lock: Path, stale: float) -> bool:
    owner = _read_int(lock / "owner")
    if owner and owner != os.getpid() and not _pid_alive(owner):
        return True
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    return age > stale or age < -stale


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, AttributeError, ValueError):
        return True  # Windows 与权限不足都无法判定，交给时间兜底
    return True


def _drop_lock(lock: Path) -> None:
    try:
        for child in lock.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        lock.rmdir()
    except OSError:
        pass


def _write_event(root: Path, payload: dict[str, Any], binding: dict[str, Any]) -> Path:
    event_id = f"claude-{uuid.uuid4().hex}"
    record = {
        "schema": SCHEMA,
        "event_id": event_id,
        "captured_at": _now(),
        "source": "claude-code",
        "session_id": payload.get("session_id"),
        "project_dir": payload.get("cwd"),
        "project_id": binding.get("project_id"),
        "workspace_keys": binding.get("workspace_keys") or [],
        "hook_event": payload.get("hook_event_name"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "payload": payload,
    }
    stamp = time.time_ns()
    path = root / "pending" / f"{stamp}_{event_id}.json"
    _atomic_json(path, record)
    return path


# --------------------------------------------------------------------------------------
# ③ transcript：逐行剥掉隐藏推理再落盘
# --------------------------------------------------------------------------------------


def _drop_thinking(value: Any, depth: int = 0) -> tuple[Any, bool]:
    """返回 (清理后的值, 是否改动过)。值为 None 表示这个节点整体该被删掉。"""
    if depth > 16:
        return value, False
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str) and kind in THINKING_TYPES:
            return None, True
        out: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            if key in THINKING_TYPES or (key == "signature" and "thinking" in value):
                changed = True  # {"thinking": "...", "signature": "..."} 这种平铺形状
                continue
            new, sub = _drop_thinking(item, depth + 1)
            if new is None:
                changed = True
                continue
            out[key] = new
            changed = changed or sub
        return out, changed
    if isinstance(value, list):
        items: list[Any] = []
        changed = False
        for item in value:
            new, sub = _drop_thinking(item, depth + 1)
            if new is None:
                changed = True
                continue
            items.append(new)
            changed = changed or sub
        return items, changed
    return value, False


def _scrub_line(line: bytes) -> bytes | None:
    """一行 transcript JSONL → 允许落盘的字节；None 表示整行丢掉。

    绝大多数行不含 `thinking` 字样，走字节判断直接原样返回，完全不进 JSON 解析器，
    所以这条每次事件都跑的路径在 10 MB transcript 上仍是一次线性扫描量级。
    含该字样但解析不了的行不能放行（无法确认里面没有隐藏推理），也不能静默消失，
    所以换成一条只有长度和 hash 的占位记录：留下缺口证据，不留下原文。
    """
    if THINKING_HINT not in line:
        return line
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return json.dumps(
            {
                "type": "research-trace.redacted",
                "reason": "unparsable transcript line containing hidden-reasoning markers",
                "bytes": len(line),
                "sha256": hashlib.sha256(line).hexdigest(),
            },
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    scrubbed, changed = _drop_thinking(value)
    if scrubbed is None:
        return None
    if not changed:
        return line
    return json.dumps(scrubbed, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _read_scrubbed(stream: Any, remaining: int, chunk_size: int) -> tuple[int, bytes]:
    """读若干完整行，返回 (从源文件消耗的字节数, 清理后要落盘的字节)。

    消耗量按源文件计，落盘量按清理后计，两者故意分开：cursor 因此不受剥离影响。
    没有换行结尾的尾巴是「正在被追加的半条记录」，留到下次，绝不切开一条 JSON。
    """
    consumed = 0
    parts: list[bytes] = []
    while consumed < chunk_size and consumed < remaining:
        line = stream.readline()
        if not line or not line.endswith(b"\n"):
            break
        consumed += len(line)
        scrubbed = _scrub_line(line)
        if scrubbed:
            parts.append(scrubbed)
    return consumed, b"".join(parts)


def _capture_transcripts(
    root: Path, payload: dict[str, Any], state: dict[str, Any], chunk_size: int = 512 * 1024
) -> list[dict[str, Any]]:
    """Copy newly appended transcript JSONL records into immutable outbox chunks.

    Hook payloads contain transcript paths, not durable transcript content.  A cursor per
    physical transcript keeps this cheap while ensuring `/clear`, process exit, or later
    host cleanup cannot remove the only copy before central ingest.
    """
    candidates: list[tuple[str, str | None]] = []
    main_path = payload.get("transcript_path")
    if isinstance(main_path, str) and main_path.strip():
        candidates.append((main_path, None))
    agent_path = payload.get("agent_transcript_path")
    if isinstance(agent_path, str) and agent_path.strip():
        candidates.append((agent_path, str(payload.get("agent_id") or "") or None))

    cursors = state.setdefault("transcript_offsets", {})
    if not isinstance(cursors, dict):
        cursors = {}
        state["transcript_offsets"] = cursors
    captured: list[dict[str, Any]] = []
    for source_text, agent_id in candidates:
        source = Path(source_text).expanduser()
        key = hashlib.sha256(os.path.normcase(str(source)).encode("utf-8")).hexdigest()[:20]
        try:
            size = source.stat().st_size
        except OSError:
            continue
        start = int(cursors.get(key, 0) or 0)
        if start < 0 or start > size:
            start = 0  # 文件被轮换/截断；重来一遍，中央按 chunk_id 去重
        if start == size:
            continue
        try:
            with source.open("rb") as stream:
                stream.seek(start)
                offset = start
                while offset < size:
                    consumed, raw = _read_scrubbed(stream, size - offset, chunk_size)
                    if consumed == 0:
                        break
                    end = offset + consumed
                    if raw:
                        digest = hashlib.sha256(raw).hexdigest()
                        filename = f"{key}_{offset:016d}_{end:016d}_{digest[:16]}.jsonl"
                        destination = root / "transcripts" / "pending" / filename
                        if not _long(destination).exists():
                            _atomic_bytes(destination, raw)
                        metadata = {
                            "path": f"transcripts/pending/{filename}",
                            "chunk_id": f"claude-transcript-{digest}",
                            "session_id": payload.get("session_id"),
                            "agent_id": agent_id,
                            "source_path": source_text,
                            "start_offset": offset,
                            "end_offset": end,
                            "sha256": digest,
                        }
                        _atomic_json(root / "transcripts" / "meta" / f"{filename}.json", metadata)
                        captured.append(metadata)
                    offset = end
                    size = max(size, end)
                    # 每成功写完一块就推进 cursor：旧实现只在整个文件走完后才写，
                    # 一次 I/O 失败就让下一次从头重抄整份 transcript。
                    cursors[key] = offset
        except OSError as exc:
            # 静默吞掉是旧实现最坏的一条：transcript 永远采不到而退出码仍是 0。
            print(
                f"research-trace hook: transcript capture failed for {source_text}: {exc}",
                file=sys.stderr,
            )
            continue
    return captured


# --------------------------------------------------------------------------------------
# Recorder 编排（语义层）
# --------------------------------------------------------------------------------------


def _extract_agent_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("agent_id", "agentId"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        for child in value.values():
            found = _extract_agent_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _extract_agent_id(child)
            if found:
                return found
    elif isinstance(value, str):
        match = re.search(r"\bagent[-_ ]?id\b[^A-Za-z0-9_-]*([A-Za-z0-9_-]{6,})", value, re.I)
        if match:
            return match.group(1)
    return None


def _open_manifests(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((root / "batches").glob("*.json")):
        value = _read_json(path, {})
        if isinstance(value, dict) and value.get("batch_id"):
            out.append((path, value))
    return out


def _chunk_bounds(name: str) -> tuple[str, int]:
    parts = name.split("_")
    try:
        return parts[0], int(parts[2])
    except (IndexError, ValueError):
        return name, 0


def _ensure_batch(
    root: Path, payload: dict[str, Any], state: dict[str, Any], binding: dict[str, Any]
) -> tuple[Path, dict[str, Any]] | None:
    """给 Recorder 组一个待处理 batch。

    候选来自 `pending/` **和** `sent/`：投递器随时可能把文件搬进 sent/，语义层的取材范围
    不能因此塌掉。用单调游标而不是「谁还在 pending 里」来判断哪些已经派过工。
    """
    cursor = str(state.get("batched_through") or "")
    events: list[tuple[str, str]] = []
    for directory in ("pending", "sent"):
        for path in (root / directory).glob("*.json"):
            if path.name > cursor:
                events.append((path.name, f"{directory}/{path.name}"))
    events.sort(key=lambda item: item[0])

    chunk_cursor = state.setdefault("transcript_batch_offsets", {})
    if not isinstance(chunk_cursor, dict):
        chunk_cursor = {}
        state["transcript_batch_offsets"] = chunk_cursor
    chunks: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for directory in ("pending", "sent"):
        for path in (root / "transcripts" / directory).glob("*.jsonl"):
            key, end = _chunk_bounds(path.name)
            if path.name in seen or end <= int(chunk_cursor.get(key, 0) or 0):
                continue
            seen.add(path.name)
            metadata = _read_json(root / "transcripts" / "meta" / f"{path.name}.json", {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["path"] = f"transcripts/{directory}/{path.name}"
            chunks.append((path.name, metadata))
    chunks.sort(key=lambda item: item[0])

    open_batches = _open_manifests(root)
    if events or chunks:
        batch_id = f"{int(time.time())}-{uuid.uuid4().hex[:10]}"
        manifest = {
            "schema": "research-trace.batch.v1",
            "batch_id": batch_id,
            "created_at": _now(),
            "session_id": payload.get("session_id"),
            "project_dir": payload.get("cwd"),
            "project_id": binding.get("project_id"),
            "workspace_keys": binding.get("workspace_keys") or [],
            "event_count": len(events),
            "events": [rel for _, rel in events],
            "transcript_chunk_count": len(chunks),
            "transcript_chunks": [metadata for _, metadata in chunks],
        }
        manifest_path = root / "batches" / f"{batch_id}.json"
        _atomic_json(manifest_path, manifest)
        if events:
            state["batched_through"] = events[-1][0]
        for name, _ in chunks:
            key, end = _chunk_bounds(name)
            chunk_cursor[key] = max(int(chunk_cursor.get(key, 0) or 0), end)
        open_batches.append((manifest_path, manifest))
    return open_batches[0] if open_batches else None


def _close_batch(root: Path, batch_id: str) -> None:
    """Recorder 处理完一个 manifest 就归档它。

    注意这只影响语义层：原始文件的去向由投递器按中央 2xx 决定，跟这里无关。
    """
    manifest_path = root / "batches" / f"{_safe(batch_id, '')}.json"
    if not batch_id or not manifest_path.is_file():
        return
    manifest = _read_json(manifest_path, {})
    if isinstance(manifest, dict):
        manifest["recorder_finished_at"] = _now()
        _atomic_json(root / "batches" / "done" / manifest_path.name, manifest)
    try:
        manifest_path.unlink()
    except OSError:
        pass


def _is_trace_orchestration(
    root: Path, payload: dict[str, Any], state: dict[str, Any]
) -> bool:
    event = str(payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")
    tool_blob = _json_text(payload.get("tool_input"))

    if event == "SessionStart" and payload.get("source") == "clear":
        state.pop("recorder_agent_id", None)
        state.pop("pending_recorder_spawn", None)

    if event == "PreToolUse" and tool == "Agent" and RECORDER_MARKER in tool_blob:
        state["pending_recorder_spawn"] = _now()
        return True

    if event == "SubagentStart" and state.get("pending_recorder_spawn"):
        agent_id = payload.get("agent_id")
        if agent_id:
            state["recorder_agent_id"] = str(agent_id)
            state.pop("pending_recorder_spawn", None)
            return True

    if event == "PostToolUse" and tool == "Agent" and RECORDER_MARKER in tool_blob:
        agent_id = _extract_agent_id(payload.get("tool_response"))
        if agent_id:
            state["recorder_agent_id"] = agent_id
        state.pop("pending_recorder_spawn", None)
        return True

    if tool == "SendMessage" and BATCH_MARKER in tool_blob:
        if event == "PostToolUseFailure":
            state.pop("recorder_agent_id", None)
        return True

    recorder_id = str(state.get("recorder_agent_id") or "")
    is_recorder = bool(recorder_id) and str(payload.get("agent_id") or "") == recorder_id
    if event == "SubagentStop" and is_recorder:
        # Recorder 身份只来自它被派发时记下的 agent id，绝不来自收尾消息里的自称文本：
        # 旧实现认任何带 batch_id 的 TRACE_RECEIPT，普通子 agent 因此会被误认成 Recorder
        # 并被此后所有 Edit/Write/Bash 拒绝，主任务当场被插件挡死。
        _close_batch(root, str(state.pop("dispatched_batch", "") or ""))
        return True
    if is_recorder:
        return True
    if event == "Stop" and payload.get("stop_hook_active"):
        return True
    return False


def _recorder_running(payload: dict[str, Any], recorder_id: str | None) -> bool:
    if not recorder_id:
        return False
    for task in payload.get("background_tasks") or []:
        if isinstance(task, dict) and str(task.get("id") or "") == recorder_id:
            return str(task.get("status") or "").lower() in {"running", "pending"}
    return False


def _recorder_tool_guard(
    payload: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any] | None:
    """Deny mutation and unrelated tools inside the full-context Recorder fork.

    A fork intentionally inherits the main agent's tools for context/cache parity. The hook is
    therefore the enforcement boundary: the Recorder may inspect existing material and write only
    through the Research Trace MCP tools.
    """
    if payload.get("hook_event_name") != "PreToolUse":
        return None
    recorder_id = str(state.get("recorder_agent_id") or "")
    if not recorder_id or str(payload.get("agent_id") or "") != recorder_id:
        return None
    tool = str(payload.get("tool_name") or "")
    tool_basename = tool.rsplit("__", 1)[-1]
    if tool in RECORDER_READ_TOOLS or tool_basename in RECORDER_TRACE_TOOLS:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Research Trace Recorder is read-only outside the trace MCP; "
                f"tool {tool or '<unknown>'} is not allowed"
            ),
        }
    }


def _nudge(
    manifest_path: Path,
    manifest: dict[str, Any],
    recorder_id: str | None,
    protocol_path: Path,
) -> dict[str, Any]:
    batch_id = manifest["batch_id"]
    batch_message = (
        f"{BATCH_MARKER}{batch_id}] Read the batch manifest at {manifest_path}. "
        f"Follow the recorder protocol at {protocol_path}. Process this batch without asking "
        "the user, then finish with one short line and no raw logs."
    )
    if recorder_id:
        action = (
            f"Use SendMessage once with to={recorder_id!r} and this message:\n{batch_message}"
        )
    else:
        action = (
            "Spawn one background Agent with subagent_type='fork'. Its task prompt must begin "
            f"with {RECORDER_MARKER!r} and then contain:\n{batch_message}\n"
            "A fork is required because it receives the main agent's complete current context."
        )
    guidance = (
        "Research Trace has durably queued a recorder batch. Raw history is already safe on disk "
        "and is uploaded by the independent trace-deliver process, so nothing here affects "
        "durability. Do not summarize or interpret the batch in the main context. " + action
        + " After dispatching it once, stop without adding a user-facing trace message and do not "
        "wait for the background recorder. If fork or SendMessage is unavailable, do not retry "
        "this turn; the batch remains safely queued."
    )
    # Stop hooks do not support hookSpecificOutput.additionalContext.  The documented
    # continuation mechanism is a top-level block decision whose reason becomes the
    # agent's next instruction.  stop_hook_active prevents a second continuation.
    return {"decision": "block", "reason": guidance}


# --------------------------------------------------------------------------------------
# ① 投递器：分离启动，绝不等待
# --------------------------------------------------------------------------------------


def _spawn_deliver(data_dir: Path, url: str, state: dict[str, Any]) -> bool:
    """Fire-and-forget 拉起一次 `trace-deliver`。

    hook 自己绝不发网络请求：DNS 挂掉或中央不可达时，重试成本必须落在这个分离进程上，
    而不是落在用户的每一次工具调用上。启动失败同样无所谓 —— 内容已经在 pending/ 里，
    下一次 SessionStart、手动 `trace-deliver` 或 `--watch` 常驻都能把它带走。
    """
    if os.environ.get("TRACE_HOOK_NO_SPAWN"):
        return False
    now = time.time()
    last = float(state.get("deliver_spawned_at") or 0.0)
    if 0 <= now - last < DELIVER_SPAWN_INTERVAL:
        return False
    state["deliver_spawned_at"] = now
    command = [sys.executable, "-m", "research_trace.deliver", "--data-dir", str(data_dir), "--quiet"]
    if url:
        command += ["--url", url]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PLUGIN_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        "cwd": str(_PLUGIN_ROOT), "env": env,
    }
    if os.name == "nt":
        options["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(command, **options)  # 故意不 wait，也不看返回码
        return True
    except Exception as exc:
        print(f"research-trace hook: could not start trace-deliver: {exc}", file=sys.stderr)
        return False


def handle(
    payload: dict[str, Any], data_dir: Path, protocol_path: Path, url: str = ""
) -> dict[str, Any] | None:
    """Handle one hook input. Exposed separately for deterministic tests."""
    binding = binding_for(payload)
    if binding is None:
        # 未绑定的项目：不建目录、不写文件、不看 transcript。采集是 opt-in 的。
        return None
    root = _session_root(data_dir, payload, binding)
    state_path = root / "state.json"
    with _state_lock(root) as acquired:
        if not acquired:
            # Unique event files remain safe without the state lock. Avoid nudging because
            # batching while another process owns state could duplicate a batch.
            _write_event(root, payload, binding)
            return None
        state = _read_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        _capture_transcripts(root, payload, state)
        internal = _is_trace_orchestration(root, payload, state)
        if not internal:
            _write_event(root, payload, binding)

        if payload.get("hook_event_name") in {"SessionStart", "SessionEnd"}:
            _spawn_deliver(data_dir, url, state)

        result = _recorder_tool_guard(payload, state)
        if result is None and payload.get("hook_event_name") == "Stop" and not payload.get("stop_hook_active"):
            selected = _ensure_batch(root, payload, state, binding)
            recorder_id = state.get("recorder_agent_id")
            if selected and not _recorder_running(payload, recorder_id):
                state["dispatched_batch"] = selected[1]["batch_id"]
                result = _nudge(selected[0], selected[1], recorder_id, protocol_path)
        state["updated_at"] = _now()
        _atomic_json(state_path, state)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--capture-enabled", default="on")
    parser.add_argument("--url", default=os.environ.get("TRACE_URL", ""))
    args = parser.parse_args(argv)
    if str(args.capture_enabled).strip().lower() in {"0", "false", "off", "no"}:
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        output = handle(payload, Path(args.data_dir), Path(args.protocol), str(args.url or ""))
        if output:
            print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:  # fail-open by design; stderr is debug-only on exit 0
        print(f"research-trace hook capture failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
