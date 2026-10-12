import threading
from contextlib import contextmanager
from datetime import timedelta

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from hisobot import periods
from hisobot.balance import SaveError, prev_for, recompute, save_entry, set_opening
from hisobot.models import Change, Entry, Mtu, Station, User
from hisobot.tests.test_periods import tk

IP = "10.0.0.1"
SECOND = timedelta(seconds=1)
DAY = timedelta(hours=24)

P1, P2, P3 = tk(12, 8), tk(12, 20), tk(13, 8)
START = tk(11, 20)  # the start of P1


def make_station(name, opening=None):
    mtu, _ = Mtu.objects.get_or_create(name="Тошкент")
    opened = {} if opening is None else {"opening_remaining": opening, "opening_at": START, "active_from": START}
    return Station.objects.create(mtu=mtu, name=name, **opened)


def stored_calcs(station):
    return list(station.entries.order_by("period_end").values_list("remaining_calc", flat=True))


class SaveEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create(username="toshkent")
        cls.station = make_station("Чуқурсой", opening=10)
        cls.fresh = make_station("Хамза")

    def tearDown(self):
        # Spec 5.8: recomputing the chain from the opening reproduces every stored calc.
        for station in Station.objects.all():
            self.assertEqual(recompute(station), dict(station.entries.values_list("id", "remaining_calc")))

    def mk(self, station, pe, unloaded, arrived, override=None, **kwargs):
        """save_entry as a form that was rendered just now would call it; staff, long after pe."""
        existing = station.entries.filter(period_end=pe).first()
        kwargs.setdefault("actor", self.staff)
        kwargs.setdefault("form_version", existing.version if existing else 0)
        kwargs.setdefault("form_prev", prev_for(Station.objects.get(pk=station.pk), pe)[0])
        kwargs.setdefault("now", pe + timedelta(days=30))
        kwargs.setdefault("ip", IP)
        return save_entry(
            station_id=station.pk, period_end=pe, unloaded=unloaded, arrived=arrived, override=override, **kwargs
        )

    @contextmanager
    def refuses(self, code):
        with self.assertRaises(SaveError) as caught:
            yield
        self.assertEqual(caught.exception.code, code)
        self.assertRegex(caught.exception.message, "[а-яўқғҳ]")
        self.assertNotRegex(caught.exception.message, "[A-Za-z]")

    def test_mtu_user_cannot_write_another_mtus_station(self):
        other = User.objects.create(username="qarshi", mtu=Mtu.objects.create(name="Қарши"))
        with self.refuses("scope"):
            self.mk(self.station, P1, 1, 1, actor=other)
        with self.refuses("scope"):
            set_opening(station_id=self.station.pk, value=3, actor=other, ip=IP)
        own = User.objects.create(username="tosh2", mtu=self.station.mtu)
        self.assertTrue(self.mk(self.station, P1, 1, 1, actor=own).changed)
        self.assertEqual(Entry.objects.count(), 1)

    def test_cascade_keeps_the_submission_time(self):
        self.mk(self.station, P1, 1, 1)
        later = self.mk(self.station, P2, 0, 0).entry
        self.assertEqual(self.mk(self.station, P1, 3, 1).cascaded, 1)
        fresh = Entry.objects.get(pk=later.pk)
        self.assertEqual((fresh.remaining_calc, fresh.updated_at), (8, later.updated_at))

    def test_first_entry_requires_opening(self):
        self.assertEqual(prev_for(self.fresh, P1), (None, False))
        with self.refuses("opening_required"):
            self.mk(self.fresh, P1, 5, 3)
        self.assertEqual((Entry.objects.count(), Change.objects.count()), (0, 0))

    def test_first_entry_sets_opening(self):
        result = self.mk(self.fresh, P1, 5, 3, opening=10)
        self.assertEqual((result.changed, result.cascaded), (True, 0))
        self.assertEqual((result.entry.remaining_calc, result.entry.version), (8, 1))
        self.fresh.refresh_from_db()
        self.assertEqual(
            (self.fresh.opening_remaining, self.fresh.opening_at, self.fresh.active_from), (10, START, START)
        )
        opening, entry = Change.objects.order_by("id")
        self.assertEqual((opening.kind, opening.station, opening.entry, opening.old), ("opening", self.fresh, None, {}))
        self.assertEqual(opening.new, {"opening_remaining": 10})
        self.assertEqual((entry.kind, entry.station, entry.entry, entry.old), ("entry", self.fresh, result.entry, {}))
        self.assertEqual(entry.new, {"unloaded": 5, "arrived": 3, "remaining_calc": 8, "remaining_override": None})
        self.assertEqual((entry.user, entry.ip, entry.cause), (self.staff, IP, None))

    def test_refused_first_entry_leaves_no_opening(self):
        with self.refuses("negative"):
            self.mk(self.fresh, P1, 5, 3, opening=1)
        self.fresh.refresh_from_db()
        self.assertIsNone(self.fresh.opening_at)
        self.assertEqual((Entry.objects.count(), Change.objects.count()), (0, 0))

    def test_opening_is_ignored_once_set(self):
        self.mk(self.station, P1, 5, 3, opening=500)
        self.station.refresh_from_db()
        self.assertEqual((self.station.opening_remaining, stored_calcs(self.station)), (10, [8]))
        self.assertFalse(Change.objects.filter(kind="opening").exists())

    def test_chain_of_three(self):
        self.mk(self.station, P1, 5, 3)
        self.mk(self.station, P2, 2, 4)
        self.mk(self.station, P3, 10, 1)
        self.assertEqual(stored_calcs(self.station), [8, 10, 1])

    def test_gap_without_any_earlier_entry(self):
        # Nothing entered yet: the first period after the opening has no gap, the one after it has.
        self.assertEqual(prev_for(self.station, P2), (10, True))
        self.mk(self.station, P1, 5, 3)
        self.assertEqual(prev_for(self.station, P2), (8, False))

    def test_skipped_period_then_filled(self):
        self.mk(self.station, P1, 5, 3)
        third = self.mk(self.station, P3, 1, 0).entry
        self.assertEqual(third.remaining_calc, 7)
        self.assertEqual(prev_for(self.station, P1), (10, False))
        self.assertEqual(prev_for(self.station, P2), (8, False))
        self.assertEqual(prev_for(self.station, P3), (8, True))

        filled = self.mk(self.station, P2, 2, 0)
        self.assertEqual((filled.entry.remaining_calc, filled.cascaded), (6, 1))
        self.assertEqual(prev_for(self.station, P3), (6, False))
        third.refresh_from_db()
        self.assertEqual((third.remaining_calc, third.version), (5, 2))
        cascade = Change.objects.get(entry=third, cause__isnull=False)
        self.assertEqual(cascade.cause, Change.objects.get(entry=filled.entry))
        self.assertEqual((cascade.kind, cascade.old, cascade.new), ("entry", {"remaining_calc": 7}, {"remaining_calc": 5}))
        self.assertEqual((cascade.user, cascade.ip), (self.staff, IP))

    def test_editing_the_first_recomputes_the_rest(self):
        self.mk(self.station, P1, 5, 3)
        self.mk(self.station, P2, 2, 4)
        self.mk(self.station, P3, 10, 1)
        result = self.mk(self.station, P1, 5, 5)
        self.assertEqual(result.cascaded, 2)
        self.assertEqual(stored_calcs(self.station), [10, 12, 3])
        versions = self.station.entries.order_by("period_end").values_list("version", flat=True)
        self.assertEqual(list(versions), [2, 2, 2])

    def test_override_anchors_the_chain(self):
        self.mk(self.station, P1, 5, 3)
        second = self.mk(self.station, P2, 2, 4, override=4).entry
        third = self.mk(self.station, P3, 1, 0).entry
        self.assertEqual(stored_calcs(self.station), [8, 10, 3])

        result = self.mk(self.station, P1, 5, 5)
        self.assertEqual(result.cascaded, 1)
        second.refresh_from_db()
        third.refresh_from_db()
        self.assertEqual((second.remaining_calc, second.remaining, second.version), (12, 4, 2))
        self.assertEqual((third.remaining_calc, third.version), (3, 1))

        # The anchor's own numbers change but its effective value does not: nothing downstream moves.
        self.assertEqual(self.mk(self.station, P2, 3, 4, override=4).cascaded, 0)
        third.refresh_from_db()
        self.assertEqual((third.remaining_calc, third.version), (3, 1))
        self.assertEqual(Change.objects.filter(entry=third).count(), 1)

    def test_override_equal_to_calc_is_stored_and_can_be_cleared(self):
        entry = self.mk(self.station, P1, 5, 3, override=8).entry
        self.assertEqual((entry.remaining_calc, entry.remaining_override, entry.overridden), (8, 8, False))
        result = self.mk(self.station, P1, 5, 3, override=None)
        self.assertEqual((result.changed, result.entry.remaining_override, result.entry.version), (True, None, 2))

    def test_negative_direct_save_needs_an_override(self):
        with self.refuses("negative"):
            self.mk(self.station, P1, 11, 0)
        self.assertEqual((Entry.objects.count(), Change.objects.count()), (0, 0))
        entry = self.mk(self.station, P1, 11, 0, override=0).entry
        self.assertEqual((entry.remaining_calc, entry.remaining), (-1, 0))

    def test_upstream_correction_may_leave_a_later_calc_negative(self):
        self.mk(self.station, P1, 0, 5)
        self.mk(self.station, P2, 12, 0)
        result = self.mk(self.station, P1, 0, 0)
        self.assertEqual(result.cascaded, 1)
        self.assertEqual(stored_calcs(self.station), [10, -2])

    def test_identical_resubmit_writes_nothing(self):
        self.mk(self.station, P1, 5, 3)
        changes = Change.objects.count()
        result = self.mk(self.station, P1, 5, 3, form_version=99)
        self.assertEqual((result.changed, result.cascaded, result.entry.version), (False, 0, 1))
        self.assertEqual(Change.objects.count(), changes)
        self.assertEqual(Entry.objects.get().version, 1)

    def test_stale_version(self):
        self.mk(self.station, P1, 5, 3)
        with self.refuses("stale"):
            self.mk(self.station, P1, 6, 3, form_version=0)
        entry = Entry.objects.get()
        self.assertEqual((entry.unloaded, entry.version), (5, 1))

    def test_stale_prev(self):
        self.mk(self.station, P1, 5, 3)
        shown, _ = prev_for(self.station, P2)
        self.mk(self.station, P1, 4, 3)
        with self.refuses("stale"):
            self.mk(self.station, P2, 1, 0, form_prev=shown)
        self.assertFalse(Entry.objects.filter(period_end=P2).exists())

    def test_station_window(self):
        for now in (P1 + DAY, P1 - SECOND):
            with self.subTest(now=now), self.refuses("window"):
                self.mk(self.station, P1, 5, 3, actor=None, now=now)
        self.assertEqual(Entry.objects.count(), 0)
        entry = self.mk(self.station, P1, 5, 3, actor=None, now=P1 + DAY - SECOND).entry
        self.assertIsNone(Change.objects.get(entry=entry).user)

    def test_staff_window(self):
        with self.refuses("window"):
            self.mk(self.station, P1, 5, 3, now=P1 - SECOND)
        self.assertTrue(self.mk(self.station, P1, 5, 3, now=P1 + timedelta(days=365)).changed)

    def test_now_defaults_to_the_clock(self):
        latest = periods.last_ended(timezone.now())
        form = {"station_id": self.fresh.pk, "unloaded": 5, "arrived": 3, "opening": 10,
                "form_version": 0, "form_prev": None, "actor": None, "ip": IP}
        with self.refuses("window"):
            save_entry(period_end=latest - 3 * DAY, **form)
        self.assertTrue(save_entry(period_end=latest, **form).changed)

    def test_inactive_station_is_closed_to_the_station_only(self):
        Station.objects.filter(pk=self.station.pk).update(active_to=P1)
        with self.refuses("inactive"):
            self.mk(self.station, P1, 5, 3, actor=None, now=P1 + SECOND)
        self.assertTrue(self.mk(self.station, P1, 5, 3, now=P1 + SECOND).changed)

    def test_before_opening(self):
        with self.refuses("before_opening"):
            self.mk(self.station, START, 0, 0)
        self.assertEqual(Entry.objects.count(), 0)

    def test_not_a_period_end(self):
        with self.refuses("period"):
            self.mk(self.station, tk(12, 8, 30), 5, 3)

    def test_values_out_of_range(self):
        for bad in ({"unloaded": 1000}, {"unloaded": -1}, {"arrived": 1000}, {"arrived": -1},
                    {"override": 10000}, {"override": -1}):
            with self.subTest(bad=bad), self.refuses("range"):
                self.mk(self.station, P1, **({"unloaded": 5, "arrived": 3} | bad))
        for opening in (10000, -1):
            with self.subTest(opening=opening), self.refuses("range"):
                self.mk(self.fresh, P1, 5, 3, opening=opening)
        self.assertEqual((Entry.objects.count(), Change.objects.count()), (0, 0))

    def test_set_opening_cascades_and_logs(self):
        self.mk(self.station, P1, 5, 3)
        self.mk(self.station, P2, 2, 4)
        self.assertEqual(set_opening(station_id=self.station.pk, value=20, actor=self.staff, ip=IP), 2)
        self.station.refresh_from_db()
        self.assertEqual((self.station.opening_remaining, self.station.opening_at), (20, START))
        self.assertEqual(stored_calcs(self.station), [18, 20])
        opening = Change.objects.get(kind="opening")
        self.assertEqual((opening.old, opening.new), ({"opening_remaining": 10}, {"opening_remaining": 20}))
        self.assertEqual((opening.station, opening.user, opening.ip), (self.station, self.staff, IP))
        self.assertEqual(Change.objects.filter(kind="entry", cause=opening).count(), 2)

        # Nothing changed, so nothing to log.
        self.assertEqual(set_opening(station_id=self.station.pk, value=20, actor=self.staff, ip=IP), 0)
        self.assertEqual(Change.objects.filter(kind="opening").count(), 1)

    def test_set_opening_refusals(self):
        with self.refuses("opening_required"):
            set_opening(station_id=self.fresh.pk, value=5, actor=self.staff, ip=IP)
        for value in (10000, -1):
            with self.subTest(value=value), self.refuses("range"):
                set_opening(station_id=self.station.pk, value=value, actor=self.staff, ip=IP)
        with self.assertRaises(ValueError):
            set_opening(station_id=self.station.pk, value=5, actor=None, ip=IP)
        self.assertEqual(Change.objects.count(), 0)


class RecomputeTests(TestCase):
    def test_recomputes_from_the_opening_and_ignores_stored_calcs(self):
        station = make_station("Чуқурсой", opening=10)
        first = Entry.objects.create(station=station, period_end=P1, unloaded=5, arrived=3, remaining_calc=0)
        second = Entry.objects.create(
            station=station, period_end=P2, unloaded=2, arrived=4, remaining_calc=0, remaining_override=4
        )
        third = Entry.objects.create(station=station, period_end=P3, unloaded=1, arrived=0, remaining_calc=0)
        self.assertEqual(recompute(station), {first.id: 8, second.id: 10, third.id: 3})


class ConcurrencyTests(TransactionTestCase):
    def test_two_saves_for_one_period_only_one_wins(self):
        station = make_station("Чуқурсой", opening=10)
        outcomes = []

        def submit(unloaded):
            try:
                save_entry(
                    station_id=station.pk, period_end=P1, unloaded=unloaded, arrived=0,
                    form_version=0, form_prev=10, actor=None, ip=IP, now=P1 + SECOND,
                )
                outcomes.append("saved")
            except SaveError as error:
                outcomes.append(error.code)
            finally:
                connection.close()

        threads = [threading.Thread(target=submit, args=(unloaded,)) for unloaded in (1, 2)]
        # Hold the station row while both saves start, so both are in flight before either can write.
        with transaction.atomic():
            Station.objects.select_for_update().get(pk=station.pk)
            for thread in threads:
                thread.start()
            threads[0].join(timeout=0.5)
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), "a save is stuck on the station lock")

        self.assertEqual(sorted(outcomes), ["saved", "stale"])
        entry = Entry.objects.get()
        self.assertEqual((entry.version, Change.objects.count()), (1, 1))
        self.assertEqual(recompute(station), {entry.id: entry.remaining_calc})
