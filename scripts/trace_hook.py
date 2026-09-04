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
# A background completion can start another parent turn with stop_hook_active=False.
# Bound that cross-turn feedback even when the host omits Recorder identity fields.
MAX_RECORDER_DISPATCHES_PER_PROMPT = 3
RECORDER_TRACE_TOOLS = {
    "trace_context", "trace_ingest", "trace_record", "trace_curate", "trace_attach", "trace_search",
}

MARKER_NAME = ".research-trace.json"

#: 这些 hook 事件本身不携带任何研究材料，它们只说「一轮结束了」「会话开始了」。
#: Lifecycle notifications and recorder diagnostics never independently justify model work.
#: Their raw evidence remains available; transcript growth alone is not a dispatch trigger.
LIFECYCLE_EVENTS = frozenset({
    "Stop", "StopFailure", "SessionStart", "SessionEnd", "PreCompact", "PostCompact",
})
RECORDING_DIAGNOSTICS = frozenset({
    "TranscriptCaptureError", "CodeCaptureError", "RecorderDispatchPaused",
})

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


#: transcript 里这些行是编辑器/会话的运行期状态，不是研究材料。实测一份 130 MB 的采集里
#: file-history-snapshot 一项就占 31.7 MB（24%）——它是 Claude Code 自己的文件快照，
#: 全额进了 outbox、投递带宽和 GitHub 备份，却没有任何溯源价值。
#: 注意这不影响提示缓存：缓存是 API 那一侧按 prompt 前缀算的，跟这里抄多少字节无关。
NOISE_TYPES = ("file-history-snapshot", "queue-operation", "bridge-session", "custom-title", "mode")
#: 用完整的 `"type":"..."` 形态匹配，而不是裸类型名——"mode" 这种词在正文里太常见。
#: 写法带空格时会匹配不上，那时这一行被保留：失败方向故意选「多存点噪声」而不是「误删内容」。
NOISE_MARKERS = tuple(f'"type":"{name}"'.encode("utf-8") for name in NOISE_TYPES)


#: Claude Code 给工具输出留了第二份结构化拷贝 `toolUseResult`。读图片时它里面的
#: `file.base64` 和 `message.content` 里的图片块是**同一份字节**——实测 50 行、26.2 MB，
#: 100% 都能在同一行的 message.content 里找到副本，占整份采集的 20%。
#: 只剥这一层，不动整个 toolUseResult：`structuredPatch` / `filePath` / `numLines`
#: 是「这次编辑改了什么」的证据，几百 KB 但有溯源价值。
BASE64_HINT = b'"base64"'


def _strip_duplicate_base64(value: dict[str, Any]) -> bool:
    """把 toolUseResult.file.base64 换成一条只有长度和 hash 的占位。

    不直接删：留下「这里曾经有一张多大的图、它的 sha256 是什么」这个事实，
    和剥 thinking 时留占位是同一个道理——缺口本身也是溯源信息。
    """
    result = value.get("toolUseResult")
    if not isinstance(result, dict):
        return False
    file_value = result.get("file")
    if not isinstance(file_value, dict):
        return False
    raw = file_value.get("base64")
    if not isinstance(raw, str) or not raw:
        return False
    encoded = raw.encode("utf-8", "replace")
    file_value["base64"] = None
    file_value["research_trace_base64_omitted"] = {
        "reason": "duplicate of the image block in message.content",
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    return True


def _is_ui_noise(line: bytes) -> bool:
    for marker in NOISE_MARKERS:
        if marker in line:
            return True
    return False


def _remember_recorder_id(state: dict[str, Any], agent_id: str | None) -> None:
    """记住这个会话里所有当过 Recorder 的 agent id。

    只留「当前那一个」是不够的：每批重新 fork 之后 id 会换，而上一个 Recorder 的
    transcript 尾巴可能在它退休之后才被采集到。漏掉一行就够重新点着那个反馈环。
    """
    agent_id = str(agent_id or "")
    if not agent_id:
        return
    known = state.setdefault("recorder_ids", [])
    if not isinstance(known, list):
        known = []
    if agent_id not in known:
        known.append(agent_id)
    state["recorder_ids"] = known[-20:]


def _scrub_line(line: bytes, recorder_ids: frozenset[str] = frozenset()) -> bytes | None:
    """一行 transcript JSONL → 允许落盘的字节；None 表示整行丢掉。

    绝大多数行不含 `thinking` 字样，走字节判断直接原样返回，完全不进 JSON 解析器，
    所以这条每次事件都跑的路径在 10 MB transcript 上仍是一次线性扫描量级。
    含该字样但解析不了的行不能放行（无法确认里面没有隐藏推理），也不能静默消失，
    所以换成一条只有长度和 hash 的占位记录：留下缺口证据，不留下原文。
    """
    if _is_ui_noise(line):
        return None
    if BASE64_HINT in line:
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            value = None
        if isinstance(value, dict) and _strip_duplicate_base64(value):
            # 重新序列化之后仍要走下面的 thinking 检查，所以不在这里 return。
            line = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    # Recorder 自己的回合不是研究材料，而且它们跟事件层走的是两条路：事件被
    # _is_trace_orchestration 挡住了，transcript 却是照单全收的。而 fork 的回合就写在
    # 同一个 transcript 文件里，于是「recorder 跑完 → transcript 变长 → 新 chunk →
    # `events or chunks` 成立 → 新 batch → 再派一个 fork」自己转起来，每转一圈烧掉
    # 一次完整 fork，产出恒为 0。这里断掉的就是那个环。
    for agent_id in recorder_ids:
        if agent_id.encode("utf-8") not in line:
            continue                      # 快路径：id 的字节都不在这行里，不必解析
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            break                         # 解析不了就交给下面的 thinking 逻辑处理
        if isinstance(value, dict) and str(value.get("agentId") or "") == agent_id:
            return None
        break
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


def _read_scrubbed(
    stream: Any, remaining: int, chunk_size: int, recorder_ids: frozenset[str] = frozenset()
) -> tuple[int, bytes]:
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
        scrubbed = _scrub_line(line, recorder_ids)
        if scrubbed:
            parts.append(scrubbed)
    return consumed, b"".join(parts)


@contextmanager
def _entire_capture_stream(root, repository, session_id):
    staged = []
    try:
        with repository.session_stream(session_id) as stream:
            yield stream, staged
        # Publish only after upstream succeeds. A failed partial export cannot
        # leave overlapping chunks for the independent delivery worker to ingest.
        for temporary, metadata in staged:
            destination = root / metadata['path']
            _mkdir(destination.parent)
            os.replace(_long(temporary), _long(destination))
            _atomic_json(root / 'transcripts' / 'meta' / (destination.name+'.json'), metadata)
    finally:
        for temporary, _ in staged:
            _long(temporary).unlink(missing_ok=True)


def _capture_entire_transcript(root, payload, state, binding, config, chunk_size=512 * 1024):
    """Consume the upstream session export; Trace only filters and queues deltas.

    Do not fall back to reading a guessed main-session path on provider failure.
    Existing physical offsets give upgraded/resumed sessions a fresh boundary.
    Subagent transcripts remain a compatibility path until Entire exports them
    as individually addressable sessions.
    """
    package_root = str(Path(__file__).resolve().parents[1])
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    from research_trace.entire_evidence import EntireRepository, EvidenceError
    sid = str(payload.get('session_id') or '')
    key = hashlib.sha256(('entire:' + sid).encode()).hexdigest()[:20]
    source = str(payload.get('transcript_path') or '')
    legacy_key = hashlib.sha256(os.path.normcase(str(Path(source).expanduser())).encode()).hexdigest()[:20]
    start = int(state.get('entire_offsets', {}).get(key, state.get('transcript_offsets', {}).get(legacy_key, 0)))
    recorder_ids = frozenset(str(x) for x in state.get('recorder_ids', []))
    captured = []
    repository = EntireRepository(binding['project_dir'], config['entire_executable'])
    with _entire_capture_stream(root, repository, sid) as (stream, staged):
        skipped = 0
        last = b''
        while skipped < start:
            raw = stream.read(min(65536, start-skipped))
            if not raw:
                raise EvidenceError('Entire export is shorter than its saved cursor; old history was not reimported')
            skipped += len(raw)
            last = raw[-1:]
        offset = start
        # The fresh boundary may cut a partially written row. Skip that row,
        # rather than importing its old prefix or treating its suffix as JSON.
        if last and last != b'\n':
            offset += len(stream.readline())
        eof = False
        while not eof:
            begin = offset
            parts = []
            while offset-begin < chunk_size:
                line = stream.readline(16 * 1024 * 1024 + 1)
                if not line:
                    eof = True
                    break
                if not line.endswith(b'\n') or len(line) > 16 * 1024 * 1024:
                    raise EvidenceError('Entire export contains an incomplete or oversized JSONL row')
                if not isinstance(json.loads(line), dict):
                    raise EvidenceError('Entire export row must be an object')
                offset += len(line)
                scrubbed = _scrub_line(line, recorder_ids)
                if scrubbed:
                    parts.append(scrubbed)
            if parts:
                raw = b''.join(parts)
                sha = hashlib.sha256(raw).hexdigest()
                filename = f'{key}_{begin:016d}_{offset:016d}_{sha[:16]}.jsonl'
                metadata = {'path':f'transcripts/pending/{filename}', 'chunk_id':f'claude-transcript-{sha}',
                            'session_id':sid, 'agent_id':None, 'source_path':'entire:session:'+sid,
                            'provider':'entire', 'start_offset':begin, 'end_offset':offset, 'sha256':sha}
                temporary=root / 'transcripts' / 'staging' / (uuid.uuid4().hex+'.tmp')
                _atomic_bytes(temporary, raw)
                staged.append((temporary, metadata))
                captured.append(metadata)
    # Advance only after the upstream process has completed successfully.
    state.setdefault('entire_offsets', {})[key] = offset
    return captured


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

    known = state.get("recorder_ids")
    recorder_ids = frozenset(str(x) for x in known if str(x)) if isinstance(known, list) else frozenset()

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
                    consumed, raw = _read_scrubbed(
                        stream, size - offset, chunk_size, recorder_ids)
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
    if _has_material(root, events):
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


def _has_material(root: Path, events: list[tuple[str, str]]) -> bool:
    """这一批里有没有「真的发生过什么」。

    判据是**事件**而不是 transcript 长度：用户说了话（UserPromptSubmit）、调了工具
    （Pre/PostToolUse）、研究子 agent 跑完（SubagentStart/Stop）——这些都写事件。
    已识别的 Recorder 活动在事件层被过滤；生命周期和采集诊断不单独触发整理。
    身份缺失时仍可能混入 Recorder 操作，所以 handle 另有跨回合派发上限。

    transcript 不能当判据：Recorder 的回合就写在同一个 transcript 文件里，chunk 照样变长
    （scrub 只按 agentId 精确匹配丢行，漏一行就够开一批），于是 `events or chunks` 会把
    Recorder 自己的活动当成新素材，再派一次 fork。

    跳过时**不推进游标**：这些事件会留到下一批真有内容时一起带上，什么都不会丢。
    原始投递跟这里无关——它由投递器按中央 2xx 决定，本来就不经过 batch。
    """
    for name, rel in events:
        record = _read_json(root / rel, {})
        if not isinstance(record, dict):
            return True     # 读不出来就当它有内容：宁可多派一次，也不要静默漏记
        if str(record.get("hook_event") or "") not in LIFECYCLE_EVENTS | RECORDING_DIAGNOSTICS:
            return True
    return False


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


def _fork_window(configured: str = "") -> int:
    """每处理多少个批次重新 fork 一次 Recorder。

    1 = 每批重新 fork，继承主 agent 当前上下文。大于 1 = 窗口内复用；0 = 持续复用。
    复用只保留 Recorder 自己的历史，必须读取新批次材料才能获知主会话之后的变化。
    缺省 / 非数字使用 1。相同前缀有利于缓存，但实际成本取决于宿主、命中及调用次数；
    此开关本身不能证明 token 节省，不把历史单次测量当作当前集成的效果。
    """
    #: 这个值来自项目 marker（`.research-trace.json` 的 `recorder_fork_window`），不是插件
    #: 配置项。插件配置项走 hooks.json 的 `${user_config.…}` 展开，而**未设置的选项会让整个
    #: hook 执行失败**——老安装升级上来时它们的 settings 里根本没有这个键，于是采集全停。
    #: marker 是 hook 本来就要读的东西，缺这个键就用默认值，不会有任何东西展开失败。
    raw = str(configured or os.environ.get("TRACE_RECORDER_FORK_WINDOW") or "").strip()
    if not raw and str(os.environ.get("TRACE_RECORDER_REUSE") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        return 0                      # 老开关继续认，等价于「整个会话只 fork 一次」
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(0, value)


def _remember_recorder(state: dict[str, Any], agent_id: str | None) -> None:
    """记下当前 Recorder 的 agent id，但**退休过的不许复活**。

    同一次派发会产生多个事件（PreToolUse / SubagentStart / SubagentStop /
    PostToolUse），而它们的先后顺序由 harness 决定。SubagentStop 之后如果还收到
    那次派发的 PostToolUse，照原样写回去就会把「已经结束、下一批重新 fork」
    悄悄改回「复用这个已经停掉的 agent」——下一批于是被 SendMessage 发给一个死掉的
    Recorder。只在某一种事件顺序下才正确的实现是脆的。
    """
    agent_id = str(agent_id or "")
    if not agent_id or agent_id == str(state.get("retired_recorder_id") or ""):
        return
    state["recorder_agent_id"] = agent_id


def _is_trace_orchestration(
    root: Path, payload: dict[str, Any], state: dict[str, Any], protocol_path: Path | None = None,
) -> bool:
    event = str(payload.get("hook_event_name") or "")
    tool = str(payload.get("tool_name") or "")
    tool_blob = _json_text(payload.get("tool_input"))

    if event == "UserPromptSubmit" and str(payload.get("prompt") or "").lstrip().startswith(
        (RECORDER_MARKER, BATCH_MARKER)
    ):
        return True

    # On older hosts tool hooks may omit agent_id. Reading our own protocol/outbox
    # is still recorder plumbing, never a fresh research finding. Do not broadly
    # discard anonymous reads of project files: retain them and rely on the cap.
    if tool in RECORDER_READ_TOOLS and isinstance(payload.get("tool_input"), dict):
        values = payload["tool_input"]
        raw_path = values.get("file_path") if tool == "Read" else values.get("path")
        if isinstance(raw_path, str) and raw_path:
            try:
                target = Path(raw_path).expanduser()
                if not target.is_absolute():
                    target = Path(str(payload.get("cwd") or ".")) / target
                target = target.resolve()
                if target.is_relative_to(root.resolve()) or (
                    protocol_path is not None and target == protocol_path.resolve()
                ):
                    return True
            except (OSError, ValueError, RuntimeError):
                pass  # Retain unresolvable evidence; never guess its owner.

    if event == "SessionStart" and payload.get("source") == "clear":
        state.pop("recorder_agent_id", None)
        state.pop("pending_recorder_spawn", None)

    if event == "PreToolUse" and tool == "Agent" and RECORDER_MARKER in tool_blob:
        state["pending_recorder_spawn"] = _now()
        state.pop("retired_recorder_id", None)   # 新的一次派发，退休名单清零
        return True

    if event == "SubagentStart" and state.get("pending_recorder_spawn"):
        agent_id = payload.get("agent_id")
        if agent_id:
            _remember_recorder(state, agent_id)
            _remember_recorder_id(state, agent_id)
            state.pop("pending_recorder_spawn", None)
            return True

    if event == "PostToolUse" and tool == "Agent" and RECORDER_MARKER in tool_blob:
        _remember_recorder(state, _extract_agent_id(payload.get("tool_response")))
        _remember_recorder_id(state, _extract_agent_id(payload.get("tool_response")))
        state.pop("pending_recorder_spawn", None)
        return True

    if tool == "SendMessage" and BATCH_MARKER in tool_blob:
        if event == "PostToolUseFailure":
            state.pop("recorder_agent_id", None)
        return True

    # Research Trace 自己的 MCP 调用永远不是研究材料——记录系统在运转，不等于研究在推进。
    # 这一条**不看 agent_id**，因为 agent_id 靠不住：实测某些 Claude Code 版本只在
    # SubagentStop 上给 agent_id，PreToolUse / PostToolUse 上一个都没有（现场 195 条
    # 工具事件全是空）。于是 Recorder 自己调 trace_attach 时 is_recorder 判不出来，
    # 那次调用被当成主 agent 的普通事件写进事件层 —— 而事件就是「素材」，素材就开新批，
    # 新批再派一个 Recorder。环就是这么闭合的，且因为它是 PreToolUse 而非生命周期事件，
    # `_has_material` 也拦不住。
    # 代价是主 agent 手动调 trace_* 时也不落事件：可以接受——那同样是记录系统在运转。
    if tool.rsplit("__", 1)[-1] in RECORDER_TRACE_TOOLS:
        return True

    recorder_id = str(state.get("recorder_agent_id") or "")
    actor_id = str(payload.get("agent_id") or "")
    is_recorder = bool(recorder_id) and actor_id == recorder_id
    if event == "SubagentStop" and is_recorder:
        # Recorder 身份只来自它被派发时记下的 agent id，绝不来自收尾消息里的自称文本：
        # 旧实现认任何带 batch_id 的 TRACE_RECEIPT，普通子 agent 因此会被误认成 Recorder
        # 并被此后所有 Edit/Write/Bash 拒绝，主任务当场被插件挡死。
        _close_batch(root, str(state.pop("dispatched_batch", "") or ""))
        window = _fork_window(str(state.get("fork_window") or ""))
        state["forked_batches"] = int(state.get("forked_batches") or 0) + 1
        if window and state["forked_batches"] >= window:
            state["forked_batches"] = 0
            state["retired_recorder_id"] = recorder_id
            # 下一批重新 fork，获得主会话当前上下文。旧 fork 的主会话快照不会自动更新；
            # 显式复用时，协议要求读取新材料。缓存是否命中仍由宿主/服务端决定。
            state.pop("recorder_agent_id", None)
        return True
    if is_recorder or (actor_id and actor_id in state.get("recorder_ids", [])):
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
        batch_message += (
            " This resumes your own Recorder history, not the parent's current context. "
            "Read the new event and transcript files listed in this manifest before summarizing."
        )
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
    # Keep the top-level block response for compatibility with existing hosts.
    # stop_hook_active guards the immediate continuation; handle also bounds
    # dispatch across separate turns caused by background completions.
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
    payload: dict[str, Any], data_dir: Path, protocol_path: Path, url: str = "",
    fork_window: str = "",
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
        # 窗口跟着项目走：marker 里没有这个键就是默认值。
        window = fork_window or str(binding.get("recorder_fork_window") or "")
        if window:
            state["fork_window"] = window
        capture_config=_read_json(Path(binding['marker_path']),{}).get('code_capture') or {}
        if capture_config.get('enabled') and not state.get('code_capture_started'):
            # Enabling on a resumed conversation establishes a forward-only
            # boundary. The current hook payload is still recorded below.
            transcript=payload.get('transcript_path')
            if transcript:
                source=Path(transcript).expanduser()
                if source.is_file():
                    key=hashlib.sha256(os.path.normcase(str(source)).encode('utf-8')).hexdigest()[:20]
                    state.setdefault('transcript_offsets',{}).setdefault(key,source.stat().st_size)
            state['code_capture_started']=_now()
        if capture_config.get('entire_executable'):
            try:
                _capture_entire_transcript(root, payload, state, binding, capture_config)
            except Exception as exc:
                _write_event(root, {**payload, 'hook_event_name':'TranscriptCaptureError',
                                   'transcript_capture_error':str(exc)[:1000], 'provider':'entire'}, binding)
            _capture_transcripts(root, {**payload, 'transcript_path':None}, state)
        else:
            _capture_transcripts(root, payload, state)
        internal = _is_trace_orchestration(root, payload, state, protocol_path)
        if (not internal and payload.get("hook_event_name") == "UserPromptSubmit"
                and not payload.get("agent_id")):
            # Only fresh user input replenishes the automatic dispatch budget.
            # Agent completions, tools, SessionStart and hook continuations cannot.
            state["recorder_dispatches_since_prompt"] = 0
            state["recorder_dispatched_batches_since_prompt"] = []
            state.pop("recorder_dispatch_paused", None)
        if not internal:
            if (payload.get("hook_event_name") in {"Stop", "SessionEnd"} and not payload.get("stop_hook_active")
                    and (_read_json(Path(binding['marker_path']), {}).get('code_capture') or {}).get('enabled')):
                try:
                    # The hook remains offline. Git/Entire evidence is queued;
                    # the existing delivery worker uploads the retained bytes.
                    package_root = str(Path(__file__).resolve().parents[1])
                    if package_root not in sys.path:
                        sys.path.insert(0, package_root)
                    from research_trace.workspace import capture_phase
                    snapshot = capture_phase(binding, payload.get("session_id"))
                    if snapshot and snapshot['commit_hash'] != state.get('last_code_commit'):
                        _write_event(root, {**payload, 'hook_event_name':'CodeCheckpoint', 'code_snapshot':snapshot}, binding)
                        state['last_code_commit'] = snapshot['commit_hash']
                except Exception as exc:
                    _write_event(root, {**payload, 'hook_event_name':'CodeCaptureError',
                                       'code_capture_error':str(exc)[:1000]}, binding)
            _write_event(root, payload, binding)

        # Stop 也要拉一次：一轮对话刚结束，batch 正好写完。只挂在 SessionStart/SessionEnd
        # 上的话，HPC 上那种一开就是几小时、从不正常结束的会话可以攒到几百个批次都不投。
        # 有 DELIVER_SPAWN_INTERVAL 的 60 秒节流兜着，不会变成每轮一个进程。
        if payload.get("hook_event_name") in {"SessionStart", "SessionEnd", "Stop"}:
            _spawn_deliver(data_dir, url, state)

        result = _recorder_tool_guard(payload, state)
        if (result is None and payload.get('hook_event_name')=='SessionStart'
                and (_read_json(Path(binding['marker_path']),{}).get('code_capture') or {}).get('enabled')):
            result={'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':
                'Research Trace passively records this project. Continue using your normal training and sbatch commands. '
                'The research agent is responsible for keeping submitted experiment directories and their referenced '
                'shared code unchanged. Research Trace does not submit jobs, create execution directories, rewrite paths '
                'or enforce that rule. It preserves observed commands, outputs, conversations and code evidence. '
                'When recording a meaningful experiment group, use source_event_ids for observed commands/results and '
                'code checkpoints; use run_ids only for run records that actually exist. '
                'Keep W&B as a curve link in the research record; never upload code to W&B. '
                'Untried ideas and unknown predecessor relationships are valid; keep summaries short and mark uncertainty.'}}
        if (result is None and not internal and payload.get("hook_event_name") == "Stop"
                and not payload.get("stop_hook_active")):
            selected = _ensure_batch(root, payload, state, binding)
            recorder_id = state.get("recorder_agent_id")
            if selected and not _recorder_running(payload, recorder_id):
                batch_id = selected[1]["batch_id"]
                attempts = int(state.get("recorder_dispatches_since_prompt") or 0)
                if attempts >= MAX_RECORDER_DISPATCHES_PER_PROMPT:
                    if not state.get("recorder_dispatch_paused"):
                        _write_event(root, {
                            **payload, "hook_event_name": "RecorderDispatchPaused",
                            "dispatch_count": attempts,
                            "reason": "automatic dispatch limit reached; resume after new user input",
                        }, binding)
                        state["recorder_dispatch_paused"] = True
                elif batch_id not in state.get("recorder_dispatched_batches_since_prompt", []):
                    # Persist before returning to the model. A repeated Stop cannot
                    # keep retrying a spawn or SendMessage for the same batch.
                    state["dispatched_batch"] = batch_id
                    state.setdefault("recorder_dispatched_batches_since_prompt", []).append(batch_id)
                    state["recorder_dispatches_since_prompt"] = attempts + 1
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
