"""Strict, Excel-compatible CSV importer for public sources (no web scraping)."""
from __future__ import annotations
import csv
import io
from urllib.parse import urlsplit
from engine import norm, safe_url
from public_hunter import validate_vk_slug

MAX_ROWS = 500
MAX_SIZE = 900_000
FIELDS = ['name', 'city', 'kind', 'locator', 'enabled', 'interval_minutes']
ALIASES = {
    'name': 'name', 'название': 'name', 'паблик': 'name', 'источник': 'name',
    'city': 'city', 'город': 'city', 'регион': 'city',
    'kind': 'kind', 'тип': 'kind', 'платформа': 'kind',
    'locator': 'locator', 'адрес': 'locator', 'ссылка': 'locator', 'url': 'locator',
    'enabled': 'enabled', 'активен': 'enabled',
    'interval_minutes': 'interval_minutes', 'интервал': 'interval_minutes',
}
KINDS = {'vk': 'vk', 'вк': 'vk', 'вконтакте': 'vk', 'telegram': 'manual', 'tg': 'manual',
         'телеграм': 'manual', 'manual': 'manual', 'вручную': 'manual',
         'rss': 'rss', 'atom': 'rss'}


def validate_source(row):
    name = str(row.get('name') or '').strip()[:120]
    city = str(row.get('city') or '').strip()[:100]
    kind = KINDS.get(norm(str(row.get('kind') or '')))
    locator = str(row.get('locator') or '').strip()[:1800]
    if not name or not city or not kind:
        raise ValueError('Нужны название, город и тип (vk / rss / telegram)')
    if kind == 'vk':
        locator = validate_vk_slug(locator)
    elif kind == 'rss':
        u = urlsplit(locator)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None,443):
            raise ValueError('RSS: нужен HTTPS URL')
    elif locator:
        locator = safe_url(locator)
        if not locator or urlsplit(locator).hostname not in {'t.me', 'telegram.me', 'vk.com', 'vk.ru'}:
            raise ValueError('Для ручного источника укажи ссылку Telegram/VK')
    enabled_raw = norm(str(row.get('enabled', '1')))
    enabled = 0 if enabled_raw in {'0','нет','no','false','off'} else 1
    try:
        interval = int(row.get('interval_minutes') or 20)
    except (TypeError, ValueError):
        raise ValueError('Интервал должен быть числом минут')
    if interval < 15 or interval > 180:
        raise ValueError('Интервал — от 15 до 180 минут')
    return dict(name=name, city=city, kind=kind, locator=locator, enabled=enabled, interval_minutes=interval)


def read_source_csv(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_SIZE:
        raise ValueError('CSV слишком большой (лимит 900 КБ)')
    if not raw.strip():
        raise ValueError('CSV пустой')
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(raw.lstrip('\ufeff')), dialect=dialect)
    headings = [ALIASES.get(norm(h), '') for h in (reader.fieldnames or [])]
    if not {'name','city','kind'}.issubset(headings):
        raise ValueError('В CSV обязательны столбцы: Название;Город;Тип')
    records, errors = [], []
    for i, row in enumerate(reader, start=2):
        if i > MAX_ROWS + 1:
            raise ValueError('Не более 500 источников за раз')
        clean = {ALIASES[norm(k)]: (v or '') for k,v in row.items() if k and norm(k) in ALIASES}
        if not any(str(v).strip() for v in clean.values()):
            continue
        try:
            records.append(validate_source(clean))
        except (TypeError, ValueError) as e:
            errors.append(f'Строка {i}: {e}')
    return records, errors


def template_csv():
    output = io.StringIO()
    w = csv.writer(output, delimiter=';', lineterminator='\r\n')
    w.writerow(['Название', 'Город', 'Тип', 'Адрес', 'Активен', 'Интервал'])
    w.writerow(['Подслушано в Туле','Тула','vk','https://vk.com/overhear.tula','1','20'])
    w.writerow(['Подслушано в Туле · Telegram','Тула','telegram','https://t.me/pdstula','1','20'])
    return '\ufeff' + output.getvalue()
