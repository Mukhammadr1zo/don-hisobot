"""Станциялар: the roster, PIN sheets, station settings, and staff passwords (plan Task 8)."""

import secrets

from django import forms
from django.contrib import messages
from django.contrib.auth import password_validation, update_session_auth_hash
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import access, balance, roster
from .forms import REQUIRED
from .models import Change, Mtu, Station, User, validate_station_name
from .views_public import TOO_MANY

TAKEN = "Бу МТУда шундай номли станция бор."
HISTORY_KINDS = ["station", "pin", "unlock", "opening"]


def _number(label, top):
    message = f"0 дан {top} гача сон киритинг"
    return forms.IntegerField(
        label=label,
        min_value=0,
        max_value=top,
        error_messages={"required": REQUIRED, "invalid": message, "min_value": message, "max_value": message},
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )


class _NameField(forms.CharField):
    def to_python(self, value):
        # As roster._text reads a cell: any run of whitespace, a no-break space included, is one space.
        return " ".join(super().to_python(value).split())


def _name():
    # No max_length here: validate_station_name covers it, in Cyrillic.
    return _NameField(
        label="Станция номи",
        validators=[validate_station_name],
        error_messages={"required": REQUIRED},
        widget=forms.TextInput(attrs={"maxlength": 100, "autocomplete": "off"}),
    )


class AddForm(forms.Form):
    mtu = forms.ModelChoiceField(
        Mtu.objects.none(), label="МТУ", empty_label=None,
        error_messages={"required": REQUIRED, "invalid_choice": "МТУни танланг"},
    )
    name = _name()

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if user.is_boshqarma:
            self.fields["mtu"].queryset = access.mtus_for(user)
        else:
            del self.fields["mtu"]  # an MTU user adds to its own MTU, whatever the POST says


class EditForm(forms.Form):
    name = _name()
    sort = _number("Тартиб рақами", 999_999)


class OpeningForm(forms.Form):
    opening = _number("Давр бошидаги қолдиқ", 9999)


def _password(label, autocomplete):
    return forms.CharField(
        label=label,
        strip=False,
        error_messages={"required": REQUIRED},
        widget=forms.PasswordInput(attrs={"autocomplete": autocomplete}),
    )


class PasswordForm(forms.Form):
    current = _password("Жорий парол", "current-password")
    new = _password("Янги парол", "new-password")
    again = _password("Янги парол (такрор)", "new-password")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    def clean_current(self):
        # Throttled like the sign-in: a session left open must not become a way to guess its own password.
        result = access.staff_recheck(self.request, self.cleaned_data["current"])
        if result is access.Login.THROTTLED:
            raise forms.ValidationError(TOO_MANY)
        if result is not access.Login.OK:
            raise forms.ValidationError("Жорий парол нотўғри.")
        return self.cleaned_data["current"]

    def clean_new(self):
        new = self.cleaned_data["new"]
        if len(new) < 10:
            raise forms.ValidationError("Парол камида 10 белгидан иборат бўлиши керак.")
        try:
            password_validation.validate_password(new, self.request.user)
        except forms.ValidationError:
            # Django's own messages are English (USE_I18N is off): one Cyrillic message for all of them.
            raise forms.ValidationError(
                "Парол жуда оддий ёки логинга ўхшаш. Тахмин қилиш қийин парол танланг."
            ) from None
        return new

    def clean(self):
        new, again = self.cleaned_data.get("new"), self.cleaned_data.get("again")
        if new and again and new != again:
            self.add_error("again", "Пароллар бир хил эмас.")


def _station(request, station_id):
    return get_object_or_404(access.stations_for(request.user), pk=station_id)


def _ids(values):
    if not all(value.isascii() and value.isdigit() for value in values):
        raise Http404
    return {int(value) for value in values}


def _taken(mtu_id, name, exclude=None):
    return Station.objects.filter(mtu_id=mtu_id, name__iexact=name).exclude(pk=exclude).exists()


def _pin_state(station):
    if not station.pin_hash:
        return "none"
    return "used" if station.last_login_at else "unused"


def _add(request, form):
    mtu = form.cleaned_data["mtu"] if request.user.is_boshqarma else request.user.mtu
    name = form.cleaned_data["name"]
    with transaction.atomic():
        Mtu.objects.select_for_update().get(pk=mtu.pk)  # takes turns with the roster import (roster.py)
        if _taken(mtu.pk, name):
            form.add_error("name", TAKEN)
            return False
        top = Station.objects.filter(mtu=mtu).aggregate(top=Max("sort"))["top"] or 0
        Station.objects.create(mtu=mtu, name=name, sort=top + 1)
    messages.success(request, f"Станция қўшилди: {name}")
    return True


def _filter(request, mtu):
    """`?mtu=` for the boshqarma's filtered view, so links and the PIN form stay on the chosen MTU."""
    return f"?mtu={mtu.pk}" if mtu is not None and request.user.is_boshqarma else ""


@access.staff_required
def index(request):
    stations, mtu = access.scope(request)
    data = request.POST if request.method == "POST" else None
    form = AddForm(data, user=request.user, initial={"mtu": mtu})
    if request.method == "POST" and form.is_valid() and _add(request, form):
        return redirect(reverse("stations") + _filter(request, mtu))

    now = timezone.now()
    by_mtu = {item: [] for item in ([mtu] if mtu else access.mtus_for(request.user))}
    for station in stations:
        station.pin_state = _pin_state(station)
        station.hard_locked = access.hard_locked(station)
        station.locked = station.hard_locked or (station.locked_until is not None and station.locked_until > now)
        by_mtu[station.mtu].append(station)
    context = {
        "form": form,
        "groups": by_mtu.items(),
        "mtu": mtu,
        "mtus": access.mtus_for(request.user) if request.user.is_boshqarma else None,
        "query": _filter(request, mtu),
        "pin_url": reverse("pin_sheet") + _filter(request, mtu),
    }
    return render(request, "hisobot/staff/stations.html", context)


def _describe(change):
    old, new = change.old, change.new
    if change.kind == "pin":
        return "ПИН янгиланди"
    if change.kind == "unlock":
        return "Блокдан чиқарилди"
    if change.kind == "opening":
        before = old.get("opening_remaining")
        after = new["opening_remaining"]
        return f"Давр бошидаги қолдиқ: {after}" if before is None else f"Давр бошидаги қолдиқ: {before} → {after}"
    if "active_to" in new:
        return "Қайта ёқилди" if new["active_to"] is None else "Ўчирилди"
    labels = {"name": "Номи", "sort": "Тартиб рақами"}
    return "; ".join(f"{labels[key]}: {old[key]} → {new[key]}" for key in labels if key in new)


def _save_settings(request, station, form):
    new = {key: value for key, value in form.cleaned_data.items() if getattr(station, key) != value}
    if not new:
        return True
    if "name" in new and _taken(station.mtu_id, new["name"], exclude=station.pk):
        form.add_error("name", TAKEN)
        return False
    old = {key: getattr(station, key) for key in new}
    with transaction.atomic():
        for key, value in new.items():
            setattr(station, key, value)
        station.save(update_fields=list(new))
        Change.objects.create(
            kind="station", station=station, user=request.user, ip=access.client_ip(request), old=old, new=new
        )
    messages.success(request, "Сақланди")
    return True


def _save_opening(request, station, form):
    try:
        cascaded = balance.set_opening(
            station_id=station.pk, value=form.cleaned_data["opening"], actor=request.user, ip=access.client_ip(request)
        )
    except balance.SaveError as error:
        form.add_error("opening", error.message)
        return False
    messages.success(request, f"Сақланди. Қайта ҳисобланган ёзувлар: {cascaded}" if cascaded else "Сақланди")
    return True


@access.staff_required
def edit(request, station_id):
    station = _station(request, station_id)
    form = EditForm(initial={"name": station.name, "sort": station.sort})
    opening_form = OpeningForm(initial={"opening": station.opening_remaining})
    if request.method == "POST":
        if request.POST.get("form") == "opening":
            opening_form = OpeningForm(request.POST)
            saved = opening_form.is_valid() and _save_opening(request, station, opening_form)
        else:
            form = EditForm(request.POST)
            saved = form.is_valid() and _save_settings(request, station, form)
        if saved:
            return redirect("station_edit", station_id=station.pk)

    changes = (
        Change.objects.filter(station=station, kind__in=HISTORY_KINDS).select_related("user__mtu").order_by("-at", "-pk")
    )
    now = timezone.now()
    context = {
        "station": station,
        "pin_state": _pin_state(station),
        "hard_locked": access.hard_locked(station),
        "locked": access.hard_locked(station) or (station.locked_until is not None and station.locked_until > now),
        "form": form,
        "opening_form": opening_form,
        "history": [(change, _describe(change)) for change in changes],
    }
    return render(request, "hisobot/staff/station_edit.html", context)


@access.staff_required
@require_POST
def toggle(request, station_id):
    """The form says which state it wants (active=0 or 1): a double click or a stale page must not flip it back."""
    with transaction.atomic():
        station = get_object_or_404(access.stations_for(request.user).select_for_update(of=("self",)), pk=station_id)
        want = request.POST.get("active")
        if want not in ("0", "1"):
            raise Http404
        if station.is_active == (want == "1"):
            messages.info(request, "Станция аллақачон фаол." if station.is_active else "Станция аллақачон ўчирилган.")
            return redirect("station_edit", station_id=station.pk)
        now = timezone.now()
        if station.is_active:
            old, new = {"active_to": None}, {"active_to": now.isoformat()}
            station.active_to = now
            fields = ["active_to"]
            text = "Станция ўчирилди. Унга кириш ёпилди."
        else:
            old = {"active_from": station.active_from.isoformat(), "active_to": station.active_to.isoformat()}
            new = {"active_from": now.isoformat(), "active_to": None}
            station.active_from, station.active_to = now, None
            fields = ["active_from", "active_to"]
            text = "Станция қайта ёқилди."
        station.save(update_fields=fields)
        Change.objects.create(
            kind="station", station=station, user=request.user, ip=access.client_ip(request), old=old, new=new
        )
    messages.success(request, text)
    return redirect("station_edit", station_id=station.pk)


def _sheet(request, issued, *, renewed=False):
    """The printable PIN sheet. The plaintext PINs exist only in this response: never in a message or the log."""
    strips = [
        {
            "station": station,
            "url": request.build_absolute_uri(reverse("station_login", args=[station.pk])),
            "pin": pin,
        }
        for station, pin in issued
    ]
    context = {"strips": strips, "renewed": renewed, "now": timezone.now()}
    return render(request, "hisobot/staff/pin_sheet.html", context)


@access.staff_required
@require_POST
@never_cache
def regenerate_pin(request, station_id):
    station = _station(request, station_id)
    if not station.is_active:
        messages.error(request, "Ўчирилган станцияга ПИН берилмайди. Аввал уни қайта ёқинг.")
        return redirect("station_edit", station_id=station.pk)
    pin = access.set_pin(station, actor=request.user, ip=access.client_ip(request))
    return _sheet(request, [(station, pin)], renewed=True)


@access.staff_required
@require_POST
def unlock(request, station_id):
    station = _station(request, station_id)
    _, mtu = access.scope(request)  # the list's ?mtu=, to go back to it
    access.unlock(station, actor=request.user, ip=access.client_ip(request))
    messages.success(request, f"{station.name}: блокдан чиқарилди")
    return redirect(reverse("stations") + _filter(request, mtu))


@access.staff_required
@require_POST
@never_cache
def pin_sheet(request):
    """«ПИН яратиш»: new PINs for the active stations whose PIN was never used; the checked ones only, if any.

    The boshqarma's `?mtu=` limits it to that MTU: another MTU's printed but unused sheet must keep working.
    """
    stations, mtu = access.scope(request)
    chosen = _ids(request.POST.getlist("station"))
    if chosen:
        if stations.filter(pk__in=chosen).count() != len(chosen):
            raise Http404
        stations = stations.filter(pk__in=chosen)
    issued = access.issue_unused_pins(stations.filter(active_to=None), actor=request.user, ip=access.client_ip(request))
    if not issued:
        messages.warning(
            request, "ПИН берилмаган ёки ишлатилмаган фаол станция йўқ. Ишлатилган ПИН станция саҳифасида янгиланади."
        )
        return redirect(reverse("stations") + _filter(request, mtu))
    return _sheet(request, issued)


@access.staff_required
def import_roster(request):
    errors = []
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            errors = ["Файлни танланг."]
        else:
            try:
                added = roster.import_roster(upload, user=request.user)
            except roster.RosterError as error:
                errors = error.errors
            else:
                text = f"{added} та станция қўшилди" if added else "Янги станция йўқ: ҳаммаси аввал қўшилган"
                messages.success(request, text)
                return redirect("stations")
    return render(request, "hisobot/staff/import.html", {"errors": errors, "mtus": access.mtus_for(request.user)})


@access.staff_required
def password_change(request):
    form = PasswordForm(request.POST if request.method == "POST" else None, request=request)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["new"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)  # this session stays; the account's other sessions end
        messages.success(request, "Парол ўзгартирилди")
        return redirect("staff_now")
    return render(request, "hisobot/staff/password_change.html", {"form": form})


@access.boshqarma_required
@never_cache
def mtu_passwords(request):
    accounts = User.objects.filter(mtu__isnull=False).select_related("mtu").order_by("mtu__sort", "username")
    reset = None
    if request.method == "POST":
        (account_id,) = _ids([request.POST.get("user", "")])
        account = get_object_or_404(accounts, pk=account_id)
        password = secrets.token_urlsafe(12)  # 12 random bytes are exactly 16 characters
        account.set_password(password)
        account.save(update_fields=["password"])  # also ends that account's sessions (session auth hash)
        reset = (account, password)
    return render(request, "hisobot/staff/mtu_passwords.html", {"accounts": accounts, "reset": reset})
