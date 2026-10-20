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


# Conservative rules: an incident mentioning construction is not a construction ad.
# A news post with a phone number is also not automatically an advertiser.
NEWS_TERMS = re.compile(
    r"\b(?:погиб\w*|скончал\w*|пострадал\w*|трагеди\w*|происшеств\w*|"
    r"дтп|авари\w*|заболеваемост\w*|covid(?:-19)?|коронавирус\w*|"
    r"орви|роспотребнадзор\w*|вакцинаци\w*|прививк\w*|"
    r"полици\w*|уголовн\w*|расследован\w*|в больниц\w*|"
    r"пожар\w*|спасател\w*|погибш\w*|сообща(?:ет|ют|ется)|"
    r"жёстк\w* новост\w*|жестк\w* новост\w*)\b", re.I)
PUBLIC_NEWS = re.compile(
    r"\b(?:по данным|в результате|на месте происшествия|в регионе|"
    r"за неделю выросл\w*|по пути в больницу|на прошлой неделе|"
    r"об этом сообщили|напомним, что|согласно статистике)\b", re.I)
BUSINESS_PITCH = re.compile(
    r"(?:\b(?:закаж(?:ите|и|ем|у|ут)|заказ(?:ать|ы|ов)?\s+(?:на|принимаем)|"
    r"принимаем\s+заказы|изготавливаем|изготовим|"
    r"предлагаем|выполняем|оказываем\s+услуг|"
    r"запиш(?:итесь|ись)|запись\s+(?:на|открыта)|"
    r"открыта\s+запись|звоните|звони|пишите\s+(?:нам|в\s+личк)|"
    r"записывайтесь|оставьте\s+заявк\w*|рассчитаем\s+стоимост\w*|"
    r"получите\s+скидк\w*|скидк\w*|акци[яиюе]|"
    r"бесплатн\w*\s+замер|"
    r"под\s+ключ|"
    r"сда[её]м\s+в\s+аренд\w*|"
    r"доставим|доставка\s+по\s+город\w*|"
    r"прода[её]м|продажа|"
    r"открыл(?:ись|ся|ась)\s+(?:магазин|салон|студия|пекарня))\b|"
    r"кухн\w*\s+на\s+заказ|мебел\w*\s+на\s+заказ|"
    r"ремонт\s+квартир\s+под\s+ключ|"
    r"аренд\w*\s+(?:спецтехник\w*|экскаватор\w*|автокран\w*))", re.I)
CALL_TO_ACTION = re.compile(
    r"\b(?:звон(?:ите|и)|пиш(?:ите|и)|запис(?:аться|ывайтесь|ь)|"
    r"остав(?:ьте|ляйте)\s+заявк\w*|"
    r"обращайтесь|успейте|приходите|"
    r"закаж(?:ите|и)|в\s+личк\w*|"
    r"по\s+телефон\w*|"
    r"цен[аыу]\s*(?:от|:)|стоимост\w*\s*(?:от|:))\b", re.I)
PRIVATE_SALE = re.compile(r"^\s*(?:продам|отдам|куплю)\s+(?:б/у|бу\b|свой|свою|личн\w*)", re.I)


def classify(text: str) -> dict:
    """Return a high-precision *advertiser* candidate, never merely keyword similarity.

    false negatives can be corrected using the 'Отобрано фильтром' review queue.
    All existing client records and manual overrides remain untouched.
    """
    plain = str(text or "")[:12000]
    lowered = norm(plain)
    contacts = detect_contacts(plain)
    matches = [s for s in SIGNALS["offer"] if s in lowered]
    cta_matches = [s for s in SIGNALS["lead"] if s in lowered]
    pitch = bool(BUSINESS_PITCH.search(lowered))
    action = bool(CALL_TO_ACTION.search(lowered))
    news = bool(NEWS_TERMS.search(lowered) or PUBLIC_NEWS.search(lowered))
    private = bool(PRIVATE_SALE.search(lowered))
    industry = next((n for n, words in NICHES.items()
                     if any(w in lowered for w in words)), "other")
    has_contact = bool(contacts.get("phone") or contacts.get("email")
                       or contacts.get("social") or contacts.get("website"))
    # A standalone "call us" in a news group is insufficient. Strong commercial
    # pitch must exist, together with evidence of business/transaction intent.
    business_context = bool(matches or industry != "other"
                            or re.search(r"\b(?:магазин\w*|студи\w*|салон\w*|"
                                         r"мастерск\w*|компани\w*|бренд\w*|"
                                         r"прайс\w*|товар\w*|услуг\w*)\b", lowered))
    candidate = bool(pitch and business_context and
                     (action or has_contact or industry != "other") and not private)
    # An accident / epidemiological report stays news even when accompanied by
    # a publisher's social link or VK marked_as_ads flag.
    if news and not (pitch and action and business_context):
        candidate = False
    if candidate:
        reason = "Есть коммерческое предложение"
        if has_contact:
            reason += " и контакт бизнеса"
        elif action:
            reason += " и призыв обратиться"
    elif news:
        reason = "Новость или происшествие, а не предложение услуг"
    elif private:
        reason = "Разовое частное объявление"
    else:
        reason = "Нет подтверждённого коммерческого предложения"
    score = (24 + 26 * int(pitch) + 12 * int(action)
             + 14 * int(has_contact) + 13 * int(industry != "other")
             + min(10, 3 * len(matches)))
    if not candidate:
        score = min(25, score // 4)
    first_line = next((p.strip() for p in plain.splitlines() if p.strip()), "")
    name = re.sub(r'^[\s#✨🔥📢📍]+', '', first_line)[:92]
    return {"ad_score": min(score, 100), "is_candidate": candidate,
            "niche": industry, "suggested_name": name, "reason": reason,
            "news": news, "signals": {
                "commercial_offer": pitch, "call_to_action": action,
                "business_contact": has_contact, "news_context": news}, **contacts}


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
