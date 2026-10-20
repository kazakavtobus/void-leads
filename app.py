"""VOID LEADS: local-first, stdlib-only HTTP app for Windows."""
from __future__ import annotations
import argparse
import json
import os
import platform
import subprocess
import sqlite3
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
from contextlib import closing
from datetime import timedelta
import math
from secure_store import save_token, load_token, delete_token
from windows_shell import Tray, is_startup_enabled, set_startup
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from engine import (NICHES, SOURCES, STATUSES, WEB_STATUSES, detect_contacts,
                    draft_message, iso_now, norm, read_import_csv, safe_url, score_lead)
from osm import CITIES, CATEGORIES, search_osm
from public_hunter import classify, fetch_source, validate_vk_slug
from freshness import freshness, iso_publication, publication_time, DEFAULT_CHECK_MINUTES
from source_import import read_source_csv, validate_source, template_csv
from release_config import APP_VERSION, RELEASE_REPO
from updates import latest_release

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("VOID_LEADS_DATA", str(Path.home() / "VOID-LEADS")))
DB_PATH = DATA_DIR / "leads.sqlite3"
_lock = threading.RLock()
_last_osm_request = 0.0
_session_vk_token = ""  # RAM, or recoverable via Windows DPAPI on opted-in devices
_tray = None
_server = None
PREFS_DEFAULT = {"notifications": True, "min_notification_score": 55}

def preferences():
    p = DATA_DIR / "settings.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return PREFS_DEFAULT.copy()
        return {**PREFS_DEFAULT, **{k:raw[k] for k in PREFS_DEFAULT if k in raw}}
    except (FileNotFoundError, ValueError, OSError):
        return PREFS_DEFAULT.copy()

def save_preferences(**fields):
    current = preferences()
    current.update({k:v for k,v in fields.items() if k in PREFS_DEFAULT})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / "settings.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return current

def token_available():
    return bool(_session_vk_token or os.environ.get("VOID_VK_TOKEN") or
                (DATA_DIR / "vk_token.dpapi").is_file())

def authorized_vk_token():
    if _session_vk_token:
        return _session_vk_token
    if os.environ.get("VOID_VK_TOKEN"):
        return os.environ["VOID_VK_TOKEN"]
    try:
        return load_token(DATA_DIR)
    except (ValueError, OSError, RuntimeError):
        return ""

def notify_new_advertisers(count, source_name, latest_score):
    if not count or not preferences()["notifications"]:
        return False
    if latest_score < int(preferences()["min_notification_score"]):
        return False
    tray = _tray
    if not tray:
        return False
    return tray.notify("VOID LEADS · новые рекламодатели",
                       f"{source_name}: {count} новых объявлений. Открой Public Hunter.")



def db_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def init_db(seed: bool = True):
    with _lock, closing(db_connection()) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, city TEXT NOT NULL DEFAULT '', niche TEXT NOT NULL DEFAULT 'other',
                source_type TEXT NOT NULL DEFAULT 'manual', source_name TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '', osm_id TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '', website_status TEXT NOT NULL DEFAULT 'unknown',
                phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', social TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new', ad_count INTEGER NOT NULL DEFAULT 0,
                potential_value INTEGER NOT NULL DEFAULT 0, closed_value INTEGER NOT NULL DEFAULT 0,
                demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS public_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, city TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL, locator TEXT NOT NULL DEFAULT '',
                last_checked TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS public_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER REFERENCES public_sources(id) ON DELETE SET NULL,
                external_id TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL, published_at TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                niche TEXT NOT NULL DEFAULT 'other',
                suggested_name TEXT NOT NULL DEFAULT '',
                review_status TEXT NOT NULL DEFAULT 'new',
                lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_id,external_id)
            );
            CREATE INDEX IF NOT EXISTS posts_review ON public_posts(review_status);
            CREATE INDEX IF NOT EXISTS posts_source ON public_posts(source_id);
            CREATE INDEX IF NOT EXISTS leads_city ON leads(city);
            CREATE INDEX IF NOT EXISTS leads_status ON leads(status);
            CREATE INDEX IF NOT EXISTS activities_lead ON activities(lead_id);
        """)
        columns = {r[1] for r in db.execute("PRAGMA table_info(public_sources)")}
        for field, ddl in {
            "enabled": "INTEGER NOT NULL DEFAULT 1",
            "interval_minutes": "INTEGER NOT NULL DEFAULT 20",
            "last_attempt": "TEXT NOT NULL DEFAULT ''",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "last_count": "INTEGER NOT NULL DEFAULT 0",
            "last_matched": "INTEGER NOT NULL DEFAULT 0",
            "last_filtered": "INTEGER NOT NULL DEFAULT 0",
            "last_added": "INTEGER NOT NULL DEFAULT 0",
            "last_archive": "INTEGER NOT NULL DEFAULT 0",
            "last_undated": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if field not in columns:
                db.execute(f"ALTER TABLE public_sources ADD COLUMN {field} {ddl}")
        columns_posts = {r[1] for r in db.execute('PRAGMA table_info(public_posts)')}
        if 'promoted' not in columns_posts:
            db.execute('ALTER TABLE public_posts ADD COLUMN promoted INTEGER NOT NULL DEFAULT 0')
        for field, ddl in {
            "analysis_reason": "TEXT NOT NULL DEFAULT ''",
            "classifier_version": "INTEGER NOT NULL DEFAULT 0",
            "manual_override": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if field not in columns_posts:
                db.execute(f"ALTER TABLE public_posts ADD COLUMN {field} {ddl}")
        # One-time reclassification of old findings: move false positives out of
        # the fresh queue without touching CRM leads or previously reviewed posts.
        for old in db.execute("""SELECT id,text,review_status,manual_override
                                 FROM public_posts WHERE classifier_version<2""").fetchall():
            result = classify(old['text'])
            status = ('filtered' if old['review_status']=='new'
                      and not result['is_candidate'] and not old['manual_override']
                      else old['review_status'])
            db.execute("""UPDATE public_posts SET score=?, niche=?,
                          analysis_reason=?, review_status=?, classifier_version=2
                          WHERE id=?""",
                       (result['ad_score'],result['niche'],result['reason'],status,old['id']))
        db.execute("CREATE INDEX IF NOT EXISTS public_posts_published ON public_posts(published_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        if not db.execute("SELECT 1 FROM app_meta WHERE key='one_minute_checks'").fetchone():
            db.execute("UPDATE public_sources SET interval_minutes=1 WHERE interval_minutes=20")
            db.execute("INSERT INTO app_meta(key,value) VALUES('one_minute_checks','yes')")
        # Legacy RSS dates may be RFC822 text. Normalize for stable UTC comparisons.
        for r in db.execute("SELECT id,published_at FROM public_posts WHERE published_at != ''"):
            new_value = iso_publication(r['published_at'])
            if new_value != r['published_at']:
                db.execute("UPDATE public_posts SET published_at=? WHERE id=?", (new_value,r['id']))
        exists = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        if not exists and seed:
            examples = [
                dict(name="Кухни «Горизонт»", city="Тула", niche="furniture", source_type="ad",
                     source_name="Городское сообщество (демо)", description="Изготовление кухонь на заказ. Объявление о скидке на замер.",
                     website_status="unknown", ad_count=3, potential_value=38000, demo=1,
                     notes="Демонстрационный лид — компания и публикации вымышлены."),
                dict(name="СтройПрофиль", city="Казань", niche="construction", source_type="map",
                     source_name="OSM (демо)", description="Ремонт и отделка коммерческих помещений.",
                     ad_count=0, potential_value=45000, demo=1,
                     notes="Демонстрационный лид — не является реальным результатом поиска."),
                dict(name="Агентство «Линия»", city="Воронеж", niche="digital", source_type="partner",
                     source_name="Партнёрство (демо)", description="Маленькое маркетинговое агентство с веб-проектами.",
                     ad_count=0, potential_value=60000, demo=1,
                     notes="Демонстрационный лид — пример возможного партнёра."),
                dict(name="СпецТранс Регион", city="Краснодар", niche="equipment", source_type="ad",
                     source_name="Городское сообщество (демо)", description="Аренда экскаваторов и погрузчиков.",
                     ad_count=2, potential_value=55000, demo=1,
                     notes="Демонстрационный лид — компания вымышлена.")
            ]
            for ex in examples:
                insert_lead(db, ex, log=False)
        db.commit()


LEAD_TEXT_FIELDS = ["name", "city", "source_name", "description", "notes", "phone", "email", "social", "website", "source_url", "osm_id"]


def validated(data: dict, partial=False):
    d = {}
    for f in LEAD_TEXT_FIELDS:
        if f in data:
            cap = 6000 if f in ("description", "notes") else 1800 if f in ("source_url", "website", "social") else 250
            d[f] = str(data[f] or "").strip()[:cap]
    for f, allowed, default in (("niche", NICHES, "other"), ("source_type", SOURCES, "manual"),
                                 ("status", STATUSES, "new"), ("website_status", WEB_STATUSES, "unknown")):
        if f in data:
            d[f] = data[f] if data[f] in allowed else default
    for f in ("potential_value", "closed_value", "ad_count", "demo"):
        if f in data:
            try:
                d[f] = max(0, min(100000000, int(data[f] or 0)))
            except (ValueError, TypeError):
                d[f] = 0
    for f in ("source_url", "website", "social"):
        if f in d:
            d[f] = safe_url(d[f])
    if d.get("website") and d.get("website_status", "unknown") == "unknown":
        d["website_status"] = "found"
    if not partial and not d.get("name"):
        raise ValueError("Нужно название компании")
    return d


def insert_lead(db, data: dict, log=True):
    d = validated(data)
    d.setdefault("city", "")
    d.setdefault("source_type", "manual")
    d.setdefault("niche", "other")
    d.setdefault("status", "new")
    d.setdefault("website_status", "unknown")
    d.setdefault("ad_count", 0)
    d.setdefault("demo", 0)
    d.setdefault("potential_value", 0)
    d.setdefault("closed_value", 0)
    now = iso_now()
    d.update(created_at=now, updated_at=now, last_seen=now)
    fields = ",".join(d.keys())
    marks = ",".join("?" for _ in d)
    cur = db.execute(f"INSERT INTO leads ({fields}) VALUES ({marks})", list(d.values()))
    if log:
        log_activity(db, cur.lastrowid, "created", "Добавлен новый лид", d.get("source_url", ""))
    return cur.lastrowid


def log_activity(db, lead_id, kind, note="", url=""):
    db.execute("INSERT INTO activities (lead_id,kind,note,source_url,created_at) VALUES (?,?,?,?,?)",
               (lead_id, kind, note[:1500], safe_url(url), iso_now()))


def serialized(row):
    d = dict(row)
    d["score"], d["reasons"] = score_lead(d)
    return d


def find_existing(db, data):
    if data.get("osm_id"):
        match = db.execute("SELECT id FROM leads WHERE osm_id=? AND demo=0", (data["osm_id"],)).fetchone()
        if match:
            return match[0]
    name = norm(data.get("name", ""))
    city = norm(data.get("city", ""))
    if not name or not city:
        return None
    rows = db.execute("SELECT id,name,city,phone FROM leads WHERE demo=0").fetchall()
    for row in rows:
        if name == norm(row["name"]) and city == norm(row["city"]):
            return row["id"]
    return None


def upsert(db, data: dict, advert=False):
    d = validated(data)
    existing = find_existing(db, d)
    if existing:
        old = db.execute("SELECT * FROM leads WHERE id=?", (existing,)).fetchone()
        updates = {}
        for key in ("phone", "email", "social", "website", "osm_id", "description", "source_url", "source_name"):
            if not old[key] and d.get(key):
                updates[key] = d[key]
        if d.get("website_status") == "found" and old["website_status"] == "unknown":
            updates["website_status"] = "found"
        if advert:
            # A repeated import of the SAME publication URL isn't a new advertisement.
            same_publication = (bool(d.get("source_url")) and db.execute(
                "SELECT 1 FROM activities WHERE lead_id=? AND kind='advert' AND source_url=? LIMIT 1",
                (existing, d["source_url"])).fetchone() is not None) or (
                    not d.get("source_url") and bool(d.get("description")) and
                    norm(d.get("description", "")) == norm(old["description"]))
            if same_publication:
                return existing, False
            updates["ad_count"] = (old["ad_count"] or 0) + 1
            if old["source_type"] != "ad":
                updates["source_type"] = "ad"
        updates["updated_at"] = iso_now()
        updates["last_seen"] = iso_now()
        db.execute(f"UPDATE leads SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                   list(updates.values()) + [existing])
        log_activity(db, existing, "advert" if advert else "discovery",
                     "Повторная рекламная публикация" if advert else "Повторное обнаружение", d.get("source_url", ""))
        return existing, False
    if advert:
        d["source_type"] = "ad"
        d["ad_count"] = 1
    pk = insert_lead(db, d)
    if advert:
        log_activity(db, pk, "advert", "Первая рекламная публикация", d.get("source_url", ""))
    return pk, True


def get_leads(db, query):
    rows = db.execute("SELECT * FROM leads ORDER BY updated_at DESC, id DESC").fetchall()
    q = norm(query.get("q", [""])[0])
    mode = query.get("source", [""])[0]
    status = query.get("status", [""])[0]
    city = norm(query.get("city", [""])[0])
    demo = query.get("demo", ["all"])[0]
    leads = []
    for row in rows:
        lead = serialized(row)
        if q and not any(q in norm(str(lead.get(f, ""))) for f in ("name", "city", "niche", "description", "notes")):
            continue
        if mode and mode != "all" and lead["source_type"] != mode:
            continue
        if status and status != "all" and lead["status"] != status:
            continue
        if city and city not in norm(lead["city"]):
            continue
        if demo == "hide" and lead["demo"]:
            continue
        leads.append(lead)
    sort = query.get("sort", ["score"])[0]
    if sort == "recent":
        leads.sort(key=lambda d: (d["last_seen"], d["id"]), reverse=True)
    elif sort == "value":
        leads.sort(key=lambda d: (d["potential_value"], d["score"]), reverse=True)
    else:
        leads.sort(key=lambda d: (d["score"], d["id"]), reverse=True)
    return leads


def stats(db):
    all_leads = [serialized(r) for r in db.execute("SELECT * FROM leads WHERE demo=0").fetchall()]
    return {
        "total": len(all_leads), "hot": sum(l["score"] >= 75 for l in all_leads),
        "contacted": sum(l["status"] in ("contacted", "replied", "won") for l in all_leads),
        "won": sum(l["status"] == "won" for l in all_leads),
        "revenue": sum(l["closed_value"] for l in all_leads if l["status"] == "won"),
        "new": sum(l["status"] == "new" for l in all_leads),
        "by_source": {k: sum(l["source_type"] == k for l in all_leads) for k in SOURCES},
        "demo_count": db.execute("SELECT COUNT(*) FROM leads WHERE demo=1").fetchone()[0]
    }


def store_public_post(db, source_id: int, data: dict):
    analysis = classify(data.get("text", ""))
    text = str(data.get("text", ""))[:10000]
    url = safe_url(data.get("url", ""))
    external = str(data.get("external_id") or url or "")[:350]
    if not external or not text:
        raise ValueError("Нужен идентификатор и текст публикации")
    row = db.execute("SELECT id FROM public_posts WHERE source_id=? AND external_id=?", (source_id, external)).fetchone()
    if row:
        return row[0], False
    # VK "marked_as_ads" is not enough: news may also be promoted.
    is_ad = analysis["is_candidate"]
    score = min(100, analysis["ad_score"] + (7 if data.get("marked_as_ads") and is_ad else 0))
    cur = db.execute("""INSERT INTO public_posts(source_id,external_id,source_url,text,published_at,
        score,niche,suggested_name,created_at,promoted,review_status,analysis_reason,classifier_version)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (source_id,external,url,text,iso_publication(data.get("published_at", "")),
         score,analysis["niche"],analysis["suggested_name"],iso_now(),
         int(bool(data.get("marked_as_ads"))), 'new' if is_ad else 'filtered',
         analysis['reason'],2))
    return cur.lastrowid, True


def public_post_rows(db, mode='fresh'):
    rows = db.execute("""SELECT p.*, COALESCE(s.name,'Источник удалён') AS source_name,
                       COALESCE(s.city,'') AS city, COALESCE(s.kind,'manual') AS source_kind
                       FROM public_posts p LEFT JOIN public_sources s ON s.id=p.source_id
                       ORDER BY p.published_at DESC, p.id DESC LIMIT 2500""").fetchall()
    values = []
    for r in rows:
        post = dict(r)
        post['freshness'] = freshness(post['published_at'])
        if mode == 'fresh' and not (post['review_status']=='new' and post['freshness'] in {'hot','fresh'}):
            continue
        if mode == 'archive' and not (post['review_status']=='new' and post['freshness']=='archive'):
            continue
        if mode == 'undated' and not (post['review_status']=='new' and post['freshness']=='undated'):
            continue
        if mode in {'new','converted','ignored','filtered'} and post['review_status']!=mode:
            continue
        values.append(post)
        if len(values) >= 500:
            break
    # Hot first, then newest within each group.
    if mode == 'fresh':
        values.sort(key=lambda d: (d['freshness']=='hot',d['published_at'],d['id']),reverse=True)
    return values


def public_post_counts(db):
    rows = db.execute('SELECT published_at,review_status FROM public_posts').fetchall()
    out = {'fresh':0, 'hot':0, 'archive':0, 'undated':0, 'converted':0, 'filtered':0, 'all':len(rows)}
    for row in rows:
        if row['review_status']=='filtered':
            out['filtered'] += 1
        if row['review_status']=='converted':
            out['converted'] += 1
        if row['review_status']=='new':
            category = freshness(row['published_at'])
            out[category] += 1
            if category=='hot':
                out['fresh'] += 1
    return out


def add_public_source(db, raw):
    src = validate_source(raw)
    # Re-adding a source updates metadata, but must not reset past publications.
    if src['locator']:
        existing = db.execute('SELECT id FROM public_sources WHERE kind=? AND locator=?',
                              (src['kind'],src['locator'])).fetchone()
    else:
        existing = db.execute('SELECT id FROM public_sources WHERE kind=? AND lower(name)=lower(?) AND lower(city)=lower(?)',
                              (src['kind'],src['name'],src['city'])).fetchone()
    if existing:
        return existing['id'], False
    cur = db.execute("""INSERT INTO public_sources(name,city,kind,locator,created_at,enabled,interval_minutes)
                        VALUES(?,?,?,?,?,?,?)""",
                     (src['name'],src['city'],src['kind'],src['locator'],iso_now(),src['enabled'],src['interval_minutes']))
    return cur.lastrowid, True


def check_public_source(db, sid, token='', bypass_cooldown=False):
    global _session_vk_token
    src = db.execute('SELECT * FROM public_sources WHERE id=?', (sid,)).fetchone()
    if not src:
        raise ValueError('Источник не найден')
    if src['kind']=='manual':
        raise ValueError('Ручной источник заполняется через форму')
    if not bypass_cooldown and src['last_attempt']:
        last = publication_time(src['last_attempt'])
        if last:
            remaining = 60 - (publication_time(iso_now())-last).total_seconds()
            if remaining > 0:
                raise ValueError(f'Повторная проверка через {math.ceil(remaining)} сек.')
    resolved_token = token or authorized_vk_token()
    if src['kind']=='vk' and token:
        _session_vk_token = token  # manual input stays in RAM until saved explicitly
    db.execute('UPDATE public_sources SET last_attempt=? WHERE id=?', (iso_now(),sid))
    db.commit()
    try:
        rows = fetch_source(dict(src), resolved_token if src['kind']=='vk' else '')
        added, matched, recent, rejected, archived, undated = 0, 0, 0, 0, 0, 0
        samples = 0
        for post in rows:
            candidate = classify(post.get('text',''))
            category = freshness(post.get('published_at', ''))
            if not candidate['is_candidate']:
                rejected += 1
                # Keep a *small, reviewable* sample of fresh rejections,
                # never a full archive of the news feed.
                if category in {'hot','fresh'} and samples < 10:
                    store_public_post(db, sid, post)
                    samples += 1
                continue
            matched += 1
            if category in {'hot','fresh'}:
                recent += 1
            elif category == 'archive':
                archived += 1
            else:
                undated += 1
            # Archive older advertiser posts for duplicate/repeat-ad history.
            _, created = store_public_post(db, sid, post)
            added += bool(created and category in {'hot','fresh'})
        # Rejected samples are not CRM entries; prune after seven days.
        db.execute("""DELETE FROM public_posts WHERE review_status='filtered'
                      AND created_at < ?""",
                   ((publication_time(iso_now()) - timedelta(days=7)).isoformat(),))
        db.execute("""UPDATE public_sources SET last_checked=?,last_error='',last_count=?,
                    last_matched=?,last_filtered=?,last_added=?,
                    last_archive=?,last_undated=? WHERE id=?""",
                   (iso_now(),len(rows),matched,rejected,added,archived,undated,sid))
        db.commit()
        if added:
            latest_score = max((p['score'] for p in public_post_rows(db, mode='fresh')
                                if p['source_id']==sid), default=0)
            notify_new_advertisers(added, src['name'], latest_score)
        return {'read':len(rows),'matched':matched,'added':added,
                'fresh_candidates':recent,'filtered':rejected,
                'archived':archived,'undated':undated}
    except Exception as exc:
        db.execute('UPDATE public_sources SET last_error=? WHERE id=?', (str(exc)[:400],sid))
        db.commit()
        raise


def auto_check_loop(stop_event):
    # Checks permissioned feeds at a configured interval while the app is running.
    while not stop_event.wait(10):
        try:
            with _lock, closing(db_connection()) as db:
                rows = db.execute("SELECT * FROM public_sources WHERE enabled=1 AND kind IN ('vk','rss') ORDER BY last_attempt ASC, id ASC").fetchall()
                now = publication_time(iso_now())
                for s in rows:
                    if s['kind']=='vk' and not authorized_vk_token():
                        continue
                    last = publication_time(s['last_attempt'])
                    if last and (now-last).total_seconds()<max(1,s['interval_minutes'])*60:
                        continue
                    try:
                        check_public_source(db, s['id'], bypass_cooldown=True)
                    except Exception:
                        pass  # the error is stored in last_error, never print credentials
                    break
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "VOIDLeads/0.6"

    def log_message(self, format, *args):
        pass

    def send_json(self, code, value):
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, file):
        mime = {".html": "text/html", ".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
        if not file.is_file():
            return self.send_json(404, {"error": "Не найдено"})
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime.get(file.suffix, 'application/octet-stream')}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ValueError("Нужен JSON")
        size = int(self.headers.get("Content-Length", "0"))
        if size > 2500000 or size < 2:
            raise ValueError("Неверный размер запроса")
        return json.loads(self.rfile.read(size))

    def do_GET(self):
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        try:
            if path == "/":
                return self.send_file(STATIC / "index.html")
            if path in {"/app.js", "/style.css"}:
                return self.send_file(STATIC / path.lstrip("/"))
            if path == "/api/config":
                return self.send_json(200, {"cities": list(CITIES), "map_categories": list(CATEGORIES),
                                            "niches": NICHES, "version": APP_VERSION, "fresh_hours": 24, "hot_hours": 2,
                                            "interval_minutes": 1, "username": "Данил",
                                            "vk_token_saved": (DATA_DIR / 'vk_token.dpapi').is_file(),
                                            "vk_token_available": token_available(),
                                            "preferences": preferences(),
                                            "startup_enabled": is_startup_enabled(),
                                            "tray_available": bool(_tray and _tray.icon)})
            if path == "/api/update/check":
                return self.send_json(200, latest_release())
            with closing(db_connection()) as db:
                if path == "/api/public/sources/template.csv":
                    payload = template_csv().encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type','text/csv; charset=utf-8')
                    self.send_header('Content-Disposition','attachment; filename="void_leads_sources_template.csv"')
                    self.send_header('Content-Length',str(len(payload)))
                    self.end_headers()
                    return self.wfile.write(payload)
                if path == "/api/public/sources":
                    sources = [dict(r) for r in db.execute("SELECT * FROM public_sources ORDER BY id DESC")]
                    for source in sources:
                        stamps = db.execute("SELECT published_at FROM public_posts WHERE source_id=? AND review_status='new'", (source['id'],)).fetchall()
                        source['new_posts'] = sum(freshness(p['published_at']) in {'hot','fresh'} for p in stamps)
                    return self.send_json(200, sources)
                if path == "/api/public/posts":
                    return self.send_json(200, public_post_rows(db, query.get('status',['fresh'])[0]))
                if path == "/api/public/stats":
                    counts = public_post_counts(db)
                    counts.update(sources=db.execute('SELECT COUNT(*) FROM public_sources').fetchone()[0],
                                  review=counts['fresh'])
                    return self.send_json(200, counts)
                if path == "/api/stats":
                    return self.send_json(200, stats(db))
                if path == "/api/leads":
                    return self.send_json(200, get_leads(db, query))
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[:2] == ["api", "leads"] and parts[2].isdigit():
                    pk = int(parts[2])
                    row = db.execute("SELECT * FROM leads WHERE id=?", (pk,)).fetchone()
                    if not row:
                        return self.send_json(404, {"error": "Лид не найден"})
                    lead = serialized(row)
                    lead["activities"] = [dict(x) for x in db.execute(
                        "SELECT * FROM activities WHERE lead_id=? ORDER BY id DESC", (pk,)).fetchall()]
                    lead["draft"] = draft_message(lead)
                    return self.send_json(200, lead)
            return self.send_json(404, {"error": "Не найдено"})
        except Exception as exc:
            return self.send_json(500, {"error": str(exc)})

    def do_POST(self):
        self.write_route("POST")

    def do_PATCH(self):
        self.write_route("PATCH")

    def do_DELETE(self):
        self.write_route("DELETE")

    def write_route(self, method):
        global _last_osm_request
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        # Avoid localhost cross-site form/posts and DNS rebinding surprises.
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            return self.send_json(403, {"error": "Только локальный доступ"})
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.send_json(403, {"error": "Недопустимый источник запроса"})
        try:
            data = {} if method == "DELETE" else self.read_body()
            if path == '/api/vk/token' and method == 'POST':
                token = str(data.get('token','')).strip()
                save_token(DATA_DIR,token)
                global _session_vk_token
                _session_vk_token = token
                return self.send_json(200, {'ok': True, 'saved': True})
            if path == '/api/vk/token' and method == 'DELETE':
                delete_token(DATA_DIR)
                _session_vk_token = ''
                return self.send_json(200, {'ok': True, 'saved': False})
            if path == '/api/preferences' and method == 'POST':
                fields = {}
                if 'notifications' in data:
                    if not isinstance(data['notifications'], bool):
                        raise ValueError('notifications должен быть true или false')
                    fields['notifications'] = data['notifications']
                if 'min_notification_score' in data:
                    score = data['min_notification_score']
                    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
                        raise ValueError('Укажи оценку от 0 до 100')
                    fields['min_notification_score'] = score
                if 'startup' in data:
                    if not isinstance(data['startup'], bool):
                        raise ValueError('startup должен быть true или false')
                    set_startup(data['startup'])
                return self.send_json(200, {'ok':True, 'preferences':save_preferences(**fields),
                                            'startup_enabled':is_startup_enabled()})
            if path == '/api/update/install' and method == 'POST':
                if os.name != 'nt' or not getattr(sys, 'frozen', False):
                    raise ValueError('Установка обновлений доступна только из установленной Windows-версии')
                if not RELEASE_REPO:
                    raise ValueError('Канал обновлений не настроен: сначала опубликуйте выпуск на GitHub')
                if not latest_release().get('available'):
                    raise ValueError('Установлена последняя версия')
                updater = Path(sys.executable).with_name('VOID-Updater.exe')
                if not updater.is_file():
                    raise ValueError('Не найден компонент обновления VOID-Updater.exe. Переустановите приложение.')
                temp_dir = Path(tempfile.mkdtemp(prefix='void-leads-update-'))
                temp_updater = temp_dir / 'VOID-Updater.exe'
                shutil.copy2(updater, temp_updater)
                subprocess.Popen([str(temp_updater), '--pid', str(os.getpid()),
                                  '--app', str(Path(sys.executable).resolve())],
                                 cwd=str(temp_dir), close_fds=True,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                threading.Timer(2.0, self.server.shutdown).start()
                return self.send_json(200, {'ok': True, 'message': 'Обновлятор запущен. Приложение сейчас закроется.'})
            with _lock, closing(db_connection()) as db:
                if path == "/api/public/sources" and method == "POST":
                    sid, fresh = add_public_source(db, data)
                    db.commit()
                    return self.send_json(201 if fresh else 200, {'id':sid,'created':fresh})
                if path == "/api/public/sources/import" and method == "POST":
                    rows, errors = read_source_csv(data.get('csv',''))
                    added=duplicates=0
                    for row in rows:
                        _, fresh = add_public_source(db, row)
                        added += bool(fresh)
                        duplicates += not fresh
                    db.commit()
                    return self.send_json(200, {'added':added,'duplicates':duplicates,
                                                'invalid':len(errors),'errors':errors[:20]})
                if path == "/api/public/posts/manual" and method == "POST":
                    source_id = int(data.get("source_id", 0))
                    src = db.execute("SELECT * FROM public_sources WHERE id=?", (source_id,)).fetchone()
                    if not src:
                        raise ValueError("Источник не найден")
                    description = str(data.get("text", "")).strip()[:10000]
                    if len(description) < 10:
                        raise ValueError("Вставь текст публикации (не менее 10 символов)")
                    link = safe_url(data.get("url", ""))
                    from hashlib import sha256
                    external = link or sha256(description.encode("utf-8")).hexdigest()
                    pk, fresh = store_public_post(db, source_id, {"external_id": external, "url": link,
                        "text": description, "published_at": data.get('published_at','')})
                    db.commit()
                    return self.send_json(200, {"id": pk, "created": fresh})
                if path == "/api/public/check" and method == "POST":
                    sid = int(data.get('source_id',0))
                    response = check_public_source(db, sid, str(data.get('token','')))
                    return self.send_json(200,response)
                if len(parts) == 4 and parts[:3] == ["api", "public", "sources"] and parts[3].isdigit() and method == "PATCH":
                    sid=int(parts[3])
                    enabled=data.get('enabled')
                    if enabled not in (0,1,True,False):
                        raise ValueError('Укажи enabled 0 или 1')
                    result=db.execute('UPDATE public_sources SET enabled=? WHERE id=?',(int(bool(enabled)),sid))
                    if not result.rowcount:
                        raise ValueError('Источник не найден')
                    db.commit()
                    return self.send_json(200, {'ok':True})
                if len(parts) == 4 and parts[:3] == ["api", "public", "sources"] and parts[3].isdigit() and method == "DELETE":
                    sid = int(parts[3])
                    db.execute("DELETE FROM public_sources WHERE id=?", (sid,))
                    db.commit()
                    return self.send_json(200, {"ok": True})
                if len(parts) == 5 and parts[:3] == ["api", "public", "posts"] and parts[3].isdigit() and parts[4] == "ignore" and method == "POST":
                    db.execute("UPDATE public_posts SET review_status='ignored' WHERE id=? AND review_status='new'", (int(parts[3]),))
                    db.commit()
                    return self.send_json(200, {"ok": True})
                if len(parts) == 5 and parts[:3] == ["api", "public", "posts"] and parts[3].isdigit() and parts[4] == "restore" and method == "POST":
                    # A user can rescue a false negative; never silently
                    # override this decision on next database migration.
                    result=db.execute("""UPDATE public_posts SET review_status='new',
                        manual_override=1,analysis_reason='Восстановлено вручную'
                        WHERE id=? AND review_status='filtered'""", (int(parts[3]),))
                    if not result.rowcount:
                        raise ValueError('Публикация уже обработана или не найдена')
                    db.commit()
                    return self.send_json(200, {'ok': True})
                if len(parts) == 5 and parts[:3] == ["api", "public", "posts"] and parts[3].isdigit() and parts[4] == "convert" and method == "POST":
                    pk = int(parts[3])
                    post = db.execute("""SELECT p.*,s.city,s.name AS source_name FROM public_posts p
                        LEFT JOIN public_sources s ON s.id=p.source_id WHERE p.id=?""", (pk,)).fetchone()
                    if not post or post["review_status"] != "new":
                        raise ValueError("Публикация уже обработана или не найдена")
                    name = str(data.get("name", "")).strip()
                    if len(name) < 2:
                        raise ValueError("Укажи проверенное название компании")
                    details = classify(post["text"])
                    vals = {"name":name,"city":post["city"] or "", "niche":post["niche"],
                            "source_type":"ad", "source_name":post["source_name"] or "Паблик",
                            "source_url":post["source_url"], "description":post["text"],
                            "website_status":"unknown", "phone":details["phone"],
                            "social":details["social"],"website":details["website"],"email":details["email"]}
                    lead_id, created = upsert(db, vals, advert=True)
                    db.execute("UPDATE public_posts SET review_status='converted',lead_id=? WHERE id=?", (lead_id,pk))
                    db.commit()
                    return self.send_json(200, {"lead_id": lead_id, "created": created})
                if path == "/api/leads" and method == "POST":
                    pk, created = upsert(db, data)
                    db.commit()
                    return self.send_json(201 if created else 200, {"id": pk, "created": created})
                if path == "/api/capture/ad" and method == "POST":
                    title = (data.get("name") or "").strip()
                    if not title:
                        raise ValueError("Введите название рекламодателя")
                    parsed = detect_contacts(data.get("description", ""))
                    values = {**parsed, **{k: v for k, v in data.items() if v not in ("", None)},
                              "source_type": "ad", "source_name": data.get("source_name") or "Местная реклама"}
                    if parsed.get("website") and not data.get("website_status"):
                        values["website_status"] = "found"
                    pk, created = upsert(db, values, advert=True)
                    db.commit()
                    lead = db.execute("SELECT * FROM leads WHERE id=?", (pk,)).fetchone()
                    return self.send_json(200, {"id": pk, "created": created, "lead": serialized(lead)})
                if path == "/api/import/csv" and method == "POST":
                    rows = read_import_csv(data.get("csv", ""))
                    created = updated = 0
                    for row in rows:
                        row["source_type"] = row.get("source_type") or "manual"
                        _, fresh = upsert(db, row)
                        created += bool(fresh)
                        updated += not fresh
                    db.commit()
                    return self.send_json(200, {"created": created, "updated": updated})
                if path == "/api/osm/search" and method == "POST":
                    city = data.get("city", "")
                    niche = data.get("niche", "")
                    if city not in CITIES or niche not in CATEGORIES:
                        raise ValueError("Выберите город и нишу")
                    # Strict cooldown protects community-hosted Overpass servers.
                    if time.monotonic() - _last_osm_request < 90:
                        raise ValueError("Между запросами к OSM нужно подождать минимум 90 секунд")
                    _last_osm_request = time.monotonic()
                    # Doing one synchronous query is safe here: ThreadingHTTPServer handles UI requests.
                    try:
                        rows = search_osm(city, niche)
                    except Exception as exc:
                        raise ValueError(f"OSM сейчас недоступен: {exc}") from exc
                    created = updated = 0
                    for row in rows:
                        _, fresh = upsert(db, row)
                        created += bool(fresh)
                        updated += not fresh
                    db.commit()
                    return self.send_json(200, {"created": created, "updated": updated, "total": len(rows)})
                if len(parts) == 3 and parts[:2] == ["api", "leads"] and parts[2].isdigit():
                    pk = int(parts[2])
                    row = db.execute("SELECT * FROM leads WHERE id=?", (pk,)).fetchone()
                    if not row:
                        return self.send_json(404, {"error": "Лид не найден"})
                    if method == "PATCH":
                        updates = validated(data, partial=True)
                        if not updates:
                            raise ValueError("Нет изменений")
                        updates["updated_at"] = iso_now()
                        db.execute(f"UPDATE leads SET {','.join(f'{k}=?' for k in updates)} WHERE id=?",
                                   list(updates.values()) + [pk])
                        log_activity(db, pk, "updated", "Обновлены данные: " + ", ".join(data.keys()))
                        db.commit()
                        return self.send_json(200, {"ok": True})
                    if method == "DELETE":
                        db.execute("DELETE FROM leads WHERE id=?", (pk,))
                        db.commit()
                        return self.send_json(200, {"ok": True})
                if len(parts) == 4 and parts[:2] == ["api", "leads"] and parts[2].isdigit() and parts[3] == "activity" and method == "POST":
                    pk = int(parts[2])
                    if not db.execute("SELECT 1 FROM leads WHERE id=?", (pk,)).fetchone():
                        return self.send_json(404, {"error": "Лид не найден"})
                    note = str(data.get("note", "")).strip()
                    if not note:
                        raise ValueError("Введите текст заметки")
                    log_activity(db, pk, "note", note)
                    db.execute("UPDATE leads SET updated_at=? WHERE id=?", (iso_now(), pk))
                    db.commit()
                    return self.send_json(200, {"ok": True})
            return self.send_json(404, {"error": "Не найдено"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except sqlite3.Error as exc:
            return self.send_json(500, {"error": f"Ошибка базы: {exc}"})
        except Exception as exc:
            return self.send_json(500, {"error": str(exc)})


def launch_ui(url):
    """On Windows prefer an app-like Edge window; fallback to the default browser."""
    if platform.system() == "Windows":
        roots = [os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""),
                 os.environ.get("LOCALAPPDATA", "")]
        for root in roots:
            if not root:
                continue
            paths = [Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                     Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"]
            for binary in paths:
                if binary.is_file():
                    try:
                        subprocess.Popen([str(binary), f"--app={url}", "--window-size=1440,900"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except OSError:
                        pass
    webbrowser.open(url)


def serve(port=8765, open_browser=True):
    global _tray, _server
    init_db()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    actual_port = server.server_address[1]
    _server = server
    url = f"http://127.0.0.1:{actual_port}"
    print(f"VOID LEADS открыт: {url}\nДанные: {DB_PATH}\nCtrl+C — остановить")
    if os.name == 'nt' and getattr(sys,'frozen',False):
        _tray = Tray(lambda: launch_ui(url), lambda: threading.Thread(
            target=server.shutdown,daemon=True).start(), ROOT / "assets" / "void-leads.png")
        _tray.start()
    if open_browser:
        threading.Timer(0.8, lambda: launch_ui(url)).start()
    stop_background = threading.Event()
    worker = threading.Thread(target=auto_check_loop, args=(stop_background,), daemon=True)
    worker.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop_background.set()
        if _tray:
            _tray.stop()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VOID LEADS local Windows CRM")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.port, not args.no_browser)
