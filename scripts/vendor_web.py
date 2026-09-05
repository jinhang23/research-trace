"""Fetch pinned upstream browser bundles from npm, verifying registry integrity.

No npm scripts are executed. Kept to make vendored updates reviewable/repeatable.
"""

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1] / 'research_trace/static'
PACKAGES = [
    ('markdown-it', '14.1.0', 'dist/markdown-it.min.js', 'markdown-it.min.js'),
    ('@dagrejs/dagre', '1.1.8', 'dist/dagre.min.js', 'dagre.min.js'),
]


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, version, source, target in PACKAGES:
        with urlopen(f'https://registry.npmjs.org/{name}/{version}', timeout=30) as response:
            metadata = json.load(response)
        with urlopen(metadata['dist']['tarball'], timeout=30) as response:
            data = response.read()
        algorithm, expected = metadata['dist']['integrity'].split('-', 1)
        assert base64.b64encode(hashlib.new(algorithm, data).digest()).decode() == expected
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            payload = archive.extractfile('package/' + source).read()
            (ROOT / target).write_bytes(payload)
            license_path = next(n for n in archive.getnames() if n.lower() in ('package/license', 'package/license.md'))
            (ROOT / (target + '.LICENSE')).write_bytes(archive.extractfile(license_path).read())
        manifest.append(
            {
                'package': name,
                'version': version,
                'url': metadata['dist']['tarball'],
                'integrity': metadata['dist']['integrity'],
                'file': target,
                'sha256': hashlib.sha256(payload).hexdigest(),
            }
        )
    (ROOT / 'vendor.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
