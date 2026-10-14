"""Station pages (spec section 9, Stansiya). The station is request.station, set from the session by
access.station_required; no station id is ever read from the URL or the form."""

from datetime import timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from . import access, balance, periods, reports
from .forms import EntryForm
from .models import Station

HISTORY_DAYS = 7


def _refusal(station, pe, now):
    """Why this station cannot enter `pe` now, or None. save_entry checks again; this keeps the form away."""
    if not periods.station_can_write(pe, now):
        return balance.MESSAGES["window"]
    if station.opening_at is not None and periods.bounds(pe)[0] < station.opening_at:
        return balance.MESSAGES["before_opening"]
    return None


@access.station_required
def home(request):
    station, now = request.station, timezone.now()
    ends = periods.open_for_station(now)  # newest first
    entries = {e.period_end: e for e in station.entries.filter(period_end__in=ends)}
    cards = [
        {
            "pe": pe,
            "night": periods.label(pe)[1] == 1,  # the dark card
            "entry": entries.get(pe),
            "deadline": pe + periods.STATION_WINDOW,
            "refusal": _refusal(station, pe, now),
        }
        for pe in ends
    ]
    # The last report days that have an ended period, newest first; a period still running is blank.
    latest = periods.label(ends[0])[0]
    scope = Station.objects.filter(pk=station.pk)
    history = []
    for day in (latest - timedelta(days=n) for n in range(HISTORY_DAYS)):
        (row,) = reports.daily_rows(scope, day, now=now)
        history.append((day, (row.night, row.day), row))
    # The longest «Туширилган» bar in the history is the week's largest value.
    top = max((cell.entry.unloaded for _, cells, _ in history for cell in cells if cell.entry), default=0)
    context = {"now": now, "cards": cards, "next_open": periods.next_end(ends[0]), "history": history, "top": top}
    return render(request, "hisobot/station/home.html", context)


@access.station_required
def entry(request, pe):
    try:
        pe = periods.from_slug(pe)
    except ValueError:
        raise Http404 from None
    station, now = request.station, timezone.now()
    refusal = _refusal(station, pe, now)
    if refusal:
        messages.warning(request, refusal)
        return redirect("station_home")

    form = error = None
    if request.method == "POST":
        data = request.POST.copy()
        if "harakatsiz" in data:  # «Ҳаракат бўлмади (0 / 0)» is a submit button: it posts zeros
            data["unloaded"] = data["arrived"] = "0"
        form = EntryForm(data, require_opening=station.opening_at is None)
        if form.is_valid():
            values = form.cleaned_data
            try:
                balance.save_entry(
                    station_id=station.pk,
                    period_end=pe,
                    unloaded=values["unloaded"],
                    arrived=values["arrived"],
                    override=values["override"],
                    opening=values["opening"],
                    form_version=values["version"],
                    form_prev=values["prev"],
                    actor=None,
                    ip=access.client_ip(request),
                    now=now,
                )
            except balance.SaveError as refused:
                error = refused.message
                if refused.code in ("stale", "opening_required"):
                    form = None  # the numbers the page showed are out of date: show the current ones
            else:
                messages.success(request, "Қабул қилинди")
                return redirect("station_home")

    station.refresh_from_db(fields=["opening_remaining", "opening_at"])  # another device may have set it
    existing = station.entries.filter(period_end=pe).first()
    prev, gap = balance.prev_for(station, pe)
    # The skipped period is still open to the station: the warning leads there. Closed, only the МТУ can enter it.
    before, gap_url = periods.prev_end(pe), None
    if gap and periods.station_can_write(before, now):
        gap_url = reverse("station_entry", args=[periods.to_slug(before)])
    if form is None:
        initial = {"version": existing.version if existing else 0, "prev": prev}
        if existing:
            initial |= {
                "unloaded": existing.unloaded,
                "arrived": existing.arrived,
                "override": existing.remaining_override,
            }
        form = EntryForm(initial=initial, require_opening=station.opening_at is None)
    context = {
        "pe": pe,
        "night": periods.label(pe)[1] == 1,
        "form": form,
        "error": error,
        "existing": existing,
        "gap": gap,
        "gap_url": gap_url,
        "needs_opening": form.fields["opening"].required,
        "override_open": form["override"].value() not in (None, "") or bool(form["override"].errors),
        # Without JavaScript the page shows the saved remaining; the live sum replaces it as soon as it runs.
        "left": existing.remaining if existing and not form.is_bound else None,
        "deadline": pe + periods.STATION_WINDOW,
    }
    return render(request, "hisobot/station/entry.html", context)
