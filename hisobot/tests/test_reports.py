from datetime import date, datetime

from django.test import TestCase

from hisobot import periods, reports
from hisobot.models import Entry, Mtu, Station
from hisobot.periods import TZ
from hisobot.reports import Cell, Submission, Totals

DAY = date(2026, 10, 12)
NIGHT_END, DAY_END = periods.ends_for(DAY)  # 12.10 08:00 and 12.10 20:00
LATER = datetime(2026, 11, 1, 8, tzinfo=TZ)  # both periods of DAY ended long ago


def tk(day, hour):
    """Tashkent wall-clock time in October 2026."""
    return datetime(2026, 10, day, hour, tzinfo=TZ)


class ReportsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mtu_a = Mtu.objects.create(name="А МТУ", sort=1)
        cls.mtu_b = Mtu.objects.create(name="Б МТУ", sort=2)

    def station(self, name, mtu=None, **fields):
        fields.setdefault("active_from", tk(1, 20))
        return Station.objects.create(mtu=mtu or self.mtu_a, name=name, **fields)

    def entry(self, station, period_end, unloaded=0, arrived=0, calc=0, override=None):
        return Entry.objects.create(
            station=station,
            period_end=period_end,
            unloaded=unloaded,
            arrived=arrived,
            remaining_calc=calc,
            remaining_override=override,
        )

    def scope(self):
        return Station.objects.order_by("mtu__sort", "sort", "id")

    def row(self, station):
        (row,) = reports.daily_rows(Station.objects.filter(pk=station.pk), DAY, now=LATER)
        return row

    def test_a_period_that_has_not_ended_is_blank_not_missing(self):
        s = self.station("Олмазор", opening_remaining=5, opening_at=tk(11, 8))
        self.entry(s, NIGHT_END, unloaded=2, calc=3)
        scope = Station.objects.filter(pk=s.pk)
        (row,) = reports.daily_rows(scope, DAY, now=tk(12, 19))
        self.assertEqual(row.day, Cell(None, False, None, False))
        self.assertEqual((row.partial, row.total_unloaded), (False, 2))
        self.assertEqual(row.night.entry.station, s)
        (row,) = reports.daily_rows(scope, DAY, now=DAY_END)  # ended exactly now: due
        self.assertEqual((row.day.expected, row.day.remaining, row.day.carried, row.partial), (True, 3, True, True))

    # --- remaining_at: the stock rule -------------------------------------------------

    def test_remaining_at_is_the_latest_entry_at_or_before_t(self):
        s = self.station("Олмазор", opening_remaining=50, opening_at=tk(9, 8))
        self.entry(s, tk(10, 8), calc=5)
        self.entry(s, tk(11, 8), calc=9, override=7)
        self.entry(s, tk(12, 8), calc=1)
        for t, expected in [
            (tk(9, 7), None),  # before the opening
            (tk(9, 8), 50),  # the opening applies from opening_at on
            (tk(10, 7), 50),
            (tk(10, 8), 5),  # period_end <= T is inclusive
            (tk(11, 7), 5),
            (tk(11, 8), 7),  # effective value: the override
            (tk(12, 7), 7),
            (tk(20, 8), 1),
        ]:
            with self.subTest(t=t):
                self.assertEqual(reports.remaining_at(Station.objects.all(), t), {s.id: expected})

    def test_remaining_at_is_unknown_without_entry_and_opening(self):
        s = self.station("Янги")
        self.assertEqual(reports.remaining_at(Station.objects.all(), DAY_END), {s.id: None})

    def test_remaining_at_covers_only_the_given_stations(self):
        mine = self.station("А1")
        foreign = self.station("Б1", mtu=self.mtu_b)
        self.entry(mine, NIGHT_END, calc=3)
        self.entry(foreign, NIGHT_END, calc=8)
        self.assertEqual(reports.remaining_at(Station.objects.filter(mtu=self.mtu_a), DAY_END), {mine.id: 3})

    # --- daily_rows --------------------------------------------------------------------

    def test_entry_present_is_not_carried(self):
        s = self.station("Олмазор")
        night = self.entry(s, NIGHT_END, unloaded=3, arrived=5, calc=7)
        day = self.entry(s, DAY_END, unloaded=4, arrived=1, calc=4, override=6)
        row = self.row(s)
        self.assertEqual(row.station, s)
        self.assertEqual(row.night, Cell(night, True, 7, False))
        self.assertEqual(row.day, Cell(day, True, 6, False))
        self.assertEqual((row.total_unloaded, row.partial), (7, False))

    def test_absent_periods_carry_the_latest_earlier_remaining(self):
        s = self.station("Олмазор", opening_remaining=50, opening_at=tk(8, 20))
        self.entry(s, tk(9, 20), calc=99)
        self.entry(s, tk(10, 20), calc=8, override=5)
        self.entry(s, tk(13, 8), calc=77)  # after the report day: must not leak back
        row = self.row(s)
        self.assertEqual(row.night, Cell(None, True, 5, True))
        self.assertEqual(row.day, Cell(None, True, 5, True))

    def test_absent_day_period_carries_the_night_entry(self):
        s = self.station("Олмазор")
        self.entry(s, tk(11, 20), calc=1)
        night = self.entry(s, NIGHT_END, unloaded=3, calc=7)
        row = self.row(s)
        self.assertEqual(row.night, Cell(night, True, 7, False))
        self.assertEqual(row.day, Cell(None, True, 7, True))
        self.assertEqual((row.total_unloaded, row.partial), (3, True))

    def test_absent_night_period_leaves_the_day_value_as_a_partial_total(self):
        s = self.station("Олмазор")
        day = self.entry(s, DAY_END, unloaded=4, calc=2)
        row = self.row(s)
        self.assertEqual(row.night, Cell(None, True, None, False))
        self.assertEqual(row.day, Cell(day, True, 2, False))
        self.assertEqual((row.total_unloaded, row.partial), (4, True))

    def test_opening_is_carried_only_once_it_applies(self):
        for opening_at, night, day in [
            (tk(11, 20), (12, True), (12, True)),  # before both period ends
            (tk(12, 8), (12, True), (12, True)),  # exactly at the night end: opening_at <= T
            (tk(12, 20), (None, False), (12, True)),  # after the night end
            (tk(13, 8), (None, False), (None, False)),  # after the report day
        ]:
            with self.subTest(opening_at=opening_at):
                s = self.station(f"Станция {opening_at:%d %H}", opening_remaining=12, opening_at=opening_at)
                row = self.row(s)
                self.assertEqual((row.night.remaining, row.night.carried), night)
                self.assertEqual((row.day.remaining, row.day.carried), day)

    def test_both_periods_absent_is_no_total_but_zero_entries_are_a_total(self):
        absent = self.row(self.station("Жим"))
        self.assertIsNone(absent.total_unloaded)
        self.assertTrue(absent.partial)

        idle = self.station("Ҳаракатсиз")
        self.entry(idle, NIGHT_END)
        self.entry(idle, DAY_END)
        idle = self.row(idle)
        self.assertEqual((idle.total_unloaded, idle.partial), (0, False))
        self.assertEqual((idle.day.remaining, idle.day.carried), (0, False))

    def test_not_expected_periods_are_not_missing(self):
        self.station("Кейин очилган", active_from=tk(13, 9))
        self.station("Ёпилган", active_to=tk(11, 9))
        midday = self.station("Тушда очилган", active_from=tk(12, 9))
        rows = reports.daily_rows(self.scope(), DAY, now=LATER)
        self.assertEqual(
            [(r.night.expected, r.day.expected, r.partial) for r in rows],
            [(False, False, False), (False, False, False), (False, True, True)],
        )
        self.assertEqual(reports.totals(rows).missing, 1)
        self.assertEqual(reports.submission(self.scope(), NIGHT_END), [Submission(self.mtu_a, 0, 0, [], [])])
        self.assertEqual(reports.submission(self.scope(), DAY_END), [Submission(self.mtu_a, 1, 0, [midday], [])])

    def test_rows_keep_the_queryset_order_and_scope(self):
        second = self.station("Иккинчи", sort=2)
        first = self.station("Биринчи", sort=1)
        self.station("Бегона", mtu=self.mtu_b)
        mine = Station.objects.filter(mtu=self.mtu_a)
        self.assertEqual([r.station for r in reports.daily_rows(mine.order_by("sort"), DAY, now=LATER)], [first, second])
        self.assertEqual([r.station for r in reports.daily_rows(mine.order_by("-sort"), DAY, now=LATER)], [second, first])

    def test_cells_agree_with_remaining_at(self):
        opened = self.station("Қолдиқли", opening_remaining=9, opening_at=tk(12, 8))
        self.entry(opened, DAY_END, calc=11)
        entered = self.station("Киритилган")
        self.entry(entered, NIGHT_END, calc=4)
        unknown = self.station("Номаълум")
        rows = reports.daily_rows(self.scope(), DAY, now=LATER)
        for cell_name, period_end, expected in [
            ("night", NIGHT_END, {opened.id: 9, entered.id: 4, unknown.id: None}),
            ("day", DAY_END, {opened.id: 11, entered.id: 4, unknown.id: None}),
        ]:
            with self.subTest(cell=cell_name):
                self.assertEqual({r.station.id: getattr(r, cell_name).remaining for r in rows}, expected)
                self.assertEqual(reports.remaining_at(self.scope(), period_end), expected)

    # --- totals, by_mtu ----------------------------------------------------------------

    def test_totals_sum_what_is_present_and_count_missing_stations(self):
        full = self.station("Тўлиқ")
        self.entry(full, NIGHT_END, unloaded=3, arrived=5, calc=7)
        self.entry(full, DAY_END, unloaded=4, arrived=1, calc=4)
        half = self.station("Ярим")
        self.entry(half, tk(11, 20), unloaded=100, arrived=100, calc=10)  # the day before: carried only
        self.entry(half, DAY_END, unloaded=2, arrived=6, calc=14)
        self.station("Жим", opening_remaining=20, opening_at=tk(12, 20))  # unknown at night, carried by day
        self.station("Номаълум")
        self.station("Ёпилган", active_to=tk(11, 9))
        self.assertEqual(
            reports.totals(reports.daily_rows(self.scope(), DAY, now=LATER)),
            Totals(
                n_unloaded=3,
                n_arrived=5,
                n_remaining=7 + 10,
                d_unloaded=4 + 2,
                d_arrived=1 + 6,
                d_remaining=4 + 14 + 20,
                total_unloaded=3 + 4 + 2,
                partial=True,
                stations=5,
                missing=3,
            ),
        )

    def test_totals_of_complete_and_of_no_rows(self):
        s = self.station("Тўлиқ")
        self.entry(s, NIGHT_END, unloaded=1, arrived=2, calc=3)
        self.entry(s, DAY_END, unloaded=4, arrived=5, calc=6)
        rows = reports.daily_rows(self.scope(), DAY, now=LATER)
        self.assertEqual(reports.totals(rows), Totals(1, 2, 3, 4, 5, 6, 5, False, 1, 0))
        self.assertEqual(reports.totals([]), Totals(0, 0, 0, 0, 0, 0, 0, False, 0, 0))

    def test_by_mtu_groups_rows_with_their_totals(self):
        a1 = self.station("А1", sort=1)
        b1 = self.station("Б1", mtu=self.mtu_b)
        a2 = self.station("А2", sort=2)
        self.entry(a1, NIGHT_END, unloaded=1, calc=1)
        self.entry(a2, NIGHT_END, unloaded=2, calc=2)
        self.entry(b1, NIGHT_END, unloaded=5, calc=5)
        self.entry(b1, DAY_END, unloaded=6, calc=6)
        groups = reports.by_mtu(reports.daily_rows(self.scope(), DAY, now=LATER))
        self.assertEqual(
            [(mtu, [r.station for r in rows]) for mtu, rows, _ in groups],
            [(self.mtu_a, [a1, a2]), (self.mtu_b, [b1])],
        )
        self.assertEqual([totals for *_, totals in groups], [reports.totals(rows) for _, rows, _ in groups])
        self.assertEqual(
            [(t.n_unloaded, t.total_unloaded, t.stations, t.missing) for *_, t in groups],
            [(3, 3, 2, 2), (5, 11, 1, 0)],
        )

    # --- submission --------------------------------------------------------------------

    def test_submission_is_n_of_m_per_mtu_with_missing_in_station_order(self):
        a3 = self.station("А3", sort=3)
        a1 = self.station("А1", sort=1)
        a2 = self.station("А2", sort=2)
        b1 = self.station("Б1", mtu=self.mtu_b, sort=1)
        self.station("Ёпилган", mtu=self.mtu_b, sort=2, active_to=tk(11, 9))
        done = self.entry(a2, NIGHT_END, unloaded=4, calc=1)
        self.entry(a1, DAY_END)  # another period
        self.assertEqual(
            reports.submission(self.scope(), NIGHT_END),
            [Submission(self.mtu_a, 3, 1, [a1, a3], [done]), Submission(self.mtu_b, 1, 0, [b1], [])],
        )
        self.assertEqual(
            reports.submission(Station.objects.filter(mtu=self.mtu_b), NIGHT_END),
            [Submission(self.mtu_b, 1, 0, [b1], [])],
        )

    def test_submission_counts_an_entry_of_a_period_no_longer_expected(self):
        # Switching a station off and on again moves active_from past its earlier entries.
        s = self.station("Қайта ёқилган", active_from=tk(12, 9))
        done = self.entry(s, NIGHT_END, unloaded=2, calc=3)
        self.assertEqual(reports.submission(self.scope(), NIGHT_END), [Submission(self.mtu_a, 1, 1, [], [done])])

    # --- attention ---------------------------------------------------------------------

    def test_attention_is_overridden_and_negative_entries_newest_first(self):
        s = self.station("Олмазор")
        other = self.station("Б1", mtu=self.mtu_b)
        self.entry(s, tk(9, 20), calc=-1)  # older than `since`
        negative = self.entry(s, tk(10, 8), calc=-2)  # exactly at `since`
        self.entry(s, tk(10, 20), calc=5)
        self.entry(s, tk(11, 8), calc=5, override=5)  # counted and equal: not a change
        overridden = self.entry(s, tk(11, 20), calc=5, override=6)
        zeroed = self.entry(s, tk(12, 8), calc=-3, override=0)
        foreign = self.entry(other, tk(12, 8), calc=-9)
        self.assertEqual(reports.attention(self.scope(), tk(10, 8)), [zeroed, foreign, overridden, negative])
        self.assertEqual(
            reports.attention(Station.objects.filter(mtu=self.mtu_a), tk(10, 8)), [zeroed, overridden, negative]
        )

    # --- cost --------------------------------------------------------------------------

    def test_query_count_does_not_grow_with_the_number_of_stations(self):
        def add_stations(count):
            for i in range(Station.objects.count(), Station.objects.count() + count):
                s = self.station(f"Станция {i}", mtu=(self.mtu_a, self.mtu_b)[i % 2], sort=i)
                self.entry(s, tk(11, 20), calc=-1)
                self.entry(s, NIGHT_END, unloaded=1, calc=3, override=4)

        def read_everything():
            with self.assertNumQueries(3):
                rows = reports.daily_rows(self.scope(), DAY, now=LATER)
                names = [(mtu.name, len(group), totals.stations) for mtu, group, totals in reports.by_mtu(rows)]
            with self.assertNumQueries(2):
                subs = reports.submission(self.scope(), NIGHT_END)
                done = [(sub.mtu.name, e.station.name) for sub in subs for e in sub.done]
            with self.assertNumQueries(2):
                stock = reports.remaining_at(self.scope(), DAY_END)
            with self.assertNumQueries(1):
                flagged = [e.station.mtu.name for e in reports.attention(self.scope(), tk(1, 8))]
            return len(rows), len(names), len(done), len(stock), len(flagged)

        add_stations(2)
        self.assertEqual(read_everything(), (2, 2, 2, 2, 4))
        add_stations(18)
        self.assertEqual(read_everything(), (20, 2, 20, 20, 40))
