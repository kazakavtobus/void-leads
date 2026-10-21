"""Official homepage contact suggestions for first-person outreach, not scraping profiles."""
from pathlib import Path

from company_hunter import audit_html, business_contact_links


def test_detects_public_business_contacts_with_max_and_vk():
    html='''<html><head><title>Кухни на заказ</title></head><body><h1>Кухни</h1>
    <a href="mailto:orders@fabrika.ru">Почта</a>
    <a href="mailto:orders@fabrika.ru">Дублированная почта</a>
    <a href="tel:+7(900) 123-45-67">Позвонить</a>
    <a href="https://max.ru/@fabrika">Написать в MAX</a>
    <a href="https://t.me/fabrika_rzn">Telegram</a>
    <a href="https://vk.com/club123456">VK</a>
    <a href="https://wa.me/79001234567">WhatsApp</a></body></html>'''
    result=audit_html({"name":"Фабрика","niche":"furniture"},html,"https://fabrika.ru/")
    c=result["contact_suggestions"]
    assert c["emails"]==["orders@fabrika.ru"]
    assert c["phones"]==["+7(900) 123-45-67"]
    assert {m["kind"] for m in c["messengers"]}=={"MAX","Telegram","VK","WhatsApp"}
    assert all(m["url"].startswith("https://") for m in c["messengers"])


def test_skips_unsafe_links_and_generic_messenger_homepages():
    c=business_contact_links([
        "javascript:alert(1)",
        "http://max.ru/@someone",
        "https://max.ru",
        "https://max.ru/",
        "https://evil.max.ru/@someone",
        "mailto:bad@example.com\nBcc:other@example.com",
        "tel:javascript:42",
        "mailto:sales@example.org",
        "tel:+7 900 123-45-67",
        "https://vk.com/company",
    ])
    assert c["emails"]==["sales@example.org"]
    assert c["phones"]==["+7 900 123-45-67"]
    assert len(c["messengers"])==1 and c["messengers"][0]["kind"]=="VK"


def test_no_contacts_is_not_evidence_of_no_business_contacts():
    result=audit_html({"name":"Производство","niche":"industrial"},
        "<html><head><title>Металл</title></head><body><h1>Металлоконструкции</h1></body></html>",
        "https://factory.example/")
    assert result["contact_suggestions"]=={"emails":[],"phones":[],"messengers":[]}
    assert "не проверены JS-формы" in result["disclaimer"]


def test_ui_renders_source_links_as_suggestions():
    root=Path(__file__).resolve().parents[1]
    js=(root/"static/company-ui.js").read_text("utf-8")
    assert "Публичные контакты с главной страницы" in js
    assert "contact_suggestions" in js
    assert "проверь, что контакт действительно принадлежит" in js
    assert "mailto:" in js and "tel:" in js
    assert "elink(x.url,x.kind)" in js
