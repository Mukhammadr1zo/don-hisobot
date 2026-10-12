"""«Ҳисобот»: sums, charts and tables over a range of report dates; the Excel export of one day."""

import re
from datetime import date, timedelta
from urllib.parse import urlencode

from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from . import access, periods, summary
from .export import CONTENT_TYPE, export_daily

MAX_DAYS = 366
PRESETS = [("Ҳафта", 7), ("Ой", 30)]
# Years 2000-2099, the window of periods.from_slug: far from the edges of datetime arithmetic.
_DATE = re.compile(r"20\d\d-\d\d-\d\d")


def _date(value):
    """A `YYYY-MM-DD` query value as a date; None for anything else."""
    if not value or not _DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _last_day(now):
    """The report date of the latest ended period."""
    return periods.label(periods.last_ended(now))[0]


def _query(request, mtu, **params):
    """A query string that keeps the boshqarma's MTU filter."""
    if mtu and request.user.is_boshqarma:
        params["mtu"] = mtu.id
    return "?" + urlencode(params)


@access.staff_required
def report(request):
    stations, mtu = access.scope(request)
    now = timezone.now()
    last = _last_day(now)
    d_to = _date(request.GET.get("to")) or last
    notice = None
    if d_to > last:
        # A day that has not begun has no numbers; its stock column would just repeat today's.
        d_to = last
        notice = f"Охирги сана {last:%d.%m.%Y} дан кейин бўлолмайди: оралиқ шу кунгача кўрсатилди."
    d_from = min(_date(request.GET.get("from")) or d_to - timedelta(days=6), last)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
        notice = "Бошланиш санаси охирги санадан кейин эди: саналар алмаштирилди."
    if (d_to - d_from).days >= MAX_DAYS:
        d_from = d_to - timedelta(days=MAX_DAYS - 1)
        notice = f"Оралиқ {MAX_DAYS} кундан ошмаслиги керак: охирги {MAX_DAYS} кун кўрсатилди."

    presets = []
    for name, days in PRESETS:
        start = last - timedelta(days=days - 1)
        presets.append(
            {
                "name": name,
                "url": _query(request, mtu, **{"from": start.isoformat(), "to": last.isoformat()}),
                "current": (d_from, d_to) == (start, last),
            }
        )

    station_rows, mtu_rows = summary.summary(stations, d_from, d_to, now=now)
    series = summary.daily_series(stations, d_from, d_to, now=now)
    by = "mtu" if request.user.is_boshqarma and mtu is None else "station"
    chart = {
        "daily": series,
        "compare": summary.compare(stations, d_from, d_to, by=by),
        "names": {
            "unloaded": "Туширилган",
            "arrived": "Кириб келган",
            "remaining": "Қолган",
            "missing": "Киритилмаган",
        },
    }
    days = zip(*(series[key] for key in ("labels", "unloaded", "arrived", "remaining", "missing")), strict=True)
    total = summary.total(station_rows)
    context = {
        "mtu": mtu,
        "mtus": access.mtus_for(request.user) if request.user.is_boshqarma else [],
        "d_from": d_from,
        "d_to": d_to,
        "notice": notice,
        "presets": presets,
        "station_rows": station_rows,
        "mtu_rows": mtu_rows,
        "grouped": len(mtu_rows) > 1,
        "total": total,
        "missing": total.missing,
        "by": by,
        "chart": chart,
        "days": list(days),
        "last": last,
        "export_url": reverse("staff_export") + _query(request, mtu, d=d_to.isoformat()),
    }
    return render(request, "hisobot/staff/report.html", context)


@access.staff_required
def export(request):
    stations, _ = access.scope(request)
    now = timezone.now()
    last = _last_day(now)
    report_date = min(_date(request.GET.get("d")) or last, last)  # as the daily page: no day that has not begun
    data = export_daily(stations, report_date, user=request.user, now=now)
    response = HttpResponse(data, content_type=CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="don-{report_date:%Y-%m-%d}.xlsx"'
    return response
