import asyncio
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from research_trace.server import create_app


def test_authlib_code_exchange_and_authenticated_identity(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    import httpx
    from authlib.integrations.httpx_client import OAuth2Client
    from research_trace import auth
    from types import SimpleNamespace
    requests=[]
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/access_token'):
            fields=parse_qs(request.content.decode())
            assert fields['code_verifier']==['verifier']
            assert fields['client_secret']==['test-secret']
            assert fields['redirect_uri']==['https://trace.example/auth/github/callback']
            return httpx.Response(200,json={'access_token':'test-access','token_type':'bearer'})
        assert request.headers['Authorization']=='Bearer test-access'
        return httpx.Response(200,json={'id':101,'login':'researcher'})
    monkeypatch.setattr(auth,'OAuth2Client',lambda **kwargs: OAuth2Client(**kwargs,transport=httpx.MockTransport(respond)))
    config=SimpleNamespace(client_id='test-id',client_secret='test-secret',scopes='read:user',
                           callback_url='https://trace.example/auth/github/callback')
    client=auth.GitHubOAuthClient(config)
    url=client.authorize_url(state='test-state',challenge='challenge')
    query=parse_qs(urlsplit(url).query)
    assert query['state']==['test-state'] and query['code_challenge_method']==['S256']
    assert query['code_challenge']==['challenge']
    token=client.exchange_code(code='test-code',verifier='verifier')
    assert client.fetch_user(token)['login']=='researcher'
    assert len(requests)==2


def test_official_sdk_handshake_tools_and_validation():
    async def check():
        params=StdioServerParameters(command=sys.executable,args=['-m','research_trace.mcp','--url','http://127.0.0.1:1'])
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                initialized=await session.initialize()
                assert initialized.serverInfo.name=='research-trace'
                listed=await session.list_tools()
                assert len(listed.tools)==7
                assert 'run_ids' in next(t for t in listed.tools if t.name=='trace_record').inputSchema['properties']
                invalid=await session.call_tool('trace_record',{'title':'missing project'})
                assert invalid.isError
                failure=await session.call_tool('trace_context',{})
                assert failure.isError
                await session.send_ping()
    asyncio.run(check())


def test_no_daily_backup_even_when_old_environment_is_set(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('TRACE_BACKUP_REPO',str(tmp_path/'must-not-be-created'))
    with TestClient(create_app(tmp_path/'data')) as client:
        assert 'backup' not in client.get('/api/health').json()
        assert 'GitHub 每日备份' not in client.get('/').text
        for name in ('markdown-it.min.js','dagre.min.js'):
            assert client.get('/assets/'+name).status_code==200
        assert client.get('/assets/unknown.js').status_code==404
    assert not (tmp_path/'must-not-be-created').exists()
    assert 'no backup destination' not in capsys.readouterr().err


def test_sources_are_exact_even_after_many_newer_events(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as client:
        store=client.app.state.store
        pid=store.create_project('long research')['id']
        store.ingest(batch_id='old',project_id=pid,session=None,agents=[],
                     events=[{'event_id':'old-source','event_type':'user','captured_at':'2020-01-01','payload':{'text':'first idea'}}])
        node=store.record_node(pid,idempotency_key='idea',title='idea',source_event_ids=['old-source','missing'])
        store.ingest(batch_id='new',project_id=pid,session=None,agents=[],events=[
            {'event_id':f'new-{i}','event_type':'user','payload':{'text':'unrelated'}} for i in range(510)])
        sources=client.get('/api/nodes/'+node['id']+'/sources').json()
        assert [e['id'] for e in sources['items']]==['old-source']
        assert sources['missing_event_ids']==['missing']
