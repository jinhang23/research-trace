"""Account-based device login and local Research Trace credential storage."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any


class DeviceLoginError(RuntimeError):
    pass


def normalize_server_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise DeviceLoginError("Research Trace URL must be an absolute http(s) URL")
    return url


def default_credential_file() -> Path:
    configured = os.environ.get("TRACE_V2_CREDENTIAL_FILE")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return (Path(os.environ["APPDATA"]) / "ResearchTrace" / "credentials.json").resolve()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return (base / "research-trace" / "credentials.json").expanduser().resolve()


def _read_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "credentials": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeviceLoginError(f"invalid credential file {path}: {exc}") from exc
    if value.get("version") != 1 or not isinstance(value.get("credentials"), dict):
        raise DeviceLoginError(f"unsupported credential file format: {path}")
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_device_credential(path: str | os.PathLike[str] | None, url: str) -> dict[str, Any] | None:
    target = Path(path).expanduser().resolve() if path else default_credential_file()
    key = normalize_server_url(url)
    value = _read_store(target).get("credentials", {}).get(key)
    if not isinstance(value, dict) or not str(value.get("credential") or "").startswith("rtv2d_"):
        return None
    return dict(value)


def save_device_credential(
    path: str | os.PathLike[str] | None, url: str, response: dict[str, Any]
) -> Path:
    target = Path(path).expanduser().resolve() if path else default_credential_file()
    key = normalize_server_url(url)
    credential = str(response.get("credential") or "")
    if not credential.startswith("rtv2d_"):
        raise DeviceLoginError("server did not return a Research Trace device credential")
    store = _read_store(target)
    store["credentials"][key] = {
        "credential": credential,
        "device": response.get("device") or {},
        "user": response.get("user") or {},
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _atomic_write(target, store)
    return target


def remove_device_credential(path: str | os.PathLike[str] | None, url: str) -> Path:
    target = Path(path).expanduser().resolve() if path else default_credential_file()
    store = _read_store(target)
    store["credentials"].pop(normalize_server_url(url), None)
    _atomic_write(target, store)
    return target


def _pending_file(path: str | os.PathLike[str] | None) -> Path:
    target = Path(path).expanduser().resolve() if path else default_credential_file()
    return target.with_name(target.name + ".pending")


def save_pending_login(
    path: str | os.PathLike[str] | None, url: str, value: dict[str, Any]
) -> None:
    target = _pending_file(path)
    store = _read_store(target)
    store["credentials"][normalize_server_url(url)] = dict(value)
    _atomic_write(target, store)


def load_pending_login(
    path: str | os.PathLike[str] | None, url: str
) -> dict[str, Any] | None:
    value = _read_store(_pending_file(path))["credentials"].get(normalize_server_url(url))
    return dict(value) if isinstance(value, dict) else None


def clear_pending_login(path: str | os.PathLike[str] | None, url: str) -> None:
    target = _pending_file(path)
    store = _read_store(target)
    store["credentials"].pop(normalize_server_url(url), None)
    _atomic_write(target, store)


def request_json(
    url: str, method: str, path: str, value: dict[str, Any] | None = None,
    *, credential: str | None = None, timeout: float = 30,
) -> tuple[int, dict[str, Any]]:
    data = None if value is None else json.dumps(value).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    request = urllib.request.Request(
        normalize_server_url(url) + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": raw or exc.reason}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise DeviceLoginError(f"Research Trace unavailable at {url}: {exc.reason}") from exc


def start_login(url: str, device_name: str) -> dict[str, Any]:
    status, value = request_json(url, "POST", "/api/v2/device/start", {"device_name": device_name})
    if status != 200:
        raise DeviceLoginError(str(value.get("error") or value.get("detail") or value))
    return value


def poll_login(url: str, device_code: str) -> dict[str, Any]:
    status, value = request_json(
        url, "POST", "/api/v2/device/token", {"device_code": device_code}
    )
    if status in {200, 202}:
        return value
    raise DeviceLoginError(str(value.get("error") or value.get("detail") or value.get("status") or value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Log this machine in to Research Trace with GitHub")
    parser.add_argument("--url", default=os.environ.get("TRACE_V2_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--device-name", default=socket.gethostname())
    parser.add_argument("--credential-file", default=os.environ.get("TRACE_V2_CREDENTIAL_FILE"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--logout", action="store_true")
    args = parser.parse_args(argv)
    try:
        url = normalize_server_url(args.url)
        if args.logout:
            current = load_device_credential(args.credential_file, url)
            if current:
                status, value = request_json(
                    url, "DELETE", "/api/v2/device/self",
                    credential=current["credential"],
                )
                if status not in {200, 401}:
                    raise DeviceLoginError(str(value.get("error") or value.get("detail") or value))
            target = remove_device_credential(args.credential_file, url)
            clear_pending_login(args.credential_file, url)
            print(f"Research Trace device credential removed: {target}")
            return 0
        started = start_login(url, args.device_name)
        print(f"Open: {started['verification_uri_complete']}", flush=True)
        print(f"Code: {started['user_code']}", flush=True)
        if not args.no_browser:
            try:
                webbrowser.open(started["verification_uri_complete"])
            except Exception:
                pass
        deadline = time.monotonic() + int(started.get("expires_in", 600))
        interval = max(int(started.get("interval", 3)), 1)
        while time.monotonic() < deadline:
            result = poll_login(url, started["device_code"])
            if result.get("status") == "authorized":
                target = save_device_credential(args.credential_file, url, result)
                print(
                    f"Logged in as @{result['user']['login']} on {result['device']['name']}. "
                    f"Credential saved to {target}"
                )
                return 0
            time.sleep(interval)
        raise DeviceLoginError("device login expired; run trace-v2-login again")
    except (DeviceLoginError, KeyboardInterrupt) as exc:
        print(str(exc) or "device login cancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
