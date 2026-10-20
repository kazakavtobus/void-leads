"""Domain logic for VOID LEADS: normalization, parsing and explainable scoring."""
from __future__ import annotations
import csv
import io
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

SOURCES = {"ad", "map", "partner", "request", "manual"}
STATUSES = {"new", "research", "ready", "contacted", "replied", "won", "lost", "skip"}
WEB_STATUSES = {"unknown", "not_found", "found"}
NICHES = {
    "furniture": "Мебель и кухни", "construction": "Строительство", "automotive": "Автосервис",
    "industrial": "Производство", "equipment": "Спецтехника", "education": "Образование",
    "events": "Мероприятия", "digital": "Digital / агентства", "beauty": "Красота", "other": "Другое"
}
NICHE_BONUS = {"furniture": 18, "construction": 18, "industrial": 18, "equipment": 18,
               "events": 18, "education": 14, "digital": 13, "automotive": 10,
               "beauty": 5, "other": 4}
URL_RX = re.compile(r"(?:https?://|www\.)[^\s<>()\[\],;]+", re.I)
PHONE_RX = re.compile(r"(?<!\d)(?:\+7|8)[\s(\-]*\d{3}[)\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
EMAIL_RX = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
SOCIAL_BARE_RX = re.compile(r"(?<![\w/@])(?:vk\.com|vk\.ru|t\.me|telegram\.me)/[A-Za-z0-9_./-]{2,}", re.I)


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().strip()
    return re.sub(r"\s+", " ", value)


def safe_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("www."):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return value[:1800]


def detect_contacts(text: str) -> dict:
    text = text or ""
    phone = PHONE_RX.search(text)
    email = EMAIL_RX.search(text)
    urls = [safe_url(url.rstrip(".!?")) for url in URL_RX.findall(text)]
    urls = [url for url in urls if url]
    urls += [safe_url("https://" + matched.rstrip(".,!?")) for matched in SOCIAL_BARE_RX.findall(text)]
    urls = [url for url in urls if url]
    social = next((u for u in urls if urlparse(u).netloc.lower().removeprefix("www.") in
                  {"t.me", "telegram.me", "vk.com", "vk.ru"}), "")
    website = next((u for u in urls if u != social and urlparse(u).netloc.lower().removeprefix("www.") not in
                    {"t.me", "telegram.me", "vk.com", "vk.ru", "wa.me", "youtube.com"}), "")
    return {"phone": phone.group(0) if phone else "", "email": email.group(0) if email else "",
            "social": social, "website": website}


def score_lead(lead: dict) -> tuple[int, list[str]]:
    """Transparent heuristic; not a predicted conversion percentage."""
    score = 19
    why = []
    source = lead.get("source_type", "manual")
    if source == "ad":
        score += 23
        why.append("Уже размещает рекламу")
    elif source == "request":
        score += 30
        why.append("Есть прямой запрос")
    elif source == "partner":
        score += 17
        why.append("Потенциальный партнёр")
    else:
        score += 9
    count = int(lead.get("ad_count") or 0)
    if source == "ad" and count >= 2:
        bonus = min(20, (count - 1) * 9)
        score += bonus
        why.append(f"Повторная реклама: {count} публикации")
    ws = lead.get("website_status", "unknown")
    if ws == "not_found":
        score += 12
        why.append("Сайт не обнаружен — проверить повторно")
    elif ws == "found":
        score -= 17
        why.append("Сайт уже указан")
    else:
        why.append("Наличие сайта ещё не проверено")
    niche = lead.get("niche", "other")
    score += NICHE_BONUS.get(niche, 4)
    if NICHE_BONUS.get(niche, 4) >= 14:
        why.append("Ниша с потенциально ценными заявками")
    if lead.get("phone") or lead.get("email") or lead.get("social"):
        score += 4
        why.append("Есть деловой контакт для проверки")
    if lead.get("status") == "won":
        why.append("Сделка завершена")
    return max(1, min(98, score)), why


def draft_message(lead: dict) -> str:
    niche = lead.get("niche", "other")
    ideas = {
        "furniture": "портфолио работ, каталог и удобную заявку на расчёт кухни или мебели",
        "construction": "кейсы выполненных объектов и простую форму расчёта стоимости",
        "equipment": "каталог техники с характеристиками и заявку на аренду",
        "industrial": "каталог продукции и удобную форму для оптовых запросов",
        "automotive": "страницу услуг с ценами и онлайн-записью",
        "education": "страницу курса с программой, оплатой и регистрацией",
        "events": "страницу мероприятия с программой и регистрацией",
        "digital": "разработку сложных блоков и интеграций для проектов агентства",
        "beauty": "страницу услуг с онлайн-записью",
        "other": "понятный сайт с услугами и формой заявки"
    }
    name = lead.get("name", "")
    if lead.get("source_type") == "partner":
        return (f"Здравствуйте! Увидел вашу студию «{name}». Я разрабатываю сайты на Tilda, "
                "нестандартные блоки и интеграции. Если периодически нужна техническая помощь "
                "по проектам, буду рад обсудить возможное сотрудничество. Могу показать реальные кейсы.")
    return (f"Здравствуйте! Обратил внимание на «{name}» и вашу сферу работы. "
            f"Есть идея, как можно сделать {ideas.get(niche, ideas['other'])}. "
            "Я занимаюсь разработкой сайтов и автоматизацией. Если задача актуальна, "
            "могу показать на примере, как это может выглядеть. Будет интересно обсудить?")


def read_import_csv(raw: str) -> list[dict]:
    if len(raw) > 2_000_000:
        raise ValueError("CSV слишком большой — максимум 2 МБ")
    if not raw.strip():
        return []
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("Нужна CSV-таблица с заголовками")
    aliases = {
        "название": "name", "компания": "name", "организация": "name", "name": "name",
        "город": "city", "city": "city", "ниша": "niche", "niche": "niche",
        "сайт": "website", "website": "website", "телефон": "phone", "phone": "phone",
        "почта": "email", "email": "email", "соцсеть": "social", "social": "social",
        "источник": "source_url", "ссылка": "source_url", "source_url": "source_url",
        "описание": "description", "description": "description", "заметки": "notes", "notes": "notes",
        "тип": "source_type", "source_type": "source_type", "статус сайта": "website_status",
        "website_status": "website_status", "контакт": "phone"
    }
    rows = []
    for row in reader:
        clean = {}
        for key, val in row.items():
            if key and norm(key) in aliases:
                clean[aliases[norm(key)]] = (val or "").strip()
        if clean.get("name"):
            rows.append(clean)
        if len(rows) > 2000:
            raise ValueError("Не более 2000 строк за импорт")
    return rows


def iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
