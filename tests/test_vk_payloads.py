"""Regression tests for VK wall size failures (no real network or credentials)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import public_hunter as vk


class FakeHTTPResponse:
    def __init__(self, raw):
        self.raw = raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.raw[:limit]


def post(index, hours=0.5, pinned=False):
    value = {
        "owner_id": -123, "id": 10000-index, "text": "Кухни на заказ: +79990000000",
        "date": int((datetime.now(timezone.utc)-timedelta(hours=hours)).timestamp())
    }
    if pinned:
        value['is_pinned'] = 1
    return value


def test_vk_accepts_payload_larger_than_old_500kb(monkeypatch):
    huge = json.dumps({"response": {"items": [post(1)]}}).encode() + b" " * 650_000
    assert len(huge) > 500_000
    requested = []
    def fake_open(req, timeout):
        requested.append(parse_qs(req.data.decode()))
        return FakeHTTPResponse(huge)
    monkeypatch.setattr(vk.urllib.request, 'urlopen', fake_open)
    found = vk.fetch_vk_wall('my_city', 'valid-service-token')
    assert len(found)==1
    assert requested[0]['count']==['25']


def test_vk_downsizes_big_page_without_skipping_and_stops_on_old(monkeypatch):
    calls = []
    def fake_request(slug, token, count, offset):
        calls.append((count,offset))
        if count==25 and offset==0:
            raise vk._VKPageTooLarge()
        # Keep the first 10 records, then stop after dated old posts in page 2.
        items = [post(i, hours=0.5 if i < 12 else 30) for i in range(offset,offset+count)]
        return {"response": {"items": items}}
    monkeypatch.setattr(vk,'_request_vk_page',fake_request)
    monkeypatch.setattr(vk.time,'sleep',lambda seconds: None)
    rows = vk.fetch_vk_wall('my_city','valid-service-token')
    assert calls == [(25,0),(10,0),(25,10)]
    assert len(rows)==35
    assert len({r['external_id'] for r in rows})==35


def test_vk_safely_caps_new_posts_at_100_and_handles_pinned(monkeypatch):
    calls=[]
    def fake_request(slug,token,count,offset):
        calls.append((count,offset))
        items=[post(i) for i in range(offset,offset+count)]
        if offset==0:
            items[0]=post(0,hours=100,pinned=True)
        return {'response':{'items':items}}
    monkeypatch.setattr(vk,'_request_vk_page',fake_request)
    monkeypatch.setattr(vk.time,'sleep',lambda seconds: None)
    rows=vk.fetch_vk_wall('my_city','valid-service-token')
    assert len(rows)==99
    assert calls==[(25,0),(25,25),(25,50),(25,75)]


def test_vk_oversized_even_single_post_returns_understandable_error(monkeypatch):
    monkeypatch.setattr(vk,'_request_vk_page',lambda *args: (_ for _ in ()).throw(vk._VKPageTooLarge()))
    monkeypatch.setattr(vk.time,'sleep',lambda seconds: None)
    with pytest.raises(ValueError,match='Один пост VK слишком большой'):
        vk.fetch_vk_wall('my_city','valid-service-token')


def test_vk_rate_limit_is_distinct_from_response_size():
    with pytest.raises(ValueError,match='частоту запросов'):
        vk.parse_vk_response({'error': {'error_code': 6, 'error_msg': 'Too many requests per second'}})
