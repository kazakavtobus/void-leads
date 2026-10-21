"""Company Hunter v0.8 pilot: respectful one-page HTTPS audit with grounded signals.

Only a user-initiated, rate-limited scan of known business websites. This is
NOT browser rendering, site design assessment, contact scraping or a conversion
prediction. The network connection is pinned to a globally routable IP and the
original site's TLS name is verified, to prevent localhost/SSRF requests.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_PAGE_BYTES = 300_000
MAX_REDIRECTS = 1
SCAN_NICHES = {"furniture", "construction", "industrial", "equipment"}
NICHE_OFFERS = {
    "furniture": "Удобный выбор мебели и запрос расчёта с размерами и фото",
    "construction": "Подбор проекта и структурированный запрос сметы",
    "industrial": "Приём заявок с чертежами и понятный процесс подготовки КП",
    "equipment": "Подбор техники и заявка с датами, адресом и задачей",
}
NICHE_QUESTIONS = {
    "furniture": "Как сейчас клиенты присылают размеры и фотографии перед расчётом?",
    "construction": "Какие параметры нужно уточнять у клиента перед расчётом сметы?",
    "industrial": "Как заказчики передают чертежи и как контролируете подготовку КП?",
    "equipment": "Как узнаёте необходимые характеристики техники и сроки аренды?",
}


def eligible_url(value: str) -> tuple[str, str, int, str]:
    """Validate HTTPS website and return host/path without performing a request."""
    if not isinstance(value, str) or len(value) > 1800:
        raise ValueError("Нужен адрес сайта HTTPS")
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port or 443
    except ValueError as exc:
        raise ValueError("Неверный адрес сайта") from exc
    if parts.scheme != "https" or parts.username or parts.password or port != 443:
        raise ValueError("Для проверки поддерживается только HTTPS (порт 443)")
    if not host or len(host) > 253 or "." not in host or host.endswith((".local", ".internal", ".localhost", ".onion")):
        raise ValueError("Сайт должен быть публичным доменом")
    if any(c.isspace() for c in host) or host in {"localhost", "metadata.google.internal"}:
        raise ValueError("Недопустимый домен сайта")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Прямые IP-адреса не проверяются")
    host = host.encode("idna").decode("ascii")
    path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    if not path.startswith("/") or "\r" in path or "\n" in path or "#" in path:
        raise ValueError("Недопустимый путь")
    return host, path, port, value.strip()


def public_ip(hostname: str) -> str:
    answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    addresses = {item[4][0] for item in answers}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Адрес сайта не является публичным")
    return sorted(addresses)[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip: str):
        super().__init__(hostname, port=443, timeout=7, context=ssl.create_default_context())
        self._verified_ip = ip

    def connect(self):
        sock = socket.create_connection((self._verified_ip, 443), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_html(url: str) -> dict:
    host, path, _, cleaned = eligible_url(url)
    for redirect in range(MAX_REDIRECTS + 1):
        ip = public_ip(host)
        conn = _PinnedHTTPS(host, ip)
        try:
            conn.request("GET", path, headers={
                "Host": host, "User-Agent": "VOID-LEADS-Company-Hunter/0.8 (one-page user-initiated audit)",
                "Accept": "text/html", "Connection": "close"
            })
            response = conn.getresponse()
            status = response.status
            if status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location", "")
                if redirect >= MAX_REDIRECTS:
                    raise ValueError("Слишком много перенаправлений; проверь сайт вручную")
                from urllib.parse import urljoin
                dest = urljoin(cleaned, location)
                new_host, new_path, _, new_url = eligible_url(dest)
                if new_host != host and new_host != "www." + host and host != "www." + new_host:
                    raise ValueError("Переход на другой домен нужно проверить вручную")
                host, path, cleaned = new_host, new_path, new_url
                continue
            if status != 200:
                raise ValueError(f"Сайт ответил HTTP {status}; без выводов о его состоянии")
            media_type = response.getheader("Content-Type", "").lower()
            if not ("text/html" in media_type or "application/xhtml+xml" in media_type):
                raise ValueError("На странице нет HTML")
            raw = response.read(MAX_PAGE_BYTES + 1)
            if len(raw) > MAX_PAGE_BYTES:
                raise ValueError("Страница больше лимита безопасного анализа")
            charset = re.search(r"charset\s*=\s*['\"]?([a-z0-9_-]+)", media_type)
            encoding = charset.group(1) if charset else "utf-8"
            try:
                html = raw.decode(encoding, errors="replace")
            except LookupError:
                html = raw.decode("utf-8", errors="replace")
            return {"url": cleaned, "html": html, "status_code": status}
        finally:
            conn.close()
    raise ValueError("Не удалось открыть HTML")


class _PageInfo(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = []
        self.has_viewport = False
        self.forms = 0
        self.file_inputs = 0
        self.headings = []
        self.links = []
        self.images = 0
        self.images_missing_alt = 0
        self._capture = ""
        self._text = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._capture = "title"
        elif tag == "h1":
            self._capture = "h1"
        elif tag in {"h2", "h3"}:
            self._capture = "heading"
        elif tag == "meta" and a.get("name", "").lower() == "viewport":
            self.has_viewport = True
        elif tag == "form":
            self.forms += 1
        elif tag == "input" and a.get("type", "").lower() == "file":
            self.file_inputs += 1
        elif tag == "a":
            self.links.append((a.get("href") or "")[:250])
        elif tag == "img":
            self.images += 1
            if not (a.get("alt") or "").strip():
                self.images_missing_alt += 1
        if tag in {"script", "style", "noscript"}:
            self._capture = "skip"
    def handle_endtag(self, tag):
        if tag in {"title", "h1", "h2", "h3", "script", "style", "noscript"}:
            self._capture = ""
    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        if self._capture == "title":
            self.title += value + " "
        elif self._capture == "h1":
            self.h1.append(value)
        elif self._capture == "heading":
            self.headings.append(value)
        elif self._capture != "skip" and len(self._text) < 1600:
            self._text.append(value)


def audit_html(lead: dict, html: str, url: str = "") -> dict:
    """Grounded technical/content signals. Never claim an observed sales loss."""
    info = _PageInfo()
    info.feed(html[:MAX_PAGE_BYTES])
    words = " ".join(info._text + info.headings + info.h1).lower()
    link_text = " ".join(info.links).lower()
    target = lead.get("niche", "other")
    findings = []
    opportunities = []
    if info.title.strip():
        findings.append("Есть заголовок страницы (title)")
    else:
        findings.append("В HTML главной не найден title")
        opportunities.append("Добавить ясный заголовок страницы для поиска и вкладки браузера")
    if info.h1:
        findings.append("На главной обнаружен заголовок H1")
    else:
        findings.append("В HTML главной не обнаружен H1")
        opportunities.append("Сделать понятный заголовок о продукции или услугах")
    if info.has_viewport:
        findings.append("В HTML указан viewport для мобильных устройств")
    else:
        findings.append("В HTML не обнаружен meta viewport (нужна визуальная проверка мобильной версии)")
        opportunities.append("Проверить мобильную адаптацию и поведение контента")
    findings.append(f"В исходном HTML обнаружено форм: {info.forms}")
    if info.forms == 0:
        findings.append("Формы в статическом HTML не обнаружены; JS-формы не проверялись")
        opportunities.append("Проверить доступность и удобство запроса расчёта")
    if info.images >= 4:
        findings.append(f"В HTML главной найдено изображений: {info.images}")
    if any(w in words or w in link_text for w in ("каталог", "проект", "portfolio", "gallery", "catalog")):
        findings.append("Есть упоминания каталога, проектов или портфолио")
    else:
        opportunities.append("Проверить, насколько заметны примеры выполненных работ")
    if any(w in words for w in ("калькулятор", "рассчитать", "расчёт", "расчет", "узнать стоимость")):
        findings.append("Есть упоминания расчёта стоимости")
    else:
        opportunities.append("Проверить путь до получения предварительной стоимости")
    if info.file_inputs:
        findings.append("В HTML есть поле для загрузки файлов")
    elif target == "industrial":
        opportunities.append("Выяснить, как заказчики передают чертежи на расчёт")
    if target in NICHE_OFFERS:
        opportunities.append(NICHE_OFFERS[target])
    score = 25 + (16 if target in SCAN_NICHES else 4)
    score += (12 if not info.title.strip() else 0) + (9 if not info.h1 else 0)
    score += (14 if not info.has_viewport else 0) + (8 if info.forms == 0 else 0)
    # Score is only a review priority (site implementation hints), not business demand.
    score = min(95, score)
    question = NICHE_QUESTIONS.get(target, "Как сейчас принимаете и обрабатываете обращения с сайта?")
    name = " ".join(str(lead.get("name", "компании")).split())[:100]
    draft = (f"Здравствуйте! Изучил сайт компании «{name}» и обратил внимание "
             f"на ваше направление работы. Есть идея, как сделать удобнее "
             f"путь клиента до запроса расчёта. {question} "
             f"Если актуально, могу показать короткий пример решения.")
    return {
        "status": "analyzed", "score": score, "url": url,
        "findings": findings[:12], "opportunities": opportunities[:8],
        "draft": draft, "question": question,
        "method": "static_html_only",
        "disclaimer": "Предварительный анализ HTML главной страницы: не проверены JS-формы, мобильная визуализация, CRM и конверсия.",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
    }


def audit_company(lead: dict, fetcher=fetch_html) -> dict:
    website = str(lead.get("website") or "").strip()
    if not website:
        return {
            "status": "not_checked", "score": 0, "url": "",
            "findings": ["Сайт не указан в карточке; это не доказывает его отсутствие"],
            "opportunities": [], "draft": "", "question": "",
            "method": "none", "disclaimer": "Сайт не проверялся.",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
    try:
        response = fetcher(website)
        return audit_html(lead, response["html"], response["url"])
    except (ValueError, OSError, ssl.SSLError, UnicodeError, TimeoutError,
            http.client.HTTPException) as exc:
        return {
            "status": "unavailable", "score": 0, "url": website,
            "findings": ["Не удалось автоматически проверить HTML: " + str(exc)[:140]],
            "opportunities": [], "draft": "", "question": "",
            "method": "static_html_only", "disclaimer": "Не делаем выводов о качестве или доступности сайта для реальных посетителей.",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
