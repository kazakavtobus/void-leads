"""VOID LEADS: local-first, stdlib-only HTTP app for Windows."""
from __future__ import annotations
import argparse
import hmac
import secrets
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
from target_hunter import analyze_target, DEFAULT_TARGET_NICHES, NICHE_NAMES
from freshness import freshness, iso_publication, publication_time, DEFAULT_CHECK_MINUTES
from source_import import read_source_csv, validate_source, template_csv
from release_config import APP_VERSION, RELEASE_REPO
from updates import latest_release
from company_hunter import audit_company, SCAN_NICHES
from company_discovery import discover_wikidata, parse_osm_geojson, CITY_IDS
from company_vk import search_vk, CITIES as VK_CITIES, TERMS as VK_TERMS
from update_state import read_status, write_status

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("VOID_LEADS_DATA", str(Path.home() / "VOID-LEADS")))
DB_PATH = DATA_DIR / "leads.sqlite3"
_lock = threading.RLock()
_last_osm_request = 0.0
_session_vk_token = ""  # service token for walls
_session_vk_user_token = ""  # separate user token for groups.search, Windows DPAPI
_tray = None
_server = None
_single_mutex = None
_update_in_progress = False
_update_key = ''
_browser_processes = []
_vk_company_lock = threading.Lock()
_vk_company_job = {'state':'idle','added':0,'updated':0,'total':0,'done':0,'message':''}
_discovery_lock = threading.Lock()
_discovery_job = {'state':'idle','added':0,'updated':0,'total':0,'message':''}
_company_job_lock = threading.Lock()
_company_job = {'state':'idle','total':0,'done':0,'ok':0,'errors':0,'message':''}
PREFS_DEFAULT = {"notifications": True, "min_notification_score": 60,
                 "target_niches": DEFAULT_TARGET_NICHES.copy(),
                 "company_auto_discovery": False, "company_auto_audit": False,
                 "company_auto_vk": False}

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

def vk_user_token_available():
    return bool(_session_vk_user_token or os.environ.get("VOID_VK_USER_TOKEN") or
                (DATA_DIR / "vk_user_token.dpapi").is_file())


def authorized_vk_user_token():
    if _session_vk_user_token:
        return _session_vk_user_token
    if os.environ.get("VOID_VK_USER_TOKEN"):
        return os.environ["VOID_VK_USER_TOKEN"]
    try:
        return load_token(DATA_DIR,filename="vk_user_token.dpapi")
    except (ValueError,OSError,RuntimeError):
        return ""


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
            CREATE TABLE IF NOT EXISTS discovery_runs (
                provider TEXT PRIMARY KEY,
                last_attempt TEXT NOT NULL DEFAULT '',
                last_success TEXT NOT NULL DEFAULT '',
                last_result TEXT NOT NULL DEFAULT '',
                last_added INTEGER NOT NULL DEFAULT 0,
                last_updated INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS company_audits (
                lead_id INTEGER PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
                status TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL DEFAULT '', site_url TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS company_audits_score ON company_audits(score);
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
            "last_reviewable": "INTEGER NOT NULL DEFAULT 0",
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
            "target_score": "INTEGER NOT NULL DEFAULT 0",
            "target_reason": "TEXT NOT NULL DEFAULT ''",
            "site_evidence": "TEXT NOT NULL DEFAULT 'not_checked'",
            "target_version": "INTEGER NOT NULL DEFAULT 0",
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
        # Opportunity score migration must not change advertisement score or review decisions.
        for old in db.execute("""SELECT id,text,review_status,manual_override
                                FROM public_posts WHERE target_version<1""").fetchall():
            analysis = classify(old['text'])
            if old['review_status']=='new' and old['manual_override']:
                analysis['is_candidate']=True
            target = analyze_target(old['text'],analysis)
            db.execute("""UPDATE public_posts SET target_score=?,target_reason=?,
                          site_evidence=?,target_version=1 WHERE id=?""",
                       (target['target_score'],target['target_reason'],
                        target['site_evidence'],old['id']))
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
    rows = db.execute("SELECT id,name,city,phone,website FROM leads WHERE demo=0").fetchall()
    # Same verified business domain in the same city is a useful secondary key
    # for legal-name vs trading-name differences. Never merge different cities.
    from urllib.parse import urlsplit
    def website_key(value):
        if not value:
            return ""
        parsed=urlsplit(safe_url(value))
        domain=(parsed.hostname or "").lower().removeprefix("www.")
        if not domain or domain in {"vk.com","vk.ru","t.me","telegram.me","instagram.com","wa.me"}:
            return ""
        return domain+(parsed.path.rstrip("/") or "/")
    incoming_site=website_key(data.get("website",""))
    for row in rows:
        if city != norm(row["city"]):
            continue
        if name == norm(row["name"]):
            return row["id"]
        if incoming_site and incoming_site == website_key(row["website"]):
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


def import_discovered(db, records, source_label):
    """Preserve live CRM statuses; merge with existing name+city records."""
    added=updated=0
    # Bound every import transaction and keep map provenance in lead activity.
    for item in records[:600]:
        if not item.get('source_url') or not item.get('name') or not item.get('city'):
            continue
        _,new=upsert(db,item)
        added+=bool(new)
        updated+=not new
    return {'added':added,'updated':updated,'total':added+updated,'source':source_label}


def discovery_status():
    with _discovery_lock:
        state=dict(_discovery_job)
    with _lock,closing(db_connection()) as db:
        row=db.execute("SELECT * FROM discovery_runs WHERE provider='wikidata'").fetchone()
        if row:
            state['last_attempt']=row['last_attempt']
            state['last_success']=row['last_success']
            state['last_result']=row['last_result']
            state['last_added']=row['last_added']
        else:
            state.update(last_attempt='',last_success='',last_result='',last_added=0)
    state['enabled']=bool(preferences().get('company_auto_discovery',False))
    state['auto_audit']=bool(preferences().get('company_auto_audit',False))
    return state


def start_discovery(manual=False):
    """Start one official API request, at most weekly or on a limited manual retry."""
    global _discovery_job
    import datetime
    now=datetime.datetime.now(datetime.timezone.utc)
    with _discovery_lock:
        if _discovery_job['state']=='running':
            if manual: raise ValueError('Поиск компаний уже выполняется')
            return dict(_discovery_job)
        with _lock,closing(db_connection()) as db:
            previous=db.execute("SELECT last_attempt,last_success FROM discovery_runs WHERE provider='wikidata'").fetchone()
            if previous and previous['last_attempt']:
                timestamp=datetime.datetime.fromisoformat(previous['last_attempt'])
                age=(now-timestamp).total_seconds()
                if age<(3600 if manual else 7*24*3600):
                    if manual: raise ValueError('Повторный запрос к Wikidata доступен не чаще раза в час')
                    return dict(_discovery_job)
                if not manual and previous['last_success']:
                    success=datetime.datetime.fromisoformat(previous['last_success'])
                    if (now-success).total_seconds()<7*24*3600:
                        return dict(_discovery_job)
            # Record attempt before network IO so repeated app restarts do not
            # hammer public endpoints if the service is unavailable.
            db.execute("""
                INSERT INTO discovery_runs(provider,last_attempt) VALUES('wikidata',?)
                ON CONFLICT(provider) DO UPDATE SET last_attempt=excluded.last_attempt
            """,(now.isoformat(),))
            db.commit()
        _discovery_job={'state':'running','added':0,'updated':0,'total':0,
                        'message':'Wikidata: ищем компании по трём городам'}
        threading.Thread(target=_run_discovery,daemon=True,name='CompanyDiscovery').start()
        return dict(_discovery_job)


def _run_discovery():
    result=None
    try:
        found=discover_wikidata()
        with _lock,closing(db_connection()) as db:
            result=import_discovered(db,found,'Wikidata')
            db.execute("""
                UPDATE discovery_runs
                SET last_success=?,last_result=?,last_added=?,last_updated=?
                WHERE provider='wikidata'
            """,(iso_now(),'ok',result['added'],result['updated']))
            db.commit()
    except Exception as exc:
        # Never publish raw exception bodies from a third-party API.
        with _lock,closing(db_connection()) as db:
            db.execute("UPDATE discovery_runs SET last_result=? WHERE provider='wikidata'",
                       ('Ошибка запроса к источнику',))
            db.commit()
        with _discovery_lock:
            _discovery_job.update(state='failed',message='Wikidata сейчас недоступна; повторим позже')
    else:
        with _discovery_lock:
            _discovery_job.update(state='done',added=result['added'],updated=result['updated'],
                                  total=result['total'],message='Поиск завершён; источник может содержать мало компаний')


def maybe_start_auto_audit():
    """At most eight queued site audits per day; separate from manual requests."""
    from datetime import datetime, timezone, timedelta
    if not preferences().get('company_auto_audit',False):
        return
    with _lock,closing(db_connection()) as db:
        last=db.execute("SELECT last_attempt FROM discovery_runs WHERE provider='site_audit'").fetchone()
        if last and last['last_attempt']:
            since=datetime.now(timezone.utc)-datetime.fromisoformat(last['last_attempt'])
            if since.total_seconds()<86400:
                return
    try:
        started=start_company_batch(limit=8,niche='all')
    except ValueError:
        return  # A manual job is already in progress
    if not started.get('total'):
        return
    with _lock,closing(db_connection()) as db:
        db.execute("""
            INSERT INTO discovery_runs(provider,last_attempt) VALUES('site_audit',?)
            ON CONFLICT(provider) DO UPDATE SET last_attempt=excluded.last_attempt
        """,(iso_now(),))
        db.commit()


def vk_company_status():
    with _vk_company_lock:
        result=dict(_vk_company_job)
    with _lock,closing(db_connection()) as db:
        all_runs=[dict(x) for x in db.execute(
            "SELECT * FROM discovery_runs WHERE provider LIKE 'vk_company:%' ORDER BY last_attempt DESC"
        ).fetchall()]
    result['runs']=all_runs[:16]
    result['enabled']=bool(preferences().get('company_auto_vk',False))
    result['token_available']=vk_user_token_available()
    return result


def start_vk_company_search(city='Рязань', niche='furniture', manual=True):
    """Either one requested pair or a daily, opt-in nine-pair discovery sweep."""
    global _vk_company_job
    import datetime
    if city not in VK_CITIES or niche not in VK_TERMS:
        raise ValueError("Выбери подходящий город и нишу поиска VK")
    token=authorized_vk_user_token()
    if not token:
        if manual: raise ValueError('Для groups.search нужен токен пользователя VK. Подключи его в Company Hunter')
        return dict(_vk_company_job)
    pairs=[(city,niche)] if manual else [
        (c,n) for c in VK_CITIES for n in VK_TERMS]
    stamp=datetime.datetime.now(datetime.timezone.utc)
    with _vk_company_lock:
        if _vk_company_job['state']=='running':
            if manual: raise ValueError('Поиск компаний VK уже выполняется')
            return dict(_vk_company_job)
        with _lock,closing(db_connection()) as db:
            last=db.execute("SELECT last_attempt FROM discovery_runs WHERE provider='vk_company:all'").fetchone()
            if not manual and last and last['last_attempt']:
                if (stamp-datetime.datetime.fromisoformat(last['last_attempt'])).total_seconds()<86400:
                    return dict(_vk_company_job)
            eligible=[]
            for c,n in pairs:
                provider=f'vk_company:{c}:{n}'
                prev=db.execute("SELECT last_attempt FROM discovery_runs WHERE provider=?",(provider,)).fetchone()
                if prev and prev['last_attempt']:
                    interval=3600 if manual else 86400
                    if (stamp-datetime.datetime.fromisoformat(prev['last_attempt'])).total_seconds()<interval:
                        continue
                eligible.append((c,n))
            if not eligible:
                if manual: raise ValueError('Повторный поиск этой пары VK доступен через час')
                return dict(_vk_company_job)
            if not manual:
                db.execute("""
                    INSERT INTO discovery_runs(provider,last_attempt) VALUES('vk_company:all',?)
                    ON CONFLICT(provider) DO UPDATE SET last_attempt=excluded.last_attempt
                """,(stamp.isoformat(),))
            for c,n in eligible:
                key=f'vk_company:{c}:{n}'
                db.execute("""
                    INSERT INTO discovery_runs(provider,last_attempt) VALUES(?,?)
                    ON CONFLICT(provider) DO UPDATE SET last_attempt=excluded.last_attempt
                """,(key,stamp.isoformat()))
            db.commit()
        _vk_company_job={'state':'running','added':0,'updated':0,'total':len(eligible),'done':0,
                         'message':'Ищем бизнес-сообщества через VK API'}
        threading.Thread(target=_run_vk_company,args=(eligible,token),daemon=True,
                         name='CompanyVKDiscovery').start()
        return dict(_vk_company_job)


def _run_vk_company(pairs,token):
    import time
    failed=0
    for i,(city,niche) in enumerate(pairs):
        provider=f'vk_company:{city}:{niche}'
        result=None
        try:
            found=search_vk(city,niche,token)
            with _lock,closing(db_connection()) as db:
                result=import_discovered(db,found,provider)
                db.execute("""
                    UPDATE discovery_runs SET last_success=?,last_result=?,
                        last_added=?,last_updated=? WHERE provider=?
                """,(iso_now(),'ok',result['added'],result['updated'],provider))
                db.commit()
        except Exception:
            failed+=1
            with _lock,closing(db_connection()) as db:
                db.execute("UPDATE discovery_runs SET last_result=? WHERE provider=?",
                           ("Ошибка доступа VK или источника",provider))
                db.commit()
        with _vk_company_lock:
            _vk_company_job['done']+=1
            if result is not None:
                _vk_company_job['added']+=result['added']
                _vk_company_job['updated']+=result['updated']
            _vk_company_job['message']=(
                f"VK: проверено {_vk_company_job['done']} из {_vk_company_job['total']} "
                f"· новых {_vk_company_job['added']}")
        if i<len(pairs)-1:
            time.sleep(1.2)
    with _vk_company_lock:
        _vk_company_job['state']='failed' if failed==len(pairs) else 'done'
        if failed:
            _vk_company_job['message']+=f" · не удалось проверить {failed} направлений"


def company_queue(db, limit=100):
    """A review queue of existing CRM businesses. Never predicts readiness to buy."""
    size=max(1,min(200,int(limit)))
    rows=db.execute("""
        SELECT l.id,l.name,l.city,l.niche,l.website,l.phone,l.email,l.status AS lead_status,
               l.source_type,l.source_name,l.source_url, a.result_json, a.checked_at
        FROM leads l LEFT JOIN company_audits a ON a.lead_id=l.id
        WHERE l.demo=0 AND l.status NOT IN ('won','lost','skip')
        ORDER BY l.updated_at DESC LIMIT ?
    """,(size,)).fetchall()
    items=[]
    for row in rows:
        company=dict(row)
        raw=company.pop('result_json')
        sources={u for (u,) in db.execute(
            "SELECT DISTINCT source_url FROM activities WHERE lead_id=? AND source_url!='' LIMIT 15",
            (company['id'],)).fetchall()}
        if company.get('source_url'):sources.add(company['source_url'])
        company['source_count']=len(sources)
        company['audit']=json.loads(raw) if raw else {
            'status':'not_checked','score':0,'findings':[],'opportunities':[],
            'draft':'','question':'','disclaimer':'Аудит ещё не запускался'}
        if not company['website'] and company['audit']['status']=='not_checked':
            company['audit']['findings']=['Сайт не указан в базе; отсутствие сайта НЕ подтверждено']
        items.append(company)
    stage={'replied':5,'ready':4,'new':3,'research':2,'contacted':1}
    items.sort(key=lambda c: (stage.get(c['lead_status'],0),
                             c['audit']['status']=='analyzed',
                             c['audit']['score'],
                             c['source_count']),reverse=True)
    return items


def start_company_batch(limit=8, niche='all'):
    """Run at most one bounded job, never upload content or message businesses."""
    global _company_job
    if isinstance(limit,bool) or not isinstance(limit,int) or limit<1 or limit>10:
        raise ValueError('За один запуск можно проверить от 1 до 10 компаний')
    if niche!='all' and niche not in SCAN_NICHES:
        raise ValueError('Неизвестная ниша Company Hunter')
    with _company_job_lock:
        if _company_job['state']=='running':
            raise ValueError('Проверка компаний уже выполняется')
        from datetime import datetime, timezone, timedelta
        cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
        with _lock, closing(db_connection()) as db:
            where=["l.demo=0","l.website != ''","l.status NOT IN ('won','lost','skip')",
                   "(a.checked_at IS NULL OR a.checked_at < ?)"]
            params=[cutoff]
            if niche!='all':
                where.append('l.niche=?');params.append(niche)
            params.append(limit)
            selected=[dict(r) for r in db.execute(
                "SELECT l.id,l.name,l.city,l.niche,l.website "
                "FROM leads l LEFT JOIN company_audits a ON a.lead_id=l.id "
                "WHERE "+' AND '.join(where)+" ORDER BY l.updated_at DESC LIMIT ?",params
            ).fetchall()]
        _company_job={'state':'running' if selected else 'done','total':len(selected),'done':0,
                      'ok':0,'errors':0,'message':'Проверяем главные страницы сайтов' if selected
                      else 'Новых сайтов для проверки нет — прошлые аудиты актуальны 7 дней'}
        if selected:
            threading.Thread(target=_run_company_batch,args=(selected,),daemon=True,
                             name='CompanyHunterAudit').start()
        return dict(_company_job)


def _run_company_batch(selected):
    import time
    try:
        for i, lead in enumerate(selected):
            result=audit_company(lead)
            with _lock, closing(db_connection()) as db:
                db.execute("""
                    INSERT INTO company_audits(lead_id,status,score,checked_at,site_url,result_json)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(lead_id) DO UPDATE SET
                    status=excluded.status, score=excluded.score,
                    checked_at=excluded.checked_at, site_url=excluded.site_url,
                    result_json=excluded.result_json
                """,(lead['id'],result['status'],result['score'],result['checked_at'],
                      result['url'],json.dumps(result,ensure_ascii=False)))
                db.commit()
            with _company_job_lock:
                _company_job['done']+=1
                if result['status']=='analyzed':
                    _company_job['ok']+=1
                else:
                    _company_job['errors']+=1
                _company_job['message']=f"Проверено {_company_job['done']} из {_company_job['total']}"
            if i<len(selected)-1:
                time.sleep(1.3)
    except Exception:
        with _company_job_lock:
            _company_job['state']='failed'
            _company_job['message']='Не удалось завершить пакет; повтори позже'
    else:
        with _company_job_lock:
            _company_job['state']='done'
            _company_job['message']='Пакетная проверка завершена'


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
    target = analyze_target(text, analysis)
    cur = db.execute("""INSERT INTO public_posts(source_id,external_id,source_url,text,published_at,
        score,niche,suggested_name,created_at,promoted,review_status,analysis_reason,classifier_version,
        target_score,target_reason,site_evidence,target_version)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (source_id,external,url,text,iso_publication(data.get("published_at", "")),
         score,analysis["niche"],analysis["suggested_name"],iso_now(),
         int(bool(data.get("marked_as_ads"))), 'new' if is_ad else 'filtered',
         analysis['reason'],2,target['target_score'],target['target_reason'],
         target['site_evidence'],1))
    return cur.lastrowid, True


def public_post_rows(db, mode='fresh'):
    # The review queue should display the posts fetched most recently, even
    # when their publication dates are old. Other queues remain chronological.
    sort = "p.created_at DESC, p.id DESC" if mode == 'filtered' else "p.published_at DESC, p.id DESC"
    rows = db.execute(f"""SELECT p.*, COALESCE(s.name,'Источник удалён') AS source_name,
                       COALESCE(s.city,'') AS city, COALESCE(s.kind,'manual') AS source_kind
                       FROM public_posts p LEFT JOIN public_sources s ON s.id=p.source_id
                       {("WHERE p.review_status='filtered'" if mode == 'filtered' else "")}
                       ORDER BY {sort} LIMIT 2500""").fetchall()
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
    rows = db.execute('SELECT published_at,review_status,target_score,niche FROM public_posts').fetchall()
    selected = set(preferences().get('target_niches', DEFAULT_TARGET_NICHES))
    threshold = int(preferences().get('min_notification_score',60))
    out = {'fresh':0, 'hot':0, 'archive':0, 'undated':0, 'converted':0, 'filtered':0, 'target_fresh':0, 'all':len(rows)}
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
            if category in {'hot','fresh'} and row['niche'] in selected and row['target_score']>=threshold:
                out['target_fresh'] += 1
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
        reviewable = 0
        target_new_scores = []
        for post in rows:
            candidate = classify(post.get('text',''))
            category = freshness(post.get('published_at', ''))
            if not candidate['is_candidate']:
                rejected += 1
                # Keep a bounded *diagnostic* queue of all rejected posts,
                # including posts older than 24h. Otherwise "filtered: 23"
                # can show an empty list whenever a public has old content.
                # Repeat checks are deduplicated in store_public_post.
                rejected_id, _ = store_public_post(db, sid, post)
                if db.execute("SELECT review_status FROM public_posts WHERE id=?",
                              (rejected_id,)).fetchone()[0] == 'filtered':
                    reviewable += 1
                continue
            matched += 1
            if category in {'hot','fresh'}:
                recent += 1
            elif category == 'archive':
                archived += 1
            else:
                undated += 1
            # Archive older advertiser posts for duplicate/repeat-ad history.
            post_id, created = store_public_post(db, sid, post)
            if created and category in {'hot','fresh'}:
                added += 1
                new_post = db.execute("SELECT target_score,niche FROM public_posts WHERE id=?",
                                      (post_id,)).fetchone()
                prefs=preferences()
                if new_post['niche'] in prefs.get('target_niches',DEFAULT_TARGET_NICHES):
                    if new_post['target_score'] >= int(prefs.get('min_notification_score',60)):
                        target_new_scores.append(new_post['target_score'])
        # Rejected posts are a *diagnostic* queue, not permanent leads.
        # Retain 7 days since discovery regardless of publication date.
        db.execute("""DELETE FROM public_posts WHERE review_status='filtered'
                      AND created_at < ?""",
                   ((publication_time(iso_now()) - timedelta(days=7)).isoformat(),))
        # Extra cap protects local storage for users monitoring many sources.
        # Manual restores are 'new' and remain untouched by this pruning.
        db.execute("""DELETE FROM public_posts
                      WHERE review_status='filtered' AND id NOT IN (
                        SELECT id FROM public_posts WHERE review_status='filtered'
                        ORDER BY created_at DESC, id DESC LIMIT 3000
                      )""")
        db.execute("""UPDATE public_sources SET last_checked=?,last_error='',last_count=?,
                    last_matched=?,last_filtered=?,last_reviewable=?,last_added=?,
                    last_archive=?,last_undated=? WHERE id=?""",
                   (iso_now(),len(rows),matched,rejected,reviewable,added,archived,undated,sid))
        db.commit()
        if target_new_scores:
            notify_new_advertisers(len(target_new_scores), src['name'], max(target_new_scores))
        return {'read':len(rows),'matched':matched,'added':added,
                'fresh_candidates':recent,'filtered':rejected,
                'reviewable':reviewable,'archived':archived,'undated':undated}
    except Exception as exc:
        db.execute('UPDATE public_sources SET last_error=? WHERE id=?', (str(exc)[:400],sid))
        db.commit()
        raise


def auto_check_loop(stop_event):
    # Checks permissioned feeds and ONE low-frequency public company source.
    while not stop_event.wait(10):
        try:
            if preferences().get('company_auto_discovery', False):
                start_discovery(manual=False)
            maybe_start_auto_audit()
            if preferences().get('company_auto_vk',False):
                start_vk_company_search(manual=False)
        except Exception:
            pass
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
        maximum = 17_000_000 if urlparse(self.path).path == '/api/company/import-geojson' else 2500000
        if size > maximum or size < 2:
            raise ValueError("Неверный размер запроса")
        return json.loads(self.rfile.read(size))

    def do_GET(self):
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        try:
            if path == "/":
                return self.send_file(STATIC / "index.html")
            if path == "/favicon.ico":
                return self.send_file(ROOT / "assets" / "void-leads.ico")
            if path in {"/app.js", "/style.css", "/company-ui.js", "/company-ui.css"}:
                return self.send_file(STATIC / path.lstrip("/"))
            if path == "/api/config":
                return self.send_json(200, {"cities": list(CITIES), "map_categories": list(CATEGORIES),
                                            "niches": NICHES, "version": APP_VERSION, "fresh_hours": 24, "hot_hours": 2,
                                            "interval_minutes": 1, "username": "Данил",
                                            "vk_token_saved": (DATA_DIR / 'vk_token.dpapi').is_file(),
                                            "vk_token_available": token_available(),
                                            "preferences": preferences(), "target_niche_names":NICHE_NAMES,
                                            "startup_enabled": is_startup_enabled(),
                                            "tray_available": bool(_tray and _tray.icon)})
            if path == "/api/company/queue":
                with _lock, closing(db_connection()) as db:
                    result=company_queue(db)
                with _company_job_lock:
                    job=dict(_company_job)
                return self.send_json(200, {'companies':result,'job':job})
            if path == "/api/company/job":
                with _company_job_lock:
                    return self.send_json(200,dict(_company_job))
            if path == "/api/company/discovery":
                return self.send_json(200,discovery_status())
            if path == "/api/company/vk":
                return self.send_json(200,vk_company_status())
            if path == "/api/update/check":
                return self.send_json(200, latest_release())
            if path == "/api/update/progress":
                if not _update_in_progress:
                    return self.send_json(200, {'state':'idle','message':'Нет активного обновления'})
                return self.send_json(200, read_status(DATA_DIR))
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
                    now = publication_time(iso_now())
                    seven_days = now - timedelta(days=7)
                    prefs=preferences()
                    threshold=int(prefs.get('min_notification_score',60))
                    selected=set(prefs.get('target_niches',DEFAULT_TARGET_NICHES))
                    for source in sources:
                        posts = [dict(r) for r in db.execute(
                            """SELECT published_at,created_at,target_score,niche,review_status
                               FROM public_posts WHERE source_id=?""",(source['id'],)).fetchall()]
                        source['new_posts'] = sum(p['review_status']=='new' and
                            freshness(p['published_at']) in {'hot','fresh'} for p in posts)
                        weekly = [p for p in posts if p['review_status'] in {'new','converted'}
                            and publication_time(p['created_at']) and publication_time(p['created_at'])>=seven_days
                            and publication_time(p['published_at']) and publication_time(p['published_at'])>=seven_days]
                        source['ads_7d']=len(weekly)
                        source['targets_7d']=sum(p['target_score']>=threshold and
                                                 p['niche'] in selected for p in weekly)
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
        global _last_osm_request, _session_vk_token, _session_vk_user_token, _update_in_progress, _update_key
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
            if path == '/api/company/vk/sweep' and method == 'POST':
                return self.send_json(200,start_vk_company_search(manual=False))
            if path == '/api/company/vk/search' and method == 'POST':
                if not isinstance(data,dict):
                    raise ValueError('Укажи город и нишу')
                result=start_vk_company_search(city=data.get('city','Рязань'),
                                                niche=data.get('niche','furniture'),manual=True)
                return self.send_json(200,result)
            if path == '/api/company/discover' and method == 'POST':
                return self.send_json(200,start_discovery(manual=True))
            if path == '/api/company/import-geojson' and method == 'POST':
                if not isinstance(data,dict) or not isinstance(data.get('geojson'),str):
                    raise ValueError('Укажи город и текст GeoJSON')
                records=parse_osm_geojson(data['geojson'],data.get('city',''))
                with _lock,closing(db_connection()) as db:
                    result=import_discovered(db,records,'OpenStreetMap extract')
                    db.commit()
                return self.send_json(200,result)
            if path == '/api/company/audit-batch' and method == 'POST':
                if not isinstance(data,dict):
                    raise ValueError('Ожидается объект настроек')
                job=start_company_batch(data.get('limit',8),data.get('niche','all'))
                return self.send_json(200,job)
            if path == '/api/notifications/test' and method == 'POST':
                available=bool(_tray and _tray.notify("VOID LEADS · проверка",
                    "Данил, уведомления на твоём компьютере работают!"))
                return self.send_json(200, {'ok':available})
            if path == '/api/vk/user-token' and method == 'POST':
                token = str(data.get('token','')).strip()
                save_token(DATA_DIR,token,filename="vk_user_token.dpapi")
                _session_vk_user_token=token
                return self.send_json(200,{'ok':True,'saved':True})
            if path == '/api/vk/user-token' and method == 'DELETE':
                delete_token(DATA_DIR,filename="vk_user_token.dpapi")
                _session_vk_user_token=''
                return self.send_json(200,{'ok':True,'saved':False})
            if path == '/api/vk/token' and method == 'POST':
                token = str(data.get('token','')).strip()
                save_token(DATA_DIR,token)
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
                if 'target_niches' in data:
                    niches = data['target_niches']
                    if not isinstance(niches,list) or not niches or len(niches)>len(NICHE_NAMES) or any(
                        not isinstance(n,str) or n not in NICHE_NAMES for n in niches):
                        raise ValueError('Выбери хотя бы одну подходящую нишу')
                    fields['target_niches'] = list(dict.fromkeys(niches))
                if 'company_auto_vk' in data:
                    enabled=data['company_auto_vk']
                    if not isinstance(enabled,bool):
                        raise ValueError('Автопоиск VK должен быть true или false')
                    fields['company_auto_vk']=enabled
                if 'company_auto_audit' in data:
                    enabled=data['company_auto_audit']
                    if not isinstance(enabled,bool):
                        raise ValueError('Автоаудит должен быть true или false')
                    fields['company_auto_audit']=enabled
                if 'company_auto_discovery' in data:
                    enabled=data['company_auto_discovery']
                    if not isinstance(enabled,bool):
                        raise ValueError('Автопоиск должен быть true или false')
                    fields['company_auto_discovery']=enabled
                if 'startup' in data:
                    if not isinstance(data['startup'], bool):
                        raise ValueError('startup должен быть true или false')
                    set_startup(data['startup'])
                return self.send_json(200, {'ok':True, 'preferences':save_preferences(**fields),
                                            'startup_enabled':is_startup_enabled()})
            if path == '/api/update/shutdown' and method == 'POST':
                if (not _update_in_progress or not _update_key or
                    not hmac.compare_digest(self.headers.get('X-Update-Key',''),_update_key)):
                    return self.send_json(403, {'error':'Нет разрешения на завершение'})
                threading.Timer(1.3, self.server.shutdown).start()
                return self.send_json(200, {'ok':True,'message':'Старая версия завершается'})
            if path == '/api/update/install' and method == 'POST':
                if os.name != 'nt' or not getattr(sys,'frozen',False):
                    raise ValueError('Обновления доступны из установленной Windows-версии')
                if not RELEASE_REPO:
                    raise ValueError('Канал обновлений не настроен')
                if _update_in_progress:
                    previous = read_status(DATA_DIR)
                    if previous['state'] not in {'failed','idle','done'}:
                        raise ValueError('Обновление уже выполняется')
                    _update_in_progress = False
                if not latest_release().get('available'):
                    raise ValueError('Установлена актуальная версия')
                updater = Path(sys.executable).with_name('VOID-Updater.exe')
                if not updater.is_file():
                    raise ValueError('Не найден VOID-Updater.exe. Переустанови программу.')
                temp_dir = Path(tempfile.mkdtemp(prefix='void-leads-update-'))
                temp_updater = temp_dir / 'VOID-Updater.exe'
                shutil.copy2(updater,temp_updater)
                key=secrets.token_urlsafe(32)
                _update_key=key
                write_status(DATA_DIR,'starting','Подготавливаем загрузку обновления')
                try:
                    subprocess.Popen([str(temp_updater),'--pid',str(os.getpid()),
                                      '--app',str(Path(sys.executable).resolve()),
                                      '--port',str(self.server.server_address[1]),
                                      '--shutdown-key',key],
                                     cwd=str(temp_dir),close_fds=True,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    _update_in_progress=True
                except OSError as exc:
                    _update_key=''
                    write_status(DATA_DIR,'failed',str(exc))
                    raise
                return self.send_json(200,{'ok':True,'message':'Скачиваем и проверяем новую версию. Программа останется открытой до начала установки.'})
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
    """Open managed, isolated browser window (does not affect other Edge tabs)."""
    if platform.system() == "Windows":
        roots=[os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('PROGRAMFILES',''),
               os.environ.get('LOCALAPPDATA','')]
        for root in roots:
            if not root:
                continue
            for binary in [Path(root)/'Microsoft'/'Edge'/'Application'/'msedge.exe',
                           Path(root)/'Google'/'Chrome'/'Application'/'chrome.exe']:
                if binary.is_file():
                    try:
                        profile=DATA_DIR/'desktop-browser'
                        profile.mkdir(parents=True,exist_ok=True)
                        proc=subprocess.Popen(
                            [str(binary),f'--app={url}',f'--user-data-dir={profile}',
                             '--no-first-run','--window-size=1440,900'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        _browser_processes.append(proc)
                        return
                    except OSError:
                        pass
    webbrowser.open(url)


def close_managed_browser_windows():
    """Only stop browser processes started by VOID LEADS with its own profile."""
    for proc in list(_browser_processes):
        try:
            if proc.poll() is None:
                proc.terminate()
        except (OSError,PermissionError):
            pass
    _browser_processes.clear()


def serve(port=8765, open_browser=True):
    global _tray, _server, _single_mutex
    # A background copy may already exist in the tray; avoid a second server
    # and concurrent VK API checks when the desktop shortcut is clicked.
    if os.name == 'nt' and getattr(sys,'frozen',False):
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32",use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None,False,"Local\\VOID-LEADS-_SINGLE-APP")
        if not handle:
            raise OSError(ctypes.get_last_error(),"Не удалось запустить VOID LEADS")
        if ctypes.get_last_error()==183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            if open_browser:
                launch_ui(f"http://127.0.0.1:{port}/")
            return
        _single_mutex = handle
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
        close_managed_browser_windows()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VOID LEADS local Windows CRM")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.port, not args.no_browser)
