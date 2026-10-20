"""UTC-aware publication freshness. Unknown dates are never called fresh."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HOT_HOURS = 2
FRESH_HOURS = 24
DEFAULT_CHECK_MINUTES = 20


def publication_time(raw):
    if raw is None or str(raw).strip() == '':
        return None
    value = str(raw).strip()
    try:
        if value.isdigit() and len(value) in (10, 13):
            stamp = int(value) / (1000 if len(value) == 13 else 1)
            candidate = datetime.fromtimestamp(stamp, tz=timezone.utc)
        else:
            try:
                candidate = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                candidate = parsedate_to_datetime(value)
        if candidate.tzinfo is None:
            # Undated local timestamps are ambiguous; do not pretend they are UTC.
            return None
        return candidate.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def iso_publication(raw):
    result = publication_time(raw)
    return result.isoformat(timespec='seconds') if result else ''


def freshness(raw, now=None):
    parsed = publication_time(raw)
    if parsed is None:
        return 'undated'
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now should contain timezone')
    elapsed = now.astimezone(timezone.utc) - parsed
    if elapsed < timedelta(minutes=-10):
        return 'undated'  # suspicious future-dated entries
    if elapsed <= timedelta(hours=HOT_HOURS):
        return 'hot'
    if elapsed <= timedelta(hours=FRESH_HOURS):
        return 'fresh'
    return 'archive'
