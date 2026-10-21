"""Wikidata discovery and offline OSM GeoJSON regression tests (no external calls)."""
import json
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
import app
from company_discovery import (
    CITY_IDS, QUERY, parse_osm_geojson, parse_wikidata, discover_wikidata
)


def wiki_record(qid, cityid, name, desc, website=None):
    row={
        "item":{"value":"http://www.wikidata.org/entity/"+qid},
        "city":{"value":"http://www.wikidata.org/entity/"+cityid},
        "itemLabel":{"value":name},
        "itemDescription":{"value":desc},
    }
    if website: row["website"]={"value":website}
    return row


def test_wikidata_matches_only_targeted_industries_and_provenance():
    raw={"results":{"bindings":[
        wiki_record("Q12345","Q2746","Фабрика кухонь","Производство мебели","https://kitchens.example"),
        wiki_record("Q12346","Q2770","Металлообрабатывающий завод","manufacturing"),
        wiki_record("Q12347","Q2837","Муниципальный музей","museum","https://museum.example"),
        wiki_record("Q12348","Q9999","Кухни другого города","furniture"),
        wiki_record("Q12345","Q2746","Фабрика кухонь","Производство мебели"),
    ]}}
    result=parse_wikidata(raw)
    assert len(result)==2
    assert result[0]["niche"]=="furniture"
    assert result[0]["city"]=="Рязань"
    assert result[0]["source_url"]=="https://www.wikidata.org/wiki/Q12345"
    assert result[0]["website"]=="https://kitchens.example"
    assert result[1]["niche"]=="industrial"
    assert result[1]["city"]=="Тула"


def test_wikidata_query_is_narrow_and_api_mockable():
    assert "Q2746" in QUERY and "Q2770" in QUERY and "Q2837" in QUERY
    assert "Q4830453" in QUERY and "Q783794" in QUERY
    assert "LIMIT 240" in QUERY
    assert discover_wikidata(fetcher=lambda: {"results":{"bindings":[]}})==[]


def test_osm_geojson_preserves_osm_ids_and_rejects_generic_shops():
    rows=[
        {"type":"Feature","id":"node/100","geometry":None,"properties":
            {"name":"Кухни уют","shop":"furniture","contact:website":"https://kitchen.example","phone":"+79990000000"}},
        {"type":"Feature","id":"way/77","geometry":None,"properties":
            {"name":"МеталлСтрой","craft":"metal_construction"}},
        {"type":"Feature","id":"node/150","geometry":None,"properties":
            {"name":"Продукты","shop":"convenience"}},
        {"type":"Feature","id":"node/151","geometry":None,"properties":
            {"name":"Аренда всего","shop":"rental"}},
        {"type":"Feature","id":"node/100","geometry":None,"properties":
            {"name":"Кухни дубль","shop":"furniture"}},
        {"type":"Feature","id":"node/999","geometry":None,"properties":
            {"name":"Техника для строителей","shop":"rental"}},
        {"type":"Feature","properties":{"name":"Без источника","shop":"furniture"}},
    ]
    data=json.dumps({"type":"FeatureCollection","features":rows},ensure_ascii=False)
    parsed=parse_osm_geojson(data,"Рязань")
    assert len(parsed)==3
    assert parsed[0]["osm_id"]=="node/100"
    assert parsed[0]["source_url"]=="https://www.openstreetmap.org/node/100"
    assert parsed[1]["niche"]=="industrial"
    assert parsed[2]["niche"]=="equipment"
    assert parsed[0]["website_status"]=="found"
    with pytest.raises(ValueError):
        parse_osm_geojson(data,"Москва")
    with pytest.raises(ValueError):
        parse_osm_geojson("{}", "Тула")


def test_osm_rejects_big_import():
    with pytest.raises(ValueError):
        parse_osm_geojson(" "*(16_000_001),"Рязань")


def test_auto_discovery_and_crm_merge(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    monkeypatch.setattr(app,"DB_PATH",tmp_path/"leads.sqlite3")
    monkeypatch.setattr(app,"_discovery_job",{"state":"idle","added":0,"updated":0,"total":0,"message":""})
    app.init_db(seed=False)
    with app._lock, app.db_connection() as db:
        original_id=app.insert_lead(db, {
            "name":"Фабрика кухонь","city":"Рязань","niche":"furniture",
            "status":"replied","notes":"Дмитрий уже получил концепт"
        })
        db.commit()
    examples={"results":{"bindings":[
        wiki_record("Q12345","Q2746","Фабрика кухонь","Производство мебели","https://kitchens.example"),
        wiki_record("Q12346","Q2837","Калужский завод","Производство металла","https://metal.example"),
    ]}}
    monkeypatch.setattr(app,"discover_wikidata",lambda:parse_wikidata(examples))
    started=app.start_discovery(manual=True)
    assert started["state"] in {"running","done"}
    for _ in range(80):
        if app.discovery_status()["state"]=="done":break
        time.sleep(.025)
    result=app.discovery_status()
    assert result["state"]=="done" and result["added"]==1 and result["updated"]==1
    with app.db_connection() as db:
        rows=db.execute("SELECT * FROM leads WHERE demo=0").fetchall()
        assert len(rows)==2
        old=db.execute("SELECT * FROM leads WHERE id=?",(original_id,)).fetchone()
        assert old["status"]=="replied"
        assert old["notes"]=="Дмитрий уже получил концепт"
        assert old["website"]=="https://kitchens.example"
    with pytest.raises(ValueError,match="раза в час"):
        app.start_discovery(manual=True)
    assert app.start_discovery(manual=False)["state"]=="done"


def test_local_geojson_import_route(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    monkeypatch.setattr(app,"DB_PATH",tmp_path/"leads.sqlite3")
    monkeypatch.setattr(app,"_discovery_job",{"state":"idle","added":0,"updated":0,"total":0,"message":""})
    app.init_db(seed=False)
    server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    base="http://127.0.0.1:"+str(server.server_port)
    payload={"type":"FeatureCollection","features":[
        {"type":"Feature","id":"node/500","properties":{"name":"Проект кухни","shop":"furniture"}},
        {"type":"Feature","id":"way/700","properties":{"name":"Металлфабрика","man_made":"works"}},
    ]}
    try:
        req=lambda city:Request(base+"/api/company/import-geojson",
            data=json.dumps({"city":city,"geojson":json.dumps(payload,ensure_ascii=False)},ensure_ascii=False).encode(),
            headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req("Калуга"),timeout=3) as r: info=json.load(r)
        assert info["added"]==2 and info["total"]==2
        with urlopen(req("Калуга"),timeout=3) as r: second=json.load(r)
        assert second["added"]==0 and second["updated"]==2
        with app.db_connection() as db:
            assert db.execute("SELECT COUNT(*) FROM leads WHERE demo=0").fetchone()[0]==2
        with pytest.raises(HTTPError) as bad:
            urlopen(req("Москва"),timeout=3)
        assert bad.value.code==400
    finally:
        server.shutdown();server.server_close()


def test_discovery_ui_controls_present():
    root=Path(__file__).resolve().parents[1]
    ui=(root/"static/company-ui.js").read_text("utf8")
    appjs=(root/"static/app.js").read_text("utf8")
    assert "company-discover" in ui and "company-import-osm" in ui
    assert "company-auto-label" in ui
    assert "/api/company/import-geojson" in appjs

def test_nonbusiness_historic_structures_are_not_prospects():
    raw={"results":{"bindings":[
        wiki_record("Q55555","Q2770","Хозяйственная постройка усадьбы Лугиных","памятник архитектуры"),
        wiki_record("Q55556","Q2770","СтройТула","Строительная компания"),
    ]}}
    out=parse_wikidata(raw)
    assert len(out)==1 and out[0]["name"]=="СтройТула"


def test_background_sources_are_opt_in_by_default(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    assert app.preferences()["company_auto_discovery"] is False
    assert app.preferences()["company_auto_audit"] is False

