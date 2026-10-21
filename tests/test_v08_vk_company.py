"""VK company search: official API access, evidence and CRM preservation."""
import json
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

import app
from company_vk import city_confirmed, parse_vk_groups, search_vk, _api


def sample_groups():
    return [
        {"id": 101, "name": "Мебельная фабрика Ваш стиль", "city": {"title": "Рязань"},
         "description": "Кухни на заказ. Производство мебели.", "site": "https://www.mebel-ryazane.ru"},
        {"id": 102, "name": "Кухни Калуга", "city": {"title": "Калуга"},
         "description": "Кухни на заказ", "site": "https://kitchen-kaluga.ru"},
        {"id": 103, "name": "Барахолка Рязань", "description": "Мебель, кухни, объявления"},
        {"id": 104, "name": "Рязань Новости", "description": "Мебельные новости города"},
        {"id": 105, "name": "Кухни и мебель", "description": "Производство кухонь в Рязани"},
        {"id": 106, "name": "Салон красоты", "city": {"title": "Рязань"}, "description": "Макияж, массаж"},
        {"id": 107, "name": "Кухни", "description": "Кухни на заказ, доставка по России"},
    ]


def test_vk_group_requires_verified_city_and_relevant_business():
    parsed=parse_vk_groups(sample_groups(),"Рязань","furniture")
    assert {x["name"] for x in parsed} == {"Мебельная фабрика Ваш стиль","Кухни и мебель"}
    assert all(x["source_url"].startswith("https://vk.com/club") for x in parsed)
    assert parsed[0]["website"]=="https://www.mebel-ryazane.ru"
    assert parsed[1]["website_status"]=="unknown"
    assert all(x["source_type"]=="directory" for x in parsed)
    assert not city_confirmed({"city":{"title":"Калуга"},"name":"Мебель Рязань"},"Рязань")


def test_vk_search_uses_only_official_api_and_mockable_searches():
    calls=[]
    def mock(method,params,token):
        assert token=="valid-test-token"
        calls.append((method,params))
        if method=="groups.search":
            assert "Рязань" in params["q"]
            return {"count":2,"items":[{"id":101,"name":"Ваш стиль Рязань"},
                                        {"id":105,"name":"Кухни Рязань"}]}
        assert method=="groups.getById"
        assert params["fields"]=="description,site,city,activity,status"
        return sample_groups()
    records=search_vk("Рязань","furniture","valid-test-token",api=mock,sleep=lambda _:None)
    assert len(calls)==3
    assert len(records)==2
    assert records[0]["source_name"]=="VK Communities API"


def test_vk_rejects_unqualified_candidates_and_missing_token():
    assert parse_vk_groups([{"id":42,"name":"Кухни Москва","city":{"title":"Москва"}}],"Рязань","furniture")==[]
    with pytest.raises(ValueError):
        search_vk("Рязань","furniture","",sleep=lambda _:None)
    with pytest.raises(ValueError):
        parse_vk_groups(sample_groups(),"Санкт-Петербург","furniture")


def test_vk_api_error_does_not_leak_key(monkeypatch):
    import company_vk
    class FakeResp:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):
            return b'{"error":{"error_code":5,"error_msg":"token_test_PRIVATE_KEY_1234567890"}}'
    monkeypatch.setattr(company_vk.urllib.request,"urlopen",lambda *a,**k:FakeResp())
    with pytest.raises(ValueError) as e:
        _api("groups.search",{"q":"кухни"},"token_test_PRIVATE_KEY_1234567890")
    assert "PRIVATE_KEY" not in str(e.value)
    assert "код 5" in str(e.value)


def test_vk_discovery_preserves_warm_contact_and_cooldown(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    monkeypatch.setattr(app,"DB_PATH",tmp_path/"leads.sqlite3")
    monkeypatch.setattr(app,"_vk_company_job",
        {"state":"idle","added":0,"updated":0,"total":0,"done":0,"message":""})
    monkeypatch.setattr(app,"authorized_vk_user_token",lambda:"fake-authorized-key")
    app.init_db(seed=False)
    with app._lock,app.db_connection() as db:
        existing=app.insert_lead(db,{
            "name":"Мебельная фабрика Ваш стиль","city":"Рязань","niche":"furniture",
            "status":"replied","notes":"Дмитрий получил интерактивный концепт",
            "website":"https://www.mebel-ryazane.ru"
        })
        db.commit()
    monkeypatch.setattr(app,"search_vk",lambda city,niche,token:[
        {
            "name":"Мебельная фабрика Ваш стиль","city":"Рязань","niche":"furniture",
            "website":"https://www.mebel-ryazane.ru","source_name":"VK Communities API",
            "source_type":"directory","source_url":"https://vk.com/club101",
            "social":"https://vk.com/club101"
        },
        {
            "name":"Кухни Юг","city":"Рязань","niche":"furniture",
            "source_type":"directory","source_name":"VK Communities API",
            "source_url":"https://vk.com/club102","social":"https://vk.com/club102"
        },
    ])
    start=app.start_vk_company_search(city="Рязань",niche="furniture",manual=True)
    assert start["total"]==1
    for _ in range(100):
        if app.vk_company_status()["state"]=="done":
            break
        time.sleep(.025)
    status=app.vk_company_status()
    assert status["state"]=="done" and status["added"]==1 and status["updated"]==1
    with app.db_connection() as db:
        old=db.execute("SELECT * FROM leads WHERE id=?",(existing,)).fetchone()
        assert old["status"]=="replied"
        assert old["notes"]=="Дмитрий получил интерактивный концепт"
        assert db.execute("SELECT COUNT(*) FROM leads WHERE demo=0").fetchone()[0]==2
    with pytest.raises(ValueError,match="через час"):
        app.start_vk_company_search(city="Рязань",niche="furniture",manual=True)


def test_vk_api_route_can_launch_job_with_config(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    monkeypatch.setattr(app,"DB_PATH",tmp_path/"leads.sqlite3")
    monkeypatch.setattr(app,"_vk_company_job",
        {"state":"idle","added":0,"updated":0,"total":0,"done":0,"message":""})
    monkeypatch.setattr(app,"authorized_vk_user_token",lambda:"fake-authorized-key")
    monkeypatch.setattr(app,"search_vk",lambda city,niche,token:[])
    app.init_db(seed=False)
    server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base="http://127.0.0.1:"+str(server.server_port)
    try:
        def post(body):
            data=json.dumps(body).encode("utf-8")
            req=Request(base+"/api/company/vk/search",data=data,method="POST",
                        headers={"Content-Type":"application/json"})
            with urlopen(req,timeout=4) as r:return json.load(r)
        job=post({"city":"Калуга","niche":"industrial"})
        assert job["total"]==1
        with urlopen(base+"/api/company/vk",timeout=3) as r:
            status=json.load(r)
        assert "runs" in status
        with pytest.raises(HTTPError) as failure:
            post({"city":"Москва","niche":"industrial"})
        assert failure.value.code==400
    finally:
        server.shutdown()
        server.server_close()


def test_vk_ui_and_engine_source_exist():
    from engine import SOURCES
    from pathlib import Path
    assert "directory" in SOURCES
    ui=(Path(__file__).resolve().parents[1]/"static"/"company-ui.js").read_text("utf8")
    client=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text("utf8")
    assert "company-vk-search" in ui and "companyAutoVK" in ui
    assert "/api/company/vk/search" in client


def test_vk_group_search_does_not_fall_back_to_service_token(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    monkeypatch.setattr(app,"DB_PATH",tmp_path/"leads.sqlite3")
    monkeypatch.setattr(app,"authorized_vk_token",lambda:"working-service-token")
    monkeypatch.setattr(app,"authorized_vk_user_token",lambda:"")
    app.init_db(seed=False)
    with pytest.raises(ValueError,match="токен пользователя"):
        app.start_vk_company_search("Рязань","furniture",manual=True)


def test_separate_protected_token_name_is_whitelisted():
    from secure_store import _filename
    assert _filename("vk_token.dpapi")=="vk_token.dpapi"
    assert _filename("vk_user_token.dpapi")=="vk_user_token.dpapi"
    with pytest.raises(ValueError):
        _filename("../../other.txt")
