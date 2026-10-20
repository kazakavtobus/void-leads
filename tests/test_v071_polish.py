"""VOID LEADS v0.7.1 — UI and two-phase updater regression tests."""
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest
import app
import updater
from update_state import read_status,write_status


def test_update_status_is_atomic_and_recoverable(tmp_path):
    assert read_status(tmp_path)['state']=='idle'
    write_status(tmp_path,'downloading','Скачиваем')
    assert read_status(tmp_path)['state']=='downloading'
    write_status(tmp_path,'verified','SHA256 проверен')
    assert read_status(tmp_path)['message']=='SHA256 проверен'
    with pytest.raises(ValueError):
        write_status(tmp_path,'nonsense','')
    (tmp_path/'update_progress.json').write_text('{broken json',encoding='utf8')
    assert read_status(tmp_path)['state']=='idle'


def test_update_shutdown_requires_nonce(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DATA_DIR',tmp_path)
    monkeypatch.setattr(app,'DB_PATH',tmp_path/'leads.sqlite3')
    app.init_db(seed=False)
    monkeypatch.setattr(app,'_update_in_progress',True)
    monkeypatch.setattr(app,'_update_key','a-very-long-test-key-32-characters')
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        bad=Request(f'http://127.0.0.1:{server.server_port}/api/update/shutdown',
                    data=b'{}',method='POST',
                    headers={'Content-Type':'application/json','X-Update-Key':'wrong'})
        with pytest.raises(HTTPError) as e:
            urlopen(bad,timeout=5)
        assert e.value.code==403
        # Missing/wrong key must NOT terminate the application.
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/update/progress',timeout=5) as r:
            assert json.load(r)['state']=='idle'
        updater.ask_running_app_to_close(server.server_port,'a-very-long-test-key-32-characters')
        thread.join(timeout=6)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()


def test_ui_filter_controls_are_distinct_and_readable():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf8')
    js=(root/'static/app.js').read_text(encoding='utf8')
    css=(root/'static/style.css').read_text(encoding='utf8')
    assert 'id="postSource"' in js
    assert 'id="manualPostSource"' in html
    assert 'id="postSource"' not in html
    for element in ['id="postStatus"','id="targetNiche"','id="targetOnly"','role="switch"',
                    'class="target-pill"','id="targetSettings"','targetSettingsOpen']:
        assert element in js
    for name in ['.public-filter-panel','.public-filter-grid','.target-pill-label',
                 '.public-toggle-track','@container','@media(max-width:680px)']:
        assert name in css
    assert "if(page==='public')state.postStatus='fresh'" not in js


def test_update_protects_browser_and_download():
    root=Path(__file__).resolve().parents[1]
    app_py=(root/'app.py').read_text(encoding='utf8')
    script=(root/'updater.py').read_text(encoding='utf8')
    js=(root/'static/app.js').read_text(encoding='utf8')
    assert 'f\'--user-data-dir={profile}\'' in app_py
    assert 'close_managed_browser_windows()' in app_py
    assert "write_status(DATA_DIR,'verified'" in script
    assert "ask_running_app_to_close(args.port,args.shutdown_key)" in script
    assert script.index("if actual.lower() != expected:") < script.index("ask_running_app_to_close(args.port,args.shutdown_key)")
    assert script.index("backup = backup_database()") < script.index("ask_running_app_to_close(args.port,args.shutdown_key)")
    assert 'monitorUpdate()' in js
    assert "window.close()" in js
