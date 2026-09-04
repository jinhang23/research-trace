"""Code evidence is passive; execution belongs to the research agent."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

from fastapi.testclient import TestClient
import pytest

from research_trace.deliver import write_marker, deliver_once
from research_trace.server import create_app
from research_trace.workspace import Workspace, atomic_json, main

ROOT=Path(__file__).resolve().parents[1]


def git(root,*args):
    return subprocess.run(['git','-c','safe.directory='+root.as_posix(),*args],cwd=root,capture_output=True,check=True).stdout.decode().strip()


def bound_workspace(root,project_id='test-project'):
    root.mkdir()
    git(root,'init','-b','main')
    git(root,'config','user.name','Test')
    git(root,'config','user.email','test@example.invalid')
    git(root,'config','core.autocrlf','false')
    (root/'.gitignore').write_text('.research-trace.json\n*.log\n')
    (root/'common').mkdir(); (root/'common/value.py').write_text('VALUE = 1\n')
    (root/'model_a').mkdir(); (root/'model_a/train.py').write_text('from common.value import VALUE\nprint(VALUE)\n')
    git(root,'add','.'); git(root,'-c','commit.gpgsign=false','commit','-m','baseline')
    write_marker(root,project_id=project_id,capture=True)
    workspace=Workspace(root,require_enabled=False)
    marker=json.loads((root/'.research-trace.json').read_text())
    marker['code_capture']={'enabled':True,'start_commit':workspace.head(),'outbox':str(root.parent/'outbox'),
                            'max_code_bytes':100*1024*1024,'url':''}
    atomic_json(root/'.research-trace.json',marker)
    return Workspace(root)


def load_hook():
    spec=importlib.util.spec_from_file_location('passive_hook',ROOT/'scripts/trace_hook.py')
    hook=importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
    return hook


def test_code_evidence_keeps_bytes_without_managing_the_experiment_directory(tmp_path):
    workspace=bound_workspace(tmp_path/'project')
    original=workspace.head()
    old_bytes=(workspace.root/'common/value.py').read_bytes()
    first=workspace.snapshot()
    (workspace.root/'common/value.py').write_text('VALUE = 2\n')
    git(workspace.root,'add','common/value.py')
    staged=git(workspace.root,'diff','--cached')
    before=workspace.files()
    second=workspace.snapshot()
    assert workspace.files()==before and workspace.head()==original
    assert git(workspace.root,'diff','--cached')==staged
    assert first['commit_hash']!=second['commit_hash']
    with zipfile.ZipFile(first['archive_path']) as archive:
        assert archive.read('common/value.py')==old_bytes
    with zipfile.ZipFile(second['archive_path']) as archive:
        assert archive.read('common/value.py')==(workspace.root/'common/value.py').read_bytes()
    assert second['provider']=='git-code-snapshot'
    assert not (workspace.state/'runs').exists()
    assert not list(workspace.state.rglob('source'))


@pytest.mark.parametrize('action',['prepare','local','submit','_execute','replay','watch','status'])
def test_retired_execution_commands_fail_before_touching_a_project(action,monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('Retired commands must not inspect or change any project')
    monkeypatch.setattr('research_trace.workspace.Workspace',forbidden)
    with pytest.raises(SystemExit) as exc:
        main(['--project','unused',action])
    assert exc.value.code==2


def test_normal_agent_sbatch_is_recorded_without_trace_submitting_or_copying_code(tmp_path,monkeypatch):
    hook=load_hook()
    monkeypatch.setenv('TRACE_HOOK_NO_SPAWN','1')
    with TestClient(create_app(tmp_path/'central')) as client:
        pid=client.post('/api/projects',json={'name':'Passive research'}).json()['id']
        workspace=bound_workspace(tmp_path/'project',pid)
        before=workspace.files()
        transcript=tmp_path/'session.jsonl'; transcript.write_text('{"type":"user","message":"old"}\n')
        base={'session_id':'passive-session','cwd':str(workspace.root),'transcript_path':str(transcript)}
        protocol=ROOT/'hooks/RECORDER_PROTOCOL.md'
        started=hook.handle({**base,'hook_event_name':'SessionStart'},tmp_path/'outbox',protocol)
        assert 'normal training and sbatch' in started['hookSpecificOutput']['additionalContext']
        with transcript.open('a') as stream:
            stream.write('{"type":"user","message":"new experiment direction"}\n')
        original_run=subprocess.run
        calls=[]
        def commands(argv,**kwargs):
            calls.append(argv)
            assert argv[0]=='git', 'Recorder must never submit or run training'
            return original_run(argv,**kwargs)
        monkeypatch.setattr('research_trace.workspace.subprocess.run',commands)
        command='sbatch model_a/train.sbatch'
        hook.handle({**base,'hook_event_name':'PreToolUse','tool_name':'Bash','tool_use_id':'tool-123',
                     'tool_input':{'command':command}},tmp_path/'outbox',protocol)
        hook.handle({**base,'hook_event_name':'PostToolUse','tool_name':'Bash','tool_use_id':'tool-123',
                     'tool_input':{'command':command},'tool_response':{'stdout':'Submitted batch job 12345'}},tmp_path/'outbox',protocol)
        hook.handle({**base,'hook_event_name':'Stop','stop_hook_active':False},tmp_path/'outbox',protocol)
        assert workspace.files()==before and not (workspace.state/'runs').exists()
        events=[json.loads(path.read_text()) for path in (tmp_path/'outbox').rglob('pending/*.json')]
        assert any(item['payload'].get('tool_input',{}).get('command')==command for item in events)
        assert 'Submitted batch job 12345' in json.dumps(events)
        assert not any('research_run' in item['payload'] for item in events)
        chunks=''.join(hook._long(path).read_text() for path in (tmp_path/'outbox').rglob('*.jsonl'))
        assert 'new experiment direction' in chunks and '"message":"old"' not in chunks
        def post(url,path,payload,token,timeout):
            response=client.post(path,json=payload)
            return response.status_code,response.json()
        monkeypatch.setattr('research_trace.deliver._post_json',post)
        monkeypatch.setattr('research_trace.run_evidence._post_json',post)
        report=deliver_once(tmp_path/'outbox',url='http://local',token='')
        assert report['failed_batches']==0
        source_ids=[item['event_id'] for item in events]
        node=client.post('/api/record',json={'project_id':pid,'idempotency_key':'passive-group',
            'title':'Agent submitted a job; results pending','source_event_ids':source_ids}).json()
        assert node['runs']==[] and node['code_snapshots']
        snapshot=node['code_snapshots'][0]
        content=client.get('/api/attachments/'+snapshot['archive_attachment_id']+'/content').content
        assert hashlib.sha256(content).hexdigest()==snapshot['archive_sha256']
        assert len(client.get('/api/nodes/'+node['id']+'/sources').json()['items'])==len(source_ids)


def test_old_run_records_and_code_attachments_remain_readable_without_executor(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path/'central')) as client:
        pid=client.post('/api/projects',json={'name':'Existing evidence'}).json()['id']
        workspace=bound_workspace(tmp_path/'project',pid)
        snapshot=workspace.snapshot()
        run={'id':'a'*32,'revision':2,'name':'old run','state':'COMPLETED','snapshot':snapshot,
             'updated_at':'2026-09-03T00:00:00Z','wandb_url':'https://wandb.ai/test/project/runs/example',
             'command':['python','model_a/train.py'],'source_dir':'historical-source-directory'}
        pending=tmp_path/'outbox/outbox/legacy/run-old/pending'
        atomic_json(pending/'old.json',{'event_id':'old-run','session_id':'run-old','project_id':pid,
            'workspace_keys':workspace.binding['workspace_keys'],'project_dir':str(workspace.root),
            'event_type':'research_run','captured_at':run['updated_at'],'payload':{'research_run':run}})
        def post(url,path,payload,token,timeout):
            response=client.post(path,json=payload); return response.status_code,response.json()
        monkeypatch.setattr('research_trace.deliver._post_json',post)
        monkeypatch.setattr('research_trace.run_evidence._post_json',post)
        assert deliver_once(tmp_path/'outbox',url='http://local',token='')['failed_batches']==0
        node=client.post('/api/record',json={'project_id':pid,'idempotency_key':'old-node','title':'Old result',
                                          'run_ids':[run['id']]}).json()
        assert node['runs'][0]['wandb_url']==run['wandb_url']
        assert node['runs'][0]['snapshot']['archive_attachment_id']
        store=client.app.state.store
        store.ingest(batch_id='older',project_id=pid,session=None,agents=[],events=[
            {'event_id':'old-run-earlier','event_type':'research_run','payload':{'research_run':{**run,'revision':1,'state':'RUNNING'}}}])
        assert client.get('/api/projects/'+pid+'/runs').json()['items'][0]['state']=='COMPLETED'


def test_fresh_setup_preserves_existing_marker_and_does_not_import_history(tmp_path):
    workspace=bound_workspace(tmp_path/'project')
    marker=workspace.root/'.research-trace.json'
    value=json.loads(marker.read_text()); value.pop('code_capture'); value['my_setting']='preserve'; atomic_json(marker,value)
    assert main(['--project',str(workspace.root),'init','--outbox',str(tmp_path/'delivery')])==0
    saved=json.loads(marker.read_text())
    assert saved['my_setting']=='preserve' and saved['code_capture']['start_commit']==workspace.head()
    assert not list((tmp_path/'delivery').rglob('*.json'))
