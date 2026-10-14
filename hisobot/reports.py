"""Read side: the numbers behind the daily table, the submission screen, the export, the summary and the charts.

Two rules from the spec live here. A period nobody entered is never a zero (section 10).
Every "remaining" value comes from the stock rule in `_stock` (section 10).

`stations` is always the caller's queryset, already scoped and ordered: results keep its order
and never reach outside it. The query count of every function is fixed, whatever the number of stations.
"""

from dataclasses import dataclass

from django.db.models import F, IntegerField, Q
from django.db.models.functions import Coalesce

from . import periods
from .models import Entry, Mtu, Station


@dataclass
class Cell:
    entry: Entry | None
    expected: bool  # station.expects(pe)
    remaining: int | None  # stock at the period end; None is "unknown", never 0
    carried: bool  # remaining comes from an earlier period or from the opening


@dataclass
class DayRow:
    station: Station
    night: Cell
    day: Cell
    total_unloaded: int | None  # sum of the unloaded values present; None when both periods are absent
    partial: bool  # an expected period has no entry


@dataclass
class Totals:
    n_unloaded: int
    n_arrived: int
    n_remaining: int
    d_unloaded: int
    d_arrived: int
    d_remaining: int
    total_unloaded: int
    partial: bool  # some row is partial
    stations: int  # rows summed
    missing: int  # stations with an expected period not entered (the partial rows)


@dataclass
class Submission:
    mtu: Mtu
    expected: int  # submitted + len(missing)
    submitted: int  # len(done)
    missing: list[Station]  # expect the period and have no entry, in station order
    done: list[Entry]  # in station order, each with .station loaded


def _stock(station, last, t):
    """Stock rule: the latest entry's effective remaining, else the opening once it applies, else unknown."""
    if last is not None:
        return last.remaining
    if station.opening_at is not None and station.opening_at <= t:
        return station.opening_remaining
    return None


def _latest(stations, **when):
    """{station id: its latest entry among those matching `when`}, in one query."""
    # ponytail: DISTINCT ON reads every matching entry of these stations (all history before T).
    # Fine for years of data at a few hundred stations; a LATERAL ... LIMIT 1 per station if it ever shows.
    found = Entry.objects.filter(station__in=stations, **when)
    return {e.station_id: e for e in found.order_by("station_id", "-period_end").distinct("station_id")}


def remaining_at(stations, t) -> dict[int, int | None]:
    latest = _latest(stations, period_end__lte=t)
    return {s.id: _stock(s, latest.get(s.id), t) for s in stations}


def daily_rows(stations, report_date, *, now) -> list[DayRow]:
    """A period that has not ended at `now` is blank: not expected, no stock, so never "missing"."""
    ends = periods.ends_for(report_date)
    stations = list(stations.select_related("mtu"))  # by_mtu reads station.mtu
    before = _latest(stations, period_end__lt=ends[0])
    entered = {
        (e.station_id, e.period_end): e for e in Entry.objects.filter(station__in=stations, period_end__in=ends)
    }
    rows = []
    for station in stations:
        last = before.get(station.id)
        cells = []
        for pe in ends:
            if pe > now:
                cells.append(Cell(None, False, None, False))
                continue
            entry = entered.get((station.id, pe))
            if entry:
                entry.station = station
            last = entry or last
            remaining = _stock(station, last, pe)
            carried = entry is None and remaining is not None
            cells.append(Cell(entry, station.expects(pe), remaining, carried))
        unloaded = [c.entry.unloaded for c in cells if c.entry]
        rows.append(
            DayRow(
                station,
                *cells,
                total_unloaded=sum(unloaded) if unloaded else None,
                partial=any(c.expected and not c.entry for c in cells),
            )
        )
    return rows


def shown(rows) -> list[DayRow]:
    """The rows the daily table and its export list: a station that expected an ended period of the day, entered one
    or holds wagons. The rest (switched off, or not on yet, with nothing entered or held) would be dashes only, and add
    nothing to the totals."""
    return [row for row in rows if any(c.expected or c.entry or c.remaining for c in (row.night, row.day))]


def _sums(cells):
    """(unloaded, arrived, remaining) over one period's cells; what is absent or unknown adds nothing."""
    entries = [c.entry for c in cells if c.entry]
    return sum(e.unloaded for e in entries), sum(e.arrived for e in entries), sum(c.remaining or 0 for c in cells)


def totals(rows) -> Totals:
    return Totals(
        *_sums([r.night for r in rows]),
        *_sums([r.day for r in rows]),
        total_unloaded=sum(r.total_unloaded or 0 for r in rows),
        partial=any(r.partial for r in rows),
        stations=len(rows),
        missing=sum(r.partial for r in rows),
    )


def by_mtu(rows) -> list[tuple[Mtu, list[DayRow], Totals]]:
    groups = {}
    for row in rows:
        groups.setdefault(row.station.mtu_id, []).append(row)
    return [(group[0].station.mtu, group, totals(group)) for group in groups.values()]


def submission(stations, period_end) -> list[Submission]:
    stations = list(stations.select_related("mtu"))
    entries = {e.station_id: e for e in Entry.objects.filter(station__in=stations, period_end=period_end)}
    result = {}
    for station in stations:
        sub = result.setdefault(station.mtu_id, Submission(station.mtu, 0, 0, [], []))
        entry = entries.get(station.id)
        if entry:
            # An entry always counts, also when the station no longer expects that period
            # (switching it off and on moves active_from): N / M has to match the lists under it.
            entry.station = station
            sub.done.append(entry)
        elif station.expects(period_end):
            sub.missing.append(station)
    for sub in result.values():
        sub.submitted = len(sub.done)
        sub.expected = sub.submitted + len(sub.missing)
    return list(result.values())


def attention(stations, since) -> list[Entry]:
    # Entry.remaining and Entry.overridden in SQL: effective != calc exactly when an override differs from calc.
    # Newest first; entries of the same period by station id.
    effective = Coalesce("remaining_override", "remaining_calc", output_field=IntegerField())
    return list(
        Entry.objects.filter(station__in=stations, period_end__gte=since)
        .alias(effective=effective)
        .filter(Q(effective__lt=0) | ~Q(effective=F("remaining_calc")))
        .select_related("station__mtu")
        .order_by("-period_end", "station_id")
    )
