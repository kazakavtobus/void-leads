"""Company Hunter pilot: safe website checks, evidence and persistent review jobs."""
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import app
import company_hunter as hunter


def test_requires_public_https_domain():
    for url in [
        "http://company.ru/", "https://localhost/", "https://127.0.0.1/",
        "https://10.0.0.3/", "https://example.local/", "https://example.com:8443/",
        "https://user:pass@example.com/", "file:///etc/passwd",
        "https://metadata.google.internal/", "https://192.168.1.5/a",
    ]:
        with pytest.raises(ValueError):
            hunter.eligible_url(url)
    host, path, port, _ = hunter.eligible_url("https://пример.рф/uslugi?a=1")
    assert host == "xn--e1afmkfd.xn--p1ai"
    assert path == "/uslugi?a=1" and port == 443


def test_reject_private_dns_answers(monkeypatch):
    monkeypatch.setattr(hunter.socket, "getaddrinfo",
                        lambda *args, **kwargs: [(2,1,6,"",("192.168.1.21",443))])
    with pytest.raises(ValueError):
        hunter.public_ip("example.com")
    monkeypatch.setattr(hunter.socket, "getaddrinfo",
                        lambda *args, **kwargs: [
                            (2,1,6,"",("9.9.9.9",443)),(2,1,6,"",("127.0.0.1",443))])
    with pytest.raises(ValueError):
        hunter.public_ip("example.com")


def test_audit_explains_only_what_static_html_shows():
    lead={"name":"Тестовая мебельная фабрика","niche":"furniture"}
    html='<html><head><title>Кухни на заказ</title><meta name="viewport" content="width=device-width"></head>' + \
         '<body><h1>Кухни на заказ</h1><a href="/catalog">Каталог</a><form>' + \
         '<input type="text"></form><img alt="Фото кухни" src="/k.jpg"></body></html>'
    result=hunter.audit_html(lead,html,"https://example.com/")
    assert result["status"]=="analyzed"
    assert "Есть заголовок страницы (title)" in result["findings"]
    assert any("форм: 1" in x for x in result["findings"])
    assert not any("не обнаружен meta viewport" in x for x in result["findings"])
    assert "не проверены JS-формы" in result["disclaimer"]
    assert "Тестовая мебельная фабрика" in result["draft"]


def test_unknown_site_not_inferred_absent():
    record=hunter.audit_company({"name":"Фирма","niche":"construction","website":""})
    assert record["status"]=="not_checked" and record["score"]==0
    assert "не доказывает" in record["findings"][0]


def test_failed_site_never_claims_bad_design():
    record=hunter.audit_company(
        {"name":"Фирма","niche":"construction","website":"https://example.com"},
        fetcher=lambda _: (_ for _ in ()).throw(ValueError("HTTP 502"))
    )
    assert record["status"]=="unavailable" and record["score"]==0
    assert not record["opportunities"]
    assert "без выводов" in record["disclaimer"].lower() or "не делаем выводов" in record["disclaimer"].lower()


def test_queue_and_async_batch_keeps_original_crm(tmp_path,monkeypatch):
    monkeypatch.setattr(app,"DATA_DIR",tmp_path)
    monkeypatch.setattr(app,"DB_PATH",tmp_path/"leads.sqlite3")
    monkeypatch.setattr(app,"_company_job",{"state":"idle","total":0,"done":0,"ok":0,"errors":0,"message":""})
    app.init_db(seed=False)
    with app._lock,app.db_connection() as db:
        lead_id=app.insert_lead(db,{"name":"Тестовая фабрика","niche":"furniture","city":"Тула",
                                      "website":"https://example.com/","notes":"Моя заметка",
                                      "status":"new"})
        missing=app.insert_lead(db,{"name":"Без URL","niche":"construction","city":"Рязань"})
        db.commit()
        before=dict(db.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone())
    def fake_audit(lead):
        return hunter.audit_html(lead,
            '<html><head><title>Каталог</title></head><body><h1>Каталог</h1><form></form></body></html>',
            lead["website"])
    monkeypatch.setattr(app,"audit_company",fake_audit)
    server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    try:
        def get(path):
            with urlopen(base+path,timeout=4) as r:
                return json.load(r)
        def post(path,body):
            req=Request(base+path, data=json.dumps(body).encode("utf8"),method="POST",
                        headers={"Content-Type":"application/json"})
            with urlopen(req,timeout=4) as r:
                return json.load(r)
        listing=get("/api/company/queue")
        assert len(listing["companies"])==2
        assert any(c["id"]==missing and "НЕ подтверждено" in " ".join(c["audit"]["findings"])
                   for c in listing["companies"])
        job=post("/api/company/audit-batch",{"limit":8,"niche":"all"})
        assert job["total"]==1
        for _ in range(40):
            if get("/api/company/job")["state"]=="done":
                break
            time.sleep(.05)
        assert get("/api/company/job")["state"]=="done"
        inspected=get("/api/company/queue")
        item=next(c for c in inspected["companies"] if c["id"]==lead_id)
        assert item["audit"]["status"]=="analyzed"
        assert item["audit"]["findings"]
        assert item["audit"]["draft"]
        assert post("/api/company/audit-batch",{"limit":8})["total"]==0
        with app.db_connection() as db:
            after=dict(db.execute("SELECT * FROM leads WHERE id=?",(lead_id,)).fetchone())
        assert before==after
        req=Request(base+"/api/company/audit-batch",data=b'{"limit":999}',
                    method="POST",headers={"Content-Type":"application/json"})
        with pytest.raises(HTTPError) as err:
            urlopen(req,timeout=4)
        assert err.value.code==400
    finally:
        server.shutdown()
        server.server_close()


def test_ui_is_wired_and_does_not_pretend_visual_analysis():
    root=Path(__file__).resolve().parents[1]
    html=(root/"static/index.html").read_text("utf8")
    ui=(root/"static/company-ui.js").read_text("utf8")
    appjs=(root/"static/app.js").read_text("utf8")
    css=(root/"static/company-ui.css").read_text("utf8")
    assert 'data-view="company"' in html
    assert '/company-ui.js' in html and '/company-ui.css' in html
    assert "companyHunter()" in appjs
    assert "company-batch" in ui
    assert "HTML" in ui and "не проверяются" in ui
    assert ".company-work-grid" in css
