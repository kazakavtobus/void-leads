"""First-party / permissioned public feeds: VK wall API, HTTPS RSS and manual forwards.

Never silently scrape pages, bypass rate limits or contact businesses.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from engine import detect_contacts, norm, safe_url
from freshness import iso_publication

SIGNALS = {
    "offer": ("заказ", "закаж", "услуг", "изготов", "производ", "ремонт", "монтаж", "аренд", "достав", "предлагаем", "продаем", "продаём", "строительств", "мебель", "кухни", "окна", "автосервис", "детейлинг", "запис", "скидк", "акци", "под ключ", "открылись"),
    "lead": ("пиши", "звон", "телефон", "личные сообщени", "в личку", "директ", "подробност", "заявк", "цены", "стоимость", "запис"),
}
NICHES = {
    "furniture": ("кухни на заказ", "мебель", "шкаф", "столяр", "гардероб"),
    "construction": ("ремонт квартир", "строительств", "отделоч", "фасад", "кровл", "натяжные потолки", "окна"),
    "equipment": ("спецтехник", "экскаватор", "аренда техник", "погрузчик", "манипулятор"),
    "industrial": ("производств", "завод", "оборудован", "металлообработ"),
    "automotive": ("автосервис", "шиномонтаж", "детейлинг", "ремонт авто"),
    "beauty": ("маникюр", "салон красоты", "парикмах", "косметолог"),
    "education": ("курсы", "обучени", "онлайн-школ"),
    "events": ("конференц", "форум", "мероприят", "билет"),
}


def classify(text: str) -> dict:
    lowered = norm(text)[:12000]
    contacts = detect_contacts(text)
    matches = [s for s in SIGNALS["offer"] if s in lowered]
    cta = [s for s in SIGNALS["lead"] if s in lowered]
    score = min(100, 12 + 12 * min(len(matches), 3) + 10 * min(len(cta), 2)
                + (17 if any(contacts.values()) else 0) + (8 if len(text) > 80 else 0))
    niche = next((n for n, words in NICHES.items() if any(w in lowered for w in words)), "other")
    first_line = next((p.strip() for p in (text or "").splitlines() if p.strip()), "")
    # This is a *suggestion* from the headline, not a verified legal/business name.
    name = re.sub(r'^[\s#✨🔥📢📍]+', '', first_line)[:92]
    # Avoid treating ordinary city news ("road repairs", "construction started") as an ad.
    # A commercial offer is stronger with a contact, call to action or sale/booking language.
    explicit_offer = any(x in lowered for x in ("закаж", "изготов", "предлагаем", "скидк", "акци", "доставк", "продаем", "продаём", "под ключ", "бесплатный замер", "спешите", "открылись"))
    candidate = bool((matches and (cta or any(contacts.values()) or explicit_offer)) or
                     (cta and any(contacts.values()) and len(matches) > 0))
    return {"ad_score": score, "is_candidate": candidate,
            "niche": niche, "suggested_name": name, **contacts}


def _validate_public_url(url: str) -> str:
    """Reject localhost, private addresses, credentials, and non-HTTPS before fetching feeds."""
    clean = safe_url(url)
    p = urlsplit(clean)
    if not clean or p.scheme != "https" or not p.hostname or p.port not in (None, 443):
        raise ValueError("Для RSS укажи публичный HTTPS-адрес без нестандартного порта")
    if p.username or p.password or p.hostname == "localhost" or p.hostname.endswith(".local"):
        raise ValueError("Локальные адреса для RSS запрещены")
    try:
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Не удалось определить адрес RSS-источника") from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("RSS-адрес должен быть публичным, не локальным")
    return clean


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("RSS с перенаправлением не поддерживается: укажи конечный HTTPS URL")


def _fetch_feed(url: str):
    url = _validate_public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "VOID-LEADS/0.2 (+local private feed reader)",
                                               "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml"})
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(req, timeout=15) as resp:
        data = resp.read(700_001)
        if len(data) > 700_000:
            raise ValueError("RSS слишком большой (лимит 700 КБ)")
        return data


def parse_rss(xml_bytes: bytes) -> list[dict]:
    if b"<!DOCTYPE" in xml_bytes.upper() or b"<!ENTITY" in xml_bytes.upper():
        raise ValueError("XML c DTD не поддерживается")
    root = ET.fromstring(xml_bytes)
    items = []
    for item in list(root.findall(".//item"))[:60]:
        title = item.findtext("title") or ""
        desc = item.findtext("description") or ""
        link = item.findtext("link") or ""
        guid = item.findtext("guid") or link
        text = re.sub(r"<[^>]+>", " ", title + "\n" + desc)
        items.append({"external_id": (guid or hashlib.sha256(text.encode()).hexdigest())[:350],
                      "url": safe_url(link), "text": text[:10000], "published_at": iso_publication(item.findtext("pubDate") or "")})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", ns)[:60]:
        title = entry.findtext("a:title", default="", namespaces=ns)
        desc = entry.findtext("a:summary", default="", namespaces=ns) or entry.findtext("a:content", default="", namespaces=ns)
        linktag = entry.find("a:link", ns)
        link = linktag.get("href", "") if linktag is not None else ""
        text = re.sub(r"<[^>]+>", " ", title + "\n" + desc)
        guid = entry.findtext("a:id", default="", namespaces=ns) or link
        items.append({"external_id": (guid or hashlib.sha256(text.encode()).hexdigest())[:350],
                      "url": safe_url(link), "text": text[:10000],
                      "published_at": iso_publication(entry.findtext("a:published", default="", namespaces=ns) or entry.findtext("a:updated", default="", namespaces=ns))})
    return items[:60]


def validate_vk_slug(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://vk.com/", "https://vk.ru/")):
        value = urlsplit(value).path.strip("/").split("/")[0]
    if not re.fullmatch(r"[a-zA-Z0-9_.]{2,80}", value):
        raise ValueError("Введи короткую ссылку паблика VK (например, podslushano_tula)")
    return value


def parse_vk_response(payload: dict) -> list[dict]:
    if "error" in payload:
        error = payload["error"]
        code = error.get('error_code')
        details = str(error.get('error_msg', 'ошибка доступа'))[:180]
        explanations = {
            5: "Ключ отклонён VK. Проверь, что вставлен полный действующий сервисный ключ приложения, без пробелов.",
            6: "VK временно ограничил частоту запросов. Подожди немного и проверь источник ещё раз.",
            27: "VK не разрешает читать стену токеном сообщества. Используй сервисный ключ приложения VK.",
            7: "Недостаточно разрешений для wall.get. Проверь тип ключа приложения и доступность сообщества.",
            15: "Доступ к запрошенной стене ограничен, проверь публичность сообщества.",
            28: "Этот тип токена не поддерживает wall.get. Попробуй сервисный ключ приложения VK.",
        }
        message = explanations.get(code, f"VK API: {details} (код {code or '?'})")
        raise ValueError(message)
    posts = payload.get("response", {}).get("items", [])
    rows = []
    for item in posts:
        if not item.get("text") or item.get("is_pinned"):
            continue
        owner, post_id = item.get("owner_id"), item.get("id")
        if owner is None or post_id is None:
            continue
        date = item.get("date")
        rows.append({"external_id": f"{owner}_{post_id}",
                     "url": f"https://vk.com/wall{owner}_{post_id}",
                     "text": item["text"][:10000],
                     "published_at": datetime.fromtimestamp(date, tz=timezone.utc).isoformat() if isinstance(date, int) else "",
                     "marked_as_ads": bool(item.get("marked_as_ads", False))})
    return rows


# A single wall.get result can contain long texts and hundreds of attachment objects.
# The previous version requested 100 posts and rejected any JSON > 500 KB.
# Read smaller pages; bound response size; shrink an oversized page rather than fail.
VK_BATCH_SIZE = 25
VK_MAX_POSTS = 100          # maximum scanned per source check; protects API quotas
VK_MAX_RESPONSE_BYTES = 3_000_000
VK_RETRY_BATCH_SIZES = (25, 10, 5, 1)
VK_PAGE_GAP_SECONDS = 0.37 # stay below the VK API per-second burst limit


class _VKPageTooLarge(Exception):
    """Internal signal: lower count and retry the same offset."""


def _request_vk_page(slug: str, token: str, count: int, offset: int) -> dict:
    args = urllib.parse.urlencode({
        "domain": slug, "count": count, "offset": offset,
        "access_token": token, "v": "5.199"
    }).encode()
    req = urllib.request.Request(
        "https://api.vk.com/method/wall.get", data=args,
        headers={"User-Agent": "VOID-LEADS/0.3.2",
                 "Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(VK_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ValueError("VK временно ограничил частоту запросов. Повтори проверку позднее.") from None
        raise ValueError(f"VK API вернул HTTP {exc.code}. Повтори проверку позднее.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError("Не удалось получить ответ VK. Проверь интернет и повтори проверку.") from exc
    if len(raw) > VK_MAX_RESPONSE_BYTES:
        raise _VKPageTooLarge()
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("VK прислал повреждённый ответ. Повтори проверку позднее.") from None
    if not isinstance(result, dict):
        raise ValueError("VK вернул неожиданный формат ответа")
    # Keep the official API error visible via our regular error mapping.
    if "error" in result:
        parse_vk_response(result)
    response = result.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise ValueError("VK вернул неожиданный формат списка публикаций")
    return result


def fetch_vk_wall(slug: str, token: str) -> list[dict]:
    """Fetch recent posts from VK official API in bounded pages (no scraping)."""
    slug = validate_vk_slug(slug)
    if not token or len(token.strip()) < 10:
        raise ValueError("Не указан сервисный ключ VK. Открой Public Hunter → «VK API — сервисный ключ», вставь ключ из настроек своего приложения VK и повтори проверку. Создать приложение: https://dev.vk.com/")
    token = token.strip()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    collected: list[dict] = []
    offset = 0
    calls = 0
    while offset < VK_MAX_POSTS:
        count = min(VK_BATCH_SIZE, VK_MAX_POSTS - offset)
        # Large payloads commonly come from embedded attachments. Reduce the
        # batch size, without moving the offset or silently skipping posts.
        while True:
            if calls:
                time.sleep(VK_PAGE_GAP_SECONDS)
            calls += 1
            try:
                payload = _request_vk_page(slug, token, count, offset)
                break
            except _VKPageTooLarge:
                smaller = next((v for v in VK_RETRY_BATCH_SIZES if v < count), None)
                if smaller is None:
                    raise ValueError("Один пост VK слишком большой даже при загрузке по одному. Проверь этот источник позже.") from None
                count = smaller

        items = payload["response"]["items"]
        if not items:
            break
        collected.extend(parse_vk_response(payload))
        offset += len(items)  # pinned/blank posts still occupy an API offset
        if len(items) < count:
            break  # reached end of wall
        # VK's wall normally returns newest first, excluding a pinned entry.
        # Once the last non-pinned dated post is older than 24h, all subsequent
        # non-pinned entries should also be older. Keep the observed old posts
        # for the archive but don't waste calls on more archival history.
        normal_dates = [v["date"] for v in items
                        if not v.get("is_pinned") and isinstance(v.get("date"), int)]
        if normal_dates and datetime.fromtimestamp(normal_dates[-1], tz=timezone.utc) < cutoff:
            break
    return collected


def fetch_source(source: dict, token: str = "") -> list[dict]:
    if source["kind"] == "vk":
        return fetch_vk_wall(source["locator"], token)
    if source["kind"] == "rss":
        return parse_rss(_fetch_feed(source["locator"]))
    raise ValueError("Ручной Telegram-источник заполняется пересылкой или вставкой текста")
