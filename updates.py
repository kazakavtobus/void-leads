"""GitHub Releases updater discovery; no arbitrary URL/install supplied by UI."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from release_config import APP_VERSION, RELEASE_REPO

REPO_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$')
VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?$')
SETUP_NAME = 'VOID-Leads-Setup.exe'
SHA_NAME = 'SHA256SUMS.txt'


def version_tuple(value: str):
    m = VERSION_RE.fullmatch(value.strip())
    if not m:
        raise ValueError('Неподдерживаемый номер версии')
    return tuple(map(int, m.groups()))


def validate_release_url(value: str, repo: str, filename: str) -> str:
    parsed = urlparse(value)
    expected = f'/{repo}/releases/download/'
    if parsed.scheme != 'https' or parsed.netloc != 'github.com':
        raise ValueError('Неожиданный адрес скачивания обновления')
    if not parsed.path.startswith(expected) or not parsed.path.endswith('/' + filename):
        raise ValueError('Файл обновления не принадлежит настроенному репозиторию')
    if parsed.query or parsed.fragment:
        raise ValueError('Лишние параметры в адресе обновления')
    return value


def latest_release(repo: str = RELEASE_REPO, current_version: str = APP_VERSION, opener=urlopen):
    if not repo:
        return {'configured': False, 'installed': current_version, 'available': False,
                'message': 'Обновления подключатся после первой публикации установщика в GitHub Releases.'}
    if not REPO_RE.fullmatch(repo):
        raise ValueError('Неверный репозиторий обновлений')
    req = Request(f'https://api.github.com/repos/{repo}/releases/latest', headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'VOID-Leads-Updater',
    })
    with opener(req, timeout=12) as r:
        body = r.read(256_001)
    if len(body) > 256_000:
        raise ValueError('Превышен допустимый размер ответа GitHub')
    release = json.loads(body)
    tag = str(release.get('tag_name', ''))
    fresh = version_tuple(tag)
    installed = version_tuple(current_version)
    assets = {x.get('name'): x.get('browser_download_url', '') for x in release.get('assets', [])}
    if fresh > installed:
        for name in (SETUP_NAME, SHA_NAME):
            validate_release_url(str(assets.get(name, '')), repo, name)
    return {'configured': True, 'installed': current_version,
            'latest': tag.lstrip('v'), 'available': fresh > installed,
            'notes': str(release.get('body') or '')[:1000] if fresh > installed else '',
            'message': 'Доступно обновление' if fresh > installed else 'Установлена актуальная версия'}
