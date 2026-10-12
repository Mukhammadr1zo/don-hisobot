"""Who is asking: station PIN login, staff login, session principals, view guards and MTU scoping."""

import enum
import ipaddress
import secrets
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import auth
from django.contrib.auth.hashers import PBKDF2PasswordHasher, check_password, make_password
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .models import Change, LoginFailure, Mtu, Station


class PinHasher(PBKDF2PasswordHasher):
    # ponytail: 8 digits fall to offline guessing whatever one hash costs; the lock ladder is the defence.
    # The default cost (about 0.5 s) would make a 200-station PIN sheet a 90-second request.
    algorithm = "pbkdf2_sha256_pin"
    iterations = 20_000


PIN_STATE = ["pin_hash", "pin_version", "last_login_at", "fail_streak", "locked_until"]


def client_ip(request):
    ip = request.META.get("REMOTE_ADDR", "")
    if settings.TRUST_PROXY:
        # The proxy appends the peer it saw; everything to the left of that is client-supplied.
        ip = (request.META.get("HTTP_X_FORWARDED_FOR") or ip).rsplit(",", 1)[-1].strip()
    try:
        address = ipaddress.ip_address(ip.partition("%")[0])  # inet columns take no zone id
    except ValueError:
        return "0.0.0.0"  # still storable in an inet column; unidentifiable peers share one bucket
    return str(getattr(address, "ipv4_mapped", None) or address)


def ip_bucket(ip):
    address = ipaddress.ip_address(ip)
    if address.version == 4:
        return str(address)
    return str(ipaddress.ip_network((address, 64), strict=False).network_address)


def new_pin():
    return f"{secrets.randbelow(10**8):08d}"


def format_pin(pin):
    return f"{pin[:4]} {pin[4:]}"


@transaction.atomic
def set_pin(station, *, actor, ip):
    pin = new_pin()
    Station.objects.filter(pk=station.pk).update(
        pin_hash=make_password(pin, hasher=PinHasher.algorithm),
        pin_version=F("pin_version") + 1,
        last_login_at=None,
        fail_streak=0,
        locked_until=None,
    )
    station.refresh_from_db(fields=PIN_STATE)
    Change.objects.create(kind="pin", station=station, user=actor, ip=ip)
    return pin


def issue_unused_pins(stations, *, actor, ip):
    # ponytail: "unused" is judged on the rows as loaded. A first login racing this call gets its PIN
    # replaced, and the replacement is on the sheet being printed; lock per station if that ever hurts.
    return [
        (station, set_pin(station, actor=actor, ip=ip))
        for station in stations
        if not station.pin_hash or station.last_login_at is None
    ]


@transaction.atomic
def unlock(station, *, actor, ip):
    station.locked_until = None
    station.save(update_fields=["locked_until"])
    Change.objects.create(kind="unlock", station=station, user=actor, ip=ip)


class Login(enum.Enum):
    OK = "ok"
    BAD = "bad"
    LOCKED = "locked"
    THROTTLED = "throttled"
    NO_PIN = "no_pin"


def _lock_duration(fail_streak):
    """The lock earned by the fail_streak-th consecutive failure; None when it earns none."""
    if fail_streak % settings.PIN_LOCK_EVERY:
        return None
    steps = settings.PIN_LOCK_STEPS
    return steps[min(fail_streak // settings.PIN_LOCK_EVERY, len(steps)) - 1]


def _failures(bucket, since, *, staff):
    """PIN and staff failures are counted apart: each limit is tuned to its own kind of login."""
    return LoginFailure.objects.filter(ip=bucket, at__gt=since, station__isnull=staff).count()


def _record_failure(bucket, now, **who):
    LoginFailure.objects.create(at=now, ip=bucket, **who)
    LoginFailure.objects.filter(at__lt=now - timedelta(days=7)).delete()


def station_login(request, station, pin):
    bucket = ip_bucket(client_ip(request))
    with transaction.atomic():
        # One attempt per station at a time, judged on the row as it is now: parallel guesses cannot
        # get past the lock. Everything before check_password is cheap, so a locked station stays cheap.
        station.refresh_from_db(fields=PIN_STATE, from_queryset=Station.objects.select_for_update())
        now = timezone.now()
        if not station.pin_hash:
            return Login.NO_PIN
        if station.locked_until and station.locked_until > now:
            return Login.LOCKED
        if _failures(bucket, now - timedelta(hours=1), staff=False) >= settings.PIN_IP_FAILS_PER_HOUR:
            return Login.THROTTLED
        # The sheet prints "1234 5678"; typing it as printed is not a wrong PIN.
        if not check_password("".join(pin.split()), station.pin_hash):
            station.fail_streak += 1
            lock = _lock_duration(station.fail_streak)
            if lock:
                station.locked_until = now + lock
            station.save(update_fields=["fail_streak", "locked_until"])
            _record_failure(bucket, now, station=station)
            return Login.BAD
        station.fail_streak = 0
        station.locked_until = None
        station.last_login_at = now
        station.save(update_fields=["fail_streak", "locked_until", "last_login_at"])
    logout(request)
    request.session["station_id"] = station.pk
    request.session["pin_version"] = station.pin_version
    return Login.OK


def staff_login(request, username, password):
    bucket = ip_bucket(client_ip(request))
    now = timezone.now()
    if _failures(bucket, now - settings.STAFF_IP_WINDOW, staff=True) >= settings.STAFF_IP_FAILS:
        return Login.THROTTLED
    user = auth.authenticate(request, username=username, password=password)
    if user is None:
        _record_failure(bucket, now, username=username[:150])
        return Login.BAD
    logout(request)
    auth.login(request, user)
    request.session["staff_login_at"] = now.timestamp()
    return Login.OK


def staff_recheck(request, password):
    """The signed-in user's own password asked again (the «Парол» page): counted and throttled like staff_login."""
    bucket = ip_bucket(client_ip(request))
    now = timezone.now()
    if _failures(bucket, now - settings.STAFF_IP_WINDOW, staff=True) >= settings.STAFF_IP_FAILS:
        return Login.THROTTLED
    if request.user.check_password(password):
        return Login.OK
    _record_failure(bucket, now, username=request.user.username[:150])
    return Login.BAD


def logout(request):
    auth.logout(request)  # session.flush(), and request.user stops being the old user within this request


def current_station(request):
    station_id = request.session.get("station_id")
    if station_id is None:
        return None
    pin_version = request.session.get("pin_version")
    return Station.objects.filter(pk=station_id, pin_version=pin_version, active_to=None).first()


def station_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        request.station = current_station(request)
        if request.station is None:
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            # A staff session without the stamp did not come through staff_login: treat it as expired.
            age = timezone.now().timestamp() - request.session.get("staff_login_at", 0)
            if age < settings.STAFF_SESSION_HOURS * 3600:
                return view(request, *args, **kwargs)
            logout(request)
        return redirect("staff_login")

    return wrapper


def boshqarma_required(view):
    @staff_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_boshqarma:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper


def stations_for(user):
    stations = Station.objects.select_related("mtu").order_by("mtu__sort", "sort", "name")
    return stations if user.is_boshqarma else stations.filter(mtu_id=user.mtu_id)


def mtus_for(user):
    mtus = Mtu.objects.order_by("sort", "name")
    return mtus if user.is_boshqarma else mtus.filter(pk=user.mtu_id)


def scope(request):
    user = request.user
    stations = stations_for(user)
    if not user.is_boshqarma:
        return stations, user.mtu
    chosen = request.GET.get("mtu")
    if not chosen:
        return stations, None
    if not chosen.isascii() or not chosen.isdigit():
        raise Http404
    mtu = get_object_or_404(Mtu, pk=chosen)
    return stations.filter(mtu=mtu), mtu
