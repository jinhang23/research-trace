"""Outbox-side code and result uploads; central-side read models use raw events."""

import base64
import hashlib
from pathlib import Path

from .deliver import DeliveryError, _post_json
from .workspace import Workspace, digest


def upload_timeout(base: float, size: int) -> float:
    """A 100 MiB code archive is a ~133 MB JSON POST; a fixed 60 s budget times out on any
    slow link and then retries forever.  Scale with size at a pessimistic 256 KiB/s."""
    return max(float(base), 30.0 + size / (256 * 1024))


def publish_evidence(batch, url, token, timeout):
    if not batch.get('project_id'):
        raise DeliveryError('Bind the project ID before delivering code/run evidence')
    for event in batch['events']:
        payload = event.get('payload') or {}
        run = payload.get('research_run') or {}
        snapshot = run.get('snapshot') or payload.get('code_snapshot')
        if not snapshot:
            continue
        workspace = Workspace(event['project_dir'], require_enabled=False)

        def upload(path, name, key, expected=None, *, tail=False):
            path = Path(path).resolve()
            if not path.is_relative_to(workspace.state.resolve()) or not path.is_file():
                raise DeliveryError('Evidence must be a retained file inside this workspace capture directory')
            if expected and digest(path) != expected:
                raise DeliveryError('Retained code archive failed its SHA256 check')
            size = path.stat().st_size
            if not tail and size > workspace.limit:
                raise DeliveryError('Evidence exceeds configured upload limit')
            with path.open('rb') as stream:
                if tail:
                    stream.seek(max(0, size - 1024 * 1024))
                raw = stream.read()
            sha = hashlib.sha256(raw).hexdigest()
            status, value = _post_json(
                url,
                '/api/attach',
                {
                    'project_id': batch['project_id'],
                    'target_type': 'overview',
                    'target_id': batch['project_id'],
                    'name': name,
                    'data_base64': base64.b64encode(raw).decode(),
                    'sha256': sha,
                    'mime_type': 'application/zip' if path.suffix == '.zip' else 'text/plain; charset=utf-8',
                    'metadata': {
                        'provider': 'research-run',
                        'capture_key': key + ':' + sha,
                        'external_path': str(path),
                        'machine': snapshot['machine'],
                        'original_size': size,
                        'truncated': len(raw) < size,
                    },
                },
                token,
                upload_timeout(timeout, size),
            )
            if not 200 <= status < 300:
                raise DeliveryError(f'Evidence upload failed (HTTP {status}); event remains pending')
            return value['id']

        snapshot['archive_attachment_id'] = upload(
            snapshot['archive_path'],
            '代码 ' + snapshot['commit_hash'][:12] + '.zip',
            'code:' + snapshot['commit_hash'],
            snapshot['archive_sha256'],
        )
        entire = Path(snapshot['archive_path']).parent / 'entire-visible.json'
        if snapshot.get('entire') and entire.is_file():
            snapshot['entire_attachment_id'] = upload(
                entire, 'Entire 会话与来源.json', 'entire:' + snapshot['entire']['checkpoint_id']
            )
        if run.get('state') in {'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY'}:
            for name in ('stdout', 'stderr'):
                if run.get(name) and Path(run[name]).is_file():
                    run[name + '_attachment_id'] = upload(
                        run[name], run['name'] + ' ' + name + '.log', run['id'] + ':' + name, tail=True
                    )
                    run[name + '_truncated'] = Path(run[name]).stat().st_size > 1024 * 1024
