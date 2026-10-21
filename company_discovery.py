"""Limited discovery using Wikidata and local OpenStreetMap GeoJSON extracts.

Wikidata is a sparse public knowledge graph (not a comprehensive business
directory). Larger geographic searches use user-supplied regional extracts,
never a continuous public Overpass scraper. Records have traceable source URLs.
"""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from engine import safe_url

CITY_IDS = {"Рязань": "Q2746", "Тула": "Q2770", "Калуга": "Q2837"}
SUPPORTED_NICHES = {"furniture", "construction", "industrial", "equipment"}
WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
MAX_WIKIDATA_BYTES = 750_000
MAX_GEOJSON_BYTES = 16_000_000
MAX_IMPORT_ROWS = 600
MATCHERS = {
    "furniture": ("мебел", "кухн", "столяр", "furniture", "kitchen", "cabinet", "joinery"),
    "construction": ("строител", "домостро", "construction company", "house builder", "construction firm"),
    "industrial": ("металл", "завод", "производств", "фабрик", "меткон", "металло",
                   "машиностро", "manufactur", "factory", "industrial", "metal", "plant"),
    "equipment": ("спецтехник", "аренд", "строительн техник", "excavator", "equipment rental"),
}
# An authoritative location statement is required before mapping a Wikidata
# item to any of these three cities. Do not infer location from a label.
QUERY = """SELECT DISTINCT ?item ?city ?itemLabel ?itemDescription ?website WHERE {
 VALUES ?city { wd:Q2746 wd:Q2770 wd:Q2837 }
 ?item (wdt:P159|wdt:P131) ?city .
 ?item wdt:P31 ?kind .
 VALUES ?businessType { wd:Q4830453 wd:Q6881511 wd:Q783794 }
 ?kind wdt:P279* ?businessType .
 FILTER NOT EXISTS { ?item wdt:P576 ?endDate }
 OPTIONAL { ?item wdt:P856 ?website }
 SERVICE wikibase:label { bd:serviceParam wikibase:language "ru,en". }
} LIMIT 240"""


def _wikidata_response():
    query = urllib.parse.urlencode({"query": QUERY, "format": "json"})
    req = urllib.request.Request(
        WDQS_ENDPOINT + "?" + query,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "VOID-LEADS-Company-Hunter/0.8 (desktop app; https://github.com/kazakavtobus/void-leads)",
        },
    )
    with urllib.request.urlopen(req, timeout=32) as response:
        raw = response.read(MAX_WIKIDATA_BYTES + 1)
    if len(raw) > MAX_WIKIDATA_BYTES:
        raise ValueError("Ответ Wikidata больше безопасного лимита")
    return json.loads(raw)


def classify_text(*values):
    source = " ".join(str(v or "") for v in values).casefold()
    # Buildings, landmarks and estates are not construction suppliers. Wikidata
    # contains many historical objects with construction-related names.
    if any(bad in source for bad in ("усадьб","памятник архитектуры","historic building",
                                    "architectural monument","постройка усадьбы")):
        return None
    if re.search(r"\bстрой[\w-]*", source):
        return "construction"
    for niche in ("furniture", "construction", "industrial", "equipment"):
        if any(term in source for term in MATCHERS[niche]):
            return niche
    return None


def _binding(row, key):
    data = row.get(key, {})
    return data.get("value", "") if isinstance(data, dict) else ""


def parse_wikidata(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("Wikidata: некорректный ответ")
    results = payload.get("results", {}).get("bindings", [])
    if not isinstance(results, list):
        raise ValueError("Wikidata: некорректные записи")
    mapping = {"Q2746": "Рязань", "Q2770": "Тула", "Q2837": "Калуга"}
    found = []
    seen = set()
    for item in results[:240]:
        uri = _binding(item, "item")
        city_uri = _binding(item, "city")
        if not re.fullmatch(r"https?://www\.wikidata\.org/entity/Q[1-9][0-9]*", uri):
            continue
        qid = uri.rsplit("/", 1)[-1]
        city = mapping.get(city_uri.rsplit("/", 1)[-1])
        if not city or (city, qid) in seen:
            continue
        name = _binding(item, "itemLabel").strip()
        description = _binding(item, "itemDescription").strip()
        if not name or name == qid or len(name) > 200:
            continue
        niche = classify_text(name, description)
        if niche not in SUPPORTED_NICHES:
            continue
        url = safe_url(_binding(item, "website"))
        if url and urlsplit(url).hostname in {"wikidata.org", "www.wikidata.org"}:
            url = ""
        seen.add((city, qid))
        found.append({
            "name": name, "city": city, "niche": niche, "source_type": "map",
            "source_name": "Wikidata", "source_url": "https://www.wikidata.org/wiki/" + qid,
            "website": url, "website_status": "found" if url else "unknown",
            "description": description[:800],
            "notes": "Wikidata: категория предварительная; адрес сайта и деятельность нужно проверить вручную.",
        })
    return found


def discover_wikidata(fetcher=_wikidata_response) -> list[dict]:
    return parse_wikidata(fetcher())


def _osm_niche(tags):
    pairs = {(str(k).casefold(), str(v).casefold()) for k,v in tags.items()}
    if ("shop", "furniture") in pairs or ("craft", "carpenter") in pairs:
        return "furniture"
    if ("office", "construction_company") in pairs or ("craft", "builder") in pairs or ("craft", "roofer") in pairs:
        return "construction"
    if ("craft", "metal_construction") in pairs or ("man_made", "works") in pairs:
        return "industrial"
    if ("shop", "rental") in pairs or ("shop", "trade") in pairs:
        # Generic rental and trade shops are not proven to sell heavy equipment.
        text=(str(tags.get("name",""))+" "+str(tags.get("description",""))).casefold()
        relevant=("спецтехник","строительная техника","техника для строител",
                  "экскаватор","погрузчик","автокран","трактор","heavy equipment",
                  "machinery rental","equipment rental")
        return "equipment" if any(term in text for term in relevant) else None
    return None


def _property(tags, *names):
    for name in names:
        value=tags.get(name)
        if isinstance(value,str) and value.strip():
            return value.strip()
    return ""


def _parse_osm_id(feature):
    raw = str(feature.get("id") or feature.get("properties",{}).get("@id") or "")
    if re.fullmatch(r"(node|way|relation)/[0-9]+", raw):
        return raw
    return ""


def parse_osm_geojson(raw: str | bytes, city: str) -> list[dict]:
    """Parse a locally obtained extract; no network calls made here."""
    if city not in CITY_IDS:
        raise ValueError("Доступны только Рязань, Тула и Калуга")
    if isinstance(raw,bytes):
        if len(raw)>MAX_GEOJSON_BYTES: raise ValueError("GeoJSON превышает 16 МБ")
        raw=raw.decode("utf-8-sig")
    if not isinstance(raw,str) or len(raw.encode("utf-8"))>MAX_GEOJSON_BYTES:
        raise ValueError("GeoJSON превышает 16 МБ")
    payload=json.loads(raw)
    if not isinstance(payload,dict) or payload.get("type")!="FeatureCollection" or not isinstance(payload.get("features"),list):
        raise ValueError("Нужен GeoJSON FeatureCollection с объектами OSM")
    found=[]
    seen=set()
    for item in payload["features"]:
        if not isinstance(item,dict) or item.get("type")!="Feature":
            continue
        props=item.get("properties")
        if not isinstance(props,dict):
            continue
        tags=props.get("tags",props)
        if not isinstance(tags,dict):
            continue
        name=_property(tags, "name", "name:ru")
        niche=_osm_niche(tags)
        if not name or not niche or len(name)>200:
            continue
        osm_id=_parse_osm_id(item)
        source=("https://www.openstreetmap.org/"+osm_id) if osm_id else ""
        # No source object identifier means the extracted business cannot be
        # verified. Exclude rather than manufacture a citation.
        if not source or osm_id in seen:
            continue
        seen.add(osm_id)
        website=safe_url(_property(tags,"website","contact:website"))
        phone=_property(tags,"phone","contact:phone")
        email=_property(tags,"email","contact:email")
        found.append({
            "name":name,"city":city,"niche":niche,"source_type":"map",
            "source_name":"OpenStreetMap extract","source_url":source,"osm_id":osm_id,
            "website":website,"website_status":"found" if website else "unknown",
            "phone":phone[:250],"email":email[:250],
            "description":_property(tags,"description")[:800],
            "notes":"OSM extract: категория по тэгам карты; актуальность и сайт проверить вручную.",
        })
        if len(found)>=MAX_IMPORT_ROWS:
            break
    return found
