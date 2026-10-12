"""The way in and out: MTU → station → PIN for stations, a login form for staff; plus healthz and the styleguide."""

from django.db import connection
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import access, periods
from .forms import EntryForm
from .models import Mtu, Station, User
from .templatetags.hisobot_extras import dt, t

TOO_MANY = "Жуда кўп уриниш. Бироздан сўнг қайта уриниб кўринг"
PIN_ERRORS = {
    access.Login.BAD: "ПИН нотўғри",
    access.Login.LOCKED: "Кириш вақтинча ёпилган. {until} дан кейин уриниб кўринг ёки МТУга мурожаат қилинг",
    access.Login.THROTTLED: TOO_MANY,
    access.Login.NO_PIN: "Бу станцияга ПИН ҳали берилмаган. МТУга мурожаат қилинг",
}
STAFF_ERRORS = {access.Login.BAD: "Логин ёки парол нотўғри", access.Login.THROTTLED: TOO_MANY}


def home(request):
    if access.current_station(request):
        return redirect("station_home")
    if request.user.is_authenticated:
        return redirect("staff_now")
    mtus = Mtu.objects.order_by("sort", "name").annotate(active=Count("stations", filter=Q(stations__active_to=None)))
    return render(request, "hisobot/public/home.html", {"mtus": mtus})


def mtu_stations(request, mtu_id):
    mtu = get_object_or_404(Mtu, pk=mtu_id)
    stations = mtu.stations.filter(active_to=None).order_by("sort", "name")
    query = request.GET.get("q", "").strip()
    if query:
        # In Python: casefold() knows Cyrillic whatever the database's locale is; an MTU has a few hundred rows.
        stations = [s for s in stations if query.casefold() in s.name.casefold()]
    return render(request, "hisobot/public/mtu.html", {"mtu": mtu, "stations": stations, "query": query})


def station_login(request, station_id):
    # access.station_login does not look at active_to: an inactive station must not get this far.
    station = get_object_or_404(Station.objects.select_related("mtu"), pk=station_id, active_to=None)
    error = None
    if request.method == "POST":
        result = access.station_login(request, station, request.POST.get("pin", ""))
        if result is access.Login.OK:
            return redirect("station_home")
        error = PIN_ERRORS[result]
        if result is access.Login.LOCKED:
            error = error.format(until=_lock_end(station.locked_until))
    return render(request, "hisobot/public/pin.html", {"station": station, "error": error})


def _lock_end(until):
    """HH:MM, with the date in front when the lock ends on another day (a lock lasts up to 24 hours)."""
    return t(until) if periods.local(until).date() == periods.local(timezone.now()).date() else dt(until)


def staff_login(request):
    username, error = "", None
    if request.method == "POST":
        username = request.POST.get("username", "")
        result = access.staff_login(request, username, request.POST.get("password", ""))
        if result is access.Login.OK:
            return redirect("staff_now")
        error = STAFF_ERRORS[result]
    return render(request, "hisobot/public/staff_login.html", {"username": username, "error": error})


@require_POST
def logout(request):
    access.logout(request)
    return redirect("home")


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
