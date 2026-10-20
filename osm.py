"""Small, user-initiated Overpass queries. No systematic bulk scraping."""
from __future__ import annotations
import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode

# Small central-city bounding boxes; deliberately NOT whole cities.
CITIES = {
    "Казань": (55.763, 49.066, 55.828, 49.183),
    "Краснодар": (45.002, 38.927, 45.066, 39.048),
    "Воронеж": (51.632, 39.148, 51.693, 39.245),
    "Тула": (54.156, 37.563, 54.219, 37.674),
    "Рязань": (54.605, 39.675, 54.664, 39.789),
    "Калуга": (54.495, 36.224, 54.554, 36.320),
    "Ярославль": (57.603, 39.824, 57.650, 39.915),
    "Владимир": (56.101, 40.338, 56.157, 40.441),
    "Тверь": (56.837, 35.845, 56.895, 35.969),
    "Смоленск": (54.757, 31.996, 54.810, 32.087),
    "Брянск": (53.218, 34.309, 53.279, 34.411),
    "Белгород": (50.571, 36.543, 50.630, 36.656),
}
CATEGORIES = {
    "furniture": [('shop', 'furniture'), ('craft', 'carpenter')],
    "construction": [('office', 'construction_company'), ('craft', 'builder'), ('craft', 'roofer')],
    "automotive": [('shop', 'car_repair'), ('shop', 'tyres')],
    "industrial": [('craft', 'metal_construction'), ('man_made', 'works')],
    "equipment": [('shop', 'rental'), ('shop', 'trade')],
}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def make_query(city: str, niche: str) -> str:
    if city not in CITIES or niche not in CATEGORIES:
        raise ValueError("Неизвестный город или категория")
    bbox = ",".join(str(v) for v in CITIES[city])
    selectors = "".join(f'nwr["{k}"="{v}"]({bbox});' for k, v in CATEGORIES[niche])
    return f"[out:json][timeout:22];({selectors});out center 80;"


def parse_elements(data: dict, city: str, niche: str) -> list[dict]:
    result = []
    seen = set()
    for el in data.get("elements", [])[:200]:
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        key = f'{el.get("type")}/{el.get("id")}'
        if key in seen:
            continue
        seen.add(key)
        website = tags.get("website") or tags.get("contact:website") or ""
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        result.append({
            "name": name, "city": city, "niche": niche, "source_type": "map",
            "source_name": "OpenStreetMap", "source_url": f'https://www.openstreetmap.org/{key}',
            "osm_id": key, "website": website, "website_status": "found" if website else "unknown",
            "phone": phone, "email": tags.get("email") or tags.get("contact:email") or "",
            "description": tags.get("description", "")[:800],
            "notes": "OSM: отсутствие website в карточке НЕ означает отсутствие сайта. Проверить вручную."
        })
    return result


def search_osm(city: str, niche: str) -> list[dict]:
    q = make_query(city, niche)
    body = urlencode({"data": q}).encode("utf-8")
    request = Request(OVERPASS_URL, data=body, headers={
        "User-Agent": "VOID-LEADS-local-MVP/0.1 (manual small-area query)",
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"
    })
    with urlopen(request, timeout=28) as resp:
        payload = resp.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("Слишком большой ответ от OSM")
    return parse_elements(json.loads(payload), city, niche)
