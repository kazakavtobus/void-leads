"""Acceptance tests for the October 20 false-positive news screenshots."""
from datetime import datetime, timezone
import json
import sqlite3
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import app
from public_hunter import classify

STAMP = datetime.now(timezone.utc).isoformat()
ACCIDENT = (
    "Трагедия произошла днём 19 октября во время изготовления арматурного "
    "каркаса опоры. Поддерживающие конструкции рухнули, а каркас упал "
    "на двух работников. Один мужчина погиб на месте, второй скончался "
    "по пути в больницу. ЖЁСТКИЕ НОВОСТИ ПУБЛИКУЕМ В ЧАТЕ https://max.ru/news"
)
EPIDEMIC = (
    "Заболеваемость COVID-19 за неделю выросла втрое в Рязанской области. "
    "С 12 по 18 октября коронавирус выявили у жителей региона. "
    "По данным Роспотребнадзора продолжается вакцинация от гриппа."
)
ADVERTISEMENT = (
    "🔥 Изготавливаем кухни на заказ в Рязани. Бесплатный замер, "
    "скидка 10%, звоните +7 999 111-22-33."
)


def test_actual_news_examples_are_not_ads():
    for item in [ACCIDENT, EPIDEMIC]:
        c = classify(item)
        assert not c["is_candidate"], c
        assert c["ad_score"] <= 25
        assert "Новость" in c["reason"]


def test_real_business_offer_remains_visible():
    result = classify(ADVERTISEMENT)
    assert result["is_candidate"] and result["ad_score"] >= 50
    assert result["niche"] == "furniture"
    assert result["signals"]["commercial_offer"]


def test_vk_promoted_news_does_not_bypass_classifier(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "leads.sqlite3")
    app.init_db(seed=False)
    with app.db_connection() as db:
        src, _ = app.add_public_source(db, dict(name="Рязань", city="Рязань", kind="vk",
                                              locator="ryazan_public"))
        db.commit()
        monkeypatch.setattr(app, "fetch_source", lambda source, token: [
            dict(external_id="-1_1", text=ACCIDENT, published_at=STAMP,
                 marked_as_ads=True, url="https://vk.com/wall-1_1"),
            dict(external_id="-1_2", text=EPIDEMIC, published_at=STAMP,
                 url="https://vk.com/wall-1_2"),
            dict(external_id="-1_3", text=ADVERTISEMENT, published_at=STAMP,
                 url="https://vk.com/wall-1_3"),
        ])
        outcome = app.check_public_source(db, src, token="fake-vk-service-token")
        assert outcome["read"] == 3
        assert outcome["matched"] == outcome["added"] == 1
        assert outcome["filtered"] == 2
        fresh = app.public_post_rows(db)
        assert len(fresh) == 1 and "кухни" in fresh[0]["text"].lower()
        filtered = app.public_post_rows(db, mode="filtered")
        assert len(filtered) == 2
        assert all(p["analysis_reason"] for p in filtered)
        source = db.execute("SELECT * FROM public_sources WHERE id=?", (src,)).fetchone()
        assert source["last_filtered"] == 2 and source["last_matched"] == 1


def test_old_news_migrate_without_touching_existing_crm(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "leads.sqlite3")
    app.init_db(seed=False)
    with app.db_connection() as db:
        app.upsert(db, {"name":"Старый клиент", "city":"Рязань"})
        source, _ = app.add_public_source(db, dict(name="Рязань", city="Рязань",
                                                   kind="vk", locator="ryazan_public"))
        db.execute("""INSERT INTO public_posts(source_id,external_id,source_url,
                      text,published_at,score,niche,suggested_name,created_at)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   (source,"legacy-news","https://vk.com/wall-1_9",
                    ACCIDENT,STAMP,61,"industrial","Новость",STAMP))
        db.commit()
    app.init_db(seed=False)
    with app.db_connection() as db:
        assert db.execute("SELECT count(*) FROM leads").fetchone()[0] == 1
        assert not app.public_post_rows(db,mode="fresh")
        row=db.execute("SELECT review_status, classifier_version, analysis_reason FROM public_posts").fetchone()
        assert row["review_status"]=="filtered"
        assert row["classifier_version"]==2
        assert row["analysis_reason"]


def test_user_can_restore_false_negative(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "leads.sqlite3")
    app.init_db(seed=False)
    with app.db_connection() as db:
        source, _ = app.add_public_source(db, dict(name="Паблик", city="Рязань", kind="manual"))
        row, _ = app.store_public_post(db, source,
            dict(external_id="rejected-test", text="Рекламное объявление без цены",
                 published_at=STAMP))
        db.commit()
    server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        req=Request(f"http://127.0.0.1:{server.server_port}/api/public/posts/{row}/restore",
                    data=b"{}", method="POST",headers={"Content-Type":"application/json"})
        with urlopen(req,timeout=5) as resp:
            assert json.load(resp)["ok"]
    finally:
        server.shutdown()
        server.server_close()
    app.init_db(seed=False)
    with app.db_connection() as db:
        assert db.execute("SELECT manual_override FROM public_posts WHERE id=?", (row,)).fetchone()[0]==1
        assert len(app.public_post_rows(db))==1
