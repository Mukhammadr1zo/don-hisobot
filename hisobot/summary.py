"""The report page: sums over a range of report dates, the daily series and the comparison bars (spec section 10).

A range d_from..d_to covers the periods ending from ends_for(d_from)[0] to ends_for(d_to)[1].
As in reports.daily_rows, a period that has not ended at `now` is not expected, so never missing,
and a period nobody entered is never a zero. Stock comes from the stock rule in reports.
Every function runs a fixed number of queries, whatever the number of stations or days.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Sum

from . import periods, reports
from .models import Entry, Mtu, Station


@dataclass
class SummaryRow:
    station: Station | None  # None on a per-MTU row and on the total
    mtu: Mtu | None  # None on the total
    unloaded: int | None  # summed over the entries in the range; None when there is none, never a zero
    arrived: int | None
    remaining_end: int | None  # stock at the end of d_to; summed over the known values, None when none is known
    submitted: int  # station-periods entered
    expected: int  # submitted + the expected periods not entered, counted as reports.submission does
    overrides: int  # entries whose remaining was typed differently from the calculation

    @property
    def missing(self):
        return self.expected - self.submitted


def _days(d_from, d_to):
    return [d_from + timedelta(days=i) for i in range((d_to - d_from).days + 1)]


def _entries(stations, d_from, d_to):
    start, end = periods.ends_for(d_from)[0], periods.ends_for(d_to)[1]
    return Entry.objects.filter(station__in=stations, period_end__gte=start, period_end__lte=end)


def _known_sum(values):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def total(rows, mtu=None) -> SummaryRow:
    return SummaryRow(
        None,
        mtu,
        _known_sum(r.unloaded for r in rows),
        _known_sum(r.arrived for r in rows),
        _known_sum(r.remaining_end for r in rows),
        sum(r.submitted for r in rows),
        sum(r.expected for r in rows),
        sum(r.overrides for r in rows),
    )


def summary(stations, d_from, d_to, *, now) -> tuple[list[SummaryRow], list[SummaryRow]]:
    """(one row per station in queryset order, one row per MTU present)."""
    stations = list(stations.select_related("mtu"))
    ended = [pe for day in _days(d_from, d_to) for pe in periods.ends_for(day) if pe <= now]
    entries = {}
    for entry in _entries(stations, d_from, d_to):
        entries.setdefault(entry.station_id, []).append(entry)
    stock = reports.remaining_at(stations, periods.ends_for(d_to)[1])
    rows = []
    for station in stations:
        mine = entries.get(station.id, [])
        entered = {e.period_end for e in mine}
        # An entry always counts, also for a period the station no longer expects (as in reports.submission).
        missing = sum(1 for pe in ended if pe not in entered and station.expects(pe))
        rows.append(
            SummaryRow(
                station,
                station.mtu,
                _known_sum(e.unloaded for e in mine),
                _known_sum(e.arrived for e in mine),
                stock[station.id],
                len(mine),
                len(mine) + missing,
                sum(e.overridden for e in mine),
            )
        )
    groups = {}
    for row in rows:
        groups.setdefault(row.mtu.id, []).append(row)
    return rows, [total(group, group[0].mtu) for group in groups.values()]


def daily_series(stations, d_from, d_to, *, now) -> dict:
    """One point per report date. Remaining is the stock at D 20:00 summed over the stations that have one.

    A day nobody entered anything for has no unloaded and arrived values; a day none of whose periods
    has ended is None throughout. Both are gaps in the chart, never zeros.
    """
    stations = list(stations)
    # The stock rule of reports, rolled forward one period at a time as reports.daily_rows does:
    # remaining_at per day would cost a query per day.
    last = reports._latest(stations, period_end__lt=periods.ends_for(d_from)[0])
    entered = {(e.station_id, e.period_end): e for e in _entries(stations, d_from, d_to)}
    series = {"labels": [], "unloaded": [], "arrived": [], "remaining": [], "missing": []}
    for day in _days(d_from, d_to):
        series["labels"].append(f"{day:%d.%m.%Y}")
        ends = periods.ends_for(day)
        if ends[0] > now:
            for key in ("unloaded", "arrived", "remaining", "missing"):
                series[key].append(None)
            continue
        found = []
        missing = 0
        for pe in ends:
            for station in stations:
                entry = entered.get((station.id, pe))
                if entry:
                    last[station.id] = entry
                    found.append(entry)
                elif pe <= now and station.expects(pe):
                    missing += 1
        series["unloaded"].append(_known_sum(e.unloaded for e in found))
        series["arrived"].append(_known_sum(e.arrived for e in found))
        series["remaining"].append(_known_sum(reports._stock(s, last.get(s.id), ends[1]) for s in stations))
        series["missing"].append(missing)
    return series


def compare(stations, d_from, d_to, *, by) -> dict:
    """Unloaded and arrived over the range, one bar per MTU (by="mtu") or per station (by="station")."""
    if by not in ("mtu", "station"):
        raise ValueError(f"compare by {by!r}")
    stations = list(stations.select_related("mtu"))
    sums = {
        row["station_id"]: row
        for row in _entries(stations, d_from, d_to)
        .values("station_id")
        .annotate(unloaded=Sum("unloaded"), arrived=Sum("arrived"))
    }
    bars = {}
    for station in stations:
        key, label = (station.mtu_id, station.mtu.name) if by == "mtu" else (station.id, station.name)
        bar = bars.setdefault(key, {"label": label, "unloaded": None, "arrived": None})  # no entries: no bar
        found = sums.get(station.id)
        if found:
            bar["unloaded"] = (bar["unloaded"] or 0) + found["unloaded"]
            bar["arrived"] = (bar["arrived"] or 0) + found["arrived"]
    bars = list(bars.values())
    return {
        "labels": [b["label"] for b in bars],
        "unloaded": [b["unloaded"] for b in bars],
        "arrived": [b["arrived"] for b in bars],
    }
