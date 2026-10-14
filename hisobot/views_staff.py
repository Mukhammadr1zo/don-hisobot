"""Staff pages: the current period, the daily table and the form for one station's period (spec 9)."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import access, balance, periods, reports, summary
from .forms import EntryForm
from .models import Change

ATTENTION_DAYS = 30
ATTENTION_SHOWN = 4  # rows of «Эътибор талаб» before «Яна N та»
TREND_DAYS = 14  # «Охирги 14 сутка»
STOCK_SHOWN = 5  # stations named in «Қолган» of one МТУ before «бошқа N станция»
_DATE = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}")  # the years periods.from_slug accepts

# Change.old / Change.new keys, as the history names them.
FIELDS = {
    "unloaded": "Туширилган",
    "arrived": "Кириб келган",
    "remaining_calc": "Қолган (ҳисобланган)",
    "remaining_override": "Қолган (қўлда)",
}


def _query(request, mtu, **params):
    """A query string for links back to the same page: keeps the MTU a boshqarma user picked."""
    if mtu is not None and request.user.is_boshqarma:
        params["mtu"] = mtu.pk
    return "?" + urlencode(params)


def _period(slug, latest):
    """?pe=: an ended period. A bad slug or a period still running gives the latest ended one."""
    try:
        pe = periods.from_slug(slug)
    except ValueError:
        return latest
    return min(pe, latest)


def _report_date(value, latest):
    """?d=: a report date up to the latest one. Anything else gives the latest."""
    if not _DATE.fullmatch(value or ""):
        return latest
    try:
        return min(date.fromisoformat(value), latest)
    except ValueError:
        return latest


@dataclass
class MtuNow:
    """One МТУ on «Ҳозир» (or all of them, mtu None): who submitted and the period's sums.

    A sum is None when nothing behind it is known, never a zero. `stock` is [(station, its stock)] for the
    stations whose stock is known, in station order. `carried` of them have no entry for the period: their
    stock comes from an earlier one (every station with an entry has a known stock)."""

    sub: reports.Submission
    unloaded: int | None
    arrived: int | None
    remaining: int | None
    stock: list
    carried: int


def _now_row(sub, stock):
    return MtuNow(
        sub,
        summary._known_sum(e.unloaded for e in sub.done),
        summary._known_sum(e.arrived for e in sub.done),
        summary._known_sum(v for _, v in stock),
        stock,
        len(stock) - len(sub.done),
    )


def _top(stock):
    """«Қолган» of one МТУ by station: [(name, stock)], largest first, stations holding no wagons left out.

    Up to six in full; beyond that the first five and «бошқа N станция» with the rest's sum."""
    parts = sorted(((station.name, value) for station, value in stock if value > 0), key=lambda part: -part[1])
    if len(parts) <= STOCK_SHOWN + 1:
        return parts
    rest = parts[STOCK_SHOWN:]
    return [*parts[:STOCK_SHOWN], (f"бошқа {len(rest)} станция", sum(value for _, value in rest))]


def _ticks(lo, hi):
    """Round values from at or below lo to at or above hi, at most four steps apart."""
    step = next(s for k in range(7) for s in (10**k, 2 * 10**k, 5 * 10**k) if -(-hi // s) - lo // s <= 4)
    bottom, top = lo // step * step, -(-hi // step) * step
    return list(range(bottom, max(top, bottom + step) + 1, step))


def _trend(series, filling):
    """The «Охирги 14 сутка» chart as positions in % of the plot box (y from the top), for inline SVG.

    A day with no value is a gap: each line is cut into the runs of days that have one. `filling` has a flag per
    day: stations may still enter one of its periods. Such a day with entries missing is partial (only the last day
    can be): its sums are not comparable yet, so it is left out of the lines, the scale and the average, unless no
    other day has numbers. `partial` is that last day's sums and missing count for the note under the chart."""
    keys = ("unloaded", "arrived")
    last = len(series["labels"]) - 1
    partial = [bool(f and m) for f, m in zip(filling, series["missing"])]
    drawn = {key: [None if p else v for v, p in zip(series[key], partial)] for key in keys}
    if all(v is None for key in keys for v in drawn[key]):
        drawn = {key: series[key] for key in keys}  # the first day of data: nothing else to compare it with
    values = [v for key in keys for v in drawn[key] if v is not None]
    if not values:
        return None
    ticks = _ticks(min(values), max(values))
    bottom, top = ticks[0], ticks[-1]

    def x(i):
        return round(100 * i / max(last, 1), 2)

    def y(v):
        return round(100 * (top - v) / (top - bottom), 2)

    def points(key, days):
        return " ".join(f"{x(i)},{y(drawn[key][i])}" for i in days)

    lines = {}
    for key in keys:
        known = [i for i, v in enumerate(drawn[key]) if v is not None]
        runs = []  # runs of consecutive days with a value
        for i in known:
            if runs and runs[-1][-1] == i - 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        end = known[-1] if known else None
        lines[key] = {
            # A lone day is drawn as a dot: a zero-length line with round caps.
            "runs": [points(key, run * 2 if len(run) == 1 else run) for run in runs],
            "end": None if end is None else {"x": x(end), "y": y(drawn[key][end]), "value": drawn[key][end]},
            "total": summary._known_sum(series[key]),
        }
    # The higher end's label goes above its dot, the other one's below, so the two never overlap.
    ends = sorted((lines[key]["end"] for key in keys if lines[key]["end"]), key=lambda end: end["y"])
    for i, end in enumerate(ends):
        end["above"] = i == 0
    days = [v for v, p in zip(series["unloaded"], partial) if v is not None and not p]
    return {
        "lines": lines,
        "ticks": [(t, y(t)) for t in reversed(ticks)],
        "days": [
            (label[:5], x(i), unloaded, arrived, missing)
            for i, (label, unloaded, arrived, missing) in enumerate(
                zip(series["labels"], series["unloaded"], series["arrived"], series["missing"])
            )
        ],
        "average": round(sum(days) / len(days)) if days else None,
        "partial": (
            {key: series[key][last] for key in (*keys, "missing")}
            if partial[last] and series["unloaded"][last] is not None
            else None
        ),
    }


@access.staff_required
def now(request):
    stations, mtu = access.scope(request)
    moment = timezone.now()
    latest = periods.last_ended(moment)
    pe = _period(request.GET.get("pe"), latest)
    subs = {s.mtu.pk: s for s in reports.submission(stations, pe)}
    listed = list(stations)  # one query; the stock and the chart below reuse it
    remaining = reports.remaining_at(listed, pe)
    stock = {}
    for station in listed:
        if remaining[station.id] is not None:
            stock.setdefault(station.mtu_id, []).append((station, remaining[station.id]))
    # submission() has no item for an MTU without stations; the boshqarma still gets its tile.
    mtus = access.mtus_for(request.user) if mtu is None else [mtu]
    rows = [_now_row(subs.get(m.pk) or reports.Submission(m, 0, 0, [], []), stock.get(m.pk, [])) for m in mtus]
    total = _now_row(
        reports.Submission(
            mtu,
            sum(r.sub.expected for r in rows),
            sum(r.sub.submitted for r in rows),
            [s for r in rows for s in r.sub.missing],
            [e for r in rows for e in r.sub.done],
        ),
        [s for row in rows for s in row.stock],
    )
    night = periods.label(pe)[1] == 1
    # The chart ends with the last whole day at pe: a day whose day period is still to come would always dip.
    d_to = periods.label(periods.prev_end(pe) if night else pe)[0]
    d_from = d_to - timedelta(days=TREND_DAYS - 1)
    # A day stations may still enter (periods.STATION_WINDOW) is still filling: what it misses is not late yet.
    open_days = {periods.label(end)[0] for end in periods.open_for_station(moment)}
    filling = [d_from + timedelta(days=i) in open_days for i in range(TREND_DAYS)]
    attention = reports.attention(stations, moment - timedelta(days=ATTENTION_DAYS))
    later = periods.next_end(pe)
    context = {
        "pe": pe,
        "night": night,
        "mtu": mtu,
        "rows": rows,
        "total": total,
        "sub": rows[0].sub if mtu else None,
        "missing_rows": [r for r in rows if r.sub.missing],
        "complete_rows": [r for r in rows if r.sub.expected and not r.sub.missing],
        "stock_top": _top(total.stock) if mtu else None,
        "deadline": pe + periods.STATION_WINDOW if periods.station_can_write(pe, moment) else None,
        "trend": _trend(summary.daily_series(listed, d_from, d_to, now=moment), filling),
        "trend_from": d_from,
        "trend_to": d_to,
        "attention": attention,
        "attention_parts": [part for part in (attention[:ATTENTION_SHOWN], attention[ATTENTION_SHOWN:]) if part],
        "attention_days": ATTENTION_DAYS,
        "prev_url": _query(request, mtu, pe=periods.to_slug(periods.prev_end(pe))),
        "next_url": _query(request, mtu, pe=periods.to_slug(later)) if later <= latest else None,
    }
    return render(request, "hisobot/staff/now.html", context)


@access.staff_required
def daily(request):
    stations, mtu = access.scope(request)
    moment = timezone.now()
    latest = periods.label(periods.last_ended(moment))[0]
    day = _report_date(request.GET.get("d"), latest)
    night_end, day_end = periods.ends_for(day)
    # A period that has ended links its cells to the form; a running one stays blank.
    slugs = [periods.to_slug(pe) if periods.staff_can_write(pe, moment) else None for pe in (night_end, day_end)]
    rows = reports.daily_rows(stations, day, now=moment)
    groups = [
        (group_mtu, [(row, [(row.night, slugs[0]), (row.day, slugs[1])]) for row in group], total)
        for group_mtu, group, total in reports.by_mtu(rows)
    ]
    context = {
        "day": day,
        "latest": latest,
        "start": periods.bounds(night_end)[0],
        "end": day_end,
        "mtu": mtu,
        "mtus": access.mtus_for(request.user) if request.user.is_boshqarma else None,
        "groups": groups,
        "grand": reports.totals(rows) if len(groups) > 1 else None,
        "night_ended": slugs[0] is not None,
        "day_ended": slugs[1] is not None,
        "prev_url": _query(request, mtu, d=(day - timedelta(days=1)).isoformat()),
        "next_url": _query(request, mtu, d=(day + timedelta(days=1)).isoformat()) if day < latest else None,
        "export_url": reverse("staff_export") + _query(request, mtu, d=day.isoformat()),
    }
    return render(request, "hisobot/staff/daily.html", context)


def _saved(result):
    if not result.changed:
        return "Ўзгариш йўқ"
    if result.cascaded:
        return f"Сақланди. Кейинги {result.cascaded} та давр қайта ҳисобланди"
    return "Сақланди"


def _history(entry):
    """Every change of the entry, newest first: (change, who, [(label, old, new), ...])."""
    if entry is None:
        return []
    changes = Change.objects.filter(entry=entry).select_related("user__mtu", "cause__entry").order_by("-at", "-id")
    return [
        (
            change,
            change.user.display_name if change.user else "Станция (ПИН)",
            [
                (label, change.old.get(key), change.new[key])
                for key, label in FIELDS.items()
                if key in change.new and change.old.get(key) != change.new[key]
            ],
        )
        for change in changes
    ]


@access.staff_required
def entry(request, station_id, pe):
    station = get_object_or_404(access.stations_for(request.user), pk=station_id)
    try:
        pe = periods.from_slug(pe)
    except ValueError:
        raise Http404 from None
    moment = timezone.now()
    if not periods.staff_can_write(pe, moment):
        messages.info(request, f"{periods.local(pe):%d.%m.%Y %H:%M} да очилади")
        return redirect("staff_now")
    back_url = reverse("staff_daily") + _query(request, station.mtu, d=periods.label(pe)[0].isoformat())
    if station.opening_at is not None and periods.bounds(pe)[0] < station.opening_at:
        # save_entry refuses it too (before_opening); as on the station's side, no form that can never be saved.
        messages.warning(request, balance.MESSAGES["before_opening"])
        return redirect(back_url)

    form = error = None
    if request.method == "POST":
        data = request.POST.copy()
        if data.get("harakatsiz"):  # «Ҳаракат бўлмади (0 / 0)»
            data["unloaded"] = data["arrived"] = "0"
        needs_opening = station.opening_at is None
        form = EntryForm(data, require_opening=needs_opening)
        if form.is_valid():
            values = form.cleaned_data
            try:
                result = balance.save_entry(
                    station_id=station.pk,
                    period_end=pe,
                    unloaded=values["unloaded"],
                    arrived=values["arrived"],
                    override=values["override"],
                    opening=values["opening"] if needs_opening else None,
                    form_version=values["version"],
                    form_prev=values["prev"],
                    actor=request.user,
                    ip=access.client_ip(request),
                    now=moment,
                )
            except balance.SaveError as exc:
                error = exc.message
                if exc.code == "stale":
                    # Someone saved in between: show what is stored now, not the numbers typed over it.
                    form = None
                    station.refresh_from_db()
            else:
                messages.success(request, _saved(result))
                return redirect(request.path)

    current = station.entries.filter(period_end=pe).first()
    prev, gap = balance.prev_for(station, pe)
    if form is None:
        initial = {"version": current.version if current else 0, "prev": prev}
        if current:
            initial.update(unloaded=current.unloaded, arrived=current.arrived, override=current.remaining_override)
        form = EntryForm(initial=initial, require_opening=station.opening_at is None)
    context = {
        "station": station,
        "pe": pe,
        "night": periods.label(pe)[1] == 1,
        "form": form,
        "error": error,
        "current": current,
        "prev": prev,
        "gap": gap,
        "needs_opening": form.fields["opening"].required,
        "back_url": back_url,
        "history": _history(current),
    }
    return render(request, "hisobot/staff/entry.html", context)
