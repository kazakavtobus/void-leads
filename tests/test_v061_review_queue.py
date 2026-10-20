"""Regression: VK returned 24 old posts; 1 ad archived, 23 rejected invisible in v0.6.0."""
from datetime import datetime, timedelta, timezone
import app


def test_all_old_rejected_posts_are_reviewable(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "void.sqlite3")
    monkeypatch.setattr(app, "_session_vk_token", "")
    app.init_db(seed=False)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    posts = []
    for i in range(23):
        posts.append({
            "external_id": f"-555_{i+1}",
            "url": f"https://vk.com/wall-555_{i+1}",
            "text": f"Новости города #{i+1}: жители обсудили дороги и погоду. В регионе работает диспетчерская.",
            "published_at": old,
        })
    posts.append({
        "external_id": "-555_24",
        "url": "https://vk.com/wall-555_24",
        "text": "Маникюр по записи. Запишитесь на маникюр, скидка 10%, "
                "звоните +7 999 123-45-67. Педикюр от 700 рублей!",
        "published_at": old,
    })
    with app.db_connection() as db:
        sid, _ = app.add_public_source(db, {
            "name": "Объявления и Услуги",
            "city": "Рязань",
            "kind": "vk",
            "locator": "bizrzn",
        })
        db.commit()
        monkeypatch.setattr(app, "fetch_source", lambda src, token: posts)
        result = app.check_public_source(db, sid, token="safe-test-token")
        assert result["read"] == 24
        assert result["matched"] == 1
        assert result["filtered"] == 23
        assert result["reviewable"] == 23
        assert result["archived"] == 1
        assert result["added"] == 0
        assert len(app.public_post_rows(db, mode="fresh")) == 0
        rejected = app.public_post_rows(db, mode="filtered")
        assert len(rejected) == 23
        assert all(p["source_id"] == sid for p in rejected)
        assert all(p["freshness"] == "archive" for p in rejected)
        assert all(p["analysis_reason"] for p in rejected)
        archive = app.public_post_rows(db, mode="archive")
        assert len(archive) == 1
        source = db.execute("SELECT * FROM public_sources WHERE id=?", (sid,)).fetchone()
        assert source["last_filtered"] == source["last_reviewable"] == 23
        assert source["last_archive"] == 1
        # Re-check never duplicates historical findings.
        repeated = app.check_public_source(db, sid, token="safe-test-token",
                                            bypass_cooldown=True)
        assert repeated["reviewable"] == 23
        assert repeated["added"] == 0
        assert len(app.public_post_rows(db, mode="filtered")) == 23


def test_filtered_sort_is_by_detection_not_old_publication_date(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "void.sqlite3")
    app.init_db(seed=False)
    with app.db_connection() as db:
        sid, _ = app.add_public_source(db, {
            "name": "Public",
            "city": "Тула",
            "kind": "vk",
            "locator": "overhear.tula",
        })
        db.commit()
        # Make sure old news stays inspectable, even if its publication timestamp is years old.
        _, created = app.store_public_post(db, sid, {
            "text": "Городская новость о дорожном ремонте, в регионе сообщили об аварии.",
            "external_id": "-333_1",
            "url": "https://vk.com/wall-333_1",
            "published_at": "2022-09-05T14:00:00+00:00",
        })
        assert created
        db.commit()
        assert len(app.public_post_rows(db, mode="filtered")) == 1


def test_review_page_explains_non_fresh_queue():
    from pathlib import Path
    js = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    assert "last_reviewable" in js
    assert "посты любого возраста" in js
    assert "state.postStatus=p?.freshness==='archive'?'archive'" in js
