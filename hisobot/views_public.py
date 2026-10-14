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
    access.Login.BLOCKED: "ПИН кўп марта нотўғри терилди, кириш ёпилди. МТУга мурожаат қилинг: улар блокдан чиқаради",
}
STAFF_ERRORS = {access.Login.BAD: "Логин ёки парол нотўғри", access.Login.THROTTLED: TOO_MANY}


def _open_now(now):
    """The «Ҳозир очиқ» tile of the visitor pages: the newest period stations can enter, and the clock."""
    pe, clock = periods.last_ended(now), periods.local(now)
    return {
        "now": now,
        "pe": pe,
        "night": periods.label(pe)[1] == 1,
        "deadline": pe + periods.STATION_WINDOW,
        "next_open": periods.next_end(pe),
        "hand": (clock.hour * 60 + clock.minute) / 4,  # degrees on the 24-hour dial, midnight at the top
    }


def home(request):
    if access.current_station(request):
        return redirect("station_home")
    if request.user.is_authenticated:
        return redirect("staff_now")
    mtus = list(
        Mtu.objects.order_by("sort", "name").annotate(active=Count("stations", filter=Q(stations__active_to=None)))
    )
    context = {"mtus": mtus, "total": sum(mtu.active for mtu in mtus), **_open_now(timezone.now())}
    return render(request, "hisobot/public/home.html", context)


# A Russian keyboard has no ў қ ғ ҳ: operators type у к г х, so the two spellings match alike.
FOLD = str.maketrans("ўқғҳё", "укгхе")

# The Uzbek Cyrillic alphabet in its own order. Codepoints would put Ё and Ў before А, and Ғ before Қ.
ALPHABET = {letter: n for n, letter in enumerate("абвгдеёжзийклмнопрстуфхцчшъьэюяўқғҳ")}


def _fold(text):
    return text.casefold().translate(FOLD)


def _alphabetical(name):
    """Sort key: Uzbek alphabetical order ignoring case; any other character comes after the letters, by codepoint."""
    return [ALPHABET.get(char, len(ALPHABET) + ord(char)) for char in name.casefold()]


def mtu_stations(request, mtu_id):
    mtu = get_object_or_404(Mtu, pk=mtu_id)
    stations = sorted(mtu.stations.filter(active_to=None), key=lambda station: _alphabetical(station.name))
    total = len(stations)
    query = request.GET.get("q", "").strip()
    if query:
        # In Python: casefold() knows Cyrillic whatever the database's locale is; an MTU has a few hundred rows.
        stations = [s for s in stations if _fold(query) in _fold(s.name)]
    context = {"mtu": mtu, "stations": stations, "query": query, "total": total, **_open_now(timezone.now())}
    return render(request, "hisobot/public/mtu.html", context)


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
    toshkent = Mtu(id=1, name="Тошкент", sort=1)
    if kim == "stansiya":
        request.station = Station(name="Чуқурсой", mtu=toshkent)
    elif kim in ("mtu", "boshqarma"):
        request.user = User(username=kim, mtu=toshkent if kim == "mtu" else None)
    now = timezone.now()
    pe = periods.last_ended(now)
    # The mockup's numbers: name, submitted, expected, unloaded, arrived, remaining, one of its stations.
    sample = [
        ("Тошкент", 21, 24, 46, 52, 118, "Чуқурсой"),
        ("Қўқон", 15, 19, 31, 28, 74, "Андижон-1"),
        ("Бухоро", 19, 19, 38, 41, 96, "Навоий"),
        ("Қўнғирот", 11, 16, 17, 22, 63, "Хива"),
        ("Қарши", 14, 18, 29, 25, 71, "Шаҳрисабз"),
        ("Термиз", 9, 12, 14, 19, 40, "Денов"),
    ]
    mtus = [
        {"mtu": Mtu(name=name, sort=sort), "submitted": n, "expected": m, "missing": m - n,
         "unloaded": u, "arrived": a, "remaining": r, "dots": [True] * n + [False] * (m - n), "station": station}
        for sort, (name, n, m, u, a, r, station) in enumerate(sample, start=1)
    ]  # fmt: skip
    keys = ("submitted", "expected", "missing", "unloaded", "arrived", "remaining")
    icons = [  # the symbols of base.html's sprite, #i-<name>
        "grain", "moon", "sun", "clock", "check", "wait", "minus-circle", "pencil", "arrow", "left", "right",
        "down", "plus", "minus", "search", "lock", "user", "menu", "download", "print",
    ]  # fmt: skip
    context = {
        "kim": kim,
        "now": now,
        "pe": pe,
        "prev_pe": periods.prev_end(pe),
        "deadline": pe + periods.STATION_WINDOW,
        "mtus": mtus,
        "total": {key: sum(row[key] for row in mtus) for key in keys},
        "icons": icons,
        "form": EntryForm(initial={"version": 0, "prev": 12}),
        "bad_form": EntryForm({"xato-unloaded": "1000", "xato-arrived": "", "xato-version": "0"}, prefix="xato"),
    }
    return render(request, "hisobot/_styleguide.html", context)
