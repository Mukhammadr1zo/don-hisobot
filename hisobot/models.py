from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


def validate_station_name(value):
    # Control characters (a line break pasted from Word, say) cannot be stored in the Excel export.
    if (
        value != value.strip()
        or not 1 <= len(value) <= 100
        or value[0] in "=+-@"
        or any(not ch.isprintable() for ch in value)
    ):
        raise ValidationError(
            "Станция номи 1–100 белгидан иборат бўлиши, = + - @ белгиси билан бошланмаслиги"
            " ва махсус белгиларсиз бўлиши керак."
        )


class Mtu(models.Model):
    name = models.CharField(max_length=50, unique=True)
    sort = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class User(AbstractUser):
    mtu = models.ForeignKey(Mtu, null=True, blank=True, on_delete=models.PROTECT)

    @property
    def is_boshqarma(self):
        return self.mtu_id is None

    @property
    def display_name(self):
        """The account as pages and the export name it, in Cyrillic; the Latin login is only typed."""
        return "Бошқарма" if self.is_boshqarma else f"{self.mtu.name} МТУ"


class Station(models.Model):
    mtu = models.ForeignKey(Mtu, on_delete=models.PROTECT, related_name="stations")
    name = models.CharField(max_length=100, validators=[validate_station_name])
    sort = models.PositiveIntegerField(default=0)
    active_from = models.DateTimeField(default=timezone.now)
    active_to = models.DateTimeField(null=True, blank=True)
    opening_remaining = models.PositiveIntegerField(null=True, blank=True)
    opening_at = models.DateTimeField(null=True, blank=True)
    pin_hash = models.CharField(max_length=128, blank=True, default="")
    pin_version = models.PositiveIntegerField(default=0)
    last_login_at = models.DateTimeField(null=True, blank=True)
    fail_streak = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "mtu",
                name="station_name_mtu_uniq",
                violation_error_message="Бу МТУда шундай номли станция бор.",
            ),
            models.CheckConstraint(
                condition=Q(opening_remaining__isnull=True, opening_at__isnull=True)
                | Q(opening_remaining__isnull=False, opening_at__isnull=False),
                name="station_opening_pair",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.active_to is None

    def expects(self, pe):
        return self.active_from < pe and (self.active_to is None or pe <= self.active_to)


class Entry(models.Model):
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="entries")
    period_end = models.DateTimeField()
    unloaded = models.PositiveSmallIntegerField()
    arrived = models.PositiveSmallIntegerField()
    remaining_calc = models.IntegerField()
    remaining_override = models.PositiveIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["station", "period_end"], name="entry_station_period_uniq"),
            models.CheckConstraint(condition=Q(unloaded__lte=999), name="entry_unloaded_max"),
            models.CheckConstraint(condition=Q(arrived__lte=999), name="entry_arrived_max"),
            models.CheckConstraint(condition=Q(remaining_override__lte=9999), name="entry_override_max"),
        ]

    @property
    def remaining(self):
        return self.remaining_calc if self.remaining_override is None else self.remaining_override

    @property
    def overridden(self):
        return self.remaining_override is not None and self.remaining_override != self.remaining_calc


class Change(models.Model):
    KINDS = ["entry", "opening", "station", "pin", "unlock"]

    at = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=10, choices=[(k, k) for k in KINDS])
    station = models.ForeignKey(Station, on_delete=models.PROTECT)
    entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    ip = models.GenericIPAddressField(null=True, blank=True)
    cause = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    old = models.JSONField(default=dict)
    new = models.JSONField(default=dict)


class LoginFailure(models.Model):
    at = models.DateTimeField(default=timezone.now)
    ip = models.GenericIPAddressField()
    station = models.ForeignKey(Station, null=True, blank=True, on_delete=models.SET_NULL)
    username = models.CharField(max_length=150, blank=True, default="")
