"""Integration / extraction tests; real external APIs are mocked (no tokens or external calls)."""
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
import threading
from contextlib import closing
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import app
import public_hunter
from engine import iso_now


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "test.sqlite3")
    app.init_db(seed=False)
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, method="GET", body=None):
        r = Request(f"http://127.0.0.1:{server.server_address[1]}{path}",
                    data=json.dumps(body).encode() if body is not None else None,
                    method=method,
                    headers={"Content-Type":"application/json"})
        with urlopen(r,timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    yield request
    server.shutdown()
    server.server_close()


def test_rss_and_atom_parser():
    rss=b"<rss><channel><item><title>Repair shop</title><description>Call 89991234567</description><link>https://example.com/a</link><guid>id1</guid></item></channel></rss>"
    parsed=public_hunter.parse_rss(rss)
    assert len(parsed)==1 and 'Repair shop' in parsed[0]['text']
    atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>item2</id><title>Repairs</title><link href="https://example.org/b"/><summary>Discount offer</summary></entry></feed>'
    assert public_hunter.parse_rss(atom)[0]['url']=='https://example.org/b'


def test_rss_rejects_entities():
    with pytest.raises(ValueError):
        public_hunter.parse_rss(b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>')


def test_classification_and_vk_validation():
    assert public_hunter.classify('Кухни на заказ, запись и замер +7 911 123-45-67')['is_candidate']
    assert public_hunter.classify('Кухни на заказ')['niche']=='furniture'
    assert not public_hunter.classify('Сегодня прекрасная погода у нас')['is_candidate']
    assert public_hunter.validate_vk_slug('https://vk.com/podslushano_tula')=='podslushano_tula'
    with pytest.raises(ValueError): public_hunter.validate_vk_slug('https://evil.example/path')


def test_public_source_manual_convert_and_dedup(client):
    _,source=client('/api/public/sources','POST',{'name':'Подслушано','city':'Тула','kind':'manual','locator':''})
    sid=source['id']
    assert client('/api/public/sources')[1][0]['name']=='Подслушано'
    post={'source_id':sid,'text':'Кухни на заказ. Изготовим быстро. Запись +7 911 123-45-67','url':'https://example.org/a','published_at':iso_now()}
    _,p1=client('/api/public/posts/manual','POST',post)
    _,p2=client('/api/public/posts/manual','POST',post)
    assert p1['created'] and not p2['created']
    assert client('/api/public/stats')[1]['review']==1
    _,lead=client(f"/api/public/posts/{p1['id']}/convert",'POST',{'name':'Мебельная мастерская'})
    assert lead['created']
    assert client('/api/public/stats')[1]['converted']==1
    _,l=client(f"/api/leads/{lead['lead_id']}")
    assert l['ad_count']==1 and l['city']=='Тула' and '911' in l['phone']
    post2={**post,'url':'https://example.org/b'}
    _,p3=client('/api/public/posts/manual','POST',post2)
    client(f"/api/public/posts/{p3['id']}/convert",'POST',{'name':'Мебельная мастерская'})
    _,l=client(f"/api/leads/{lead['lead_id']}")
    assert l['ad_count']==2
    assert client('/api/public/stats')[1]['review']==0


def test_vk_fetch_mock(client,monkeypatch):
    _,source=client('/api/public/sources','POST',{'name':'Городское сообщество','city':'Казань','kind':'vk','locator':'city_ads'})
    sid=source['id']
    posts=[{'external_id':'-11_10','text':'Скидка на монтаж окон! Звони +7 911 123-45-67','url':'https://vk.com/wall-11_10','published_at':iso_now()},
           {'external_id':'-11_11','text':'Хорошая погода','url':'https://vk.com/wall-11_11'}]
    monkeypatch.setattr(app,'fetch_source',lambda src,token: posts)
    _,r=client('/api/public/check','POST',{'source_id':sid,'token':'authorized-test-token'})
    assert all(r[key] == value for key,value in {'read':2,'matched':1,'added':1,'fresh_candidates':1}.items())
    assert r['filtered']==1
    _,data=client('/api/public/posts')
    assert len(data)==1 and 'монтаж окон' in data[0]['text']


def test_ignore_and_delete_source(client):
    _,s=client('/api/public/sources','POST',{'name':'Новости','city':'Тула','kind':'rss','locator':'https://example.org/rss'})
    _,post=client('/api/public/posts/manual','POST',{'source_id':s['id'],'text':'Аренда спецтехники, закажите по телефону'})
    client(f"/api/public/posts/{post['id']}/ignore",'POST',{})
    assert not client('/api/public/posts')[1]
    client(f"/api/public/sources/{s['id']}",'DELETE')
    assert client('/api/public/sources')[1]==[]


def test_city_news_not_false_positive():
    assert not public_hunter.classify('Сегодня в городе завершился ремонт дороги и строительство школы')['is_candidate']
    assert public_hunter.classify('Кухни на заказ. Бесплатный замер, пишите в личку!')['is_candidate']


def test_vk_requires_service_key_before_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("VK request must not be issued without a token")
    monkeypatch.setattr(public_hunter.urllib.request, 'urlopen', no_network)
    with pytest.raises(ValueError, match="сервисный ключ VK"):
        public_hunter.fetch_vk_wall('city_ads', '')


def test_vk_auth_errors_explain_token_type():
    with pytest.raises(ValueError, match="токеном сообщества"):
        public_hunter.parse_vk_response({'error': {'error_code': 27, 'error_msg': 'Group authorization failed'}})
    with pytest.raises(ValueError, match="Ключ отклонён VK"):
        public_hunter.parse_vk_response({'error': {'error_code': 5, 'error_msg': 'User authorization failed'}})
