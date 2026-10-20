"""VOID v0.6 acceptance tests for the six requested desktop improvements."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import secure_store
from source_import import validate_source

def test_default_source_interval_is_one_minute():
    assert validate_source({'name':'City','city':'Тула','kind':'vk','locator':'overhear.tula'})['interval_minutes']==1
    assert validate_source({'name':'City','city':'Тула','kind':'vk','locator':'overhear.tula',
                            'interval_minutes':1})['interval_minutes']==1

def test_60_seconds_cooldown_without_90_second_lock(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DATA_DIR',tmp_path)
    monkeypatch.setattr(app,'DB_PATH',tmp_path/'leads.sqlite3')
    app.init_db(seed=False)
    with app.db_connection() as db:
        sid, _ = app.add_public_source(db,dict(name='City',city='Тула',kind='rss',
                                              locator='https://example.org/rss.xml'))
        db.execute("UPDATE public_sources SET last_attempt=? WHERE id=?",
                   ((datetime.now(timezone.utc)-timedelta(seconds=75)).isoformat(),sid))
        db.commit()
        monkeypatch.setattr(app,'fetch_source',lambda source,token:[])
        assert app.check_public_source(db,sid)['read']==0
        with pytest.raises(ValueError,match='через'):
            app.check_public_source(db,sid)

def test_legacy_source_interval_migrates_once(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DATA_DIR',tmp_path)
    monkeypatch.setattr(app,'DB_PATH',tmp_path/'leads.sqlite3')
    app.init_db(seed=False)
    with app.db_connection() as db:
        sid,_=app.add_public_source(db,dict(name='Old',city='Тула',kind='vk',
                    locator='overhear.tula','interval_minutes':20))
        db.execute("DELETE FROM app_meta WHERE key='one_minute_checks'")
        db.commit()
    app.init_db(seed=False)
    with app.db_connection() as db:
        assert db.execute("SELECT interval_minutes FROM public_sources WHERE id=?",
                          (sid,)).fetchone()[0]==1

def test_preferences_local_only(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DATA_DIR',tmp_path)
    assert app.preferences()['notifications'] is True
    app.save_preferences(notifications=False)
    assert app.preferences()['notifications'] is False
    data=(tmp_path/'settings.json').read_text(encoding='utf-8')
    assert 'token' not in data.lower()

def test_notify_fires_only_for_new_and_above_threshold(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DATA_DIR',tmp_path)
    class FakeTray:
        def __init__(self):self.events=[]
        def notify(self,title,msg):self.events.append((title,msg));return True
    tray=FakeTray()
    monkeypatch.setattr(app,'_tray',tray)
    assert app.notify_new_advertisers(1,'City',80)
    assert not app.notify_new_advertisers(0,'City',80)
    assert not app.notify_new_advertisers(1,'City',20)
    assert len(tray.events)==1
    app.save_preferences(notifications=False)
    assert not app.notify_new_advertisers(1,'City',90)

def test_vk_token_not_in_plaintext_app_settings(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'DATA_DIR',tmp_path)
    assert not app.token_available()
    if os.name=='nt':
        token='vk1.a-safe-test-service-token-'+'x'*30
        secure_store.save_token(tmp_path,token)
        assert secure_store.load_token(tmp_path)==token
        assert token.encode('utf-8') not in (tmp_path/'vk_token.dpapi').read_bytes()
        assert app.token_available()
        secure_store.delete_token(tmp_path)
        assert not (tmp_path/'vk_token.dpapi').exists()
    else:
        with pytest.raises(RuntimeError):
            secure_store.save_token(tmp_path,'vk1.a-test-service-token')

def test_public_hunter_has_source_dropdown_and_personal_name():
    ui=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text('utf8')
    assert 'id="postSource"' in ui
    assert 'function visiblePosts()' in ui
    assert 'Данил, свежие возможности' in ui

def test_packaging_has_app_icon_and_tray():
    root=Path(__file__).resolve().parents[1]
    workflow=(root/'.github/workflows/windows-release.yml').read_text('utf8')
    assert 'packaging/make_icon.py' in workflow
    assert '--icon assets/void-leads.ico' in workflow
    assert 'pystray' in workflow
