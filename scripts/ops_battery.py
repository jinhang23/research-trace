"""Operations battery: the angles the research simulation does not cover.

Real CLIs (trace-project / trace-deliver / trace-recorder / trace-backup / trace_mcp.py),
a real hook process, a real central server (in-process uvicorn), and every Recorder failure
path driven through scripts/fake_claude.py so no subscription quota is spent.

    python scripts/ops_battery.py                 # ~1 minute, no model calls
    REAL_WATCH=1 python scripts/ops_battery.py    # + one real `trace-recorder --watch` process (haiku)

Run it with the interpreter the package is installed into (the console scripts are looked
up next to it).  On UF, REAL_WATCH=1 is the check that the watcher, the subscription login
and the installed CLI version actually work together.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
BIN = Path(sys.executable).parent  # console scripts live next to the (unresolved) interpreter
FAKE = REPO / "scripts" / "fake_claude.py"


def cli(name: str) -> str:
    candidate = BIN / name
    if candidate.exists():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise SystemExit(
            f"{name} is not installed next to {sys.executable} nor on PATH; pip install -e '.[server]' first"
        )
    return found


sys.path.insert(0, str(REPO))
os.environ["TRACE_HOOK_NO_SPAWN"] = "1"
os.environ.pop("ANTHROPIC_BASE_URL", None)

import uvicorn  # noqa: E402

from research_trace.deliver import _DeliverLock, deliver_once  # noqa: E402
from research_trace.server import create_app  # noqa: E402
from research_trace.storage import Store  # noqa: E402

TOKEN = "ops-token"
RESULTS: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:300]))
    print(
        ("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"\n      -> {str(detail)[:300]}"),
        flush=True,
    )


def run(cmd, env=None, stdin=None, timeout=120):
    merged = dict(os.environ)
    merged.update(env or {})
    return subprocess.run(
        [str(c) for c in cmd], input=stdin, env=merged, capture_output=True, text=True, timeout=timeout
    )


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(url, path, body=None):
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def start_server(central):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    app = create_app(central, token=TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            api(url, "/api/health")
            return url, app
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


def hook(payload, data, url, capture="on"):
    return run(
        [PY, REPO / "scripts" / "trace_hook.py", "--data-dir", data, "--capture-enabled", capture, "--url", url],
        stdin=json.dumps(payload),
        timeout=60,
    )


def session_root(data, session):
    matches = list((data / "outbox").glob(f"*/{session}"))
    assert len(matches) == 1, matches
    return matches[0]


def stage_turn(data, url, proj, session, prompt, transcript):
    base = {"session_id": session, "transcript_path": str(transcript), "cwd": str(proj)}
    with open(transcript, "a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
            + "\n"
        )
    hook({**base, "hook_event_name": "UserPromptSubmit", "prompt": prompt}, data, url)
    hook(
        {
            **base,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "t1",
            "tool_input": {"command": "echo hi"},
        },
        data,
        url,
    )
    hook(
        {
            **base,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_use_id": "t1",
            "tool_input": {"command": "echo hi"},
            "tool_response": {"stdout": "hi", "exit_code": 0},
        },
        data,
        url,
    )
    hook({**base, "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "done"}, data, url)
    return session_root(data, session)


def sidecar(root, name=None):
    states = sorted((root / "batches" / "state").glob("*.json"))
    return states[0] if states else None


def set_retry_now(root):
    path = sidecar(root)
    if path:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("retry_at"):
            state["retry_at"] = 0
            path.write_text(json.dumps(state), encoding="utf-8")


def fake_calls(log):
    try:
        return sum(1 for line in log.read_text(encoding="utf-8").splitlines() if '"argv": ["--print"' in line)
    except OSError:
        return 0


def main():
    work = Path(tempfile.mkdtemp(prefix="rt-ops-"))
    central = work / "central"
    central.mkdir()
    url, app = start_server(central)
    store = app.state.store
    data = work / "plugin-data"
    log = work / "fake-claude.log"
    os.chmod(FAKE, 0o755)

    # ---------------- A. operator CLIs ----------------
    print("\n## A. operator CLIs")
    proj = work / "proj"
    proj.mkdir()
    p = run(
        [
            cli("trace-project"),
            "bind",
            proj,
            "--url",
            url,
            "--token",
            TOKEN,
            "--create",
            "--name",
            "Ops 项目",
            "--no-git",
        ]
    )
    marker = (
        json.loads((proj / ".research-trace.json").read_text(encoding="utf-8"))
        if (proj / ".research-trace.json").exists()
        else {}
    )
    check(
        "trace-project bind --create writes a marker with a central project_id",
        p.returncode == 0 and marker.get("project_id"),
        p.stdout + p.stderr,
    )
    pid = marker.get("project_id", "")
    p = run([cli("trace-project"), "status", proj, "--url", url, "--token", TOKEN])
    check(
        "trace-project status reports the binding",
        p.returncode == 0 and pid in (p.stdout + p.stderr),
        p.stdout + p.stderr,
    )
    clone = work / "clone"
    clone.mkdir()
    p = run([cli("trace-project"), "bind", clone, "--url", url, "--token", TOKEN, "--project-id", pid, "--no-git"])
    clone_marker = (
        json.loads((clone / ".research-trace.json").read_text(encoding="utf-8"))
        if (clone / ".research-trace.json").exists()
        else {}
    )
    check(
        "a second directory binds to the same central project by --project-id",
        clone_marker.get("project_id") == pid,
        p.stdout + p.stderr,
    )
    offline = work / "scratch-node"
    offline.mkdir()
    p = run([cli("trace-project"), "bind", offline, "--offline", "--no-git", "--url", "http://127.0.0.1:1"])
    check(
        "bind --offline writes a marker without contacting central (HPC scratch)",
        p.returncode == 0 and (offline / ".research-trace.json").exists(),
        p.stdout + p.stderr,
    )
    p = run([cli("trace-project"), "recorder-enable", proj, "--model", "haiku"])
    check(
        "recorder-enable refuses without --confirm-extra-usage-disabled",
        p.returncode != 0 and "extra" in (p.stdout + p.stderr).lower(),
        p.stdout + p.stderr,
    )
    p = run(
        [
            cli("trace-project"),
            "recorder-enable",
            proj,
            "--model",
            "haiku",
            "--confirm-extra-usage-disabled",
            "--claude",
            FAKE,
        ]
    )
    marker = json.loads((proj / ".research-trace.json").read_text(encoding="utf-8"))
    rec = marker.get("recorder") or {}
    check(
        "recorder-enable writes enabled+model+confirmation into the marker",
        p.returncode == 0
        and rec.get("enabled")
        and rec.get("extra_usage_disabled") is True
        and rec.get("model") == "haiku",
        p.stdout + p.stderr,
    )
    p = run([PY, REPO / "trace_mcp.py", "--selfcheck", "--url", url, "--token", TOKEN])
    check(
        "trace_mcp.py --selfcheck reaches central and sees write protection",
        p.returncode == 0 and '"write_protected": true' in p.stdout,
        p.stdout + p.stderr,
    )
    p = run([PY, REPO / "trace_mcp.py", "--selfcheck", "--url", "http://127.0.0.1:1"])
    check(
        "trace_mcp.py --selfcheck fails loudly when central is unreachable",
        p.returncode == 1 and p.stderr.strip(),
        p.stdout + p.stderr,
    )

    # ---------------- B. hook robustness ----------------
    print("\n## B. hook robustness")
    big = work / "big.jsonl"
    line = (
        json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "x" * 900}]}}
        )
        + "\n"
    )
    with big.open("w", encoding="utf-8") as stream:
        for _ in range(25_000):
            stream.write(line)
    payload = {
        "session_id": "big",
        "transcript_path": str(big),
        "cwd": str(proj),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hi",
    }
    started = time.time()
    p = hook(payload, data, url)
    elapsed = time.time() - started
    root = session_root(data, "big")
    chunks = list((root / "transcripts" / "pending").glob("*.jsonl"))
    check(
        f"hook copies a {big.stat().st_size / 1e6:.0f} MB transcript in one call under the 10 s hook timeout ({elapsed:.1f}s)",
        p.returncode == 0 and elapsed < 10,
        p.stderr,
    )
    check(
        "transcript is split into ~512 KiB chunks and the cursor sits at EOF",
        len(chunks) >= 40
        and sum(c.stat().st_size for c in chunks) == big.stat().st_size
        and max(json.loads((root / "state.json").read_text()).get("transcript_offsets", {}).values())
        == big.stat().st_size,
        f"{len(chunks)} chunks",
    )
    started = time.time()
    p = hook(payload, data, url)
    elapsed2 = time.time() - started
    check(
        f"a second call with no transcript growth is quick and copies nothing ({elapsed2:.2f}s)",
        p.returncode == 0
        and elapsed2 < 3
        and len(list((root / "transcripts" / "pending").glob("*.jsonl"))) == len(chunks),
    )

    conc = work / "conc.jsonl"
    conc.write_text('{"type":"user","message":"x"}\n', encoding="utf-8")

    def fire(i):
        value = {
            "session_id": "conc",
            "transcript_path": str(conc),
            "cwd": str(proj),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": f"t{i}",
            "tool_input": {"command": f"echo {i}"},
        }
        return hook(value, data, url).returncode

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(fire, range(120)))
    root = session_root(data, "conc")
    files = list((root / "pending").glob("*.json"))
    check(
        "120 concurrent hook processes on one session: 120 event files, all exit 0, no stale lock",
        all(c == 0 for c in codes)
        and len(files) == 120
        and not (root / ".state-lock").exists()
        and isinstance(json.loads((root / "state.json").read_text()), dict),
        f"codes={set(codes)} files={len(files)}",
    )
    bad = work / "not-a-dir"
    bad.write_text("x", encoding="utf-8")
    p = hook(payload, bad, url)
    check(
        "hook exits 0 (fail-open) when the data dir is unusable, and says so on stderr",
        p.returncode == 0 and "failed" in p.stderr.lower(),
        p.stderr,
    )
    p = hook({"cwd": str(work / "unbound"), "hook_event_name": "Stop", "session_id": "nope"}, data, url)
    check("an unbound directory writes nothing", p.returncode == 0 and not list((data / "outbox").glob("*/nope")))

    # ---------------- C. delivery ----------------
    print("\n## C. delivery")
    s1 = stage_turn(data, url, proj, "s1", "第一轮", work / "s1.jsonl")
    pending_before = len(list((s1 / "pending").glob("*.json")))
    report = deliver_once(data, "http://127.0.0.1:1", token=TOKEN)
    status = json.loads((data / "outbox" / "delivery-status.json").read_text(encoding="utf-8"))
    check(
        "central down: nothing moves, pending retained, error visible in delivery-status.json",
        report["delivered_events"] == 0
        and report["last_error"]
        and len(list((s1 / "pending").glob("*.json"))) == pending_before
        and status.get("last_error"),
        json.dumps(report)[:200],
    )
    p = run([cli("trace-deliver"), "--status", "--data-dir", data])
    check(
        "trace-deliver --status exits 1 while anything is pending (no network)",
        p.returncode == 1 and "pending" in p.stdout.lower(),
        p.stdout[:200],
    )
    report = deliver_once(data, url, token=TOKEN)
    check(
        "central up: everything delivered, pending drained, files in sent/",
        report["delivered_events"] > 0
        and report["failed_batches"] == 0
        and not list((s1 / "pending").glob("*.json"))
        and list((s1 / "sent").glob("*.json")),
        json.dumps(report)[:200],
    )
    with store._lock:
        events_before = store._db.execute("SELECT COUNT(*) FROM events WHERE project_id=?", (pid,)).fetchone()[0]
    replay = sorted((s1 / "sent").glob("*.json"))[0]
    shutil.copy(replay, s1 / "pending" / replay.name)
    report = deliver_once(data, url, token=TOKEN)
    with store._lock:
        events_after = store._db.execute("SELECT COUNT(*) FROM events WHERE project_id=?", (pid,)).fetchone()[0]
    check(
        "at-least-once replay of an already-stored event creates no duplicate and is accepted",
        events_after == events_before and report["failed_batches"] == 0 and not list((s1 / "pending").glob("*.json")),
        json.dumps(report)[:200],
    )
    with _DeliverLock(data / "outbox"):
        report = deliver_once(data, url, token=TOKEN)
    check(
        "a second deliverer while the lock is held is skipped and says so",
        report["skipped"] is True and "lock" in (report["last_error"] or ""),
    )
    p = run([cli("trace-deliver"), "--status", "--data-dir", data])
    check("trace-deliver --status exits 0 once nothing is pending", p.returncode == 0, p.stdout[:200])

    # ---------------- D. Recorder failure paths through a fake claude ----------------
    print("\n## D. Recorder failure paths (fake claude, no quota)")
    fake_env = {"FAKE_CLAUDE_LOG": str(log)}

    def recorder(*args, mode="success", timeout=60):
        return run(
            [cli("trace-recorder"), "--data-dir", data, "--url", url, "--token", TOKEN, "--claude", FAKE, *args],
            env={**fake_env, "FAKE_CLAUDE_MODE": mode},
            timeout=timeout,
        )

    def recorder_status():
        p = run([cli("trace-recorder"), "--status", "--data-dir", data])
        return p.returncode, json.loads(p.stdout or "{}")

    s2 = stage_turn(data, url, proj, "s2", "记一条", work / "s2.jsonl")
    deliver_once(data, url, token=TOKEN)
    rc, st = recorder_status()
    # s1 (delivered in section C) still has its batch queued: two batches, two sessions.
    check(
        "trace-recorder --status exits 1 with the queued batch count",
        rc == 1 and st.get("pending_batches") == 2,
        json.dumps(st)[:200],
    )
    p = recorder(mode="success")
    done = list((s2 / "batches" / "done").glob("*.state.json")) + list((s1 / "batches" / "done").glob("*.state.json"))
    with store._lock:
        nodes = store._db.execute(
            "SELECT title, created_by, review_state FROM nodes WHERE project_id=?", (pid,)
        ).fetchall()
    check(
        "success: both queued batches archived, one unreviewed recorder Node each, through the real subprocess path",
        p.returncode == 0
        and len(done) == 2
        and len(nodes) == 2
        and all(n[1] == "recorder" and n[2] == "unreviewed" for n in nodes),
        p.stdout[-300:] + p.stderr[-300:],
    )
    rc, st = recorder_status()
    check(
        "status after success: idle, auth recorded, usage counters summed over both calls, exit 0",
        rc == 0
        and st.get("status") == "idle"
        and (st.get("auth") or {}).get("auth_method") == "claude.ai"
        and st.get("usage_totals", {}).get("cache_read_input_tokens") == 5000,
        json.dumps(st)[:300],
    )

    s3 = stage_turn(data, url, proj, "s3", "坏输出", work / "s3.jsonl")
    deliver_once(data, url, token=TOKEN)
    before = fake_calls(log)
    statuses = []
    for _ in range(6):
        recorder(mode="malformed")
        statuses.append(json.loads(sidecar(s3).read_text()).get("status"))
        set_retry_now(s3)
    check(
        "malformed output: capped at 4 real calls, then attempts_exhausted, batch retained",
        fake_calls(log) - before == 4 and statuses[3:] == ["attempts_exhausted"] * 3 and sidecar(s3) is not None,
        f"{statuses} calls={fake_calls(log) - before}",
    )
    p = recorder("--retry-blocked", mode="success")
    check(
        "--retry-blocked clears the block and the next run completes the batch",
        p.returncode == 0 and list((s3 / "batches" / "done").glob("*.state.json")),
        p.stdout[-300:],
    )

    s4 = stage_turn(data, url, proj, "s4", "额度", work / "s4.jsonl")
    deliver_once(data, url, token=TOKEN)
    before = fake_calls(log)
    recorder(mode="quota")
    st4 = json.loads(sidecar(s4).read_text())
    rc, st = recorder_status()
    check(
        "quota: batch deferred until the CLI's resetsAt, worker status quota with pause_until, exit 1",
        st4.get("status") == "quota"
        and st4.get("retry_at", 0) > time.time() + 600
        and st.get("status") == "quota"
        and st.get("pause_until"),
        json.dumps(st4)[:200],
    )
    recorder(mode="quota")
    check("a deferred batch is not sent to the model again before its reset time", fake_calls(log) - before == 1)
    set_retry_now(s4)
    recorder(mode="overage")
    st4 = json.loads(sidecar(s4).read_text())
    calls = fake_calls(log)
    recorder(mode="overage")
    check(
        "overage: hard block, no automatic retry at all",
        st4.get("status") == "overage" and not st4.get("retry_at") and fake_calls(log) == calls,
        json.dumps(st4)[:200],
    )
    p = recorder("--retry-blocked", mode="success")
    check(
        "after the operator fixes the account, --retry-blocked lets the batch through",
        p.returncode == 0 and list((s4 / "batches" / "done").glob("*.state.json")),
    )

    s5 = stage_turn(data, url, proj, "s5", "没登录", work / "s5.jsonl")
    deliver_once(data, url, token=TOKEN)
    calls = fake_calls(log)
    p = recorder("--watch", "--interval", "1", mode="auth", timeout=30)
    st5 = json.loads(sidecar(s5).read_text())
    check(
        "not logged in: preflight blocks before any model call and --watch exits 1 instead of looping",
        p.returncode == 1 and st5.get("status") == "auth" and fake_calls(log) == calls,
        p.stdout[-200:] + p.stderr[-200:],
    )
    set_retry_now(s5)
    p = recorder(mode="noauthcmd")
    check(
        "old CLI without `auth status`: preflight passes as unverified and the batch completes",
        p.returncode == 0 and list((s5 / "batches" / "done").glob("*.state.json")),
        p.stdout[-300:],
    )
    rc, st = recorder_status()
    check(
        "status records the unverified preflight honestly",
        (st.get("auth") or {}).get("auth_method") == "unverified",
        json.dumps(st.get("auth")),
    )

    s6 = stage_turn(data, url, proj, "s6", "卡住", work / "s6.jsonl")
    deliver_once(data, url, token=TOKEN)
    started = time.time()
    p = recorder("--timeout", "3", mode="hang", timeout=60)
    st6 = json.loads(sidecar(s6).read_text())
    check(
        f"a hanging CLI is killed at --timeout and the batch waits for retry ({time.time() - started:.1f}s)",
        st6.get("status") == "timeout" and st6.get("attempts") == 1 and time.time() - started < 20,
        json.dumps(st6)[:200],
    )
    from filelock import FileLock

    with FileLock(str(data / "outbox" / ".recorder.lock")):
        calls = fake_calls(log)
        started = time.time()
        p = recorder(mode="success")
    check(
        "a second trace-recorder on the same outbox exits 0 at once without touching anything",
        p.returncode == 0 and fake_calls(log) == calls and time.time() - started < 10,
    )
    set_retry_now(s6)
    p = recorder(mode="success")
    check(
        "after the timeout, the retry completes normally",
        p.returncode == 0 and list((s6 / "batches" / "done").glob("*.state.json")),
    )
    with store._lock:
        node_count = store._db.execute("SELECT COUNT(*) FROM nodes WHERE project_id=?", (pid,)).fetchone()[0]
    check("every completed batch wrote exactly one Node (no duplicates across retries)", node_count == 6, node_count)

    # ---------------- E. backup ----------------
    print("\n## E. backup / restore")
    p = run([cli("trace-backup"), "export", "--data-dir", central, "--target", work / "bk"])
    check("trace-backup export succeeds", p.returncode == 0, p.stdout[-200:] + p.stderr[-200:])
    p = run([cli("trace-backup"), "verify", "--source", work / "bk"])
    check("trace-backup verify passes on the fresh export", p.returncode == 0, p.stdout[-200:] + p.stderr[-200:])
    restored = work / "restored"
    restored.mkdir()
    p = run([cli("trace-backup"), "restore", "--source", work / "bk", "--data-dir", restored])
    ok = p.returncode == 0
    if ok:
        other = Store(restored)
        with other._lock:
            restored_nodes = other._db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            restored_events = other._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with store._lock:
            orig_events = store._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        ok = restored_nodes == node_count and restored_events == orig_events
    check("restore into an empty data dir reproduces every Node and event", ok, p.stdout[-200:] + p.stderr[-200:])

    # ---------------- F. plugin packaging ----------------
    print("\n## F. plugin packaging")
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    check(
        "hooks.json and plugin.json parse; every hook runs scripts/trace_hook.py; version is alpha.29",
        all(
            h["args"][0].endswith("scripts/trace_hook.py")
            for g in hooks["hooks"].values()
            for grp in g
            for h in grp["hooks"]
        )
        and plugin["version"].endswith("29"),
    )
    if shutil.which("claude"):
        p = run(["claude", "--plugin-dir", REPO, "plugin", "details", "research-trace"], timeout=90)
        text = (p.stdout + p.stderr).strip()
        check(
            "the installed claude loads this checkout as a plugin (12 hooks, 1 skill, current version)",
            p.returncode == 0 and "Hooks (12)" in text and "Skills (1)" in text and plugin["version"] in text,
            text[:300],
        )
        print("  " + "\n  ".join(text.splitlines()[:8]))

    # ---------------- G. real watcher process (one haiku call) ----------------
    if os.environ.get("REAL_WATCH") == "1":
        print("\n## G. real trace-recorder --watch process (real CLI, haiku)")
        real_proj = work / "real-proj"
        real_proj.mkdir()
        run([cli("trace-project"), "bind", real_proj, "--url", url, "--token", TOKEN, "--project-id", pid, "--no-git"])
        run([cli("trace-project"), "recorder-enable", real_proj, "--model", "haiku", "--confirm-extra-usage-disabled"])
        real_data = work / "real-data"
        stage_turn(
            real_data,
            url,
            real_proj,
            "real1",
            "真调一次：这只是一个用于验证 watcher 进程的合成回合。",
            work / "real1.jsonl",
        )
        deliver_once(real_data, url, token=TOKEN)
        proc = subprocess.Popen(
            [
                str(cli("trace-recorder")),
                "--watch",
                "--interval",
                "2",
                "--data-dir",
                str(real_data),
                "--url",
                url,
                "--token",
                TOKEN,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        root = session_root(real_data, "real1")
        started = time.time()
        while time.time() - started < 180 and not list((root / "batches" / "done").glob("*.state.json")):
            time.sleep(2)
        processed = bool(list((root / "batches" / "done").glob("*.state.json")))
        time.sleep(3)  # it should now be idling
        proc.send_signal(signal.SIGINT)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        st = json.loads((real_data / "outbox" / "recorder-status.json").read_text(encoding="utf-8"))
        check(
            f"real watcher: processed the queued batch in {time.time() - started:.0f}s, idled, exited 130 on SIGINT",
            processed and proc.returncode == 130 and st.get("status") == "idle" and st.get("last_usage"),
            f"rc={proc.returncode} {err[-300:]}",
        )

    print("\n## SUMMARY")
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for name, _ok, detail in failed:
        print("FAIL", name, "|", detail)
    print("work dir:", work)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
