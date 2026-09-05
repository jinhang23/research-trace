"""Read local Entire checkpoints through its CLI and archive code through Git.

This adapter does not install hooks, commit changes, submit jobs or generate
research conclusions. The isolated smoke script exercises those boundaries.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import subprocess
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .visible import visible_content


class EvidenceError(ValueError):
    pass


def visible_transcript(text: str) -> str:
    """Reject partial JSONL and remove hidden fields before central persistence."""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise EvidenceError("Entire transcript is not complete JSONL") from exc
        if not isinstance(value, dict):
            raise EvidenceError("Entire transcript entries must be objects")
        lines.append(json.dumps(visible_content(value), ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def extract_code_archive(archive: bytes, destination: Path, *, max_bytes: int = 100 * 1024 * 1024) -> list[str]:
    """Materialize a new code directory; reject symlinks and path escapes.

    A separate directory freezes source selection for the smoke workload. This
    does not sandbox executed code or freeze external datasets/environments.
    """
    destination = Path(destination).resolve()
    if destination.exists():
        raise EvidenceError("Code snapshot destination must be new")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        members = zipped.infolist()
        if sum(member.file_size for member in members) > max_bytes:
            raise EvidenceError("Code snapshot exceeds configured size limit")
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.filename
                or ":" in member.filename
                or stat.S_ISLNK(mode)
            ):
                raise EvidenceError("Code archive contains an unsafe path or symlink")
            if not (destination / member.filename).resolve().is_relative_to(destination):
                raise EvidenceError("Code archive escapes destination")
        destination.mkdir(parents=True)
        zipped.extractall(destination)
        for member in members:
            if not member.is_dir() and (member.external_attr >> 16) & 0o111:
                (destination / member.filename).chmod(0o755)
    return [m.filename for m in members if not m.is_dir()]


@dataclass(frozen=True)
class CheckpointEvidence:
    commit_hash: str
    checkpoint_id: str
    checkpoint_ref: str
    metadata: dict
    transcripts: tuple[dict, ...]
    code_archive: bytes


class EntireRepository:
    """Explicit local repo + executable; pinned to the tested public CLI shape."""

    def __init__(self, repository: str | Path, executable: str | Path):
        self.repository = Path(repository).resolve()
        self.executable = str(Path(executable).resolve())

    def _run(self, args: list[str]) -> bytes:
        result = subprocess.run(args, cwd=self.repository, capture_output=True, timeout=60, check=False)
        if result.returncode:
            # stderr may contain paths/content; callers can inspect local CLI logs.
            raise EvidenceError(f"Evidence command failed ({result.returncode}): {args[0]}")
        return result.stdout

    def git(self, *args: str) -> bytes:
        return self._run(["git", "-c", f"safe.directory={self.repository.as_posix()}", *args])

    @contextmanager
    def session_stream(self, session_id: str):
        """Official live-session export owns path discovery and partial-line handling."""
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', session_id):
            raise EvidenceError('Invalid Entire session ID')
        process = subprocess.Popen(
            [self.executable, 'session', 'info', session_id, '--transcript'],
            cwd=self.repository,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        timer = threading.Timer(60, process.kill)
        timer.daemon = True
        timer.start()
        try:
            yield process.stdout
            if process.wait(timeout=5):
                raise EvidenceError('Entire live-session export failed; transcript cursor was not advanced')
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    def pending(self):
        value = json.loads(self._run([self.executable, 'checkpoint', 'list', '--pending', '--json']))
        if not isinstance(value, list):
            raise EvidenceError('Unsupported Entire pending-checkpoint response')
        return value

    def matching_pending(self, files, *, session_id=None, since_ns=0, exclude_ids=(), max_code_bytes=100 * 1024 * 1024):
        """Reuse an upstream immutable code tree only when every captured byte matches.

        Metadata and hook settings are excluded from executable archives. Keep
        upstream session/checkpoint identity, without rebuilding its shadow tree.
        """
        for point in self.pending():
            if point.get('id') in exclude_ids:
                continue
            if since_ns:
                try:
                    created = datetime.fromisoformat(point['date'].replace('Z', '+00:00')).timestamp()
                except (KeyError, ValueError, TypeError):
                    continue
                if created < since_ns // 1_000_000_000:
                    continue
            if session_id and point.get('session_id') != session_id:
                continue
            commit = point.get('id', '')
            if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', commit):
                continue
            names = self.git('ls-tree', '-r', '--name-only', '-z', commit).decode().split('\0')
            names = {
                name
                for name in names
                if name and name != '.research-trace.json' and name.split('/')[0] not in {'.claude', '.entire'}
            }
            if names != set(files):
                continue
            # Git exports the upstream tree. Never persist the raw archive,
            # which can also contain Entire's private session metadata.
            raw = self.git('archive', '--format=zip', commit)
            output = io.BytesIO()
            matched = True
            with (
                zipfile.ZipFile(io.BytesIO(raw)) as original,
                zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as clean,
            ):
                selected = [item for item in original.infolist() if item.filename in files]
                if sum(item.file_size for item in selected) > max_code_bytes:
                    raise EvidenceError('Entire code exceeds configured size limit')
                if {item.filename for item in selected} != names:
                    continue
                for item in selected:
                    content = original.read(item)
                    expected = files[item.filename]
                    mode = item.external_attr >> 16
                    if (
                        hashlib.sha256(content).hexdigest() != expected['sha256']
                        or stat.S_ISLNK(mode)
                        or bool(mode & 0o111) != (expected['mode'] == '100755')
                    ):
                        matched = False
                        break
                    clean.writestr(item, content)
            if matched:
                return point, output.getvalue()
        raise EvidenceError('No matching Entire pending checkpoint; the current turn may not have ended yet')

    def collect(self, commit_hash: str, *, max_code_bytes: int = 100 * 1024 * 1024) -> CheckpointEvidence:
        if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit_hash):
            raise EvidenceError("Use a full immutable Git commit hash")
        commit_hash = self.git("rev-parse", "--verify", f"{commit_hash}^{{commit}}").decode().strip()
        message = self.git("show", "-s", "--format=%B", commit_hash).decode("utf-8")
        matches = re.findall(r"^Entire-Checkpoint: ([0-9A-HJKMNP-TV-Z]{26}|[a-f0-9]{12})$", message, re.M)
        if len(matches) != 1:
            raise EvidenceError("Commit must reference exactly one Entire checkpoint")
        checkpoint_id = matches[0]
        refs = self.git("for-each-ref", "--format=%(refname)", "refs/entire/checkpoints/").decode().splitlines()
        local_refs = [ref for ref in refs if ref.endswith("/" + checkpoint_id)]
        if len(local_refs) != 1:
            raise EvidenceError("Checkpoint ref must already be local; automatic remote fetch is not supported")
        raw = self._run([self.executable, "checkpoint", "explain", checkpoint_id, "--json"])
        try:
            metadata = json.loads(raw)
        except ValueError as exc:
            raise EvidenceError("Entire returned invalid checkpoint metadata") from exc
        if not isinstance(metadata, dict) or metadata.get("checkpoint_id") != checkpoint_id:
            raise EvidenceError("Entire checkpoint identity mismatch")
        sessions = metadata.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise EvidenceError("Entire checkpoint has no exported sessions")
        transcripts = []
        for index, session in enumerate(sessions):
            if not isinstance(session, dict) or not session.get("session_id") or session.get("index") != index:
                raise EvidenceError("Unsupported Entire session metadata")
            raw_text = self._run(
                [self.executable, "checkpoint", "explain", checkpoint_id, "--transcript", "--session-index", str(index)]
            ).decode("utf-8")
            transcripts.append({"session_id": session["session_id"], "content": visible_transcript(raw_text)})
        archive = self.git("archive", "--format=zip", commit_hash)
        if len(archive) > max_code_bytes:
            raise EvidenceError("Code archive exceeds configured size limit")
        return CheckpointEvidence(
            commit_hash, checkpoint_id, local_refs[0], visible_content(metadata), tuple(transcripts), archive
        )
