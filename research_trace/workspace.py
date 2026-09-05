"""Passive project code evidence through Entire and Git.

This module does not submit, run, cancel or replay experiments, create execution
directories, change training paths, or enforce immutability. The research agent
owns those decisions. Captures preserve code bytes for later inspection only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

from filelock import FileLock

from .deliver import default_data_dir, find_marker, long_path, project_binding, read_marker


class WorkspaceError(ValueError):
    pass


def atomic_json(path, value):
    path = long_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + uuid.uuid4().hex[:12] + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


class Workspace:
    def __init__(self, path='.', *, require_enabled=True):
        self.binding = project_binding(path)
        if not self.binding and not require_enabled:
            marker = find_marker(path)
            if marker:
                value = read_marker(marker)
                self.binding = {
                    **value,
                    'marker_path': str(marker),
                    'project_dir': str(marker.parent),
                    'workspace_keys': value.get('workspace_keys') or [value.get('workspace_key')],
                }
        if not self.binding:
            raise WorkspaceError('Bind this project first with trace-project bind')
        self.root = Path(self.binding['project_dir']).resolve()
        self.config = read_marker(self.binding['marker_path']).get('code_capture') or {}
        if require_enabled and not self.config.get('enabled'):
            raise WorkspaceError('Enable prospective code capture with trace-code init')
        top = Path(self.git('rev-parse', '--show-toplevel').decode().strip()).resolve()
        if top != self.root:
            raise WorkspaceError('Bind the Git repository root so model folders and shared code are captured together')
        common = Path(self.git('rev-parse', '--git-common-dir').decode().strip())
        if not common.is_absolute():
            common = self.root / common
        self.state = long_path(
            common.resolve() / 'research-trace' / hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        )
        self.state.mkdir(parents=True, exist_ok=True)
        self.limit = int(self.config.get('max_code_bytes', 100 * 1024 * 1024))

    def git(self, *args, data=None, env=None):
        result = subprocess.run(
            ['git', '-c', f'safe.directory={self.root.as_posix()}', '-c', 'core.longpaths=true', *args],
            cwd=self.root,
            input=data,
            capture_output=True,
            env=env,
            timeout=60,
        )
        if result.returncode:
            raise WorkspaceError(f'Git {args[0]} failed: {result.stderr.decode("utf-8", errors="replace")[:500]}')
        return result.stdout

    def head(self):
        return self.git('rev-parse', '--verify', 'HEAD').decode().strip()

    def files(self):
        if self.git('ls-files', '--unmerged'):
            raise WorkspaceError('Resolve Git merge conflicts before capturing executable code')
        tracked_modes = {}
        for entry in self.git('ls-files', '--stage', '-z').decode('utf-8').split('\0'):
            if entry:
                metadata, name = entry.split('\t', 1)
                tracked_modes[name] = metadata.split()[0]
        names = self.git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode('utf-8').split('\0')
        selected = {}
        total = 0
        for name in sorted(set(names)):
            if not name or name == '.research-trace.json' or name.split('/')[0] in {'.claude', '.entire'}:
                continue
            path = self.root / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise WorkspaceError(f'Code capture requires regular files; review symlink/submodule: {name}')
            if not path.exists():
                continue  # a tracked deletion belongs to this snapshot
            if Path(name).name in {'.env', 'id_rsa', 'id_ed25519'} or Path(name).suffix in {'.pem', '.key'}:
                raise WorkspaceError(f'Credential-like file is not captured: {name}; move it outside code or ignore it')
            total += path.stat().st_size
            if total > self.limit:
                raise WorkspaceError(
                    'Code exceeds max_code_bytes; put large datasets/weights in .gitignore and register them as data references'
                )
            mode = (
                tracked_modes.get(name, '100644')
                if os.name == 'nt'
                else ('100755' if path.stat().st_mode & 0o111 else '100644')
            )
            selected[name] = {'sha256': digest(path), 'mode': mode}
        return selected

    def _entire_snapshot(self, files, head, session_id):
        from .entire_evidence import EntireRepository

        repository = EntireRepository(self.root, self.config['entire_executable'])
        point, archive = repository.matching_pending(
            files,
            session_id=session_id,
            since_ns=self.config.get('started_at', 0),
            exclude_ids=self.config.get('entire_start_checkpoints', ()),
            max_code_bytes=self.limit,
        )
        if self.files() != files or self.head() != head:
            raise WorkspaceError('Code changed while checking Entire checkpoint; retry after edits finish')
        commit = point['id']
        # Pin upstream objects so cleanup of its temporary shadow branch cannot
        # remove the source of a retained research record.
        ref = 'refs/research-trace/checkpoints/' + commit
        self.git('update-ref', ref, commit)
        folder = self.state / 'snapshots' / commit
        folder.mkdir(parents=True, exist_ok=True)
        archive_path = folder / 'code.zip'
        archive_path.write_bytes(archive)
        entire = {
            'provider': 'entire',
            'checkpoint_id': point.get('condensation_id') or commit,
            'checkpoint_kind': 'persistent' if point.get('is_logs_only') else 'pending',
            'commit_hash': commit,
            'matches_snapshot': True,
            'sessions': [{'session_id': point.get('session_id')}],
        }
        result = {
            'schema': 'research-trace.code.v1',
            'provider': 'entire',
            'commit_hash': commit,
            'base_commit': head,
            'tree': self.git('rev-parse', commit + '^{tree}').decode().strip(),
            'git_ref': ref,
            'files': files,
            'machine': socket.gethostname(),
            'archive_path': str(archive_path),
            'archive_sha256': digest(archive_path),
            'captured_at': time.time_ns(),
            'session_id': session_id,
            'entire': entire,
            'coverage': 'Entire code tree, excluding hook/session metadata; external data and environment are references',
        }
        atomic_json(
            folder / 'entire-visible.json',
            {
                'metadata': point,
                'transcripts': [],
                'transcript_source': 'Entire live session export is delivered as separate visible transcript chunks',
            },
        )
        atomic_json(folder / 'snapshot.json', result)
        atomic_json(self.state / 'latest.json', result)
        return result

    def snapshot(self, *, session_id=None, require_entire=False):
        with FileLock(str(self.state / 'capture.lock'), timeout=30):
            files = self.files()
            if not files:
                raise WorkspaceError('No code files selected')
            head = self.head()
            entire_gap = None
            if self.config.get('entire_executable'):
                try:
                    return self._entire_snapshot(files, head, session_id)
                except Exception as exc:
                    if require_entire:
                        raise
                    entire_gap = str(exc)
            previous_path = self.state / 'latest.json'
            previous = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
            if (
                previous.get('files') == files
                and previous.get('base_commit') == head
                and Path(previous['archive_path']).is_file()
                and digest(previous['archive_path']) == previous['archive_sha256']
            ):
                return previous
            index = self.state / ('index-' + uuid.uuid4().hex)
            # Git for Windows accepts long paths with core.longpaths, but its
            # lock-file API does not accept Python's extended-path prefix.
            git_index = str(index)
            if git_index.startswith('\\\\?\\UNC\\'):
                git_index = '\\\\' + git_index[8:]
            elif git_index.startswith('\\\\?\\'):
                git_index = git_index[4:]
            env = dict(
                os.environ,
                GIT_INDEX_FILE=git_index,
                GIT_AUTHOR_NAME='Research Trace',
                GIT_AUTHOR_EMAIL='trace@localhost',
                GIT_COMMITTER_NAME='Research Trace',
                GIT_COMMITTER_EMAIL='trace@localhost',
            )
            try:
                self.git('read-tree', '--empty', env=env)
                entries = []
                for name, info in files.items():
                    raw = (self.root / name).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != info['sha256']:
                        raise WorkspaceError('Code changed during capture; retry after concurrent edits finish')
                    blob = self.git('hash-object', '-w', '--stdin', data=raw).decode().strip()
                    entries.append(f"{info['mode']} {blob}\t{name}\0")
                self.git('update-index', '-z', '--index-info', data=''.join(entries).encode(), env=env)
                tree = self.git('write-tree', env=env).decode().strip()
                if self.files() != files:
                    raise WorkspaceError('Code changed during capture; retry after concurrent edits finish')
                if tree == self.git('rev-parse', head + '^{tree}').decode().strip():
                    commit = head
                else:
                    parents = ['-p', head]
                    if previous.get('commit_hash') and previous['commit_hash'] != head:
                        parents += ['-p', previous['commit_hash']]
                    commit = (
                        self.git('commit-tree', tree, *parents, data=b'Research Trace workspace checkpoint\n', env=env)
                        .decode()
                        .strip()
                    )
                ref = 'refs/research-trace/checkpoints/' + commit
                self.git('update-ref', ref, commit)
                folder = self.state / 'snapshots' / commit
                folder.mkdir(parents=True, exist_ok=True)
                archive = self.git('archive', '--format=zip', commit)
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                    names = {m.filename for m in zipped.infolist() if not m.is_dir()}
                    if names != set(files):
                        raise WorkspaceError('Git export-ignore omitted code; remove export-ignore for captured files')
                archive_path = folder / 'code.zip'
                archive_path.write_bytes(archive)
                result = {
                    'schema': 'research-trace.code.v1',
                    'provider': 'git-code-snapshot',
                    'entire_gap': entire_gap,
                    'commit_hash': commit,
                    'base_commit': head,
                    'tree': tree,
                    'git_ref': ref,
                    'files': files,
                    'machine': socket.gethostname(),
                    'archive_path': str(archive_path),
                    'archive_sha256': digest(archive_path),
                    'captured_at': time.time_ns(),
                    'session_id': session_id,
                    'entire': None,
                    'coverage': 'repository code; external data and environment are references',
                }
                executable = self.config.get('entire_executable')
                if executable and head != self.config.get('start_commit'):
                    try:
                        from .entire_evidence import EntireRepository

                        evidence = EntireRepository(self.root, executable).collect(head, max_code_bytes=self.limit)
                        result['entire'] = {
                            'checkpoint_id': evidence.checkpoint_id,
                            'checkpoint_ref': evidence.checkpoint_ref,
                            'commit_hash': head,
                            'matches_snapshot': head == commit,
                            'sessions': evidence.metadata['sessions'],
                        }
                        atomic_json(
                            folder / 'entire-visible.json',
                            {'metadata': evidence.metadata, 'transcripts': evidence.transcripts},
                        )
                    except Exception as exc:
                        result['entire_gap'] = str(exc)
                atomic_json(folder / 'snapshot.json', result)
                atomic_json(previous_path, result)
                return result
            finally:
                index.unlink(missing_ok=True)


def capture_phase(binding, session_id):
    if not (read_marker(binding['marker_path']).get('code_capture') or {}).get('enabled'):
        return None
    workspace = Workspace(binding['project_dir'])
    # Entire-enabled phase recording never builds a competing shadow history.
    # Missing/stale upstream checkpoints are surfaced as capture gaps by hook.
    from .entire_evidence import EvidenceError

    for delay in (0, 0.25, 0.5, 1):
        if delay:
            time.sleep(delay)
        try:
            return workspace.snapshot(
                session_id=session_id, require_entire=bool(workspace.config.get('entire_executable'))
            )
        except EvidenceError as exc:
            # Native Claude hooks may run concurrently. Briefly allow Entire's
            # Stop hook to finish; never substitute an earlier mismatching tree.
            if delay == 1 or 'No matching Entire' not in str(exc):
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init', help='enable prospective code evidence on a bound Git project')
    init.add_argument('--entire', help='official Entire executable; defaults to entire on PATH')
    init.add_argument('--outbox', default=default_data_dir())
    init.add_argument('--url', default=os.environ.get('TRACE_URL', 'http://127.0.0.1:8765'))
    init.add_argument('--max-code-mb', type=int, default=100)
    sub.add_parser('snapshot', help='save code evidence without changing the experiment directory')
    sub.add_parser('list', help='read retained code evidence')
    args = parser.parse_args(argv)
    try:
        workspace = Workspace(args.project, require_enabled=args.action != 'init')
        if args.action == 'init':
            marker = read_marker(workspace.binding['marker_path'])
            if (marker.get('code_capture') or {}).get('enabled'):
                raise WorkspaceError('Already enabled; keep the original capture start instead of reinitializing')
            if args.max_code_mb < 1:
                raise WorkspaceError('--max-code-mb must be positive')
            config = {
                'enabled': True,
                'start_commit': workspace.head(),
                'started_at': time.time_ns(),
                'outbox': str(Path(args.outbox or workspace.state / 'delivery').resolve()),
                'url': args.url,
                'max_code_bytes': args.max_code_mb * 1024 * 1024,
            }
            entire = args.entire or shutil.which('entire')
            if entire:
                executable = shutil.which(entire) or str(Path(entire).resolve())
                subprocess.run(
                    [
                        executable,
                        'enable',
                        '--agent',
                        'claude-code',
                        '--no-init-repo',
                        '--local',
                        '--skip-push-sessions',
                        '--telemetry=false',
                        '--absolute-git-hook-path',
                    ],
                    cwd=workspace.root,
                    check=True,
                    timeout=60,
                )
                # Entire's Claude commands otherwise look up `entire` on PATH,
                # even when enable was invoked through an explicit binary path.
                settings_path = workspace.root / '.claude/settings.json'
                settings = json.loads(settings_path.read_text(encoding='utf-8'))
                for groups in settings.get('hooks', {}).values():
                    for group in groups:
                        for hook in group.get('hooks', []):
                            command = hook.get('command', '')
                            match = re.fullmatch(
                                r"sh -c 'if ! command -v entire .*; exec entire hooks claude-code ([a-z-]+)'", command
                            )
                            if match:
                                binary = Path(executable).as_posix()
                                hook['command'] = 'sh -c ' + shlex.quote(
                                    'exec ' + shlex.quote(binary) + ' hooks claude-code ' + match[1]
                                )
                atomic_json(settings_path, settings)
                config['entire_executable'] = executable
                from .entire_evidence import EntireRepository

                config['entire_start_checkpoints'] = [
                    point['id'] for point in EntireRepository(workspace.root, executable).pending()
                ]
            workspace.config = config
            workspace.limit = config['max_code_bytes']
            result = workspace.snapshot()
            marker['code_capture'] = config
            atomic_json(workspace.binding['marker_path'], marker)
        elif args.action == 'snapshot':
            result = workspace.snapshot()
        else:
            result = [
                json.loads(path.read_text(encoding='utf-8'))
                for path in sorted((workspace.state / 'snapshots').glob('*/snapshot.json'))
            ]

        def summary(value):
            output = dict(value)
            if 'files' in output:
                output['file_count'] = len(output.pop('files'))
            return output

        print(
            json.dumps(
                [summary(item) for item in result] if isinstance(result, list) else summary(result),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(f'Research Trace: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
