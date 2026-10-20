import hashlib
import io
import json
from pathlib import Path

import pytest
from urllib.request import Request
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import updates
import updater


class DummyResponse:
    def __init__(self, data):
        self.data = data
    def read(self, size=-1):
        return self.data[:size] if size >= 0 else self.data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def release(tag='v0.4.1', repo='example/void-leads', setup=True):
    assets = [{'name': name, 'browser_download_url':f'https://github.com/{repo}/releases/download/{tag}/{name}'}
              for name in ([updates.SETUP_NAME, updates.SHA_NAME] if setup else [])]
    return {'tag_name': tag, 'assets': assets, 'body': 'Bug fixes'}


def opener(data):
    def fn(request, timeout=12):
        assert isinstance(request, Request)
        assert request.full_url == 'https://api.github.com/repos/example/void-leads/releases/latest'
        return DummyResponse(json.dumps(data).encode())
    return fn


def test_channel_not_configured():
    result = updates.latest_release(repo='')
    assert not result['configured'] and not result['available']


def test_new_version_discovered():
    found = updates.latest_release(repo='example/void-leads', current_version='0.4.0',
                                   opener=opener(release()))
    assert found['latest'] == '0.4.1' and found['available']


def test_equal_version_stays_put():
    assert updates.latest_release(repo='example/void-leads', current_version='0.4.1',
                                  opener=opener(release()))['available'] is False


def test_refuses_unknown_release_assets():
    with pytest.raises(ValueError):
        updates.latest_release(repo='example/void-leads', current_version='0.4.0', opener=opener(release(setup=False)))


def test_no_cross_repo_asset_or_http():
    for url in ['http://github.com/example/void-leads/releases/download/v0.4.1/VOID-Leads-Setup.exe',
                'https://github.com/evil/repo/releases/download/v0.4.1/VOID-Leads-Setup.exe',
                'https://evil.example/fake/VOID-Leads-Setup.exe']:
        with pytest.raises(ValueError):
            updates.validate_release_url(url,'example/void-leads', updates.SETUP_NAME)


def test_semver_compares_numeric():
    assert updates.version_tuple('v0.4.10') > updates.version_tuple('0.4.9')


def test_checksum_parser():
    sha = hashlib.sha256(b'setup').hexdigest()
    assert updater.expected_hash(f'{sha}  VOID-Leads-Setup.exe') == sha
    with pytest.raises(ValueError):
        updater.expected_hash(f'{sha}  malicious.exe')


def test_backup_database_no_data_loss(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(updater, 'DATA_DIR', tmp_path)
    with sqlite3.connect(tmp_path/'leads.sqlite3') as db:
        db.execute('CREATE TABLE customers (name text)')
        db.execute("INSERT INTO customers VALUES ('Казань')")
    path = updater.backup_database()
    assert path.is_file()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT name FROM customers').fetchone()[0] == 'Казань'
    with sqlite3.connect(tmp_path/'leads.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM customers').fetchone()[0] == 1


def test_update_check_endpoint_when_unconfigured(tmp_path, monkeypatch):
    import threading
    from urllib.request import urlopen
    monkeypatch.setattr(app, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(app, 'DB_PATH', tmp_path/'leads.sqlite3')
    app.init_db(seed=False)
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/update/check',timeout=3) as response:
            data=json.load(response)
        assert data['configured'] is False
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/config',timeout=3) as response:
            data=json.load(response)
        assert data['version']==app.APP_VERSION
    finally:
        server.shutdown()
        server.server_close()
