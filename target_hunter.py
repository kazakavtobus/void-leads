"""Separate advertisement detection from commercial lead potential.

Rules are deliberately explainable. Scores are triage heuristics, NOT sales
conversion forecasts. Missing a website URL in a post is NOT evidence that a
business has no website. No personal data is enriched or scraped here.
"""
from __future__ import annotations
import re

NICHE_NAMES = {
    "construction": "Строительство и ремонт",
    "furniture": "Мебель и кухни",
    "equipment": "Аренда спецтехники",
    "industrial": "Производства и B2B",
    "automotive": "Автосервисы",
    "events": "Организаторы мероприятий",
    "education": "Онлайн-образование",
    "beauty": "Салоны красоты",
    "other": "Другая ниша",
}
DEFAULT_TARGET_NICHES = ["construction", "furniture", "equipment", "industrial", "automotive", "events", "education"]
HIGH_VALUE = {"construction": 45, "furniture": 45, "equipment": 48, "industrial": 45}
MID_VALUE = {"automotive": 26, "events": 34, "education": 28}
GROUP_PATTERN = re.compile(
    r"\b(?:компани[яиюей]|производств[оа]|фабрик\w*|цех\w*|"
    r"бригада\w*|команд[аы]|наш[иае]\s+(?:мастер\w*|специалист\w*|сотрудник\w*)|"
    r"официальн\w* дилер\w*|сеть\s+(?:магазинов|салонов)|нескольк\w*\s+мастер\w*)\b",
    re.I,
)
SOLO_PATTERN = re.compile(
    r"\b(?:я\s+(?:мастер|специалист|делаю|занимаюсь)|"
    r"пополнени[яюе]\s+портфолио|работаю\s+(?:на\s+дому|в\s+кабинете)|"
    r"начинающ\w*\s+мастер|модел(?:и|ей)\s+для\s+маникюр\w*)\b",
    re.I,
)
SPECIFIC_OFFER = re.compile(
    r"\b(?:рассч[её]т\s+стоимост\w*|калькулятор|каталог|"
    r"портфолио\s+(?:работ|проектов)|"
    r"индивидуальн\w*\s+проект\w*|замер\w*|"
    r"собственн\w*\s+производств\w*|оптов\w*|"
    r"комплектац\w*|снабжени\w*)\b",
    re.I,
)
URL = re.compile(r"https?://[^\s<>]+", re.I)
NON_SITE_DOMAINS = (
    "vk.com", "vk.ru", "t.me", "telegram.me", "wa.me", "max.ru",
    "instagram.com", "youtube.com", "youtu.be", "avito.ru",
    "2gis.ru", "yandex.ru", "maps.yandex.ru", "taplink.cc",
)

def analyze_target(text: str, ad: dict) -> dict:
    niche = ad.get("niche", "other")
    if not ad.get("is_candidate"):
        return {"target_score": 0, "target_reason": "Не подтверждено коммерческое предложение",
                "site_evidence": "not_checked", "site_note": "Наличие сайта не проверялось"}
    raw = str(text or "")
    niche_weight = HIGH_VALUE.get(niche, MID_VALUE.get(niche, 5 if niche=="beauty" else 12))
    grouped = bool(GROUP_PATTERN.search(raw))
    solo = bool(SOLO_PATTERN.search(raw))
    specific = bool(SPECIFIC_OFFER.search(raw))
    signals = ad.get("signals") or {}
    has_contact = bool(signals.get("business_contact"))
    # Do not confuse ability to identify an ad with purchasing propensity.
    value = 12 + niche_weight + (15 if grouped else 0) + (8 if has_contact else 0)
    value += 8 if specific else 0
    value -= 27 if solo else 0
    # Company site is not absent merely because no website was included.
    # Known website URL may lower *new-site* sales priority, but not rule out redesign.
    sites = [u.rstrip(".,);]") for u in URL.findall(raw)]
    from urllib.parse import urlsplit
    external = []
    for u in sites:
        try:
            host = (urlsplit(u).hostname or "").lower().removeprefix("www.")
            if host and not any(host==d or host.endswith("."+d) for d in NON_SITE_DOMAINS):
                external.append(u)
        except ValueError:
            pass
    if external:
        value -= 16
        site_evidence, site_note = ("linked", "Есть ссылка на сторонний сайт — уточни, принадлежит ли компании")
    else:
        site_evidence, site_note = ("not_checked", "Сайт не проверялся; отсутствие ссылки ничего не доказывает")
    value = min(100, max(0, value))
    reasons = [NICHE_NAMES.get(niche, "Ниша не определена")]
    if grouped:
        reasons.append("есть признаки команды или компании")
    if specific:
        reasons.append("услуга требует презентации/расчёта")
    if has_contact:
        reasons.append("в посте указан контакт")
    if solo:
        reasons.append("возможно, частный мастер")
    if external:
        reasons.append("указана внешняя веб-ссылка")
    return {
        "target_score": value,
        "target_reason": "; ".join(reasons).capitalize(),
        "site_evidence": site_evidence,
        "site_note": site_note,
    }
