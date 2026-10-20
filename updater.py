"""External Windows updater; packaged independently as VOID-Updater.exe.

It downloads a release installer from the *embedded* trusted GitHub repository,
verifies SHA-256, waits for the previous app process to end, takes a database
backup, runs Inno Setup in silent mode, and relaunches the installed program.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from release_config import RELEASE_REPO
from updates import latest_release, validate_release_url, SETUP_NAME, SHA_NAME, REPO_RE

DATA_DIR = Path.home() / 'VOID-LEADS'
MAX_INSTALLER_BYTES = 250 * 1024 * 1024


def allowed_download_url(url: str, repo: str, filename: str):
    # github.com initially, then GitHub may redirect to its asset CDN.
    return validate_release_url(url, repo, filename)


def download_file(url: str, dest: Path, max_bytes: int = MAX_INSTALLER_BYTES):
    req = Request(url, headers={'User-Agent': 'VOID-Leads-Updater'})
    with urlopen(req, timeout=35) as resp, dest.open('wb') as output:
        end = urlparse(resp.geturl())
        if end.scheme != 'https' or end.hostname not in {
            'github.com', 'release-assets.githubusercontent.com',
            'objects.githubusercontent.com', 'github-releases.githubusercontent.com',
        }:
            raise ValueError('Неожиданный адрес перенаправления скачивания')
        total = 0
        sha = hashlib.sha256()
        while True:
            block = resp.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise ValueError('Файл обновления слишком большой')
            sha.update(block)
            output.write(block)
    return sha.hexdigest()


def expected_hash(contents: str, filename: str = SETUP_NAME):
    for line in contents.splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?' + re.escape(filename), line.strip())
        if match:
            return match.group(1).lower()
    raise ValueError('В выпуске отсутствует контрольная сумма установщика')


def wait_for_windows_pid(pid: int, timeout_ms: int = 180_000):
    if os.name != 'nt':
        return
    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return  # parent already exited
    try:
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result == 0x102:
            raise TimeoutError('Предыдущая версия VOID LEADS не закрылась')
    finally:
        kernel32.CloseHandle(handle)


def backup_database():
    source = DATA_DIR / 'leads.sqlite3'
    if not source.exists():
        return None
    backup_dir = DATA_DIR / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / ('before_update_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '.sqlite3')
    with sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    for path in sorted(backup_dir.glob('before_update_*.sqlite3'), reverse=True)[5:]:
        path.unlink(missing_ok=True)
    return dest


def log(message: str):
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    with (DATA_DIR / 'updater.log').open('a', encoding='utf-8') as f:
        f.write(datetime.now(timezone.utc).isoformat() + ' ' + message + '\n')


def show_error(message: str):
    log('ERROR: ' + message)
    if os.name == 'nt':
        ctypes.windll.user32.MessageBoxW(None, message, 'VOID LEADS — ошибка обновления', 0x10)
    else:
        print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--app', required=True)
    args = parser.parse_args()
    if os.name != 'nt' or not REPO_RE.fullmatch(RELEASE_REPO):
        raise RuntimeError('Обновление доступно только в установленной Windows-версии с настроенным каналом')
    target = Path(args.app).resolve()
    if target.name.lower() != 'void-leads.exe':
        raise ValueError('Некорректный путь приложения')
    release = latest_release()
    if not release.get('available'):
        log('Нет новых выпусков')
        return
    version = release['latest']
    root = f'https://github.com/{RELEASE_REPO}/releases/download/v{version}/'
    setup_url = allowed_download_url(root + SETUP_NAME, RELEASE_REPO, SETUP_NAME)
    sha_url = allowed_download_url(root + SHA_NAME, RELEASE_REPO, SHA_NAME)
    with tempfile.TemporaryDirectory(prefix='void-leads-download-') as scratch:
        temp_dir = Path(scratch)
        checksum_file = temp_dir / SHA_NAME
        download_file(sha_url, checksum_file, 100_000)
        expected = expected_hash(checksum_file.read_text(encoding='ascii'))
        installer = temp_dir / SETUP_NAME
        actual = download_file(setup_url, installer)
        if actual.lower() != expected:
            raise ValueError('Контрольная сумма установщика не совпала. Обновление отменено.')
        log(f'Загружена версия {version}; SHA256 подтверждён')
        wait_for_windows_pid(args.pid)
        backup = backup_database()
        log('Резервная копия базы: ' + str(backup))
        process = subprocess.run([str(installer), '/SILENT', '/SUPPRESSMSGBOXES',
                                  '/NORESTART', '/CLOSEAPPLICATIONS'], timeout=360)
        if process.returncode != 0:
            raise RuntimeError(f'Установщик завершился с кодом {process.returncode}')
        log('Установлена версия ' + version)
    if target.is_file():
        subprocess.Popen([str(target)], cwd=str(target.parent), close_fds=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        show_error(str(exc))
        sys.exit(1)
