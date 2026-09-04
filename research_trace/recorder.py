"""Independent, subscription-only semantic Recorder.

The worker shape, output classification and quota pause state are adapted from
Claude-Mem at be44b6c8e238a7e2bc5b3403c05afac071a59ead (Apache-2.0).  Research
Trace keeps its own durable outbox and Node model; this module replaces the old
main-session fork with an isolated Claude Code CLI conversation.

The process deliberately has no tools or project settings.  It receives a
bounded evidence packet on stdin, returns structured JSON, and ordinary Python
code performs the idempotent writes.  Authentication is accepted only from a
Claude subscription login.  API-key and cloud-provider environments stop the
worker before a model request is made.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from filelock import FileLock, Timeout

from .deliver import long_path, project_binding, read_marker
from .mcp import Remote, _manifest_payload


STATE_SCHEMA = "research-trace.recorder-state.v1"
BATCH_STATE_SCHEMA = "research-trace.recorder-batch-state.v1"
DEFAULT_MODEL = "sonnet"
ALLOWED_MODELS = frozenset({"sonnet", "haiku"})
SESSION_TURNS = 12
MODEL_TIMEOUT = 900.0
MAX_EVIDENCE_CHARS = 52_000
MAX_CONTEXT_CHARS = 18_000
MAX_RECORDS = 8

# These variables can make the official CLI bill an API/cloud account instead
# of the logged-in Claude subscription.  Refuse ambiguity rather than deleting
# them and silently changing the operator's authentication route.
PAID_CREDENTIAL_ENV = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
})

QUOTA_PATTERNS = (
    "usage limit", "rate limit", "hit your limit", "quota exceeded",
    "credit balance", "resets at",
)
AUTH_PATTERNS = ("not logged in", "authentication", "unauthorized", "invalid api key")


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "records", "curations"],
    "properties": {
        "status": {"type": "string", "enum": ["record", "skip"]},
        "records": {
            "type": "array", "maxItems": MAX_RECORDS,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["title", "body", "source_event_ids"],
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                    "body": {"type": "string", "minLength": 1, "maxLength": 12_000},
                    "chapter_id": {"type": ["string", "null"]},
                    "parent_id": {"type": ["string", "null"]},
                    "labels": {"type": "array", "maxItems": 12, "items": {"type": "string"}},
                    "run_ids": {"type": "array", "maxItems": 100, "items": {"type": "string"}},
                    "source_event_ids": {
                        "type": "array", "minItems": 1, "maxItems": 100,
                        "items": {"type": "string"},
                    },
                    "occurred_at": {"type": ["string", "null"]},
                    "code_evidence": {
                        "type": "array", "maxItems": 12,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["file_path"],
                            "properties": {
                                "repo_url": {"type": ["string", "null"]},
                                "commit_hash": {"type": ["string", "null"]},
                                "file_path": {"type": "string"},
                                "symbol": {"type": ["string", "null"]},
                                "start_line": {"type": ["integer", "null"]},
                                "end_line": {"type": ["integer", "null"]},
                                "snippet": {"type": ["string", "null"]},
                                "diff": {"type": ["string", "null"]},
                                "annotation": {"type": ["string", "null"]},
                                "content_sha256": {"type": ["string", "null"]},
                                "attribution": {
                                    "type": ["string", "null"],
                                    "enum": ["exact", "reported", "ambiguous", "unknown", None],
                                },
                                "contributor_agent_ids": {
                                    "type": "array", "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
        "curations": {
            "type": "array", "maxItems": 2,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["target_type", "body", "expect_version", "source_event_ids"],
                "properties": {
                    "target_type": {"type": "string", "enum": ["overview", "chapter"]},
                    "target_id": {"type": ["string", "null"]},
                    "body": {"type": "string", "minLength": 1, "maxLength": 20_000},
                    "expect_version": {"type": "integer", "minimum": 0},
                    "source_event_ids": {
                        "type": "array", "minItems": 1, "maxItems": 100,
                        "items": {"type": "string"},
                    },
                    "resolve_comment_ids": {
                        "type": "array", "maxItems": 100, "items": {"type": "string"},
                    },
                    "milestone": {"type": "boolean"},
                },
            },
        },
    },
}


SYSTEM_PROMPT = """You are the independent Research Trace Recorder. Edit memory for a person or
agent returning months later. Observe only the evidence packet in the user message. Do not run
commands, use tools, investigate, or direct the research.

Select durable research meaning, not a diary of operations. A record may capture a finding and its
limits, a decision and rationale, a useful failed attempt, an untested direction with a proposed
check, or a meaningful implementation and its validation state. Group related experiments by the
research question. Routine edits, listings, installs, repeated status checks and already-known facts
usually produce no record. Zero records is a valid successful result.

Write concise connected prose in the evidence's original language. Name the concrete variant,
dataset, metric, split and configuration when known. Keep observation, inference, hypothesis, user
decision, proposed work and agreed work distinguishable. A submitted job is not a result; one failed
run does not disprove a scientific hypothesis. Never manufacture a result, causal explanation,
predecessor, code version, run, or reason an idea was deferred.

Use only chapter IDs, parent IDs, run IDs and event IDs listed in the packet. Omit an unknown chapter
for Inbox and omit an unknown parent. Every record needs at least one event ID from NEW EVIDENCE that
directly supports it. Existing memory and corrections are context, never new evidence. Human
corrections have highest authority. Curate an Overview or Chapter summary only when the current
research understanding materially changed; keep it concise and retain unresolved issues. Return
only the JSON required by the supplied schema."""


class RecorderError(RuntimeError):
    """A retryable or operator-actionable Recorder failure."""

    def __init__(self, message: str, *, kind: str = "error", retry_at: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_at = retry_at


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _clip(value: Any, limit: int) -> Any:
    """Bound model input while retaining the beginning and final result/error."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    head = max(1, int(limit * 0.62))
    tail = max(1, limit - head - 80)
    return value[:head] + f"\n… [{len(value) - head - tail} characters omitted] …\n" + value[-tail:]


def _bounded(value: Any, limit: int) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(raw) <= limit:
        return raw
    # Keep the packet valid JSON even after compaction. A raw head/tail cut can
    # end inside a quoted command or object and makes IDs/field boundaries
    # ambiguous to the model.
    room = max(200, limit - 180)
    while True:
        wrapped = json.dumps({
            "truncated": True,
            "original_characters": len(raw),
            "json_excerpt": _clip(raw, room),
        }, ensure_ascii=False, sort_keys=True)
        if len(wrapped) <= limit or room <= 200:
            return wrapped[:limit]
        room = max(200, room - (len(wrapped) - limit) - 16)


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            try:
                return _epoch(float(text))
            except ValueError:
                return None
    return None


def classify_cli_output(stdout: str, stderr: str, returncode: int) -> tuple[str, Any, float | None]:
    """Classify CLI output using Claude-Mem's explicit error-first approach."""
    messages: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            messages.append(value)

    reset_at: float | None = None
    overage = False
    quota_rejected = False
    for item in _walk(messages):
        for key in ("resetsAt", "resets_at", "resetAt", "reset_at"):
            reset_at = _epoch(item.get(key)) or reset_at
        if item.get("isUsingOverage") is True or item.get("is_using_overage") is True:
            overage = True
        kind = str(item.get("type") or "").lower()
        status = str(item.get("status") or item.get("rateLimitStatus") or "").lower()
        if "rate_limit" in kind and status in {"rejected", "blocked", "exhausted", "rate_limited"}:
            quota_rejected = True
    combined = (stdout + "\n" + stderr).lower()
    if overage:
        return "overage", "Claude CLI reported extra-usage/overage billing", reset_at
    if quota_rejected or any(pattern in combined for pattern in QUOTA_PATTERNS):
        return "quota", _clip((stderr or stdout).strip(), 1200), reset_at
    if returncode and any(pattern in combined for pattern in AUTH_PATTERNS):
        return "auth", _clip((stderr or stdout).strip(), 1200), None

    candidate: Any = None
    for item in reversed(messages):
        if isinstance(item.get("structured_output"), dict):
            candidate = item["structured_output"]
            break
        if item.get("type") == "result" and item.get("is_error"):
            error_text = str(item.get("result") or item.get("error") or "Claude CLI error")
            return ("quota" if any(p in error_text.lower() for p in QUOTA_PATTERNS) else "error"), error_text, reset_at
        if item.get("type") == "result" and item.get("result") is not None:
            candidate = item.get("result")
            break
    if isinstance(candidate, str):
        text = candidate.strip()
        try:
            candidate = json.loads(text)
        except ValueError:
            match = re.search(r"\{.*\}", text, re.S)
            if match:
                try:
                    candidate = json.loads(match.group(0))
                except ValueError:
                    pass
    if returncode:
        return "error", _clip((stderr or stdout or f"Claude exited {returncode}").strip(), 1200), None
    if candidate is None:
        return "empty", "Claude returned no structured Recorder output", None
    if not isinstance(candidate, dict):
        return "malformed", "Recorder output is not a JSON object", None
    return "success", candidate, None


def extract_usage(stdout: str) -> dict[str, int]:
    """Return cache/token counters from the final CLI result when available."""
    result: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict) or value.get("type") != "result":
            continue
        usage = value.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in (
            "input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            try:
                result[key] = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                result[key] = 0
    return result


def subscription_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    present = sorted(key for key in PAID_CREDENTIAL_ENV if str(env.get(key) or "").strip())
    if present:
        raise RecorderError(
            "paid/API authentication environment is present: " + ", ".join(present),
            kind="paid_credentials",
        )
    # Keep the Claude subscription OAuth token or the CLI's keychain login.  Do
    # not pass Git context: the Recorder is unrelated to a repository checkout.
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def verify_subscription_auth(
    executable: str, env: dict[str, str], cwd: Path, *, timeout: float = 30.0,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [executable, "auth", "status", "--json"], cwd=str(cwd), env=env,
            text=True, capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecorderError(f"cannot inspect Claude subscription login: {exc}", kind="auth") from exc
    try:
        value = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise RecorderError("Claude auth status was not JSON", kind="auth") from exc
    method = str(value.get("authMethod") or value.get("auth_method") or "").lower()
    if result.returncode or value.get("loggedIn") is not True:
        raise RecorderError("Claude Code is not logged in", kind="auth")
    if not any(name in method for name in ("claude.ai", "oauth", "subscription")):
        raise RecorderError(
            f"Claude auth method {method or '<unknown>'!r} is not a confirmed subscription login",
            kind="auth",
        )
    return {
        "logged_in": True,
        "auth_method": method,
        "subscription_type": value.get("subscriptionType") or value.get("subscription_type"),
    }


def _event_packet(event: dict[str, Any], payload_limit: int = 8_000) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    # Keep a valid outer packet while bounding arbitrary nested command output.
    # A clipped JSON string remains readable evidence; parsing it back would be
    # invalid because the omission marker can fall inside a JSON token.
    return {
        "event_id": event.get("event_id"),
        "captured_at": event.get("captured_at"),
        "hook_event": event.get("hook_event"),
        "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "payload_json": _bounded(payload, payload_limit),
    }


def _transcript_packet(chunks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    remaining = 14_000
    for chunk in chunks:
        if remaining <= 0:
            break
        content = str(chunk.get("content") or "")
        item = {key: value for key, value in chunk.items() if key != "content"}
        item["content"] = _clip(content, min(remaining, 7_000))
        remaining -= len(str(item["content"]))
        result.append(item)
    return result


def _semantic_hit(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _clip(value, 1600) if isinstance(value, str) else value
        for key, value in item.items()
        if key in {"id", "scope", "title", "body", "chapter_id", "parent_id", "occurred_at", "labels"}
    }


def _context_packet(context: dict[str, Any], related: list[dict[str, Any]]) -> dict[str, Any]:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    value = {
        "project": {
            "id": project.get("id"), "name": project.get("name"),
            "overview": _clip(project.get("overview") or "", 4_000),
            "overview_version": project.get("overview_version"),
        },
        "chapters": [
            {"id": x.get("id"), "name": x.get("name"),
             "summary": _clip(x.get("summary") or "", 1800),
             "summary_version": x.get("summary_version")}
            for x in (project.get("chapters") or []) if isinstance(x, dict)
        ],
        "recent_nodes": [
            _semantic_hit(x) for x in (project.get("recent_nodes") or []) if isinstance(x, dict)
        ],
        "related_old_records": [_semantic_hit(x) for x in related if isinstance(x, dict)],
        "unresolved_human_corrections": [
            {key: _clip(val, 1800) if isinstance(val, str) else val
             for key, val in x.items() if key in {"id", "target_type", "target_id", "body", "kind"}}
            for x in (project.get("unresolved_corrections") or []) if isinstance(x, dict)
        ],
        "recent_runs": [
            {key: val for key, val in x.items()
             if key in {"id", "command", "status", "job_id", "wandb_url", "code_commit", "created_at"}}
            for x in ((context.get("recent_runs") or project.get("recent_runs") or [])) if isinstance(x, dict)
        ],
    }
    return value


def _search_query(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("hook_event") != "UserPromptSubmit":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not payload and isinstance(event.get("payload_json"), str):
            try:
                payload = json.loads(event["payload_json"])
            except ValueError:
                payload = {}
        text = str(payload.get("prompt") or "").strip()
        if text:
            return re.sub(r"\s+", " ", text)[:240]
    return ""


def build_prompt(
    manifest: dict[str, Any], material: dict[str, Any], context: dict[str, Any], related: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    raw_events = [x for x in (material.get("events") or []) if isinstance(x, dict)]
    per_event = max(320, min(8_000, 34_000 // max(1, len(raw_events))))
    events = [_event_packet(x, per_event) for x in raw_events]
    packet = {
        "batch": {
            "batch_id": manifest.get("batch_id"), "session_id": manifest.get("session_id"),
            "created_at": manifest.get("created_at"), "project_id": manifest.get("project_id"),
        },
        "existing_memory": _context_packet(context, related),
        "new_evidence": {
            "events": events,
            "visible_transcript_chunks": _transcript_packet(material.get("transcript_chunks") or []),
        },
    }
    evidence = _bounded(packet["new_evidence"], MAX_EVIDENCE_CHARS)
    prompt = (
        "Compare NEW EVIDENCE with EXISTING MEMORY. Return zero or more durable research records. "
        "Existing memory supplies context only; source_event_ids must come from NEW EVIDENCE.\n\n"
        "BATCH AND EXISTING MEMORY\n" + _bounded({"batch": packet["batch"], "existing_memory": packet["existing_memory"]}, MAX_CONTEXT_CHARS + 2000)
        + "\n\nNEW EVIDENCE\n" + evidence
    )
    return prompt, packet


def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def validate_plan(output: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    status = output.get("status")
    records = output.get("records")
    if status not in {"record", "skip"} or not isinstance(records, list):
        raise RecorderError("Recorder output needs status=record|skip and a records array", kind="format")
    curations = output.get("curations") or []
    if status == "skip" and (records or curations):
        raise RecorderError("status=skip cannot contain records or curations", kind="format")
    if status == "record" and not records and not curations:
        raise RecorderError("status=record needs at least one record or curation", kind="format")
    if len(records) > MAX_RECORDS:
        raise RecorderError(f"Recorder returned more than {MAX_RECORDS} records", kind="format")

    evidence_events = packet["new_evidence"]["events"]
    event_ids = {str(x.get("event_id")) for x in evidence_events if x.get("event_id")}
    memory = packet["existing_memory"]
    chapter_ids = {str(x.get("id")) for x in memory.get("chapters") or [] if x.get("id")}
    nodes = [*(memory.get("recent_nodes") or []), *(memory.get("related_old_records") or [])]
    node_chapters = {str(x.get("id")): x.get("chapter_id") for x in nodes if x.get("id")}
    run_ids = {str(x.get("id")) for x in memory.get("recent_runs") or [] if x.get("id")}

    clean: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise RecorderError(f"record {index} is not an object", kind="format")
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if not title or len(title) > 240 or not body or len(body) > 12_000:
            raise RecorderError(f"record {index} has an invalid title/body", kind="format")
        sources = sorted({str(x) for x in item.get("source_event_ids") or [] if str(x)})
        if not sources or not set(sources) <= event_ids:
            raise RecorderError(f"record {index} cites an event outside this batch", kind="format")
        chapter = _clean_optional(item.get("chapter_id"))
        if chapter and chapter not in chapter_ids:
            raise RecorderError(f"record {index} uses an unknown chapter_id", kind="format")
        parent = _clean_optional(item.get("parent_id"))
        if parent and parent not in node_chapters:
            raise RecorderError(f"record {index} uses an unknown parent_id", kind="format")
        if parent and node_chapters[parent] != chapter:
            raise RecorderError(f"record {index} parent is not in the selected Chapter", kind="format")
        requested_runs = sorted({str(x) for x in item.get("run_ids") or [] if str(x)})
        if not set(requested_runs) <= run_ids:
            raise RecorderError(f"record {index} uses an unknown run_id", kind="format")
        code = item.get("code_evidence") or []
        if not isinstance(code, list) or any(not isinstance(x, dict) or not x.get("file_path") for x in code):
            raise RecorderError(f"record {index} has invalid code_evidence", kind="format")
        clean.append({
            "title": title, "body": body, "chapter_id": chapter, "parent_id": parent,
            "labels": sorted({str(x).strip() for x in item.get("labels") or [] if str(x).strip()}),
            "run_ids": requested_runs, "source_event_ids": sources,
            "occurred_at": _clean_optional(item.get("occurred_at")), "code_evidence": code,
        })
    return clean


def validate_curations(output: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    curations = output.get("curations") or []
    if not isinstance(curations, list) or len(curations) > 2:
        raise RecorderError("Recorder output has an invalid curations array", kind="format")
    evidence_ids = {
        str(x.get("event_id")) for x in packet["new_evidence"]["events"] if x.get("event_id")
    }
    memory = packet["existing_memory"]
    project = memory.get("project") or {}
    chapters = {
        str(x.get("id")): x for x in memory.get("chapters") or [] if isinstance(x, dict) and x.get("id")
    }
    corrections = {
        str(x.get("id")): x for x in memory.get("unresolved_human_corrections") or []
        if isinstance(x, dict) and x.get("id")
    }
    clean: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str | None]] = set()
    for index, item in enumerate(curations):
        if not isinstance(item, dict) or item.get("target_type") not in {"overview", "chapter"}:
            raise RecorderError(f"curation {index} has an invalid target", kind="format")
        target_type = str(item["target_type"])
        target_id = _clean_optional(item.get("target_id"))
        if target_type == "overview":
            target_id = None
            actual_version = int(project.get("overview_version") or 0)
        elif target_id not in chapters:
            raise RecorderError(f"curation {index} uses an unknown Chapter", kind="format")
        else:
            actual_version = int(chapters[target_id].get("summary_version") or 0)
        expected = item.get("expect_version")
        if not isinstance(expected, int) or expected != actual_version:
            raise RecorderError(f"curation {index} does not use the current summary version", kind="format")
        body = str(item.get("body") or "").strip()
        if not body or len(body) > 20_000:
            raise RecorderError(f"curation {index} has an invalid body", kind="format")
        sources = sorted({str(x) for x in item.get("source_event_ids") or [] if str(x)})
        if not sources or not set(sources) <= evidence_ids:
            raise RecorderError(f"curation {index} cites an event outside this batch", kind="format")
        resolved = sorted({str(x) for x in item.get("resolve_comment_ids") or [] if str(x)})
        if not set(resolved) <= set(corrections):
            raise RecorderError(f"curation {index} acknowledges an unknown correction", kind="format")
        for comment_id in resolved:
            comment = corrections[comment_id]
            if comment.get("target_type") != target_type:
                raise RecorderError(f"curation {index} correction belongs to another target", kind="format")
            if target_type == "chapter" and str(comment.get("target_id") or "") != target_id:
                raise RecorderError(f"curation {index} correction belongs to another Chapter", kind="format")
        key = (target_type, target_id)
        if key in seen_targets:
            raise RecorderError(f"curation {index} repeats a summary target", kind="format")
        seen_targets.add(key)
        clean.append({
            "target_type": target_type, "target_id": target_id, "body": body,
            "expect_version": expected, "source_event_ids": sources,
            "resolve_comment_ids": resolved, "milestone": bool(item.get("milestone")),
        })
    return clean


def _curation_already_applied(context: dict[str, Any], curation: dict[str, Any]) -> bool:
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    if curation["target_type"] == "overview":
        body = project.get("overview") or ""
        version = int(project.get("overview_version") or 0)
    else:
        chapter = next(
            (x for x in project.get("chapters") or []
             if isinstance(x, dict) and x.get("id") == curation.get("target_id")),
            {},
        )
        body = chapter.get("summary") or ""
        version = int(chapter.get("summary_version") or 0)
    return body == curation["body"] and version == int(curation["expect_version"]) + 1


def _recorder_config(manifest: dict[str, Any]) -> dict[str, Any]:
    binding = project_binding(manifest.get("project_dir"))
    if binding and isinstance(binding.get("recorder"), dict):
        return dict(binding.get("recorder") or {})
    configured = manifest.get("recorder")
    return dict(configured) if isinstance(configured, dict) else {}


def _manifest_paths(outbox: Path) -> list[Path]:
    paths: list[Path] = []
    try:
        workspaces = list(outbox.iterdir())
    except OSError:
        return []
    for workspace in workspaces:
        if not workspace.is_dir():
            continue
        try:
            sessions = list(workspace.iterdir())
        except OSError:
            continue
        for session in sessions:
            try:
                paths.extend(path for path in (session / "batches").glob("*.json") if path.is_file())
            except OSError:
                continue
    return sorted(paths, key=lambda p: p.name)


def _batch_state_path(manifest_path: Path) -> Path:
    return manifest_path.parent / "state" / manifest_path.name


def _finish_batch(manifest_path: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    done = manifest_path.parent / "done"
    done.mkdir(parents=True, exist_ok=True)
    final_manifest = dict(manifest)
    final_manifest["recorder_finished_at"] = _now()
    final_manifest["recorder_outcome"] = state.get("outcome")
    final_manifest["recorder_record_count"] = len(state.get("plan") or [])
    final_manifest["recorder_curation_count"] = len(state.get("curations") or [])
    _atomic_json(done / manifest_path.name, final_manifest)
    _atomic_json(done / f"{manifest_path.stem}.state.json", state)
    manifest_path.unlink()
    source_state = _batch_state_path(manifest_path)
    try:
        source_state.unlink()
    except OSError:
        pass


class RecorderWorker:
    def __init__(
        self, data_dir: str | os.PathLike[str], url: str, *, token: str = "",
        credential_file: str | os.PathLike[str] | None = None, executable: str = "claude",
        model_timeout: float = MODEL_TIMEOUT,
    ):
        self.data_dir = Path(data_dir).expanduser()
        self.outbox = long_path(self.data_dir / "outbox")
        self.url = url.rstrip("/")
        self.remote = Remote(self.url, token, credential_file)
        self.executable = executable
        self.model_timeout = model_timeout
        self.workspace = self.data_dir / "recorder-workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.state_path = self.outbox / "recorder-status.json"
        self.state = _read_json(self.state_path)
        if self.state.get("schema") != STATE_SCHEMA:
            self.state = {"schema": STATE_SCHEMA, "projects": {}}
        self.auth: dict[str, Any] | None = None

    def save(self) -> None:
        self.state["updated_at"] = _now()
        _atomic_json(self.state_path, self.state)

    def _set_status(self, status: str, error: str | None = None, pause_until: float | None = None) -> None:
        self.state["status"] = status
        self.state["last_error"] = error
        self.state["pause_until"] = pause_until
        self.save()

    def _context(self, manifest: dict[str, Any], material: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        value = {
            "project_id": manifest.get("project_id"),
            "workspace_keys": manifest.get("workspace_keys") or [],
            "recent_limit": 8,
            "create_if_missing": False,
        }
        context = self.remote.request("POST", "/api/context", value)
        if not isinstance(context, dict) or context.get("matched") is False:
            raise RecorderError("no unambiguous central project is bound yet", kind="waiting_project")
        query = _search_query(material.get("events") or [])
        related: list[dict[str, Any]] = []
        if query:
            import urllib.parse
            path = "/api/search?" + urllib.parse.urlencode({
                "q": query, "project_id": (context.get("project") or {}).get("id"),
                "scope": "semantic", "limit": 6,
            })
            try:
                result = self.remote.request("GET", path)
                related = (
                    result.get("items") or result.get("results") or []
                    if isinstance(result, dict)
                    else []
                )
            except RuntimeError:
                related = []  # Recent context is sufficient; focused recall is an optimization.
        return context, related

    def _session(self, project_id: str, model: str) -> tuple[str, bool]:
        projects = self.state.setdefault("projects", {})
        key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
        current = projects.get(key) if isinstance(projects.get(key), dict) else {}
        signature = hashlib.sha256((SYSTEM_PROMPT + json.dumps(OUTPUT_SCHEMA, sort_keys=True)).encode()).hexdigest()
        fresh = (
            not current.get("session_id") or current.get("model") != model
            or current.get("prompt_signature") != signature
            or int(current.get("turns") or 0) >= SESSION_TURNS
        )
        if fresh:
            current = {
                "session_id": str(uuid.uuid4()), "turns": 0, "model": model,
                "prompt_signature": signature,
            }
            projects[key] = current
            self.save()
        return str(current["session_id"]), bool(current.get("turns"))

    def _advance_session(self, project_id: str) -> None:
        key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
        current = self.state.setdefault("projects", {}).get(key)
        if isinstance(current, dict):
            current["turns"] = int(current.get("turns") or 0) + 1
            current["last_used_at"] = _now()
            self.save()

    def _invoke(self, prompt: str, config: dict[str, Any], project_id: str) -> dict[str, Any]:
        model = str(config.get("model") or DEFAULT_MODEL).strip().lower()
        if model not in ALLOWED_MODELS:
            raise RecorderError(f"model {model!r} is not allowed in subscription-only mode", kind="config")
        executable = str(config.get("claude_executable") or self.executable).strip() or "claude"
        env = subscription_environment()
        if self.auth is None:
            self.auth = verify_subscription_auth(executable, env, self.workspace)
        session_id, resume = self._session(project_id, model)
        command = [
            executable, "--print", "--output-format", "stream-json", "--verbose",
            "--model", model, "--setting-sources", "", "--tools", "",
            "--permission-mode", "dontAsk", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--system-prompt", SYSTEM_PROMPT, "--exclude-dynamic-system-prompt-sections",
            "--json-schema", json.dumps(OUTPUT_SCHEMA, separators=(",", ":")),
        ]
        command += ["--resume", session_id] if resume else ["--session-id", session_id]
        try:
            result = subprocess.run(
                command, input=prompt, cwd=str(self.workspace), env=env, text=True,
                capture_output=True, timeout=self.model_timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RecorderError("Recorder model timed out; batch retained", kind="timeout") from exc
        except OSError as exc:
            raise RecorderError(f"cannot start Claude Code CLI: {exc}", kind="cli") from exc
        kind, value, reset_at = classify_cli_output(result.stdout or "", result.stderr or "", result.returncode)
        if kind == "overage":
            raise RecorderError(str(value), kind="overage", retry_at=None)
        if kind == "quota":
            raise RecorderError(str(value), kind="quota", retry_at=reset_at or (time.time() + 900))
        if kind != "success":
            if resume:
                key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
                self.state.setdefault("projects", {}).pop(key, None)
                self.save()
            raise RecorderError(str(value), kind=kind)
        usage = extract_usage(result.stdout or "")
        if usage:
            self.state["last_usage"] = usage
            totals = self.state.setdefault("usage_totals", {})
            for key, count in usage.items():
                totals[key] = int(totals.get(key) or 0) + count
            self.save()
        self._advance_session(project_id)
        return value

    def process(self, manifest_path: Path) -> dict[str, Any]:
        manifest = _read_json(manifest_path)
        batch_id = str(manifest.get("batch_id") or manifest_path.stem)
        batch_path = _batch_state_path(manifest_path)
        batch = _read_json(batch_path)
        if batch.get("schema") != BATCH_STATE_SCHEMA:
            batch = {"schema": BATCH_STATE_SCHEMA, "batch_id": batch_id, "completed_records": []}
        if batch.get("status") == "overage" and not batch.get("retry_at"):
            return {"batch_id": batch_id, "status": "overage", "error": batch.get("last_error")}
        retry_at = float(batch.get("retry_at") or 0.0)
        if retry_at > time.time():
            return {"batch_id": batch_id, "status": "deferred", "retry_at": retry_at}

        config = _recorder_config(manifest)
        if not config.get("enabled"):
            self._set_status("disabled")
            return {"batch_id": batch_id, "status": "disabled"}
        if config.get("extra_usage_disabled") is not True:
            batch.update({
                "status": "blocked_config", "last_error":
                "subscription-only Recorder requires an explicit confirmation that Claude extra usage is disabled",
            })
            _atomic_json(batch_path, batch)
            self._set_status("blocked_config", str(batch["last_error"]))
            return {"batch_id": batch_id, "status": "blocked_config"}

        try:
            material = _manifest_payload(str(manifest_path), manifest.get("project_id"))
            context, related = self._context(manifest, material)
            project = context.get("project") if isinstance(context.get("project"), dict) else {}
            project_id = str(project.get("id") or "")
            if not project_id:
                raise RecorderError("resolved context has no project id", kind="waiting_project")
            if "plan" not in batch:
                prompt, packet = build_prompt(manifest, material, context, related)
                output = self._invoke(prompt, config, project_id)
                batch["plan"] = validate_plan(output, packet)
                batch["curations"] = validate_curations(output, packet)
                batch["project_id"] = project_id
                batch["planned_at"] = _now()
                batch["status"] = "planned"
                batch.pop("last_error", None)
                batch.pop("retry_at", None)
                _atomic_json(batch_path, batch)  # durable before the first central write

            completed = {int(x) for x in batch.get("completed_records") or []}
            for index, record in enumerate(batch.get("plan") or []):
                if index in completed:
                    continue
                value = dict(record)
                value["project_id"] = batch["project_id"]
                value["idempotency_key"] = f"semantic:{batch_id}:{index}"
                self.remote.request("POST", "/api/record", value)
                completed.add(index)
                batch["completed_records"] = sorted(completed)
                _atomic_json(batch_path, batch)

            completed_curations = {int(x) for x in batch.get("completed_curations") or []}
            for index, curation in enumerate(batch.get("curations") or []):
                if index in completed_curations:
                    continue
                # If the process died after the central write but before the
                # sidecar update, recognize that exact version/body as our
                # already-applied operation. A different body or version still
                # goes through optimistic concurrency and protects human work.
                if not _curation_already_applied(context, curation):
                    value = dict(curation)
                    value["project_id"] = batch["project_id"]
                    self.remote.request("POST", "/api/curate", value)
                completed_curations.add(index)
                batch["completed_curations"] = sorted(completed_curations)
                _atomic_json(batch_path, batch)
            batch["status"] = "complete"
            batch["outcome"] = "recorded" if batch.get("plan") or batch.get("curations") else "skipped"
            batch["finished_at"] = _now()
            _finish_batch(manifest_path, manifest, batch)
            self.state["last_processed_at"] = batch["finished_at"]
            self.state["processed_batches"] = int(self.state.get("processed_batches") or 0) + 1
            self.state["written_records"] = int(self.state.get("written_records") or 0) + len(batch.get("plan") or [])
            self.state["written_curations"] = int(self.state.get("written_curations") or 0) + len(batch.get("curations") or [])
            self._set_status("idle")
            return {
                "batch_id": batch_id, "status": "complete",
                "records": len(batch.get("plan") or []),
                "curations": len(batch.get("curations") or []),
            }
        except RecorderError as exc:
            attempts = int(batch.get("attempts") or 0) + 1
            if exc.kind in {"quota"}:
                delay = exc.retry_at or time.time() + 900
            elif exc.kind == "overage":
                delay = 0.0  # hard stop: never automatically repeat a billed-overage signal
            elif exc.kind in {"paid_credentials", "auth", "config"}:
                delay = time.time() + 300  # preflight/config checks do not consume model quota
            elif exc.kind == "waiting_project":
                delay = time.time() + 300
            else:
                delay = time.time() + min(3600, 60 * (2 ** min(attempts, 6)))
            batch.update({
                "status": exc.kind, "attempts": attempts, "last_error": str(exc),
                "last_attempt_at": _now(), "retry_at": delay or None,
            })
            _atomic_json(batch_path, batch)
            self._set_status(exc.kind, str(exc), delay or None)
            return {"batch_id": batch_id, "status": exc.kind, "error": str(exc), "retry_at": delay or None}
        except Exception as exc:
            attempts = int(batch.get("attempts") or 0) + 1
            delay = time.time() + min(3600, 60 * (2 ** min(attempts, 6)))
            batch.update({
                "status": "storage_or_network_error", "attempts": attempts,
                "last_error": str(exc), "last_attempt_at": _now(), "retry_at": delay,
            })
            _atomic_json(batch_path, batch)
            self._set_status("storage_or_network_error", str(exc), delay)
            return {"batch_id": batch_id, "status": "storage_or_network_error", "error": str(exc), "retry_at": delay}

    def run_once(self) -> dict[str, Any]:
        paths = _manifest_paths(self.outbox)
        counts: dict[str, int] = {}
        results = []
        for path in paths:
            result = self.process(path)
            results.append(result)
            status = str(result.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            # Subscription/auth/operator blocks apply to every batch. Stop after
            # the first one so a backlog cannot repeat the same check or request.
            if status in {"quota", "overage", "paid_credentials", "auth", "config", "blocked_config"}:
                break
        self.state["pending_batches"] = len(_manifest_paths(self.outbox))
        self.save()
        return {"pending_batches": self.state["pending_batches"], "counts": counts, "results": results}


def _next_retry(data_dir: Path) -> float | None:
    outbox = long_path(data_dir / "outbox")
    values: list[float] = []
    for path in _manifest_paths(outbox):
        state = _read_json(_batch_state_path(path))
        if state.get("retry_at"):
            values.append(float(state["retry_at"]))
    return min(values) if values else None


def _clear_blocked(outbox: Path) -> int:
    cleared = 0
    for path in _manifest_paths(outbox):
        state_path = _batch_state_path(path)
        state = _read_json(state_path)
        if state.get("status") in {
            "overage", "paid_credentials", "auth", "config", "blocked_config",
        }:
            state.pop("status", None)
            state.pop("last_error", None)
            state["retry_at"] = 0
            _atomic_json(state_path, state)
            cleared += 1
    global_state = _read_json(outbox / "recorder-status.json")
    if global_state:
        global_state.update({"status": "retry_requested", "last_error": None, "pause_until": None})
        _atomic_json(outbox / "recorder-status.json", global_state)
    return cleared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process Research Trace semantic batches with an isolated Claude subscription session")
    parser.add_argument("--data-dir", default=os.environ.get("TRACE_DATA_DIR") or os.environ.get("CLAUDE_PLUGIN_DATA") or "")
    parser.add_argument("--url", default=os.environ.get("TRACE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("TRACE_TOKEN", ""))
    parser.add_argument("--credential-file", default=os.environ.get("TRACE_CREDENTIAL_FILE"))
    parser.add_argument("--claude", default=os.environ.get("TRACE_RECORDER_CLAUDE", "claude"))
    parser.add_argument("--timeout", type=float, default=MODEL_TIMEOUT)
    parser.add_argument(
        "--watch", action="store_true",
        help="run as an independent long-lived consumer, including while the queue is empty",
    )
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--status", action="store_true", help="show local Recorder state without model or network calls")
    parser.add_argument(
        "--retry-blocked", action="store_true",
        help="clear operator-action blocks after authentication/account/configuration was fixed",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if not args.data_dir:
        print("no Recorder outbox: pass --data-dir or set CLAUDE_PLUGIN_DATA", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir).expanduser()
    outbox = long_path(data_dir / "outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    if args.status:
        value = _read_json(outbox / "recorder-status.json")
        value["pending_batches"] = len(_manifest_paths(outbox))
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 1 if value["pending_batches"] else 0
    if args.retry_blocked:
        cleared = _clear_blocked(outbox)
        if not args.quiet:
            print(f"cleared {cleared} blocked Recorder batch state(s)")

    lock = FileLock(str(outbox / ".recorder.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return 0
    try:
        worker = RecorderWorker(
            data_dir, args.url, token=args.token, credential_file=args.credential_file,
            executable=args.claude, model_timeout=args.timeout,
        )
        while True:
            report = worker.run_once()
            if not args.quiet:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            if not args.watch:
                return 0 if not report["pending_batches"] else 1
            status = str(worker.state.get("status") or "")
            if status in {"overage", "paid_credentials", "auth", "config"}:
                return 1
            retry = _next_retry(data_dir)
            delay = max(float(args.interval), (retry - time.time()) if retry else float(args.interval))
            time.sleep(min(max(5.0, delay), 300.0))
    except KeyboardInterrupt:
        return 130
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
