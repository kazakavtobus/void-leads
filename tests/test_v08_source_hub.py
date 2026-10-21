"""Source Hub must be truthful: no unlicensed automatic import claims."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_source_hub_labels_manual_and_gated_channels():
    js=(ROOT/"static/source-hub.js").read_text("utf-8")
    assert "Source Hub" in js and "13 каналов" not in js
    assert "kind:'manual'" in js and "kind:'gated'" in js
    assert "2ГИС" in js and "Яндекс Карты" in js
    assert "Авито" in js and "Торгово-промышленная палата" in js
    assert "Нужна лицензия/демоключ" in js
    assert "массовый сбор" in js
    assert "disabled:!v.token_available" in js

def test_source_hub_is_served_and_actions_work():
    html=(ROOT/"static/index.html").read_text("utf-8")
    server=(ROOT/"app.py").read_text("utf-8")
    ui=(ROOT/"static/company-ui.js").read_text("utf-8")
    main=(ROOT/"static/app.js").read_text("utf-8")
    assert 'src="/source-hub.js"' in html
    assert 'href="/source-hub.css"' in html
    assert '"/source-hub.js"' in server and '"/source-hub.css"' in server
    assert "sourceHub()" in ui
    assert "source-query-copy" in main
    assert (ROOT/"static/source-hub.css").is_file()

def test_external_search_links_are_encoded_and_manual():
    js=(ROOT/"static/source-hub.js").read_text("utf-8")
    assert 'encodeURIComponent(query)' in js
    assert "safeLink(c.url)" in js
    assert 'rel="noopener noreferrer"' in js
    assert "kind:'manual'" in js and "Ручной просмотр" in js
    assert 'kind:"manual"' not in js  # expectation documents exact format
