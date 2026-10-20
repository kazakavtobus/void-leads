"""Populate a fixed version and official GitHub repo before Windows build."""
from pathlib import Path
import argparse, re

p = argparse.ArgumentParser()
p.add_argument('--repo', required=True)
p.add_argument('--tag', required=True)
args = p.parse_args()
assert re.fullmatch(r'[A-Za-z0-9][\w.-]{0,99}/[A-Za-z0-9][\w.-]{0,99}', args.repo)
assert re.fullmatch(r'v\d+\.\d+\.\d+', args.tag)
Path(__file__).resolve().parents[1].joinpath('release_config.py').write_text(
    f'APP_VERSION = {args.tag[1:]!r}\nRELEASE_REPO = {args.repo!r}\n', encoding='utf-8')
print('Building', args.tag, 'with updater channel', args.repo)
