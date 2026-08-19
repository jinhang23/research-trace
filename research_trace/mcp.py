"""Minimal stdio MCP client for the Research Trace v2 central HTTP service."""

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
SERVER_INFO = {"name": "research-trace", "version": "2.0.0-alpha.4"}
INSTRUCTIONS = (
    "Research Trace has a raw-history layer and a selective semantic layer. "
    "Use trace_ingest for immutable hook batches. Use trace_record only for work worth understanding "
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
            },
        },
    },
    {
        "name": "trace_ingest",
        "description": "Idempotently upload one raw hook batch. Prefer manifest_path so raw events do not pass through model context.",
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
                "actor_type": {"type": "string", "default": "recorder"},
                "actor_id": {"type": "string"},
                "source_event_ids": {"type": "array", "items": {"type": "string"}},
                "milestone": {"type": "boolean"},
                "resolve_comment_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "trace_attach",
        "description": "Attach a small file to Overview/Chapter/Node or register an external input/output/reference artifact.",
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
    {
        "name": "trace_search",
        "description": "Search semantic records and/or permanent raw history across projects.",
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
                    self.url, "GET", "/api/v2/health", credential=current["credential"]
                )
                if status == 200 and not health.get("authentication_required"):
                    return {
                        "status": "connected", "user": current.get("user"),
                        "device": current.get("device"),
                    }
            value = start_login(self.url, device_name or socket.gethostname())
            save_pending_login(self.credential_file, self.url, value)
            return {
                "status": "approval_required",
                "verification_uri": value["verification_uri"],
                "verification_uri_complete": value["verification_uri_complete"],
                "user_code": value["user_code"],
                "device_name": value["device_name"],
                "expires_in": value["expires_in"],
                "next": "Open the URL, approve with GitHub, then call trace_login with action=status.",
            }
        pending = load_pending_login(self.credential_file, self.url)
        if not pending:
            current = load_device_credential(self.credential_file, self.url)
            if current:
                status, health = request_json(
                    self.url, "GET", "/api/v2/health", credential=current["credential"]
                )
                if status == 200 and not health.get("authentication_required"):
                    return {
                        "status": "connected", "user": current.get("user"),
                        "device": current.get("device"),
                    }
                return {"status": "disconnected", "next": "Call trace_login with action=start."}
            return {"status": "not_started", "next": "Call trace_login with action=start."}
        value = poll_login(self.url, pending["device_code"])
        if value.get("status") == "authorized":
            save_device_credential(self.credential_file, self.url, value)
            clear_pending_login(self.credential_file, self.url)
            return {
                "status": "connected", "user": value.get("user"), "device": value.get("device"),
            }
        return {
            "status": "pending", "user_code": pending.get("user_code"),
            "verification_uri_complete": pending.get("verification_uri_complete"),
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


def _manifest_payload(path_value: str, project_id: str | None = None) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent.parent
    events = []
    for item in manifest.get("events") or []:
        rel = item.get("path") if isinstance(item, dict) else item
        source = (root / str(rel)).resolve()
        if root not in source.parents or not source.is_file():
            raise RuntimeError(f"unsafe or missing event path in manifest: {rel}")
        events.append(json.loads(source.read_text(encoding="utf-8")))
    chunks = []
    for item in manifest.get("transcript_chunks") or []:
        if isinstance(item, dict):
            rel = item.get("path")
            metadata = dict(item)
        else:
            rel = item
            metadata = {}
        source = (root / str(rel)).resolve()
        if root not in source.parents or not source.is_file():
            raise RuntimeError(f"unsafe or missing transcript path in manifest: {rel}")
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
    return {
        "batch_id": manifest.get("batch_id"),
        "project_id": project_id or manifest.get("project_id"),
        "session": {
            "id": session_id,
            "source": "claude-code",
            "cwd": manifest.get("project_dir"),
            "metadata": {"manifest": str(path)},
        } if session_id else None,
        "agents": list(agents.values()),
        "events": events,
        "transcript_chunks": chunks,
    }


def call_tool(remote: Remote, name: str, args: dict[str, Any]) -> Any:
    if name == "trace_context":
        return remote.request("POST", "/api/v2/context", args)
    if name == "trace_ingest":
        value = _manifest_payload(args["manifest_path"], args.get("project_id")) if args.get("manifest_path") else args
        return remote.request("POST", "/api/v2/ingest", value)
    if name == "trace_record":
        value = dict(args)
        value.pop("chapter_name", None)
        value["created_by"] = "recorder"
        value["review_state"] = "unreviewed"
        return remote.request("POST", "/api/v2/record", value)
    if name == "trace_curate":
        return remote.request("POST", "/api/v2/curate", args)
    if name == "trace_attach":
        value = dict(args)
        local_path = value.pop("local_path", None)
        if local_path:
            raw = Path(local_path).expanduser().resolve().read_bytes()
            value["data_base64"] = base64.b64encode(raw).decode("ascii")
            value.setdefault("name", Path(local_path).name)
            value.setdefault("size", len(raw))
        return remote.request("POST", "/api/v2/attach", value)
    if name == "trace_search":
        query = urllib.parse.urlencode({
            key: value for key, value in {
                "q": args.get("query"),
                "project_id": args.get("project_id"),
                "scope": args.get("scope", "all"),
                "limit": args.get("limit", 50),
            }.items() if value is not None
        })
        return remote.request("GET", "/api/v2/search?" + query)
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


def serve(remote: Remote) -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": INSTRUCTIONS,
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                try:
                    value = call_tool(remote, params.get("name"), params.get("arguments") or {})
                    result = {
                        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
                        "isError": False,
                    }
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            elif method in {"notifications/initialized", "notifications/cancelled"}:
                continue
            elif request_id is None:
                continue
            else:
                print(json.dumps(_response(request_id, error={"code": -32601, "message": f"method not found: {method}"})), flush=True)
                continue
            if request_id is not None:
                print(json.dumps(_response(request_id, result=result), ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps(_response(None, error={"code": -32603, "message": str(exc)})), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Trace v2 MCP server")
    parser.add_argument("--url", default=os.environ.get("TRACE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("TRACE_TOKEN", ""))
    parser.add_argument("--credential-file", default=os.environ.get("TRACE_CREDENTIAL_FILE"))
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args(argv)
    remote = Remote(args.url, args.token, args.credential_file)
    if args.selfcheck:
        try:
            health = remote.request("GET", "/api/v2/health")
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
