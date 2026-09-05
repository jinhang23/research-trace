#!/usr/bin/env python3
"""A stand-in `claude` CLI for quota-free failure-path tests.

Mode comes from FAKE_CLAUDE_MODE: success | malformed | empty | quota | overage | auth | hang.
Every invocation appends one line to FAKE_CLAUDE_LOG so callers can count model calls.
"""

import json
import os
import re
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
log = os.environ.get("FAKE_CLAUDE_LOG")
argv = sys.argv[1:]
if log:
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"mode": mode, "argv": argv[:4]}) + "\n")

if argv[:2] == ["auth", "status"]:
    if mode == "auth":
        print(json.dumps({"loggedIn": False}))
        sys.exit(1)
    if mode == "noauthcmd":
        print("error: unknown command 'auth'", file=sys.stderr)
        sys.exit(1)
    print(
        json.dumps(
            {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "team"}
        )
    )
    sys.exit(0)

if argv[:1] == ["--help"]:
    print(
        "--print --output-format --verbose --model --setting-sources --tools --permission-mode --strict-mcp-config "
        "--mcp-config --no-session-persistence --system-prompt --json-schema"
    )
    sys.exit(0)

prompt = sys.stdin.read()
if "--print" not in argv or "--json-schema" not in argv or "--no-session-persistence" not in argv:
    print("fake claude: unexpected invocation " + " ".join(argv[:6]), file=sys.stderr)
    sys.exit(2)

if mode == "hang":
    time.sleep(3600)
if mode == "auth":
    print("Not logged in. Please run /login", file=sys.stderr)
    sys.exit(1)
if mode == "quota":
    print(
        json.dumps(
            {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": int(time.time()) + 1800}}
        )
    )
    print(json.dumps({"type": "result", "is_error": True, "result": "You have hit your usage limit. Resets at 12:00"}))
    sys.exit(1)
if mode == "overage":
    print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "isUsingOverage": True}}))
    print(json.dumps({"type": "result", "structured_output": {"status": "skip", "records": [], "curations": []}}))
    sys.exit(0)
if mode == "empty":
    sys.exit(0)
if mode == "malformed":
    print(json.dumps({"type": "result", "result": "Sure! Here is a summary of the research so far."}))
    sys.exit(0)

match = re.search(r'"event_id":\s*"([^"]+)"', prompt.split("NEW EVIDENCE", 1)[-1])
event_id = match.group(1) if match else "unknown"
output = {
    "status": "record",
    "records": [
        {
            "title": "假 Recorder 记录：来自 " + event_id,
            "body": "由 fake claude 生成的记录，用于验证子进程路径、幂等写入与恢复。",
            "chapter_id": None,
            "parent_id": None,
            "labels": ["fake"],
            "run_ids": [],
            "source_event_ids": [event_id],
            "occurred_at": None,
            "artifact_refs": [],
            "code_evidence": [],
        }
    ],
    "curations": [],
}
print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "isUsingOverage": False}}))
print(
    json.dumps(
        {
            "type": "result",
            "structured_output": output,
            "usage": {
                "input_tokens": 12,
                "output_tokens": 300,
                "cache_read_input_tokens": 2500,
                "cache_creation_input_tokens": 4000,
            },
        }
    )
)
