import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import engine
import osm
import pytest


def test_contacts_from_local_ad():
    result = engine.detect_contacts('Закажите кухню: +7 (999) 123-45-67, https://vk.com/myshop и info@myshop.ru')
    assert result['phone'] == '+7 (999) 123-45-67'
    assert result['email'] == 'info@myshop.ru'
    assert result['social'] == 'https://vk.com/myshop'
    assert result['website'] == ''


def test_score_repeated_ad_higher():
    base = dict(source_type='ad', niche='furniture', website_status='unknown')
    s1, _ = engine.score_lead({**base, 'ad_count':1})
    s3, rs = engine.score_lead({**base, 'ad_count':3})
    assert s3 > s1
    assert s3 >= 75
    assert 'Повторная реклама' in ' '.join(rs)


def test_no_site_unknown_is_not_absent():
    _, why = engine.score_lead({'source_type':'map','niche':'industrial','website_status':'unknown'})
    assert any('не проверено' in x for x in why)


def test_safe_url_blocks_js():
    assert engine.safe_url('javascript:alert(1)') == ''
    assert engine.safe_url('https://example.com/a') == 'https://example.com/a'
    assert engine.safe_url('https://someone:secret@example.org') == ''


def test_csv_russian_semicolon():
    rows = engine.read_import_csv('Название;Город;Телефон\nТестКухни;Тула;+79999999999\n')
    assert rows == [{'name':'ТестКухни','city':'Тула','phone':'+79999999999'}]


def test_csv_rejects_large_batch():
    with pytest.raises(ValueError):
        engine.read_import_csv('name\n' + 'x\n'*2002)


def test_osm_limit_city_and_category():
    assert 'out center 80' in osm.make_query('Тула','furniture')
    with pytest.raises(ValueError):osm.make_query('Россия','furniture')


def test_osm_missing_website_is_unknown():
    values=osm.parse_elements({'elements':[{'type':'node','id':13,'tags':{'name':'Тест', 'shop':'furniture'}}]},'Тула','furniture')
    assert values[0]['website_status']=='unknown'
    assert values[0]['source_url']=='https://www.openstreetmap.org/node/13'
