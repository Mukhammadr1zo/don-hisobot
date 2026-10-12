"""The write side of the balance chain: the only code that writes Entry rows (spec section 5)."""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from hisobot import periods
from hisobot.models import Change, Entry, Station

MESSAGES = {
    "period": "Давр нотўғри кўрсатилган.",
    "window": "Бу даврни ҳозир киритиб бўлмайди.",
    "inactive": "Станция фаол эмас.",
    "opening_required": "Давр бошидаги қолдиқни киритинг.",
    "before_opening": "Давр бошидаги қолдиқ киритилган даврдан олдинги даврга ёзув киритиб бўлмайди.",
    "negative": "Қолган вагонлар сони манфий бўлмаслиги керак. Рақамларни тузатинг ёки қолдиқни қўлда киритинг.",
    "stale": "Маълумот ўзгарган, қайта текширинг.",
    "scope": "Бу станция сизнинг МТУга тегишли эмас.",
    "range": "Туширилган ва кириб келган вагонлар сони 0 дан 999 гача, қолдиқ 0 дан 9999 гача бўлиши керак.",
}


class SaveError(Exception):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message or MESSAGES[code]
        super().__init__(self.message)


@dataclass
class SaveResult:
    entry: Entry
    changed: bool
    cascaded: int


def _values(entry):
    return {
        "unloaded": entry.unloaded,
        "arrived": entry.arrived,
        "remaining_calc": entry.remaining_calc,
        "remaining_override": entry.remaining_override,
    }


def prev_for(station, period_end) -> tuple[int | None, bool]:
    """(remaining the period starts from, whether the period right before it was skipped)."""
    before = periods.prev_end(period_end)
    last = station.entries.filter(period_end__lt=period_end).order_by("-period_end").first()
    gap = (
        station.opening_at is not None
        and periods.bounds(before)[0] >= station.opening_at
        and (last is None or last.period_end != before)
    )
    return (last.remaining if last else station.opening_remaining), gap


def _check_scope(actor, station):
    """Views already start from access.stations_for; the writer refuses a foreign MTU regardless."""
    if actor is not None and not actor.is_boshqarma and actor.mtu_id != station.mtu_id:
        raise SaveError("scope")


def _cascade(entries, running, cause):
    """Recompute `entries` (ordered by period_end) from `running`; returns how many rows changed."""
    changed = 0
    # ponytail: loads every later entry of the station (2 per day) though it usually stops
    # at the first; switch to .iterator() if a station ever carries decades of rows.
    for entry in entries:
        calc = running + entry.arrived - entry.unloaded
        if calc == entry.remaining_calc:
            break
        Change.objects.create(
            kind="entry", station_id=entry.station_id, entry=entry, user_id=cause.user_id, ip=cause.ip,
            cause=cause, old={"remaining_calc": entry.remaining_calc}, new={"remaining_calc": calc},
        )
        entry.remaining_calc = calc
        entry.version += 1
        # updated_at stays: it is the time of the last submission, shown as «Қабул қилинди 08:07».
        entry.save(update_fields=["remaining_calc", "version"])
        changed += 1
        if entry.remaining_override is not None:
            break
        running = calc
    return changed


def save_entry(*, station_id, period_end, unloaded, arrived, override=None, opening=None,
               form_version, form_prev, actor, ip, now=None) -> SaveResult:
    """Save one period of one station. actor None means the station itself (PIN session)."""
    now = now or timezone.now()
    with transaction.atomic():
        station = Station.objects.select_for_update().get(pk=station_id)
        _check_scope(actor, station)

        if not periods.is_period_end(period_end):
            raise SaveError("period")
        can_write = periods.station_can_write if actor is None else periods.staff_can_write
        if not can_write(period_end, now):
            raise SaveError("window")
        if actor is None and not station.is_active:
            raise SaveError("inactive")
        limits = [(unloaded, 999), (arrived, 999), (override, 9999), (opening, 9999)]
        if any(value is not None and not 0 <= value <= top for value, top in limits):
            raise SaveError("range")

        start = periods.bounds(period_end)[0]
        sets_opening = station.opening_at is None
        if sets_opening:
            if opening is None:
                raise SaveError("opening_required")
            station.opening_remaining = opening
            station.opening_at = station.active_from = start
            station.save(update_fields=["opening_remaining", "opening_at", "active_from"])
            Change.objects.create(
                kind="opening", station=station, user=actor, ip=ip, new={"opening_remaining": opening}
            )
        elif start < station.opening_at:
            raise SaveError("before_opening")

        prev = prev_for(station, period_end)[0]
        calc = prev + arrived - unloaded
        effective = calc if override is None else override

        entry = station.entries.filter(period_end=period_end).first()
        if entry and (entry.unloaded, entry.arrived, entry.remaining_override) == (unloaded, arrived, override):
            return SaveResult(entry, False, 0)
        if (entry.version if entry else 0) != form_version or (not sets_opening and prev != form_prev):
            raise SaveError("stale")
        if effective < 0:
            raise SaveError("negative")

        old = _values(entry) if entry else {}
        entry = entry or Entry(station=station, period_end=period_end, version=0)
        entry.unloaded, entry.arrived, entry.remaining_calc, entry.remaining_override = unloaded, arrived, calc, override
        entry.version += 1
        entry.save()
        change = Change.objects.create(
            kind="entry", station=station, entry=entry, user=actor, ip=ip, old=old, new=_values(entry)
        )
        later = station.entries.filter(period_end__gt=period_end).order_by("period_end")
        return SaveResult(entry, True, _cascade(later, effective, change))


def set_opening(*, station_id, value, actor, ip) -> int:
    """Staff correct the opening balance of a station that already has one; returns cascaded entries."""
    if actor is None:
        raise ValueError("set_opening is staff only")
    with transaction.atomic():
        station = Station.objects.select_for_update().get(pk=station_id)
        _check_scope(actor, station)
        if station.opening_at is None:
            raise SaveError("opening_required", "Давр бошидаги қолдиқ биринчи ёзув билан бирга киритилади.")
        if not 0 <= value <= 9999:
            raise SaveError("range")
        if value == station.opening_remaining:
            return 0
        change = Change.objects.create(
            kind="opening", station=station, user=actor, ip=ip,
            old={"opening_remaining": station.opening_remaining}, new={"opening_remaining": value},
        )
        station.opening_remaining = value
        station.save(update_fields=["opening_remaining"])
        return _cascade(station.entries.order_by("period_end"), value, change)


def recompute(station) -> dict[int, int]:
    """Entry id -> calc recomputed from the opening, ignoring the stored calcs (spec 5.8)."""
    calcs, running = {}, station.opening_remaining
    for entry in station.entries.order_by("period_end"):
        calcs[entry.id] = running + entry.arrived - entry.unloaded
        running = calcs[entry.id] if entry.remaining_override is None else entry.remaining_override
    return calcs
