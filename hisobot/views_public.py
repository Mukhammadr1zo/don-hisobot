"""Task 6 replaces the stubs below. `healthz` and `styleguide` are final: keep them (urls.py routes both)."""

from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from . import periods
from .forms import EntryForm
from .models import Mtu, Station, User


def _stub(request, *args, **kwargs):
    return HttpResponse("Ҳали тайёр эмас", status=501)


home = mtu_stations = station_login = staff_login = logout = _stub


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"ok": True, "time": periods.local(timezone.now()).isoformat()})


def styleguide(request):
    """Every component of app.css once. Routed only when DEBUG is on.

    ?kim=stansiya | mtu | mehmon shows the page as that principal sees the top bar (default: boshqarma).
    """
    kim = request.GET.get("kim", "boshqarma")
    if kim == "stansiya":
        request.station = Station(name="Чуқурсой")
    elif kim in ("mtu", "boshqarma"):
        request.user = User(username=kim, mtu=Mtu(id=1, name="Тошкент") if kim == "mtu" else None)
    now = timezone.now()
    pe = periods.last_ended(now)
    context = {
        "now": now,
        "pe": pe,
        "prev_pe": periods.prev_end(pe),
        "form": EntryForm(initial={"version": 0, "prev": 12}),
        "bad_form": EntryForm({"xato-unloaded": "1000", "xato-arrived": "", "xato-version": "0"}, prefix="xato"),
    }
    return render(request, "hisobot/_styleguide.html", context)
