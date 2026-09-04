"""Optional real upstream CLI acceptance; hook inputs are synthetic test fixtures."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

import pytest

from research_trace.entire_evidence import EntireRepository, EvidenceError
from research_trace.workspace import Workspace, atomic_json, capture_phase
from test_workspace_runtime import bound_workspace


ROOT=Path(__file__).resolve().parents[1]
ENTIRE=Path(os.environ.get('TRACE_TEST_ENTIRE', ROOT/'.integration-demo/tools/entire-v0.10.5/entire.exe'))
pytestmark=pytest.mark.skipif(not ENTIRE.is_file(),reason='Set TRACE_TEST_ENTIRE to a real Entire 0.10.5 executable')


def test_real_entire_pending_snapshot_and_live_session_export(tmp_path,monkeypatch):
    workspace=bound_workspace(tmp_path/'project')
    def upstream(*args,payload=None):
        result=subprocess.run([str(ENTIRE),*args],cwd=workspace.root,capture_output=True,text=True,
            input=json.dumps(payload) if payload else None,timeout=60,check=True)
        return result.stdout
    upstream('enable','--agent','claude-code','--local','--no-init-repo','--skip-push-sessions','--telemetry=false')
    marker=json.loads(Path(workspace.binding['marker_path']).read_text())
    marker['code_capture']['entire_executable']=str(ENTIRE)
    atomic_json(workspace.binding['marker_path'],marker)
    workspace=Workspace(workspace.root)
    spec=importlib.util.spec_from_file_location('entire_runtime_hook',ROOT/'scripts/trace_hook.py')
    hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
    monkeypatch.setenv('TRACE_HOOK_NO_SPAWN','1')
    transcript=tmp_path/'upstream-session.jsonl'
    transcript.write_text('')
    sid=str(uuid.uuid4())
    base={'session_id':sid,'cwd':str(workspace.root),'transcript_path':str(transcript)}
    payload={**base,'hook_event_name':'SessionStart','source':'startup'}
    upstream('hooks','claude-code','session-start',payload=payload)
    hook.handle(payload,tmp_path/'outbox',ROOT/'hooks/RECORDER_PROTOCOL.md')
    def row(role,content):
        return json.dumps({'type':role,'sessionId':sid,'uuid':str(uuid.uuid4()),
            'timestamp':datetime.now(timezone.utc).isoformat(),'cwd':str(workspace.root),
            'message':{'role':role,'content':content}})+'\n'
    transcript.write_text(row('user','Synthetic idea: compare a second shared-code variant.'),encoding='utf-8')
    upstream('hooks','claude-code','user-prompt-submit',payload={**base,'hook_event_name':'UserPromptSubmit','prompt':'Compare shared code'})
    (workspace.root/'common/value.py').write_text('VALUE = 2\n')
    with transcript.open('a',encoding='utf-8') as stream:
        stream.write(row('assistant',[{'type':'text','text':'Synthetic result: version two ready.'},
                                     {'type':'thinking','thinking':'SYNTHETIC_PRIVATE_SENTINEL'}]))
    stop={**base,'hook_event_name':'Stop','stop_hook_active':False}
    upstream('hooks','claude-code','stop',payload=stop)
    repository=EntireRepository(workspace.root,ENTIRE)
    points=repository.pending()
    assert points and any(item.get('session_id')==sid for item in points)
    snapshot=capture_phase(workspace.binding,sid)
    assert snapshot['provider']=='entire' and snapshot['entire']['checkpoint_kind']=='pending'
    assert snapshot['commit_hash'] in {point['id'] for point in points}
    with zipfile.ZipFile(snapshot['archive_path']) as archive:
        assert archive.read('common/value.py')==(workspace.root/'common/value.py').read_bytes()
        assert not any(name.startswith('.entire/') for name in archive.namelist())
    # The actual source path comes from Entire, not the caller's bogus hook path.
    hook.handle({**stop,'transcript_path':str(tmp_path/'does-not-exist')},tmp_path/'outbox',ROOT/'hooks/RECORDER_PROTOCOL.md')
    chunks=list((tmp_path/'outbox').rglob('*.jsonl'))
    captured=''.join(hook._long(path).read_text(encoding='utf-8') for path in chunks)
    assert 'version two ready' in captured and 'SYNTHETIC_PRIVATE_SENTINEL' not in captured
    count=len(chunks)
    hook.handle({**stop,'stop_hook_active':True},tmp_path/'outbox',ROOT/'hooks/RECORDER_PROTOCOL.md')
    assert len(list((tmp_path/'outbox').rglob('*.jsonl')))==count
    # A pure discussion also goes through Entire's live export, with no new
    # code checkpoint and no dependence on a real transcript path in the hook.
    with transcript.open('a',encoding='utf-8') as stream:
        stream.write(row('user','Synthetic untried direction: change the negative sampling scheme.'))
    hook.handle({**base,'hook_event_name':'UserPromptSubmit','transcript_path':str(tmp_path/'missing')},
                tmp_path/'outbox',ROOT/'hooks/RECORDER_PROTOCOL.md')
    captured=''.join(hook._long(path).read_text(encoding='utf-8') for path in (tmp_path/'outbox').rglob('*.jsonl'))
    assert 'untried direction' in captured
    # Official export trims a concurrently appended partial JSONL row.
    with transcript.open('a',encoding='utf-8') as stream: stream.write('{"type":')
    with repository.session_stream(sid) as stream: exported=stream.read()
    assert exported.endswith(b'\n') and not exported.endswith(b'{"type":')
    (workspace.root/'common/value.py').write_text('VALUE = 3\n')
    with pytest.raises(EvidenceError,match='No matching Entire'):
        capture_phase(workspace.binding,sid)
    observed=workspace.snapshot()
    assert observed['provider']=='git-code-snapshot'
    assert observed['commit_hash']!=snapshot['commit_hash']
    assert 'No matching Entire' in observed['entire_gap']
    assert not (workspace.state/'runs').exists()
