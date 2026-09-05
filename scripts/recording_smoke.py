"""Real code evidence/delivery with synthetic Claude and Slurm inputs.

Never submits or runs a training job. Creates a new disposable project to show
that ordinary agent tool events become sources without an execution wrapper.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from research_trace.deliver import deliver_once, write_marker
from research_trace.workspace import Workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--entire', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8768')
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    project = root / 'project'
    if project.exists():
        raise ValueError('Choose a new disposable project directory')
    project.mkdir(parents=True)
    entire = Path(args.entire).resolve()
    package = Path(__file__).resolve().parents[1]
    os.environ['TRACE_HOOK_NO_SPAWN'] = '1'

    def command(argv, payload=None):
        return subprocess.run(
            [str(x) for x in argv],
            cwd=project,
            capture_output=True,
            text=True,
            input=json.dumps(payload) if payload else None,
            timeout=90,
            check=True,
        ).stdout

    def git(*argv):
        return command(['git', '-c', 'safe.directory=' + project.as_posix(), *argv])

    def api(path, value=None):
        request = Request(
            args.url + path,
            data=json.dumps(value, ensure_ascii=False).encode() if value else None,
            headers={'Content-Type': 'application/json'},
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    git('init', '-b', 'main')
    git('config', 'user.name', 'Passive recording verification')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'core.autocrlf', 'false')
    (project / '.gitignore').write_text('.research-trace.json\n__pycache__/\n')
    (project / 'common.py').write_text('VALUE = 1\n')
    (project / 'model_a').mkdir()
    (project / 'model_a/train.sbatch').write_text('#!/bin/bash\npython -m model_a.train\n')
    git('add', '.')
    git('-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture baseline')
    pid = api('/api/projects', {'name': '被动记录验收 · alpha.24'})['id']
    write_marker(project, project_id=pid, capture=True)
    command(
        [
            sys.executable,
            '-m',
            'research_trace.workspace',
            '--project',
            project,
            'init',
            '--entire',
            entire,
            '--outbox',
            root / 'outbox',
            '--url',
            args.url,
        ]
    )
    spec = importlib.util.spec_from_file_location('recording_hook', package / 'scripts/trace_hook.py')
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    protocol = package / 'hooks/RECORDER_PROTOCOL.md'
    transcript = root / 'synthetic-session.jsonl'
    transcript.write_text('')
    sid = str(uuid.uuid4())
    base = {'session_id': sid, 'cwd': str(project), 'transcript_path': str(transcript)}
    start = {**base, 'hook_event_name': 'SessionStart', 'source': 'startup'}
    command([entire, 'hooks', 'claude-code', 'session-start'], start)
    hook.handle(start, root / 'outbox', protocol)
    transcript.write_text(
        json.dumps(
            {
                'type': 'user',
                'sessionId': sid,
                'message': {
                    'role': 'user',
                    'content': 'Synthetic fixture: record this experiment without managing its directory or submitting it.',
                },
            }
        )
        + '\n'
    )
    command(
        [entire, 'hooks', 'claude-code', 'user-prompt-submit'],
        {**base, 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Record the experiment'},
    )
    (project / 'model_a/train.py').write_text('from common import VALUE\nprint(VALUE)\n')
    workspace = Workspace(project)
    before = workspace.files()
    tool = {
        **base,
        'tool_name': 'Bash',
        'tool_use_id': 'fixture-submit',
        'tool_input': {'command': 'sbatch model_a/train.sbatch'},
    }
    hook.handle({**tool, 'hook_event_name': 'PreToolUse'}, root / 'outbox', protocol)
    # This is a supplied event fixture, not an executed sbatch command.
    hook.handle(
        {**tool, 'hook_event_name': 'PostToolUse', 'tool_response': {'stdout': 'Submitted batch job 12345'}},
        root / 'outbox',
        protocol,
    )
    with transcript.open('a') as stream:
        stream.write(
            json.dumps(
                {
                    'type': 'assistant',
                    'sessionId': sid,
                    'message': {
                        'role': 'assistant',
                        'content': 'Synthetic fixture: submission acknowledged; results remain unknown. An ablation is still untried.',
                    },
                }
            )
            + '\n'
        )
    stop = {**base, 'hook_event_name': 'Stop', 'stop_hook_active': False}
    command([entire, 'hooks', 'claude-code', 'stop'], stop)
    hook.handle(stop, root / 'outbox', protocol)
    assert workspace.files() == before and not (workspace.state / 'runs').exists()
    events = [json.loads(path.read_text()) for path in (root / 'outbox').rglob('pending/*.json')]
    assert any(item['payload'].get('code_snapshot') for item in events)
    delivery = deliver_once(root / 'outbox', url=args.url, token='')
    assert delivery['failed_batches'] == 0, delivery
    node = api(
        '/api/record',
        {
            'project_id': pid,
            'idempotency_key': 'passive-fixture',
            'title': '原始 sbatch 命令和代码版本已保存，实验结果尚未确认',
            'body': '这是被动记录验收，非真实研究结果。Claude 对话、sbatch 命令及 job ID 为合成输入，没有执行训练或提交 UF 作业。\n\n'
            '实际运行了 Entire/Git 取证与中央投递。取证前后原目录内容一致，没有创建运行副本。节点可展开原命令、提交回执和代码附件。后续消融方向尚未实施。',
            'source_event_ids': [item['event_id'] for item in events],
        },
    )
    assert node['runs'] == [] and node['code_snapshots'][0]['archive_attachment_id']
    assert node['code_snapshots'][0]['provider'] == 'entire'
    report = {
        'project_id': pid,
        'node_id': node['id'],
        'unchanged_directory': True,
        'created_execution_directory': False,
        'submitted_job': False,
        'code_provider': 'entire',
        'source_count': len(events),
        'url': args.url,
    }
    (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
