from datetime import UTC, date, datetime, timedelta
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from hisobot import periods
from hisobot.models import Change, Entry, Mtu, Station, User, validate_station_name
from hisobot.periods import TZ


def tk(day, hour, minute=0, second=0, micro=0):
    """Tashkent wall-clock time in October 2026."""
    return datetime(2026, 10, day, hour, minute, second, micro, tzinfo=TZ)


class PeriodsTests(SimpleTestCase):
    def test_label_ends_for_bounds(self):
        self.assertEqual(periods.label(tk(12, 8)), (date(2026, 10, 12), 1))
        self.assertEqual(periods.label(tk(12, 20)), (date(2026, 10, 12), 2))
        self.assertEqual(periods.ends_for(date(2026, 10, 12)), (tk(12, 8), tk(12, 20)))
        self.assertEqual(periods.bounds(tk(12, 8)), (tk(11, 20), tk(12, 8)))

    def test_prev_and_next_end(self):
        self.assertEqual(periods.prev_end(tk(12, 8)), tk(11, 20))
        self.assertEqual(periods.next_end(tk(12, 8)), tk(12, 20))
        self.assertEqual(periods.next_end(tk(12, 20)), tk(13, 8))

    def test_last_ended(self):
        for now, expected in [
            (tk(12, 7, 59, 59), tk(11, 20)),
            (tk(12, 8), tk(12, 8)),
            (tk(12, 19, 59, 59), tk(12, 8)),
            (tk(12, 20), tk(12, 20)),
            (tk(13, 0, 30), tk(12, 20)),
        ]:
            with self.subTest(now=now):
                self.assertEqual(periods.last_ended(now), expected)

    def test_open_for_station(self):
        self.assertEqual(periods.open_for_station(tk(12, 7, 59, 59)), [tk(11, 20), tk(11, 8)])
        self.assertEqual(periods.open_for_station(tk(12, 8)), [tk(12, 8), tk(11, 20)])

    def test_station_can_write(self):
        pe = tk(12, 8)
        self.assertFalse(periods.station_can_write(pe, tk(12, 7, 59, 59)))
        self.assertTrue(periods.station_can_write(pe, tk(12, 8)))
        self.assertTrue(periods.station_can_write(pe, tk(13, 7, 59, 59)))
        self.assertFalse(periods.station_can_write(pe, tk(13, 8)))

    def test_staff_can_write(self):
        pe = tk(12, 8)
        self.assertFalse(periods.staff_can_write(pe, tk(12, 7, 59, 59)))
        self.assertTrue(periods.staff_can_write(pe, tk(12, 8)))
        self.assertTrue(periods.staff_can_write(pe, pe + timedelta(days=365)))

    def test_utc_inputs_give_identical_results(self):
        night = datetime(2026, 10, 12, 3, tzinfo=UTC)
        day = datetime(2026, 10, 12, 15, tzinfo=UTC)
        second = timedelta(seconds=1)
        self.assertTrue(periods.is_period_end(night))
        self.assertTrue(periods.is_period_end(day))
        self.assertEqual(periods.label(night), (date(2026, 10, 12), 1))
        self.assertEqual(periods.label(day), (date(2026, 10, 12), 2))
        self.assertEqual(periods.bounds(night), (tk(11, 20), tk(12, 8)))
        self.assertEqual(periods.last_ended(night - second), tk(11, 20))
        self.assertEqual(periods.last_ended(night), tk(12, 8))
        self.assertEqual(periods.last_ended(day - second), tk(12, 8))
        self.assertEqual(periods.last_ended(day), tk(12, 20))
        # 13.10 00:30 in Tashkent is still 12.10 on the UTC calendar
        self.assertEqual(periods.last_ended(datetime(2026, 10, 12, 19, 30, tzinfo=UTC)), tk(12, 20))
        self.assertEqual(periods.open_for_station(night - second), [tk(11, 20), tk(11, 8)])
        self.assertEqual(periods.open_for_station(night), [tk(12, 8), tk(11, 20)])
        self.assertFalse(periods.station_can_write(night, night - second))
        self.assertTrue(periods.station_can_write(night, night))
        self.assertTrue(periods.station_can_write(night, night + timedelta(hours=24) - second))
        self.assertFalse(periods.station_can_write(night, night + timedelta(hours=24)))
        self.assertEqual(periods.to_slug(night), "2026101208")
        self.assertEqual(periods.to_slug(day), "2026101220")
        self.assertEqual(periods.from_slug("2026101208"), night)

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            periods.local(datetime(2026, 10, 12, 8))  # noqa: DTZ001 - naive on purpose

    def test_label_and_slug_reject_non_boundaries(self):
        for fn in (periods.label, periods.to_slug):
            with self.subTest(fn=fn.__name__), self.assertRaises(ValueError):
                fn(tk(12, 8, 30))

    def test_is_period_end(self):
        self.assertTrue(periods.is_period_end(tk(12, 8)))
        self.assertTrue(periods.is_period_end(tk(12, 20)))
        self.assertFalse(periods.is_period_end(tk(12, 8, 0, 1)))
        self.assertFalse(periods.is_period_end(tk(12, 8, 30)))
        self.assertFalse(periods.is_period_end(tk(12, 8, 0, 0, 1)))

    def test_slug_roundtrip(self):
        self.assertEqual(periods.to_slug(tk(12, 8)), "2026101208")
        for pe in (tk(12, 8), tk(12, 20)):
            self.assertEqual(periods.from_slug(periods.to_slug(pe)), pe)

    def test_from_slug_rejects_anything_else(self):
        for bad in ["2026101209", "abc", "20261012", "", "2026023008", "2026101208\n", "0001010108", None]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                periods.from_slug(bad)


class ModelLogicTests(SimpleTestCase):
    def test_validate_station_name(self):
        for ok in ["Чуқурсой", "А" * 100, "Тошкент-Товар", "1-разъезд"]:
            validate_station_name(ok)
        for bad in ["", "   ", "  Хамза", "Хамза ", "А" * 101, "=SUM(A1)", "+998", "-1", "@станция", "  =1+1", "\t=1+1",
                    "Альфа\x0bбекат", "Альфа\nбекат", "Альфа\x01бекат", "Альфа\u200bбекат"]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_station_name(bad)

    def test_station_is_active_and_expects(self):
        station = Station(active_from=tk(12, 8))
        self.assertTrue(station.is_active)
        self.assertFalse(station.expects(tk(12, 8)))
        self.assertTrue(station.expects(tk(12, 20)))
        self.assertTrue(station.expects(tk(20, 8)))
        station.active_to = tk(13, 8)
        self.assertFalse(station.is_active)
        self.assertTrue(station.expects(tk(13, 8)))
        self.assertFalse(station.expects(tk(13, 20)))

    def test_entry_remaining_and_overridden(self):
        entry = Entry(remaining_calc=-2)
        self.assertEqual(entry.remaining, -2)
        self.assertFalse(entry.overridden)
        entry.remaining_override = 0
        self.assertEqual(entry.remaining, 0)
        self.assertTrue(entry.overridden)
        entry.remaining_calc = 0
        self.assertEqual(entry.remaining, 0)
        self.assertFalse(entry.overridden)

    def test_user_is_boshqarma(self):
        self.assertTrue(User().is_boshqarma)
        self.assertFalse(User(mtu_id=1).is_boshqarma)


class DatabaseGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mtu = Mtu.objects.create(name="Тошкент", sort=1)
        cls.station = Station.objects.create(mtu=cls.mtu, name="Чуқурсой")

    def entry(self, period_end, **values):
        values = {"unloaded": 1, "arrived": 2, "remaining_calc": 1} | values
        return Entry.objects.create(station=self.station, period_end=period_end, **values)

    def assertRejected(self, action, *args, **kwargs):
        with self.assertRaises(IntegrityError), transaction.atomic():
            action(*args, **kwargs)

    def test_entry_period_end_must_be_a_tashkent_boundary(self):
        self.entry(tk(12, 8))
        self.entry(datetime(2026, 10, 12, 15, tzinfo=UTC))
        for bad in [tk(12, 8, 30), tk(12, 8, 0, 1), tk(12, 20, 0, 0, 1), datetime(2026, 10, 12, 8, tzinfo=UTC)]:
            with self.subTest(bad=bad):
                self.assertRejected(self.entry, bad)

    def test_entry_cannot_be_deleted(self):
        entry = self.entry(tk(12, 8))
        self.assertRejected(entry.delete)
        self.assertRejected(Entry.objects.filter(pk=entry.pk).delete)
        self.assertTrue(Entry.objects.filter(pk=entry.pk).exists())

    def test_change_cannot_be_updated_or_deleted(self):
        change = Change.objects.create(kind="station", station=self.station, new={"name": "Чуқурсой"})
        change.kind = "pin"
        self.assertRejected(change.save)
        self.assertRejected(Change.objects.filter(pk=change.pk).update, new={})
        self.assertRejected(change.delete)
        change.refresh_from_db()
        self.assertEqual((change.kind, change.new), ("station", {"name": "Чуқурсой"}))

    def test_entry_is_unique_per_station_and_period(self):
        self.entry(tk(12, 8))
        self.assertRejected(self.entry, tk(12, 8))

    def test_entry_value_limits(self):
        self.entry(tk(12, 8), unloaded=999, arrived=999, remaining_calc=-999, remaining_override=9999)
        self.entry(tk(12, 20), unloaded=0, arrived=0, remaining_calc=0, remaining_override=0)
        for bad in [{"unloaded": 1000}, {"arrived": 1000}, {"remaining_override": 10000},
                    {"unloaded": -1}, {"arrived": -1}, {"remaining_override": -1}]:
            with self.subTest(bad=bad):
                self.assertRejected(self.entry, tk(13, 8), **bad)

    def test_station_name_is_unique_per_mtu_ignoring_case(self):
        self.assertRejected(Station.objects.create, mtu=self.mtu, name="чуқурсой")
        other = Mtu.objects.create(name="Қўқон", sort=2)
        Station.objects.create(mtu=other, name="Чуқурсой")

    def test_station_opening_fields_are_set_together(self):
        self.assertRejected(Station.objects.create, mtu=self.mtu, name="А", opening_remaining=5)
        self.assertRejected(Station.objects.create, mtu=self.mtu, name="Б", opening_at=tk(11, 20))
        Station.objects.create(mtu=self.mtu, name="В", opening_remaining=0, opening_at=tk(11, 20))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SeedTests(TestCase):
    def test_seed_creates_accounts_once(self):
        out = StringIO()
        call_command("seed", stdout=out)
        self.assertEqual(
            list(Mtu.objects.order_by("sort").values_list("sort", "name")),
            [(1, "Тошкент"), (2, "Қўқон"), (3, "Бухоро"), (4, "Қўнғирот"), (5, "Қарши"), (6, "Термиз")],
        )
        printed = dict(line.split(": ") for line in out.getvalue().splitlines())
        self.assertEqual(
            sorted(printed),
            ["boshqarma", "buxoro", "qarshi", "qongirot", "qoqon", "termiz", "toshkent"],
        )
        for username, password in printed.items():
            self.assertEqual(len(password), 16)
            self.assertTrue(User.objects.get(username=username).check_password(password))
        self.assertEqual(len(set(printed.values())), 7)
        self.assertTrue(User.objects.get(username="boshqarma").is_boshqarma)
        self.assertEqual(
            {u.username: u.mtu.name for u in User.objects.exclude(mtu=None).select_related("mtu")},
            {"toshkent": "Тошкент", "qoqon": "Қўқон", "buxoro": "Бухоро",
             "qongirot": "Қўнғирот", "qarshi": "Қарши", "termiz": "Термиз"},
        )

        again = StringIO()
        call_command("seed", stdout=again)
        self.assertEqual(again.getvalue(), "")
        self.assertEqual((Mtu.objects.count(), User.objects.count()), (6, 7))
        self.assertTrue(User.objects.get(username="qoqon").check_password(printed["qoqon"]))
