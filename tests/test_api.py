import os, sys, json, threading
from pathlib import Path
from urllib.request import Request, urlopen
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(app, 'DB_PATH', tmp_path/'test.sqlite3')
    app.init_db(seed=False)
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    port=server.server_address[1]
    def call(path, method='GET', data=None):
        buf=json.dumps(data).encode() if data is not None else None
        req=Request(f'http://127.0.0.1:{port}{path}', data=buf,method=method,
            headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'})
        with urlopen(req,timeout=5) as resp:return resp.status,resp.read(),resp.headers
    yield call
    server.shutdown();server.server_close()


def j(call, path, method='GET', data=None):
    code,body,headers=call(path,method,data)
    return code,json.loads(body)


def test_full_lifecycle(client):
    _,stat=j(client,'/api/stats')
    assert stat['total']==0
    code,lead=j(client,'/api/leads','POST',{'name':'Строй Город','city':'Тула','niche':'construction','source_type':'partner'})
    assert code==201
    pk=lead['id']
    _,list_=j(client,'/api/leads')
    assert len(list_)==1
    _,detail=j(client,f'/api/leads/{pk}')
    assert 'draft' in detail and detail['name']=='Строй Город'
    j(client,f'/api/leads/{pk}','PATCH',{'status':'won','closed_value':35000})
    _,stat=j(client,'/api/stats')
    assert stat['revenue']==35000 and stat['won']==1
    j(client,f'/api/leads/{pk}/activity','POST',{'note':'Клиент оплатил'})
    _,detail=j(client,f'/api/leads/{pk}')
    assert any(a['note']=='Клиент оплатил' for a in detail['activities'])
    j(client,f'/api/leads/{pk}','DELETE')
    _,stat=j(client,'/api/stats')
    assert stat['total']==0


def test_repeat_ad_dedup_by_publication_url(client):
    d={'name':'Мебельный дом','city':'Казань','niche':'furniture','source_url':'https://example.com/post1','description':'Кухни на заказ! +7 911 123-45-67'}
    _,r1=j(client,'/api/capture/ad','POST',d)
    _,r2=j(client,'/api/capture/ad','POST',d)
    assert r1['id']==r2['id']
    _,detail=j(client,f"/api/leads/{r1['id']}")
    assert detail['ad_count']==1 and detail['phone']=='+7 911 123-45-67'
    _,r3=j(client,'/api/capture/ad','POST',{**d,'source_url':'https://example.com/post2'})
    _,detail=j(client,f"/api/leads/{r1['id']}")
    assert r1['id']==r3['id'] and detail['ad_count']==2
    assert len([a for a in detail['activities'] if a['kind']=='advert'])==2


def test_csv_import_repeat(client):
    _,r=j(client,'/api/import/csv','POST',{'csv':'Название;Город;Телефон\nМебель;Тула;+79991234567\nМебель;Тула;+79991234567'})
    assert r=={'created':1,'updated':1}
    _,stat=j(client,'/api/stats')
    assert stat['total']==1


def test_frontend_serves(client):
    code,data,head=client('/')
    assert code==200 and b'VOID LEADS' in data
    code,_,head=client('/app.js')
    assert code==200 and 'javascript' in head['Content-Type']
