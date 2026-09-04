"""The removed scheduled-backup feature cannot be reactivated by old settings."""
from fastapi.testclient import TestClient
from research_trace.server import create_app, main

def test_server_starts_without_backup_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('TRACE_BACKUP_REPO', str(tmp_path/'old-repo'))
    monkeypatch.setattr('uvicorn.run', lambda *a, **k: None)
    assert main(['--data-dir', str(tmp_path)]) == 0
    assert 'backup' not in capsys.readouterr().err.lower()
    assert not (tmp_path/'old-repo').exists()

def test_legacy_no_backup_flag_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr('uvicorn.run', lambda *a, **k: None)
    assert main(['--data-dir', str(tmp_path), '--no-backup']) == 0

def test_server_has_no_backup_scheduler(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert not hasattr(client.app.state, 'backup_status')
        assert 'backup' not in client.get('/api/health').json()
