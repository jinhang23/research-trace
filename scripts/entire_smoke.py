"""Real Entire/Git + local toy executions; synthetic Claude hook input only.

No model API, HPC job, W&B upload, user transcript or existing research is used.
Run with the project's Python environment and an explicit Entire executable.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from research_trace.backup import export_backup, restore_backup
from research_trace.entire_evidence import EntireRepository, extract_code_archive
from research_trace.storage import Conflict, Store

TRAIN = '''import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.features import SCALE
parser = argparse.ArgumentParser()
parser.add_argument('--config', required=True)
args = parser.parse_args()
config = json.loads(Path(args.config).read_text())
prediction = SCALE * sum([1, 2, 3]) / 3 + config['bias']
print(json.dumps({'synthetic': True, 'scale': SCALE, 'config': config,
                  'prediction': prediction, 'loss': (prediction - 2) ** 2}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entire', required=True)
    parser.add_argument('--directory', default='.integration-demo/entire-live')
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    if root.exists():
        raise SystemExit('Choose a new --directory; existing runs are preserved')
    root.mkdir(parents=True)
    repo = root / 'workspace'
    repo.mkdir()
    transcript_dir = root / 'synthetic-transcripts'
    transcript_dir.mkdir()
    exe = Path(args.entire).resolve()
    global_config = root / 'isolated-gitconfig'
    global_config.write_text('', encoding='utf-8')
    env = dict(
        os.environ,
        PATH=str(exe.parent) + os.pathsep + os.environ.get('PATH', ''),
        GIT_CONFIG_GLOBAL=str(global_config),
        GIT_CONFIG_NOSYSTEM='1',
        GIT_TERMINAL_PROMPT='0',
        PYTHONDONTWRITEBYTECODE='1',
        PYTHONUTF8='1',
    )
    commands = []

    def run(command, *, cwd=repo, payload=None):
        command = [str(x) for x in command]
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            input=json.dumps(payload) if payload is not None else None,
            text=True,
            encoding='utf-8',
            timeout=90,
        )
        commands.append(
            {'args': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        )
        (root / 'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2), encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'{command[0]} failed: {result.stderr[-3000:]}')
        return result.stdout

    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def commit(message):
        run(['git', '-c', 'commit.gpgsign=false', 'commit', '-m', message])
        return run(['git', 'rev-parse', 'HEAD']).strip()

    run(['git', 'init', '--initial-branch=codex/entire-smoke'])
    run(['git', 'config', 'user.name', 'Research Trace synthetic test'])
    run(['git', 'config', 'user.email', 'test@example.invalid'])
    run(['git', 'config', 'core.autocrlf', 'false'])
    write(repo / '.gitignore', '.claude/\n.entire/\n__pycache__/\n')
    write(repo / 'README.md', 'Synthetic code versioning test. No research results.\n')
    write(repo / 'common/features.py', 'SCALE = 1\n')
    run(['git', 'add', '.gitignore', 'README.md', 'common'])
    baseline = commit('Synthetic initial code baseline')
    version = run([exe, 'version']).splitlines()[0]
    run(
        [
            exe,
            'enable',
            '--agent',
            'claude-code',
            '--no-init-repo',
            '--local',
            '--skip-push-sessions',
            '--telemetry=false',
            '--absolute-git-hook-path',
        ]
    )
    assert json.loads(run([exe, 'checkpoint', 'list', '--json'])) == []
    reader = EntireRepository(repo, exe)

    def session(prompt, files, answer, *, make_commit):
        sid = str(uuid.uuid4())
        transcript = transcript_dir / f'{sid}.jsonl'
        transcript.write_text('', encoding='utf-8')
        payload = {
            'session_id': sid,
            'transcript_path': str(transcript),
            'cwd': str(repo),
            'permission_mode': 'default',
        }

        def hook(name, **extra):
            return run([exe, 'hooks', 'claude-code', name], payload={**payload, **extra})

        def append(kind, content):
            value = {
                'type': kind,
                'uuid': str(uuid.uuid4()),
                'sessionId': sid,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'cwd': str(repo),
                'message': {'role': kind, 'content': content},
            }
            with transcript.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(value, ensure_ascii=False) + '\n')

        hook('session-start', hook_event_name='SessionStart', source='startup')
        append('user', prompt)
        hook('user-prompt-submit', hook_event_name='UserPromptSubmit', prompt=prompt)
        for name, text in files.items():
            write(repo / name, text)
            tool_id = str(uuid.uuid4())
            append(
                'assistant',
                [
                    {
                        'type': 'tool_use',
                        'id': tool_id,
                        'name': 'Write',
                        'input': {'file_path': str(repo / name), 'content': text},
                    }
                ],
            )
            append('user', [{'type': 'tool_result', 'tool_use_id': tool_id, 'content': 'File written'}])
        append('assistant', [{'type': 'text', 'text': answer}])
        hook('stop', hook_event_name='Stop', stop_hook_active=False)
        sha = None
        if make_commit:
            run(['git', 'add', '--', *files])
            sha = commit(make_commit)
        hook('session-end', hook_event_name='SessionEnd', reason='other')
        return sha, sid, transcript

    commit_a, _, _ = session(
        '模拟探索：先验证模型 A，公共代码 SCALE=1。',
        {
            'model_a/train.py': TRAIN,
            'model_a/config.json': json.dumps({'bias': 0, 'learning_rate': 0.01}),
        },
        '模型 A 准备完成。这只是版本记录测试，尚无真实蛋白质–配体实验结果。',
        make_commit='Synthetic model A',
    )
    evidence_a = reader.collect(commit_a)
    snapshot_a = root / 'runs/model-a/source'
    extract_code_archive(evidence_a.code_archive, snapshot_a)

    commit_b, _, _ = session(
        '模拟探索：准备模型 B，同时修改外层公共代码。',
        {
            'common/features.py': 'SCALE = 2\n',
            'model_b/train.py': TRAIN,
            'model_b/config.json': json.dumps({'bias': 1, 'learning_rate': 0.02}),
        },
        '模型 B 使用 SCALE=2；后续应比较各版本的真实结果。',
        make_commit='Synthetic model B with updated shared code',
    )
    evidence_b = reader.collect(commit_b)
    snapshot_b = root / 'runs/model-b/source'
    extract_code_archive(evidence_b.code_archive, snapshot_b)

    # Jobs start after the live workspace changed again, like queued jobs starting late.
    write(repo / 'common/features.py', 'SCALE = 999\n')
    tasks = []
    for label, snapshot in [('a', snapshot_a), ('b', snapshot_b)]:
        tasks.append(
            subprocess.Popen(
                [sys.executable, '-B', f'model_{label}/train.py', '--config', f'model_{label}/config.json'],
                cwd=snapshot,
                env=env,
                text=True,
                encoding='utf-8',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    outputs = []
    for process in tasks:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        outputs.append(json.loads(stdout))
    assert [item['scale'] for item in outputs] == [1, 2]
    assert (repo / 'common/features.py').read_text() == 'SCALE = 999\n'
    for label, output in zip(('a', 'b'), outputs):
        write(root / f'runs/model-{label}/result.json', json.dumps(output, indent=2))

    before_idea = run(['git', 'rev-parse', 'HEAD']).strip()
    _, idea_sid, idea_transcript = session(
        '尚未实施的想法：以后按配体骨架划分验证集，检查是否有数据泄漏。',
        {},
        '保留这个待探索方向；本轮没有实施、没有产生新实验。',
        make_commit=None,
    )
    assert run(['git', 'rev-parse', 'HEAD']).strip() == before_idea

    # Only durable checkpoint refs plus code history are backed up; no shadow branch.
    bundle = root / 'evidence.bundle'
    run(
        [
            'git',
            'bundle',
            'create',
            bundle,
            'refs/heads/codex/entire-smoke',
            evidence_a.checkpoint_ref,
            evidence_b.checkpoint_ref,
        ]
    )
    run(['git', 'bundle', 'verify', bundle])
    restored = root / 'restored'
    restored.mkdir()
    run(['git', 'init', '--initial-branch=codex/restored'], cwd=restored)
    refspecs = [
        f'{ref}:{ref}'
        for ref in ('refs/heads/codex/entire-smoke', evidence_a.checkpoint_ref, evidence_b.checkpoint_ref)
    ]
    run(['git', 'fetch', bundle, *refspecs], cwd=restored)
    run(['git', 'checkout', '--detach', commit_a], cwd=restored)
    recovered = EntireRepository(restored, exe).collect(commit_a)
    assert recovered.code_archive == evidence_a.code_archive
    assert recovered.transcripts == evidence_a.transcripts
    restored_source = root / 'restored-run/source'
    extract_code_archive(recovered.code_archive, restored_source)
    replayed_output = json.loads(
        run([sys.executable, '-B', 'model_a/train.py', '--config', 'model_a/config.json'], cwd=restored_source)
    )
    assert replayed_output == outputs[0]

    store = Store(root / 'trace-data')
    project = store.create_project(
        '代码与探索追溯试验（模拟数据）',
        overview=(
            '验证两个模型共用公共代码时的版本留存，以及未实施想法的记录。'
            '使用真实 Entire/Git 和本地小程序；Claude 会话及摘要为预置测试材料，未提交 UF 作业或创建 W&B 实验。'
        ),
    )
    pid = project['id']
    chapter = store.create_chapter(pid, '并行模型探索', summary='两个模型引用不同的公共代码版本；未实施想法独立保留。')

    def ingest(sid, text, key):
        event_ids = []
        events = []
        for index, line in enumerate(text.splitlines()):
            payload = json.loads(line)
            event_id = f'{key}:{index}'
            event_ids.append(event_id)
            events.append({'event_id': event_id, 'event_type': payload.get('type', 'unknown'), 'payload': payload})
        batch = dict(
            batch_id=key,
            project_id=pid,
            session={'id': sid, 'source': 'entire-synthetic-smoke', 'cwd': str(repo), 'metadata': {'synthetic': True}},
            agents=[],
            events=events,
            transcript_chunks=[{'chunk_id': hashlib.sha256(key.encode()).hexdigest(), 'content': text}],
        )
        assert store.ingest(**batch)['duplicate'] is False
        assert store.ingest(**batch)['duplicate'] is True
        return event_ids

    source_ids = []
    code_evidence = []
    for label, evidence in [('A', evidence_a), ('B', evidence_b)]:
        for index, transcript in enumerate(evidence.transcripts):
            source_ids.extend(
                ingest(transcript['session_id'], transcript['content'], f'entire:{evidence.checkpoint_id}:{index}')
            )
        with zipfile.ZipFile(io.BytesIO(evidence.code_archive)) as archive:
            text = archive.read('common/features.py').decode()
        code_evidence.append(
            {
                'file_path': 'common/features.py',
                'commit_hash': evidence.commit_hash,
                'snippet': text,
                'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'attribution': 'exact',
                'annotation': f'模型 {label} 实际运行的公共代码；Entire {evidence.checkpoint_id}',
            }
        )
    node = store.record_node(
        pid,
        chapter_id=chapter['id'],
        idempotency_key='comparison',
        title='公共代码变化后，两个模型仍能运行各自版本',
        body=(
            '本轮验证代码留存机制。模型 A 保存后，外层公共代码由 SCALE=1 改为 2，再保存模型 B。'
            '工作目录继续改为 999 后，同时启动保存的两份代码，仍分别得到 SCALE=1 和 2。\n\n'
            '两次运行合并为这条探索记录，下面可检查公共代码、下载完整代码快照和查看执行清单。'
            '这是合成程序测试，不能据此判断任何模型方法有效。\n\n'
            '下一步是在 UF 验证实际提交入口。当前没有真实 SLURM job ID 或 W&B run，未生成虚假曲线链接。'
        ),
        labels=['模拟验证', '代码版本'],
        source_event_ids=source_ids,
        code_evidence=code_evidence,
    )
    for label, evidence, output in zip(('A', 'B'), (evidence_a, evidence_b), outputs):
        store.attach(
            pid,
            target_type='node',
            target_id=node['id'],
            name=f'模型 {label} 完整代码.zip',
            data_base64=base64.b64encode(evidence.code_archive).decode(),
            mime_type='application/zip',
            direction='input',
            metadata={
                'provider': 'entire',
                'checkpoint_id': evidence.checkpoint_id,
                'commit_hash': evidence.commit_hash,
            },
        )
        manifest = {
            'schema': 'research-trace.entire-smoke.v1',
            'synthetic': True,
            'execution': 'local-subprocess',
            'slurm_job_id': None,
            'wandb_url': None,
            'entrypoint': f'model_{label.lower()}/train.py',
            'commit_hash': evidence.commit_hash,
            'command': [
                'python',
                '-B',
                f'model_{label.lower()}/train.py',
                '--config',
                f'model_{label.lower()}/config.json',
            ],
            'working_directory': 'extracted code archive root',
            'python_version': sys.version,
            'external_dependencies': [],
            'checkpoint': evidence.metadata,
            'output': output,
            'source_archive_sha256': hashlib.sha256(evidence.code_archive).hexdigest(),
        }
        store.attach(
            pid,
            target_type='node',
            target_id=node['id'],
            name=f'模型 {label} 运行与来源.json',
            data_base64=base64.b64encode(json.dumps(manifest, ensure_ascii=False, indent=2).encode()).decode(),
            mime_type='application/json',
            direction='output',
            metadata={'provider': 'entire', 'synthetic': True},
        )
    idea_sources = ingest(idea_sid, idea_transcript.read_text(encoding='utf-8'), 'uncommitted-idea:' + idea_sid)
    idea = store.record_node(
        pid,
        chapter_id=chapter['id'],
        idempotency_key='untried-idea',
        title='待探索：按配体骨架划分验证集',
        body='想法：检查随机划分是否掩盖了相似配体之间的数据泄漏。尚未实施，也没有实验结论。后续可先核对划分方式，再决定是否值得投入。\n\n这是预置的合成会话，用来验证没有代码提交也能保留想法。',
        labels=['未实施', '模拟记录'],
        source_event_ids=idea_sources,
    )
    human_body = node['body'] + '\n\n人工补充：这次只证明本地版本隔离成立；集群作业仍待验收。'
    store.update_node(
        node['id'], {'body': human_body}, expect_version=1, actor_type='human', actor_id='synthetic-reviewer'
    )
    stale_rejected = False
    try:
        store.update_node(node['id'], {'body': 'stale recorder overwrite'}, expect_version=1, actor_type='recorder')
    except Conflict:
        stale_rejected = True
    assert stale_rejected
    assert len(store.get_project(pid)['nodes']) == 2
    # Also recover the semantic records, revisions and attachment bytes, not just Git.
    export_backup(store, root / 'record-backup')
    restored_store = Store(root / 'restored-trace-data')
    restore_backup(root / 'record-backup', restored_store)
    original_project = store.get_project(pid)
    assert restored_store.get_project(pid) == original_project
    assert restored_store.revisions('node', node['id']) == store.revisions('node', node['id'])
    for attachment in (item for saved_node in original_project['nodes'] for item in saved_node['attachments']):
        original_path, _, _ = store.attachment_content(attachment['id'])
        restored_path, _, _ = restored_store.attachment_content(attachment['id'])
        assert original_path.read_bytes() == restored_path.read_bytes()
    restored_store.close()
    report = {
        'schema': 'research-trace.entire-smoke-report.v1',
        'entire_version': version,
        'synthetic_hook_input': True,
        'real_live_claude_session': False,
        'summary_generation': 'prewritten-fixture',
        'project_id': pid,
        'node_id': node['id'],
        'idea_node_id': idea['id'],
        'baseline': baseline,
        'commits': [commit_a, commit_b],
        'checkpoints': [evidence_a.checkpoint_id, evidence_b.checkpoint_id],
        'local_outputs': outputs,
        'checks': {
            'real_entire_checkpoints': True,
            'shared_code_pinned': True,
            'concurrent_local_runs': True,
            'code_and_transcript_restored_from_git_bundle': True,
            'restored_code_rerun_matches': True,
            'records_revisions_attachments_restored': True,
            'uncommitted_idea_recorded': True,
            'raw_replay_deduplicated': True,
            'human_revision_protected': stale_rejected,
        },
        'not_verified': [
            'live Claude automatic capture/Recorder quality',
            'UF/SLURM execution',
            'real W&B link',
            'Entire first-write hidden-content filtering',
            'offline delivery',
            'GitButler coexistence',
        ],
    }
    store.close()
    write(root / 'report.json', json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
