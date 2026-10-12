"""Staff pages: the current period, the daily table and the form for one station's period (spec 9)."""

import re
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import access, balance, periods, reports
from .forms import EntryForm
from .models import Change

ATTENTION_DAYS = 30
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
    return pe if pe <= latest else latest


def _report_date(value, latest):
    """?d=: a report date up to the latest one. Anything else gives the latest."""
    if not _DATE.fullmatch(value or ""):
        return latest
    try:
        return min(date.fromisoformat(value), latest)
    except ValueError:
        return latest


@access.staff_required
def now(request):
    stations, mtu = access.scope(request)
    moment = timezone.now()
    latest = periods.last_ended(moment)
    pe = _period(request.GET.get("pe"), latest)
    subs = reports.submission(stations, pe)
    cards = sub = None
    if mtu is None:
        # submission() has no item for an MTU without stations; the boshqarma still gets its card.
        found = {s.mtu.pk: s for s in subs}
        cards = [found.get(m.pk) or reports.Submission(m, 0, 0, [], []) for m in access.mtus_for(request.user)]
    else:
        sub = subs[0] if subs else reports.Submission(mtu, 0, 0, [], [])
    later = periods.next_end(pe)
    context = {
        "pe": pe,
        "mtu": mtu,
        "cards": cards,
        "sub": sub,
        "attention": reports.attention(stations, moment - timedelta(days=ATTENTION_DAYS)),
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
    changes = Change.objects.filter(entry=entry).select_related("user", "cause__entry").order_by("-at", "-id")
    return [
        (
            change,
            change.user.username if change.user else "Станция (ПИН)",
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
        "form": form,
        "error": error,
        "current": current,
        "prev": prev,
        "gap": gap,
        "needs_opening": form.fields["opening"].required,
        "back_url": reverse("staff_daily") + "?" + urlencode({"d": periods.label(pe)[0].isoformat()}),
        "history": _history(current),
    }
    return render(request, "hisobot/staff/entry.html", context)
