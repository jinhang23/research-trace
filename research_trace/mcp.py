"""Minimal stdio MCP client for the Research Trace central HTTP service.

stdio 传输下 stdout 是协议通道：任何诊断输出都必须走 stderr，两端的编码都必须钉死
UTF-8，否则 Windows 的默认 code page 会在协议层就把中文改掉。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .device_login import (
    clear_pending_login,
    default_credential_file,
    load_device_credential,
    load_pending_login,
    poll_login,
    request_json,
    save_device_credential,
    save_pending_login,
    start_login,
)


PROTOCOL_VERSION = "2025-03-26"
# initialize 必须做版本协商。客户端报的版本我们支持就原样回；不支持就回自己偏好的那个，
# 由客户端决定是否继续握手。无条件回一个固定字符串会让老客户端误以为握手成功，
# 然后在第一次 tools/call 上以看不懂的方式失败。
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# JSON-RPC 2.0 错误码。客户端按码分流：解析失败报成 -32603 会被当成服务端内部崩溃，
# 而不是"这一行坏了、重发即可"。
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SERVER_INFO = {"name": "research-trace", "version": "2.0.0-alpha.16"}
INSTRUCTIONS = (
    "Research Trace has a raw-history layer and a selective semantic layer. "
    "Capture is opt-in per project: a directory without a .research-trace.json marker records "
    "nothing at all, and says nothing about it — so when a user expects records and finds none, "
    "check the binding first. Binding is a human decision: resolve the project with trace_context "
    "and hand the user `trace-project bind`; never write that marker on your own initiative. "
    "Raw batches are delivered by the independent trace-deliver process; do not call trace_ingest "
    "for hook manifests — durability never depends on a model remembering to call a tool. "
    "Use trace_record only for work worth understanding "
    "or reusing later; a batch may legitimately create no node. Experiments, ideas, papers, data "
    "understanding, failures and implementations all use the same Node. Chapters are human-defined "
    "parallel research tracks such as main and ablation experiments, not content types or pipeline stages. "
    "Recorder-created Nodes must use an existing chapter_id or omit it for Inbox, and always remain "
    "unreviewed until a human confirms them. Use trace_curate for the current "
    "Overview or Chapter summary, and never overwrite unresolved human corrections. Do not record every "
    "file edit or infer per-agent authorship from a shared working tree. "
    "Use trace_login only when the user explicitly asks to connect this machine or authentication is missing."
)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "trace_context",
        "description": "Resolve a stable project and return its Overview, Chapters, recent Nodes, corrections and raw cursor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "workspace_keys": {"type": "array", "items": {"type": "string"}},
                "create_if_missing": {"type": "boolean", "default": False},
                "project_name": {"type": "string"},
                "recent_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "include_dataflow": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Also return the derived data-flow view: edges between Nodes whose "
                        "registered output and input artifacts share the same sha256/uri/machine+path "
                        "key. Nothing is inferred from prose, so a project that never registered "
                        "artifacts simply has no edges — that is normal, not an error."
                    ),
                },
                "bind_path": {
                    "type": "string",
                    "description": (
                        "Only when the user explicitly asked to bind this directory: write the "
                        ".research-trace.json capture marker there after resolving the project. "
                        "Never pass it on your own initiative — capture is opt-in and binding is "
                        "the human's decision."
                    ),
                },
            },
        },
    },
    {
        "name": "trace_ingest",
        "description": "Manual backfill / non-Claude hosts only. Claude Code hook batches are delivered by the independent trace-deliver process; never call this for a hook manifest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "manifest_path": {"type": "string"},
                "batch_id": {"type": "string"},
                "project_id": {"type": "string"},
                "session": {"type": "object"},
                "agents": {"type": "array", "items": {"type": "object"}},
                "events": {"type": "array", "items": {"type": "object"}},
                "transcript_chunks": {"type": "array", "items": {"type": "object"}},
            },
            "anyOf": [{"required": ["manifest_path"]}, {"required": ["batch_id", "events"]}],
        },
    },
    {
        "name": "trace_record",
        "description": "Create or retry one valuable unreviewed Node in an existing human-defined Chapter; omit chapter_id for Inbox.",
        "inputSchema": {
            "type": "object",
            "required": ["project_id", "idempotency_key", "title"],
            "properties": {
                "project_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "chapter_id": {"type": "string"},
                "parent_id": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "occurred_at": {"type": "string"},
                "source_event_ids": {"type": "array", "items": {"type": "string"}},
                "code_evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["file_path"],
                        "properties": {
                            "repo_url": {"type": "string"},
                            "commit_hash": {"type": "string"},
                            "file_path": {"type": "string"},
                            "symbol": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                            "snippet": {"type": "string"},
                            "diff": {"type": "string"},
                            "annotation": {"type": "string"},
                            "content_sha256": {"type": "string"},
                            "attribution": {"type": "string", "enum": ["exact", "reported", "ambiguous", "unknown"]},
                            "contributor_agent_ids": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "trace_curate",
        "description": "Versioned update of the current Project Overview or a Chapter summary. Human corrections must be acknowledged.",
        "inputSchema": {
            "type": "object",
            "required": ["project_id", "target_type", "body", "expect_version"],
            "properties": {
                "project_id": {"type": "string"},
                "target_type": {"type": "string", "enum": ["overview", "chapter"]},
                "target_id": {"type": "string"},
                "body": {"type": "string"},
                "expect_version": {"type": "integer"},
                # actor_type / actor_id 不在这里：写入身份只来自凭证，请求体里的这两个字段
                # 服务端一律忽略。留着它们等于告诉模型有一个它其实没有的旋钮。
                "source_event_ids": {"type": "array", "items": {"type": "string"}},
                "milestone": {"type": "boolean"},
                "resolve_comment_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "trace_attach",
        "description": (
            "Attach a small file to Overview/Chapter/Node or register an external "
            "input/output/reference artifact. ALWAYS give a comparable key: either sha256, or an "
            "absolute uri with a scheme (s3://…, file:///…), or machine together with an absolute "
            "external_path. That key is the only thing that can ever link one Node's output to "
            "another Node's input — the data-flow view joins registered artifacts on it and never "
            "guesses producers or consumers from prose, so an artifact registered with a name "
            "alone is silently unjoinable forever. Relative paths, bare '~/…' paths, an "
            "external_path with no machine, and truncated hashes are all treated as no key at all. "
            "Use direction=output for what this Node produced, input for what it consumed, and "
            "reference for anything you are only pointing at."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["project_id", "target_type", "target_id", "name"],
            "properties": {
                "project_id": {"type": "string"},
                "target_type": {"type": "string", "enum": ["overview", "chapter", "node"]},
                "target_id": {"type": "string"},
                "name": {"type": "string"},
                "direction": {"type": "string", "enum": ["input", "output", "reference"]},
                "mime_type": {"type": "string"},
                "local_path": {"type": "string"},
                "data_base64": {"type": "string"},
                "uri": {"type": "string"},
                "machine": {"type": "string"},
                "external_path": {"type": "string"},
                "size": {"type": "integer"},
                "sha256": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    },
    # 数据流挂在 trace_context 的 include_dataflow 上，不挂在这里：search 的输入是
    # 一个文本 query，而数据流是整个项目的派生图，没有可查询的文本；做成 scope=dataflow
    # 会让必填的 q 变成一个被忽略的参数。也不能新开一个第七个研究工具——§9 把研究工具
    # 钉死在六个。
    {
        "name": "trace_search",
        "description": "Search semantic records and/or permanent raw history across projects. Use scope=semantic when looking for conclusions or existing records; scope=all also dredges up raw events.",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "project_id": {"type": "string"},
                "scope": {"type": "string", "enum": ["all", "semantic", "raw"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    },
    {
        "name": "trace_login",
        "description": (
            "Start or finish one-time GitHub account login for this machine. "
            "Use only when the user asks to connect/login Research Trace or authentication is missing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "status"], "default": "start"},
                "device_name": {"type": "string"},
            },
        },
    },
]


class Remote:
    def __init__(
        self, url: str, token: str = "", credential_file: str | os.PathLike[str] | None = None
    ):
        self.url = url.rstrip("/")
        self.explicit_token = token
        self.credential_file = Path(credential_file).expanduser().resolve() if credential_file else default_credential_file()

    def auth_token(self) -> str:
        if self.explicit_token:
            return self.explicit_token
        value = load_device_credential(self.credential_file, self.url)
        return str(value.get("credential") or "") if value else ""

    def device_login(self, action: str, device_name: str | None = None) -> dict[str, Any]:
        if action == "start":
            current = load_device_credential(self.credential_file, self.url)
            if current:
                status, health = request_json(
                    self.url, "GET", "/api/health", credential=current["credential"]
                )
                if status == 200 and not health.get("authentication_required"):
                    return {
                        "status": "connected", "user": current.get("user"),
                        "device": current.get("device"),
                    }
            value = start_login(self.url, device_name or socket.gethostname())
            save_pending_login(self.credential_file, self.url, value)
            # 服务端刻意不再返回 verification_uri_complete：能被转发的一键批准链接
            # 就能被用来钓鱼（RFC 8628 §5.4）。这里只给裸 URI 和验证码，
            # 由本人手工输入。以前这行是硬取，服务端去掉该键之后每次 start 都 KeyError。
            return {
                "status": "approval_required",
                "verification_uri": value["verification_uri"],
                "user_code": value["user_code"],
                "device_name": value.get("device_name"),
                "expires_in": value.get("expires_in"),
                "next": (
                    f"Tell the user to open {value['verification_uri']} and type the code "
                    f"{value['user_code']} there by hand. There is no clickable approval link; "
                    "never approve a code somebody else sent you. Then call trace_login with "
                    "action=status."
                ),
            }
        pending = load_pending_login(self.credential_file, self.url)
        if not pending:
            current = load_device_credential(self.credential_file, self.url)
            if current:
                status, health = request_json(
                    self.url, "GET", "/api/health", credential=current["credential"]
                )
                if status == 200 and not health.get("authentication_required"):
                    return {
                        "status": "connected", "user": current.get("user"),
                        "device": current.get("device"),
                        "expires_at": current.get("expires_at"),
                    }
                return {"status": "disconnected", "next": "Call trace_login with action=start."}
            return {"status": "not_started", "next": "Call trace_login with action=start."}
        value = poll_login(self.url, pending["device_code"])
        if value.get("status") == "authorized":
            save_device_credential(self.credential_file, self.url, value)
            clear_pending_login(self.credential_file, self.url)
            expires_at = value.get("expires_at") or (value.get("device") or {}).get("expires_at")
            return {
                "status": "connected", "user": value.get("user"), "device": value.get("device"),
                "expires_at": expires_at,
                "next": (
                    "This credential expires; renew it on the command line with "
                    "`trace-login --renew` before then."
                ) if expires_at else None,
            }
        return {
            "status": "pending",
            "user_code": pending.get("user_code"),
            "verification_uri": pending.get("verification_uri"),
            "next": "The user still has to type the code at the verification URI.",
        }

    def request(self, method: str, path: str, value: dict[str, Any] | None = None) -> Any:
        data = None if value is None else json.dumps(value, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        token = self.auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(raw).get("error") or json.loads(raw).get("detail") or raw
            except ValueError:
                message = raw
            hint = " Run trace-login or trace_login to reconnect." if exc.code == 401 else ""
            raise RuntimeError(f"Research Trace HTTP {exc.code}: {message}.{hint}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Research Trace unavailable at {self.url}: {exc.reason}") from exc


def _manifest_source(root: Path, rel: str, alternates: tuple[str, ...]) -> Path | None:
    """把 manifest 里的相对路径解析成 session 目录下的一个真实文件。

    manifest 写的是 `pending/x.json`，但投递器随时可能在这之后把同一个文件搬进
    `sent/`——它按中央 2xx 决定去向，跟语义层无关。所以解析不到时按 basename 在
    `sent/` 与 `transcripts/sent/` 里回退找一次；两边都没有才算真的缺失。
    session 目录之外的路径任何时候都拒绝（manifest 里的路径不可信）。
    """
    candidates = [root / rel] + [root / alt / Path(rel).name for alt in alternates]
    for candidate in candidates:
        resolved = candidate.resolve()
        if root in resolved.parents and resolved.is_file():
            return resolved
    return None


def _manifest_payload(path_value: str, project_id: str | None = None) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent.parent
    if path.parent.name == "done":  # batches/done/<id>.json 比 batches/<id>.json 深一层
        root = root.parent
    root = root.resolve()
    missing: list[str] = []
    events = []
    for item in manifest.get("events") or []:
        rel = item.get("path") if isinstance(item, dict) else item
        source = _manifest_source(root, str(rel), ("sent",))
        if source is None:
            # 缺一条不该让整批重投失败：这个工具现在只用于事后手工补录，
            # 抛异常等于让归档的 manifest 永远不可重投（旧实现就是这样）。
            missing.append(str(rel))
            continue
        try:
            events.append(json.loads(source.read_text(encoding="utf-8")))
        except ValueError:
            missing.append(str(rel))
    chunks = []
    for item in manifest.get("transcript_chunks") or []:
        if isinstance(item, dict):
            rel = item.get("path")
            metadata = dict(item)
        else:
            rel = item
            metadata = {}
        source = _manifest_source(root, str(rel), ("transcripts/sent", "transcripts/pending"))
        if source is None:
            missing.append(str(rel))
            continue
        metadata.pop("path", None)
        metadata["content"] = source.read_text(encoding="utf-8", errors="replace")
        chunks.append(metadata)
    session_id = manifest.get("session_id")
    agents: dict[str, dict[str, Any]] = {}
    for event in events:
        agent_id = event.get("agent_id")
        if agent_id:
            agents[str(agent_id)] = {
                "id": str(agent_id),
                "session_id": event.get("session_id") or session_id,
                "agent_type": event.get("agent_type"),
            }
    if not events and not chunks and missing:
        raise RuntimeError(
            f"manifest {path} references {len(missing)} file(s) that no longer exist: "
            + ", ".join(missing[:5])
        )
    return {
        "batch_id": manifest.get("batch_id"),
        "project_id": project_id or manifest.get("project_id"),
        "session": {
            "id": session_id,
            "source": "claude-code",
            "cwd": manifest.get("project_dir"),
            "metadata": {"manifest": str(path), "missing_files": missing} if missing
            else {"manifest": str(path)},
        } if session_id else None,
        "agents": list(agents.values()),
        "events": events,
        "transcript_chunks": chunks,
    }


def _bind_marker(bind_path: str, result: dict[str, Any], requested_keys: list[str]) -> dict[str, Any]:
    """把解析出来的项目写成目录里的 opt-in marker（§7）。

    marker 同时是采集开关和项目身份，所以只在用户明确要求绑定时才写：agent 不得
    替用户决定录哪个目录。写入是合并式的，不会覆盖 marker 里别人放的键。
    """
    from .deliver import git_remote_key, new_workspace_key, read_marker, write_marker

    project = result.get("project") if isinstance(result.get("project"), dict) else result
    existing = read_marker(Path(bind_path).expanduser() / ".research-trace.json")
    key = str(existing.get("workspace_key") or "").strip()
    if not key:
        key = next((item for item in requested_keys if str(item).startswith("rt-ws-")), "")
    extra = [item for item in requested_keys if item != key]
    remote_key = git_remote_key(bind_path)
    if remote_key and remote_key != key and remote_key not in extra:
        extra.append(remote_key)
    target = write_marker(
        bind_path,
        workspace_key=key or new_workspace_key(),
        workspace_keys=extra,
        project_id=str(project.get("id") or "") or None,
        project_name=str(project.get("name") or "") or None,
        capture=True,
    )
    return {"marker_path": str(target), "marker": read_marker(target)}


def call_tool(remote: Remote, name: str, args: dict[str, Any]) -> Any:
    if name == "trace_context":
        value = dict(args)
        bind_path = str(value.pop("bind_path", "") or "").strip()
        result = remote.request("POST", "/api/context", value)
        if value.get("include_dataflow") and isinstance(result, dict):
            project = result.get("project")
            if isinstance(project, dict) and "dataflow" not in project:
                # "空图"和"这台中央服务还不会算数据流"在响应里长得一模一样，而 §8 说
                # 空图是正常的——不说破就会让 agent 把后者报成"这个项目没有产物关系"。
                result = dict(result)
                result["dataflow_unavailable"] = (
                    "this central service did not return a dataflow view; it is older than the "
                    "derived data-flow query. Do not report this as 'no artifact relations'."
                )
        if bind_path and isinstance(result, dict) and result.get("matched") is not False:
            result = dict(result)
            result["bound"] = _bind_marker(
                bind_path, result, [str(k) for k in (value.get("workspace_keys") or [])]
            )
        return result
    if name == "trace_ingest":
        value = _manifest_payload(args["manifest_path"], args.get("project_id")) if args.get("manifest_path") else args
        return remote.request("POST", "/api/ingest", value)
    if name == "trace_record":
        # created_by / review_state 不再由这里写：服务端只从凭证推身份，请求体被忽略。
        value = dict(args)
        value.pop("chapter_name", None)
        return remote.request("POST", "/api/record", value)
    if name == "trace_curate":
        return remote.request("POST", "/api/curate", args)
    if name == "trace_attach":
        value = dict(args)
        local_path = value.pop("local_path", None)
        if local_path:
            raw = Path(local_path).expanduser().resolve().read_bytes()
            value["data_base64"] = base64.b64encode(raw).decode("ascii")
            value.setdefault("name", Path(local_path).name)
            value.setdefault("size", len(raw))
        return remote.request("POST", "/api/attach", value)
    if name == "trace_search":
        query = urllib.parse.urlencode({
            key: value for key, value in {
                "q": args.get("query"),
                "project_id": args.get("project_id"),
                "scope": args.get("scope", "all"),
                "limit": args.get("limit", 50),
            }.items() if value is not None
        })
        return remote.request("GET", "/api/search?" + query)
    if name == "trace_login":
        return remote.device_login(args.get("action") or "start", args.get("device_name"))
    raise RuntimeError(f"unknown tool: {name}")


def _response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        value["error"] = error
    else:
        value["result"] = result
    return value


def force_utf8_stdio() -> None:
    """把协议通道两端钉死在 UTF-8。

    Claude Code 按 UTF-8 写 stdin，但 Windows 上的 Python 默认按本地 code page
    (cp936/cp1252) 解码，于是中文 Node 标题在进入 call_tool 之前就已经是乱码，
    再被原样 POST 进中央库——落库之后没有任何办法还原。stdout 侧顺便关掉
    \\n → \\r\\n 转换，免得协议行尾多出一个字节。
    """
    for stream, extra in (
        (sys.stdin, {"errors": "replace"}),
        (sys.stdout, {"newline": "\n"}),
        (sys.stderr, {"errors": "replace"}),
    ):
        try:
            stream.reconfigure(encoding="utf-8", **extra)
        except (AttributeError, OSError, ValueError):  # 被替换成非文本流时忽略
            pass


def _is_request_id(value: Any) -> bool:
    # JSON-RPC 允许 string/number；MCP 在其上收紧，禁止 null。bool 是 int 的子类，单独挡掉。
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def handle(remote: Remote, message: Any) -> dict[str, Any] | None:
    """处理一条消息，返回要发回去的响应；通知返回 None。"""
    if not isinstance(message, dict):
        return _response(None, error={"code": INVALID_REQUEST, "message": "request must be a JSON object"})
    has_id = "id" in message
    request_id = message.get("id")
    if has_id and not _is_request_id(request_id):
        # id 为 null 的"请求"官方客户端解析不了；只有错误响应可以带 id: null。
        return _response(None, error={"code": INVALID_REQUEST, "message": "request id must be a string or integer"})
    if not has_id:
        # JSON-RPC §4.1：通知不回复。更重要的是不执行——一条没有 id 的 tools/call
        # 曾经会照常写入中央库，调用方却拿不到任何回执。
        return None
    method = message.get("method")
    if not isinstance(method, str) or not method:
        return _response(request_id, error={"code": INVALID_REQUEST, "message": "method must be a non-empty string"})
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _response(request_id, error={"code": INVALID_PARAMS, "message": "params must be an object"})

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _response(request_id, result={
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        # 客户端用 ping 判断进程还活着；缺了它这个连接会被当成挂死然后重启。
        return _response(request_id, result={})
    if method == "tools/list":
        return _response(request_id, result={"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(name, str) or not name:
            return _response(request_id, error={"code": INVALID_PARAMS, "message": "tools/call requires a tool name"})
        if not isinstance(args, dict):
            return _response(request_id, error={"code": INVALID_PARAMS, "message": "tools/call arguments must be an object"})
        try:
            value = call_tool(remote, name, args)
        except Exception as exc:
            # 工具内部的失败一律 isError。回成 JSON-RPC error 会被客户端当成传输故障
            # 抛给上层，模型既看不到原因也没法自己纠正。
            return _response(request_id, result={
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
        return _response(request_id, result={
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
            "isError": False,
        })
    return _response(request_id, error={"code": METHOD_NOT_FOUND, "message": f"method not found: {method}"})


def serve(remote: Remote, stream_in: Any = None, stream_out: Any = None) -> int:
    if stream_in is None and stream_out is None:
        force_utf8_stdio()
    source = stream_in if stream_in is not None else sys.stdin
    sink = stream_out if stream_out is not None else sys.stdout

    def emit(value: Any) -> None:
        # ensure_ascii=True：输出行全是 ASCII，任何控制台/管道编码都改不了它的内容。
        sink.write(json.dumps(value, ensure_ascii=True) + "\n")
        sink.flush()

    def dispatch(message: Any) -> dict[str, Any] | None:
        try:
            return handle(remote, message)
        except Exception as exc:  # handle 自己不该抛，抛了也不能让连接断掉
            request_id = message.get("id") if isinstance(message, dict) else None
            return _response(
                request_id if _is_request_id(request_id) else None,
                error={"code": INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"},
            )

    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            # RecursionError 不是 ValueError 的子类：嵌套上万层的数组会逃出去掀翻整个循环。
            emit(_response(None, error={"code": PARSE_ERROR, "message": "invalid JSON"}))
            continue
        if isinstance(message, list):
            if not message:
                emit(_response(None, error={"code": INVALID_REQUEST, "message": "batch must not be empty"}))
                continue
            responses = [value for value in (dispatch(item) for item in message) if value is not None]
            if responses:  # 整批都是通知时不回任何东西
                emit(responses)
            continue
        response = dispatch(message)
        if response is not None:
            emit(response)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Trace MCP server")
    parser.add_argument("--url", default=os.environ.get("TRACE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("TRACE_TOKEN", ""))
    parser.add_argument("--credential-file", default=os.environ.get("TRACE_CREDENTIAL_FILE"))
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args(argv)
    force_utf8_stdio()
    remote = Remote(args.url, args.token, args.credential_file)
    if args.selfcheck:
        try:
            health = remote.request("GET", "/api/health")
            print(json.dumps(health, ensure_ascii=False, indent=2))
            if (health.get("write_protected") or health.get("authentication_required")) and not remote.auth_token():
                print("device login or legacy write token is required", file=sys.stderr)
                return 1
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
    return serve(remote)


if __name__ == "__main__":
    raise SystemExit(main())
