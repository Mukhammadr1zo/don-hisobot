"""All time logic. A period is identified by its end: 08:00 or 20:00 Tashkent time.

Every datetime passed in must be aware; the process time zone is never consulted.
"""

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")
HALF = timedelta(hours=12)
STATION_WINDOW = timedelta(hours=24)

# ponytail: slugs cover years 2000-2099 only, which keeps the +-24h arithmetic
# on a hand-typed URL far from datetime.min/max. Widen the pattern if that ever matters.
_SLUG = re.compile(r"20[0-9]{6}(08|20)")


def local(dt):
    if dt.utcoffset() is None:
        raise ValueError("naive datetime")
    return dt.astimezone(TZ)


def is_period_end(dt):
    return local(dt).time() in (time(8), time(20))


def last_ended(now):
    now = local(now)
    night_end = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now >= night_end + HALF:
        return night_end + HALF
    if now >= night_end:
        return night_end
    return night_end - HALF


def prev_end(pe):
    return pe - HALF


def next_end(pe):
    return pe + HALF


def bounds(pe):
    return pe - HALF, pe


def _end(pe):
    pe = local(pe)
    if pe.time() not in (time(8), time(20)):
        raise ValueError(f"not a period end: {pe}")
    return pe


def label(pe):
    pe = _end(pe)
    return pe.date(), 1 if pe.hour == 8 else 2


def ends_for(report_date):
    return (
        datetime.combine(report_date, time(8), TZ),
        datetime.combine(report_date, time(20), TZ),
    )


def open_for_station(now):
    pe = last_ended(now)
    return [pe, prev_end(pe)]


def station_can_write(pe, now):
    return pe <= now < pe + STATION_WINDOW


def staff_can_write(pe, now):
    return pe <= now


def to_slug(pe):
    pe = _end(pe)
    return f"{pe.year:04}{pe.month:02}{pe.day:02}{pe.hour:02}"


def from_slug(s):
    if not isinstance(s, str) or not _SLUG.fullmatch(s):
        raise ValueError(f"not a period slug: {s!r}")
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:]), tzinfo=TZ)
