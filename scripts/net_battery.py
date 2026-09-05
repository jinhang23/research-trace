"""Network battery: the client → remote central path over real TLS.

The central service will live on a server; every workstation and HPC login node talks
to it over HTTPS.  This exercises that path with the real CLIs against a real TLS server
(self-signed certificate on a non-loopback interface, in-process uvicorn):

- certificate verification is enforced (no CA → refused, pending retained) and a private CA
  works through SSL_CERT_FILE;
- a wrong token is a clear 401 with a hint, never data loss;
- a plain-http URL that redirects to https is refused with the real cause instead of a 405;
- an unreachable HTTPS_PROXY fails cleanly and NO_PROXY bypasses it;
- multi-megabyte transcripts are split into several POSTs under the 6 MiB batch cap;
- MCP --selfcheck and the Recorder (fake claude, no quota) work over the same TLS URL;
- a non-loopback server without OAuth prints the loud warning.

    python scripts/net_battery.py            # ~1 minute, no model calls

It needs `openssl` for the throwaway certificate and the package installed into the
interpreter that runs it (console scripts are looked up next to it).  Delivery checks go
through the real `trace-deliver` process because TLS/proxy settings are environment-driven
and that is how operators will run it.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
BIN = Path(sys.executable).parent
FAKE = REPO / "scripts" / "fake_claude.py"
sys.path.insert(0, str(REPO))
os.environ["TRACE_HOOK_NO_SPAWN"] = "1"
for key in (
    "ANTHROPIC_BASE_URL",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "https_proxy",
    "http_proxy",
    "NO_PROXY",
    "SSL_CERT_FILE",
):
    os.environ.pop(key, None)

import uvicorn  # noqa: E402

from research_trace.server import create_app  # noqa: E402

TOKEN = "net-token"
RESULTS: list[tuple[str, bool, str]] = []


def cli(name: str) -> str:
    candidate = BIN / name
    if candidate.exists():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} is not installed next to {sys.executable}; pip install -e '.[server]' first")
    return found


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:400]))
    print(
        ("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else f"\n      -> {str(detail)[:400]}"),
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
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def lan_ip() -> str:
    """A non-loopback address of this machine: the server binds 0.0.0.0, clients use this."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def mint_certificate(work: Path, ip: str) -> tuple[Path, Path]:
    cnf = work / "san.cnf"
    cnf.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n[dn]\nCN = research-trace-net-test\n"
        f"[v3]\nsubjectAltName = IP:127.0.0.1,IP:{ip},DNS:localhost\nbasicConstraints = CA:FALSE\n",
        encoding="utf-8",
    )
    cert, key = work / "cert.pem", work / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "2",
            "-config",
            str(cnf),
            "-extensions",
            "v3",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def start_tls_server(central: Path, cert: Path, key: Path, port: int):
    app = create_app(central, token=TOKEN)
    config = uvicorn.Config(
        app, host="0.0.0.0", port=port, log_level="warning", ssl_certfile=str(cert), ssl_keyfile=str(key)
    )
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    return app


def start_redirector(port: int, target: str):
    class Redirect(http.server.BaseHTTPRequestHandler):
        def _go(self):
            self.send_response(301)
            self.send_header("Location", target + self.path)
            self.end_headers()

        do_GET = do_POST = _go

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait_healthy(url: str, cert: Path):
    import ssl

    context = ssl.create_default_context(cafile=str(cert))
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=5, context=context) as response:
                return json.loads(response.read())
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("TLS server did not come up")


def hook(payload, data, url):
    return run(
        [PY, REPO / "scripts" / "trace_hook.py", "--data-dir", data, "--capture-enabled", "on", "--url", url],
        stdin=json.dumps(payload),
        timeout=60,
    )


def stage_turn(data, url, proj, session, prompt, transcript, extra_lines=0):
    base = {"session_id": session, "transcript_path": str(transcript), "cwd": str(proj)}
    with open(transcript, "a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
            + "\n"
        )
        for i in range(extra_lines):
            line = {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": f"line {i} " + "x" * 900}]},
            }
            stream.write(json.dumps(line) + "\n")
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
    return next((data / "outbox").glob(f"*/{session}"))


def pending_count(root: Path) -> int:
    return len(list((root / "pending").glob("*.json")))


def main():
    work = Path(tempfile.mkdtemp(prefix="rt-net-"))
    central = work / "central"
    central.mkdir()
    ip = lan_ip()
    cert, key = mint_certificate(work, ip)
    port = free_port()
    url = f"https://{ip}:{port}"
    app = start_tls_server(central, cert, key, port)
    health = wait_healthy(url, cert)
    store = app.state.store
    trusted = {"SSL_CERT_FILE": str(cert)}
    print(f"TLS central at {url} (non-loopback), CA={cert}")
    check(
        "server answers over TLS on a non-loopback address with writes protected", health.get("write_protected") is True
    )

    # ---------------- A. certificate verification ----------------
    print("\n## A. TLS verification and private CA")
    proj = work / "proj"
    proj.mkdir()
    bind = [
        cli("trace-project"),
        "bind",
        proj,
        "--url",
        url,
        "--token",
        TOKEN,
        "--create",
        "--name",
        "网络项目",
        "--no-git",
    ]
    p = run(bind)
    marker_path = proj / ".research-trace.json"
    offline = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
    check(
        "without the CA, bind names the certificate failure and falls back to an offline marker without project_id",
        "CERTIFICATE_VERIFY_FAILED" in (p.stdout + p.stderr) and offline and not offline.get("project_id"),
        (p.stdout + p.stderr)[-300:],
    )
    p = run(bind, env=trusted)
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
    check(
        "with SSL_CERT_FILE pointing at the private CA, bind succeeds over TLS",
        p.returncode == 0 and marker.get("project_id"),
        (p.stdout + p.stderr)[-300:],
    )
    pid = marker.get("project_id", "")
    run(
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
    p = run([PY, REPO / "trace_mcp.py", "--selfcheck", "--url", url, "--token", TOKEN], env=trusted)
    check(
        "trace_mcp.py --selfcheck works over TLS with the private CA", p.returncode == 0, (p.stdout + p.stderr)[-200:]
    )
    p = run([PY, REPO / "trace_mcp.py", "--selfcheck", "--url", url, "--token", TOKEN])
    check(
        "trace_mcp.py --selfcheck names the certificate problem without the CA",
        p.returncode == 1 and "CERTIFICATE" in p.stderr.upper(),
        p.stderr[-200:],
    )

    # ---------------- B. delivery over the network (real trace-deliver process) ----------------
    print("\n## B. delivery over TLS")
    data = work / "plugin-data"

    def deliver(target, token=TOKEN, env=None):
        run(
            [cli("trace-deliver"), "--data-dir", data, "--url", target, "--token", token, "--quiet"],
            env=env,
            timeout=300,
        )
        return json.loads((data / "outbox" / "delivery-status.json").read_text(encoding="utf-8"))

    s1 = stage_turn(data, url, proj, "s1", "第一轮", work / "s1.jsonl")
    before = pending_count(s1)
    report = deliver(url)
    check(
        "deliverer without the CA: refused, nothing moved, error names the certificate",
        report["delivered_events"] == 0
        and "CERTIFICATE" in str(report["last_error"]).upper()
        and pending_count(s1) == before,
        report["last_error"],
    )
    report = deliver(url, token="wrong-token", env=trusted)
    check(
        "wrong token: clear 401 hint naming TRACE_TOKEN/--token, pending retained",
        report["delivered_events"] == 0
        and "401" in str(report["last_error"])
        and "TRACE_TOKEN" in str(report["last_error"])
        and pending_count(s1) == before,
        report["last_error"],
    )
    report = deliver(url, env=trusted)
    check(
        "correct token over TLS with SSL_CERT_FILE: delivered, pending drained",
        report["delivered_events"] > 0 and report["failed_batches"] == 0 and pending_count(s1) == 0,
        report["last_error"] or json.dumps(report)[:200],
    )

    redirect_url = f"http://127.0.0.1:{free_port()}"
    start_redirector(int(redirect_url.rsplit(':', 1)[1]), url)
    s2 = stage_turn(data, redirect_url, proj, "s2", "第二轮", work / "s2.jsonl")
    before = pending_count(s2)
    report = deliver(redirect_url, env=trusted)
    check(
        "a plain-http URL that redirects to https is refused with the real cause, pending retained",
        report["delivered_events"] == 0 and "redirected" in str(report["last_error"]) and pending_count(s2) == before,
        report["last_error"],
    )
    report = deliver(url, env={**trusted, "HTTPS_PROXY": "http://127.0.0.1:9"})
    check(
        "an unreachable HTTPS_PROXY fails cleanly (pending retained)",
        report["delivered_events"] == 0 and report["last_error"] and pending_count(s2) == before,
        report["last_error"],
    )
    report = deliver(url, env={**trusted, "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ip})
    check(
        "NO_PROXY=<central host> bypasses the proxy and delivers",
        report["delivered_events"] > 0 and pending_count(s2) == 0,
        report["last_error"] or json.dumps(report)[:200],
    )

    s3 = stage_turn(data, url, proj, "s3", "大 transcript", work / "s3.jsonl", extra_lines=14_000)  # ~13 MB
    report = deliver(url, env=trusted)
    with store._lock:
        chunks = store._db.execute("SELECT COUNT(*) FROM transcript_chunks WHERE project_id=?", (pid,)).fetchone()[0]
    check(
        f"a ~13 MB transcript is delivered in several POSTs under the 6 MiB batch cap ({report['delivered_batches']} batches, {chunks} chunks stored)",
        report["delivered_batches"] >= 3 and report["failed_batches"] == 0 and chunks >= 20 and pending_count(s3) == 0,
        report["last_error"] or json.dumps(report)[:200],
    )

    # ---------------- C. Recorder over TLS (fake claude) ----------------
    print("\n## C. Recorder over TLS")
    log = work / "fake.log"
    p = run(
        [cli("trace-recorder"), "--data-dir", data, "--url", url, "--token", TOKEN, "--claude", FAKE],
        env={**trusted, "FAKE_CLAUDE_MODE": "success", "FAKE_CLAUDE_LOG": str(log)},
    )
    with store._lock:
        nodes = store._db.execute("SELECT COUNT(*) FROM nodes WHERE project_id=?", (pid,)).fetchone()[0]
    check(
        "Recorder fetches context and writes Nodes through the TLS central",
        p.returncode == 0 and nodes >= 1,
        (p.stdout + p.stderr)[-300:],
    )

    # ---------------- D. large uploads ----------------
    print("\n## D. large uploads")
    from research_trace.run_evidence import upload_timeout

    check(
        "evidence upload timeout scales with archive size (100 MiB → > 6 min, small → base)",
        upload_timeout(60, 100 * 1024 * 1024) > 400 and upload_timeout(60, 1024) == 60,
    )

    # ---------------- E. server warns when exposed without OAuth ----------------
    print("\n## E. exposure warning")
    proc = subprocess.Popen(
        [
            cli("trace-server"),
            "--data-dir",
            str(work / "warn-central"),
            "--host",
            "0.0.0.0",
            "--port",
            str(free_port()),
            "--token",
            TOKEN,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(4)
    proc.terminate()
    try:
        _out, err = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        _out, err = proc.communicate()
    check(
        "trace-server on 0.0.0.0 without OAuth prints the loud exposure warning",
        "WITHOUT GitHub OAuth" in err,
        err[-300:],
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
