"""Freshness, source CSV and migration acceptance tests, independent of external API keys."""
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from freshness import freshness, iso_publication
from source_import import read_source_csv, template_csv
import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR',tmp_path)
    monkeypatch.setattr(app, 'DB_PATH',tmp_path/'test.sqlite3')
    monkeypatch.setattr(app, '_session_vk_token','')
    app.init_db(seed=False)
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    t=threading.Thread(target=server.serve_forever,daemon=True)
    t.start()
    port=server.server_address[1]
    def request(path, method='GET', data=None):
        b=json.dumps(data).encode() if data is not None else None
        req=Request(f'http://127.0.0.1:{port}{path}',data=b,method=method,
                    headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=10) as resp:
            body=resp.read()
            return resp.status, json.loads(body) if 'json' in resp.headers.get('Content-Type','') else body
    yield request
    server.shutdown(); server.server_close()


def hours_ago(hours):
    return (datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat()


def test_time_boundaries_and_rss_formats():
    now=datetime(2026,10,20,12,0,tzinfo=timezone.utc)
    assert freshness((now-timedelta(minutes=50)).isoformat(),now)=='hot'
    assert freshness((now-timedelta(hours=3)).isoformat(),now)=='fresh'
    assert freshness((now-timedelta(hours=30)).isoformat(),now)=='archive'
    assert freshness('',now)=='undated'
    assert freshness('2026-10-20T12:00:00',now)=='undated' # no timezone -> can't trust
    assert freshness((now+timedelta(hours=2)).isoformat(),now)=='undated'
    assert iso_publication('Tue, 20 Oct 2026 09:00:00 GMT')=='2026-10-20T09:00:00+00:00'


def test_source_csv_duplicate_and_pause(client):
    rows,errors=read_source_csv(template_csv())
    assert len(rows)==2 and not errors
    raw='Название;Город;Тип;Адрес;Активен;Интервал\nПодслушано;Тула;VK;https://vk.com/overhear.tula;1;20\nПодслушано;Тула;vk;overhear.tula;1;20\nТула TG;Тула;Telegram;https://t.me/pdstula;0;30\nСломан;Тула;vk;https://fake.example/x;1;20\n'
    _,result=client('/api/public/sources/import','POST',{'csv':raw})
    assert result['added']==2 and result['duplicates']==1 and result['invalid']==1
    _,items=client('/api/public/sources')
    assert len(items)==2
    tg=next(x for x in items if x['kind']=='manual')
    assert tg['enabled']==0 and tg['locator']=='https://t.me/pdstula'
    client(f"/api/public/sources/{tg['id']}",'PATCH',{'enabled':1})
    _,items=client('/api/public/sources')
    assert next(x for x in items if x['kind']=='manual')['enabled']==1
    _,again=client('/api/public/sources/import','POST',{'csv':raw})
    assert again['added']==0 and again['duplicates']==3


def test_live_archive_and_undated_manual(client):
    _,source=client('/api/public/sources','POST',{'name':'Паблик','city':'Тула','kind':'manual',
                                                   'locator':'https://t.me/pdstula'})
    sid=source['id']
    for idx,(label,stamp) in enumerate([('Новые кухни',hours_ago(.5)),('Монтаж фасадов',hours_ago(5)),
                        ('Аренда техники',hours_ago(40)),('Окна и двери','')]):
        client('/api/public/posts/manual','POST',{'source_id':sid,'url':'https://t.me/pdstula/'+str(idx+1),
                                                 'published_at':stamp,
                                                 'text':label+'! Принимаем заказы по телефону +7 999 333-22-11'})
    _,fresh=client('/api/public/posts')
    assert len(fresh)==2 and fresh[0]['freshness']=='hot'
    _,archive=client('/api/public/posts?status=archive')
    assert len(archive)==1 and archive[0]['freshness']=='archive'
    _,unknown=client('/api/public/posts?status=undated')
    assert len(unknown)==1
    _,count=client('/api/public/stats')
    assert count['fresh']==2 and count['hot']==1 and count['archive']==1 and count['undated']==1


def test_vk_mock_old_not_in_live(client,monkeypatch):
    _,source=client('/api/public/sources','POST',{'name':'ВК город','city':'Тула','kind':'vk','locator':'overhear.tula'})
    monkeypatch.setattr(app,'fetch_source',lambda src,token:[
        {'external_id':'-9_7','url':'https://vk.com/wall-9_7','text':'Кухни на заказ, телефон +79994442233', 'published_at':hours_ago(72)},
        {'external_id':'-9_8','url':'https://vk.com/wall-9_8','text':'Кухни на заказ, телефон +79994442233', 'published_at':hours_ago(.2)}
    ])
    _,out=client('/api/public/check','POST',{'source_id':source['id'],'token':'mock-authorized-token'})
    assert out['matched']==2 and out['added']==1 and out['fresh_candidates']==1
    _,posts=client('/api/public/posts')
    assert len(posts)==1
    _,history=client('/api/public/posts?status=all')
    assert len(history)==2


def test_source_bad_file_headers_and_injection():
    with pytest.raises(ValueError):read_source_csv('foo;bar\n1;2')
    rows,errors=read_source_csv('Название;Город;Тип;Адрес\nFake;Тула;rss;http://localhost/private\n')
    assert rows==[] and errors


def test_background_vk_check_after_session_authorization(client,monkeypatch):
    _,src=client('/api/public/sources','POST',{'name':'Авто VK','city':'Тверь',
        'kind':'vk','locator':'tver_podslushano','interval_minutes':20})
    monkeypatch.setattr(app,'_session_vk_token','temporary-session-token')
    monkeypatch.setattr(app,'fetch_source',lambda s,token:[{
        'external_id':'-1_10','url':'https://vk.com/wall-1_10',
        'text':'Кухни на заказ. Звоните +7 999 111-22-33',
        'published_at':hours_ago(.5), 'marked_as_ads':True
    }])
    class TwoTicks:
        counter=0
        def wait(self,_):
            self.counter+=1
            return self.counter>1
    app.auto_check_loop(TwoTicks())
    _,sources=client('/api/public/sources')
    assert sources[0]['last_checked']
    _,posts=client('/api/public/posts')
    assert len(posts)==1 and posts[0]['promoted']==1
    # Duplicate page should not appear as new again.
    _,status=client('/api/public/stats')
    assert status['fresh']==1


def test_migration_keeps_old_leads_and_adds_source_fields(client):
    _,record=client('/api/leads','POST',{'name':'Реальный старый клиент', 'city':'Тула'})
    app.init_db(seed=True)
    app.init_db(seed=True)
    _,existing=client('/api/leads')
    assert len(existing)==1 and existing[0]['id']==record['id']
