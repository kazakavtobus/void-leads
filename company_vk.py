"""VK community business discovery using permissioned VK API, not scraping.

Requires VK token explicitly provided by the user (existing Windows DPAPI
integration); no messaging, no individual user profile harvesting.
Search API results only enter CRM when the community itself provides city
evidence. Such discovery is a prospect hypothesis, not a verified company.
"""
from __future__ import annotations
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from engine import safe_url

CITIES=("Рязань","Тула","Калуга")
TERMS={
    "furniture":("кухни на заказ","мебель на заказ","мебельная фабрика","производство мебели"),
    "construction":("строительная компания","строительство домов","строим дома","ремонт квартир"),
    "industrial":("металлоконструкции","металлообработка","производство металла","производственная компания"),
}
TOKENS={
    "furniture":("кухн","мебел","шкаф","гардероб","кухон"),
    "construction":("строител","строительств","домов","строй","ремонт квартир","кровл","отделк"),
    "industrial":("металл","производств","завод","фабрик","изготовлен","станк","обработк"),
}
BAD=("новости", "барахолка", "подслушано", "куплю", "продам всё",
     "вакансии", "сборник", "сообщество жильцов", "объявления", "мемы")
MAX_RESULTS=30
API="https://api.vk.com/method/"
API_VERSION="5.199"


def _api(method, params, token):
    if method not in {"groups.search","groups.getById"}:
        raise ValueError("Неподдерживаемый метод VK")
    if not isinstance(token,str) or len(token.strip())<10:
        raise ValueError("Для поиска сообществ необходим действующий ключ VK API")
    args={**params,"access_token":token.strip(),"v":API_VERSION}
    request=urllib.request.Request(API+method, data=urllib.parse.urlencode(args).encode("utf-8"),
                                   headers={"User-Agent":"VOID-LEADS-Company-Hunter/0.8",
                                            "Accept":"application/json"})
    try:
        with urllib.request.urlopen(request,timeout=22) as response:
            raw=response.read(700_001)
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError) as exc:
        raise ValueError("VK API сейчас недоступен; повтори позже") from exc
    if len(raw)>700_000:
        raise ValueError("Слишком большой ответ VK")
    try:
        payload=json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("VK ответил некорректным JSON") from exc
    if "error" in payload:
        code=payload["error"].get("error_code","unknown")
        # Never show messages containing the user's token.
        raise ValueError(f"VK API отказал в доступе к поиску сообществ (код {code}); проверь права ключа")
    return payload.get("response",{})


def _groups(response):
    """VK API versions return either a response object with items or an array."""
    if isinstance(response,dict):
        items=response.get("items",response.get("groups",[]))
    elif isinstance(response,list):
        items=response
    else:
        return []
    return [x for x in items if isinstance(x,dict)]


def city_confirmed(group, city):
    location=group.get("city",{})
    if isinstance(location,dict):
        actual=str(location.get("title") or location.get("name") or "").casefold()
        if city.casefold() in actual:
            return True
        if actual:
            return False
    elif location:
        return False
    # Avoid trusting search query geography; group-owned description/name
    # must identify the target city, otherwise this is unverified.
    text=" ".join(str(group.get(k) or "") for k in ("name","description","status","activity")).casefold()
    forms={
        "Рязань":("рязань","рязани","рязанск"),
        "Тула":("тула","туле","тулы","тулой","тульск"),
        "Калуга":("калуга","калуге","калуги","калужск"),
    }
    return any(form in text for form in forms.get(city,()))


def parse_vk_groups(groups, city, niche):
    if city not in CITIES or niche not in TERMS:
        raise ValueError("Неверный город или ниша VK")
    results=[]
    seen=set()
    for group in groups[:100]:
        try:
            gid=int(group.get("id",0))
        except (TypeError,ValueError):
            continue
        if gid<=0 or gid in seen or group.get("is_closed")==2 or group.get("deactivated"):
            continue
        name=" ".join(str(group.get("name") or "").split())
        if not name or len(name)>200:
            continue
        description=" ".join(str(group.get("description") or "").split())
        text=(name+" "+description+" "+str(group.get("activity") or "")).casefold()
        if any(word in text[:300] for word in BAD):
            continue
        if not any(k in text for k in TOKENS[niche]) or not city_confirmed(group,city):
            continue
        # URL of business profile only; no personal contact harvesting.
        seen.add(gid)
        site=safe_url(str(group.get("site") or "").strip())
        if urllib.parse.urlsplit(site).hostname in {"vk.com","m.vk.com","t.me","wa.me"}:
            site=""
        public_url="https://vk.com/club"+str(gid)
        results.append({
            "name":name,"city":city,"niche":niche,"source_type":"directory",
            "source_name":"VK Communities API","source_url":public_url,
            "website":site,"website_status":"found" if site else "unknown",
            "social":public_url,"description":description[:700],
            "notes":"VK: открытое бизнес-сообщество. Сферу деятельности и контакт с представителем компании необходимо подтвердить.",
        })
    return results


def search_vk(city, niche, token, api=_api, sleep=time.sleep):
    if city not in CITIES or niche not in TERMS:
        raise ValueError("Выбери Рязань, Тулу или Калугу и одну из трёх ниш")
    if not token:
        raise ValueError("Не подключён ключ VK API. Добавь его в Public Hunter → VK API")
    terms=TERMS[niche][:2]
    candidates={}
    for i,term in enumerate(terms):
        if i: sleep(0.5)
        result=api("groups.search",{"q":term+" "+city,"count":MAX_RESULTS,"sort":0},token)
        for g in _groups(result):
            try:
                gid=int(g.get("id",0))
            except (TypeError,ValueError):
                continue
            if gid>0:
                candidates[gid]=g
    if not candidates:
        return []
    sleep(0.5)
    ids=",".join(str(v) for v in list(candidates)[:60])
    details=api("groups.getById",{"group_ids":ids,"fields":"description,site,city,activity,status"},token)
    enriched={}
    for group in _groups(details):
        try:
            gid=int(group.get("id",0))
        except (TypeError,ValueError):
            continue
        if gid in candidates:
            enriched[gid]={**candidates[gid],**group}
    # If the detail permission fails, caller gets a clear error instead of
    # pretending search results contain enough evidence.
    return parse_vk_groups(list(enriched.values()),city,niche)
