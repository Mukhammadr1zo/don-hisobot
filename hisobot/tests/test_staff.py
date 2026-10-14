from datetime import date, datetime, timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from hisobot import access, balance, periods, reports, views_staff
from hisobot.models import Change, Entry, Mtu, Station, User
from hisobot.periods import TZ
from hisobot.tests.test_periods import tk

NOW = tk(12, 9)  # 12.10 09:00: the night period of 12.10 has ended, its day period is running
PE = tk(12, 8)  # the last ended period
SLUG = "2026101208"
STALE = "Маълумот ўзгарган, қайта текширинг"
ENTER = '<a class="btn btn-sm btn-tint" href="{}">Киритиш</a>'  # «Киритиш» of a station that has not submitted
# The labels a phone shows on the seven number cells of a daily table row (night, day, the day's total).
LABELS = [
    'data-label="Туширилган" data-period="Тунги"', 'data-label="Кириб келган"', 'data-label="Қолган"',
    'data-label="Туширилган" data-period="Кундузги"', 'data-label="Кириб келган"', 'data-label="Қолган"',
    'data-label="Жами"',
]  # fmt: skip


def entry_url(station, pe):
    return reverse("staff_entry", args=[station.pk, periods.to_slug(pe)])


class StaffTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mtu_a = Mtu.objects.create(name="Тошкент", sort=1)
        cls.mtu_b = Mtu.objects.create(name="Қўқон", sort=2)
        cls.boss = User.objects.create_user("boshqarma")
        cls.user_a = User.objects.create_user("toshkent", mtu=cls.mtu_a)
        opened = {"opening_remaining": 10, "opening_at": tk(1, 8), "active_from": tk(1, 8)}
        cls.first = Station.objects.create(mtu=cls.mtu_a, name="Чуқурсой", sort=1, **opened)
        cls.second = Station.objects.create(mtu=cls.mtu_a, name="Сергели", sort=2, **opened)
        cls.third = Station.objects.create(mtu=cls.mtu_a, name="Тошкент-Товар", sort=3, **opened)
        cls.foreign = Station.objects.create(mtu=cls.mtu_b, name="Бета-бекат", **opened)

    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["staff_login_at"] = NOW.timestamp()
        session.save()

    def entry(self, station, pe, unloaded=0, arrived=0, calc=0, override=None):
        return Entry.objects.create(
            station=station,
            period_end=pe,
            unloaded=unloaded,
            arrived=arrived,
            remaining_calc=calc,
            remaining_override=override,
        )


class AccessTests(StaffTestCase):
    def test_anonymous_is_sent_to_the_staff_login(self):
        url = entry_url(self.first, PE)
        for page in [reverse("staff_now"), reverse("staff_daily"), url]:
            with self.subTest(page=page):
                self.assertRedirects(self.client.get(page), reverse("staff_login"), fetch_redirect_response=False)
        response = self.client.post(url, {"unloaded": 1, "arrived": 1, "version": 0, "prev": 10})
        self.assertRedirects(response, reverse("staff_login"), fetch_redirect_response=False)
        self.assertFalse(Entry.objects.exists())

    def test_an_mtu_user_sees_only_its_own_stations_on_every_page(self):
        self.entry(self.foreign, PE, calc=-5)  # negative: «Эътибор талаб» material
        self.entry(self.foreign, tk(11, 20), calc=3, override=4)
        self.login(self.user_a)
        for page in [
            reverse("staff_now"),
            reverse("staff_now") + f"?mtu={self.mtu_b.pk}",  # ignored for an MTU user
            reverse("staff_daily"),
            reverse("staff_daily") + f"?mtu={self.mtu_b.pk}&d=2026-10-11",
            entry_url(self.first, PE),
        ]:
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertContains(response, "Чуқурсой")
                self.assertNotContains(response, "Бета-бекат")
                self.assertNotContains(response, f"/xodim/yozuv/{self.foreign.pk}/")

    def test_a_foreign_or_unknown_station_or_period_is_not_found(self):
        self.login(self.user_a)
        url = entry_url(self.foreign, PE)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"unloaded": 1, "arrived": 1, "version": 0, "prev": 10}).status_code, 404)
        self.assertFalse(Entry.objects.exists())
        self.assertEqual(self.client.get(reverse("staff_entry", args=[0, SLUG])).status_code, 404)
        self.assertEqual(self.client.get(reverse("staff_entry", args=[self.first.pk, "2026101209"])).status_code, 404)


class NowTests(StaffTestCase):
    def test_one_mtu_lists_missing_stations_first_with_counts_from_submission(self):
        self.entry(self.first, PE, unloaded=5, arrived=3, calc=8)
        self.login(self.user_a)
        response = self.client.get(reverse("staff_now"))

        (sub,) = reports.submission(access.stations_for(self.user_a), PE)
        self.assertEqual((sub.submitted, sub.expected), (1, 3))
        html = response.content.decode()
        self.assertInHTML(
            f'<div class="kpi-row"><span class="big big-xl">{sub.submitted}</span>'
            f'<span class="big big-md muted">/ {sub.expected}</span><span class="big big-md push">33%</span></div>',
            html,
        )
        self.assertContains(response, "топширди")
        lists = html[html.index('id="n-miss"'):]  # the bars above name stations too, in station order
        self.assertLess(lists.index("Сергели"), lists.index("Тошкент-Товар"))
        self.assertLess(lists.index("Тошкент-Товар"), lists.index("Чуқурсой"))  # submitted, though it sorts first
        for station in (self.second, self.third):
            self.assertInHTML(ENTER.format(entry_url(station, PE)), html)
        self.assertContains(response, f'href="{entry_url(self.first, PE)}"')
        self.assertInHTML(
            '<dl class="done-stats"><div><dt>Туширилган</dt><dd>5</dd></div><div><dt>Кириб келган</dt><dd>3</dd></div>'
            "<div><dt>Қолган</dt><dd>8</dd></div></dl>",
            html,
        )
        self.assertInHTML('<p class="t-sub">1 станция бўйича</p>', html, count=2)  # under both sums, no bar

    def test_boshqarma_gets_one_tile_per_mtu_linking_to_its_list(self):
        empty = Mtu.objects.create(name="Бухоро", sort=3)
        self.entry(self.first, PE)
        self.login(self.boss)
        response = self.client.get(reverse("staff_now"))
        html = response.content.decode()

        subs = reports.submission(access.stations_for(self.boss), PE)
        self.assertEqual([(s.mtu, s.submitted, s.expected) for s in subs], [(self.mtu_a, 1, 3), (self.mtu_b, 0, 1)])
        for mtu, ring in [(self.mtu_a, (33, 1, 3)), (self.mtu_b, (0, 0, 1)), (empty, (0, 0, 0))]:
            with self.subTest(mtu=mtu.name):
                self.assertContains(response, f'href="?mtu={mtu.pk}&amp;pe={SLUG}"')
                ring = '<div class="ring" style="--p:{}"><b>{}</b><span>/ {}</span></div>'.format(*ring)
                self.assertInHTML(ring, html)
        # Who has not submitted, with «Киритиш»; who has, not.
        for station in (self.second, self.third, self.foreign):
            self.assertInHTML(ENTER.format(entry_url(station, PE)), html)
        self.assertNotIn(entry_url(self.first, PE), html)

        response = self.client.get(reverse("staff_now"), {"mtu": self.mtu_b.pk})
        self.assertInHTML(
            '<div class="kpi-row"><span class="big big-xl">0</span><span class="big big-md muted">/ 1</span>'
            '<span class="big big-md push">0%</span></div>',
            response.content.decode(),
        )
        self.assertInHTML(ENTER.format(entry_url(self.foreign, PE)), response.content.decode())
        self.assertNotContains(response, "Чуқурсой")

    def test_boshqarma_sees_the_periods_sums_and_the_stock_per_mtu(self):
        self.entry(self.first, PE, unloaded=5, arrived=3, calc=8)
        self.entry(self.foreign, PE, unloaded=2, arrived=4, calc=9)
        self.login(self.boss)
        html = self.client.get(reverse("staff_now")).content.decode()
        kpi = '<div class="kpi-row"><span class="big">{}</span><span class="unit-word">вагон</span></div>'
        self.assertInHTML(kpi.format(7), html, count=2)  # unloaded 5 + 2, arrived 3 + 4
        # Stock at the period end: 8 + 10 + 10 (two openings, carried: no entry for the period) + 9.
        self.assertInHTML(
            '<div class="kpi-row"><span class="big">37</span><span class="unit-word">вагон</span>'
            '<span class="t-sub push">Кўчирилган қолдиқ: 2 станция</span></div>',
            html,
        )
        self.assertInHTML('<li class="mtu-1"><i class="key"></i>Тошкент <b>28</b></li>', html)
        self.assertInHTML('<li class="mtu-2"><i class="key"></i>Қўқон <b>9</b></li>', html)
        self.assertInHTML('<span class="mtu-2" style="--v:4" title="Қўқон · 4"></span>', html)  # its arrived
        # Қўқон is complete: the seal on its tile, and named over the columns of those still missing.
        self.assertIn('aria-label="Ҳаммаси топширди"', html)
        self.assertInHTML(
            '<span class="push miss-done"><span class="chip chip-ok"><svg class="ico" aria-hidden="true">'
            '<use href="#i-check"/></svg>Ҳаммаси топширди</span><span class="chip chip-mtu mtu-2">Қўқон</span></span>',
            html,
        )

        # A period nobody entered: no sums, never zeros (nor «вагон» after the dash), and no bar of them; every
        # stock is carried.
        html = self.client.get(reverse("staff_now"), {"pe": "2026101120"}).content.decode()
        self.assertInHTML('<div class="kpi-row"><span class="big muted">—</span></div>', html, count=2)
        self.assertInHTML('<p class="t-sub">Ҳали киритилмаган</p>', html, count=2)
        self.assertEqual(html.count('class="share"'), 1)  # «Қолган» only
        self.assertInHTML(
            '<div class="kpi-row"><span class="big">40</span><span class="unit-word">вагон</span>'
            '<span class="t-sub push">Кўчирилган қолдиқ: 4 станция</span></div>',
            html,
        )
        self.assertInHTML(
            '<div class="stat"><span class="stat-num">30</span><span class="stat-cap">Қолган</span></div>', html
        )

    def test_one_mtu_names_the_stations_holding_the_most_stock(self):
        opened = {"opening_at": tk(1, 8), "active_from": tk(1, 8)}
        for i, stock in enumerate([1, 7, 3, 9, 2, 0]):
            Station.objects.create(mtu=self.mtu_a, name=f"Бекат-{i}", sort=10 + i, opening_remaining=stock, **opened)
        self.entry(self.first, PE, unloaded=5, arrived=3, calc=8, override=6)
        self.login(self.user_a)
        html = self.client.get(reverse("staff_now")).content.decode()
        # Largest first, ties in station order; past six the first five and the rest as one; no empty station.
        self.assertInHTML(
            '<ul class="legend legend-grid" aria-label="Станциялар бўйича">'
            '<li><i class="key"></i><span>Сергели <b>10</b></span></li>'
            '<li><i class="key"></i><span>Тошкент-Товар <b>10</b></span></li>'
            '<li><i class="key"></i><span>Бекат-3 <b>9</b></span></li>'
            '<li><i class="key"></i><span>Бекат-1 <b>7</b></span></li>'
            '<li><i class="key"></i><span>Чуқурсой <b>6</b></span></li>'
            '<li><i class="key"></i><span>бошқа 3 станция <b>6</b></span></li></ul>',
            html,
        )
        self.assertEqual(html.count('class="share"'), 1)  # no bars of unnamed stations under the sums
        # Its card marks the stock typed by hand.
        self.assertInHTML(
            '<div class="is-override"><dt>Қолган</dt><dd>6<span class="sr">, қўлда ўзгартирилган</span></dd></div>',
            html,
        )

    def test_many_missing_stations_show_the_first_five_and_a_link_to_the_rest(self):
        opened = {"opening_remaining": 0, "opening_at": tk(1, 8), "active_from": tk(1, 8)}
        extra = [Station.objects.create(mtu=self.mtu_a, name=f"Бекат-{i}", sort=10 + i, **opened) for i in range(3)]
        self.login(self.boss)
        html = self.client.get(reverse("staff_now")).content.decode()
        for station in [self.first, self.second, self.third, *extra]:  # six: every one, no link
            self.assertInHTML(ENTER.format(entry_url(station, PE)), html)
        self.assertNotIn('class="miss-more"', html)

        extra.append(Station.objects.create(mtu=self.mtu_a, name="Бекат-3", sort=13, **opened))
        html = self.client.get(reverse("staff_now")).content.decode()
        shown = [self.first, self.second, self.third, extra[0], extra[1]]
        for station in shown:
            self.assertInHTML(ENTER.format(entry_url(station, PE)), html)
        for station in extra[2:]:
            self.assertNotIn(entry_url(station, PE), html)
        self.assertInHTML(
            f'<a class="miss-more" href="?mtu={self.mtu_a.pk}&amp;pe={SLUG}">Яна 2 та'
            '<svg class="ico ico-sm" aria-hidden="true"><use href="#i-arrow"/></svg></a>',
            html,
        )
        # The МТУ's own page lists every one of them.
        html = self.client.get(reverse("staff_now"), {"mtu": self.mtu_a.pk, "pe": SLUG}).content.decode()
        for station in shown + extra[2:]:
            self.assertInHTML(ENTER.format(entry_url(station, PE)), html)

    def test_the_query_count_does_not_grow_with_the_stations(self):
        def queries(user, **params):
            self.login(user)
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(reverse("staff_now"), params).status_code, 200)
            return len(captured)

        def counts():
            return [queries(self.boss), queries(self.boss, mtu=self.mtu_a.pk), queries(self.user_a)]

        def add(station, i):
            self.entry(station, PE, unloaded=i % 4, arrived=2, calc=i - 1)  # some negative: «Эътибор талаб»
            self.entry(station, tk(11, 20), calc=3, override=4)  # typed by hand: «Эътибор талаб»
            self.entry(station, tk(10, 8), unloaded=1, arrived=1, calc=3)  # in the 14-day chart

        Station.objects.exclude(pk__in=[self.first.pk, self.foreign.pk]).delete()  # 2 stations, one per МТУ
        for i, station in enumerate([self.first, self.foreign]):
            add(station, i)
        two = counts()
        opened = {"opening_remaining": 5, "opening_at": tk(1, 8), "active_from": tk(1, 8)}
        for i in range(18):  # 20 stations
            add(Station.objects.create(mtu=(self.mtu_a, self.mtu_b)[i % 2], name=f"Бекат-{i}", **opened), i)
        self.assertEqual(counts(), two)

    def test_the_chart_leaves_out_a_last_day_stations_may_still_enter(self):
        # At NOW the chart ends with 11.10: stations may enter its day period until 12.10 20:00.
        self.entry(self.first, tk(10, 8), unloaded=4, arrived=4, calc=10)
        self.entry(self.first, tk(11, 8), unloaded=3, arrived=1, calc=8)
        self.login(self.user_a)
        response = self.client.get(reverse("staff_now"))
        trend = response.context["trend"]
        # 11.10 misses both periods of two stations and the day period of the third.
        self.assertEqual(trend["partial"], {"unloaded": 3, "arrived": 1, "missing": 5})
        self.assertEqual(trend["average"], 4)  # 10.10 misses five as well, but for good: it counts
        self.assertEqual([value for value, _ in trend["ticks"]], [5, 4])  # from 10.10 alone, not down to 11.10's 1
        self.assertEqual(trend["lines"]["unloaded"]["runs"], ["92.31,100.0 92.31,100.0"])  # 10.10 alone: a dot
        html = response.content.decode()
        self.assertIn('<polyline class="series-1" points="92.31,100.0 92.31,100.0"/>', html)
        self.assertInHTML('<p class="trend-note">11.10: туширилган 3, кириб келган 1, киритилмаган 5</p>', html)
        self.assertInHTML("<tr><th scope=\"row\">11.10</th><td>3</td><td>1</td><td>5</td></tr>", html)

    def test_the_period_defaults_to_the_last_ended_one(self):
        self.login(self.user_a)
        for pe in ["", "abc", "2026101209", "2026101220", "2027010108"]:  # junk, not an end, running, future
            with self.subTest(pe=pe):
                self.assertEqual(self.client.get(reverse("staff_now"), {"pe": pe}).context["pe"], PE)
        response = self.client.get(reverse("staff_now"))
        self.assertContains(response, 'href="?pe=2026101120"')
        self.assertInHTML('<span class="btn" aria-disabled="true"></span>', response.content.decode())

        response = self.client.get(reverse("staff_now"), {"pe": "2026101120"})
        self.assertEqual(response.context["pe"], tk(11, 20))
        self.assertContains(response, 'href="?pe=2026101108"')
        self.assertContains(response, f'href="?pe={SLUG}"')

    def test_attention_lists_recent_overrides_and_negatives(self):
        self.entry(self.second, tk(10, 8), calc=-2)
        self.entry(self.third, tk(11, 20), calc=5, override=7)
        self.entry(self.first, tk(11, 8), calc=5, override=5)  # counted, equal to calc: not a change
        self.entry(self.first, datetime(2026, 9, 1, 8, tzinfo=TZ), calc=-1)  # older than 30 days
        self.login(self.user_a)
        html = self.client.get(reverse("staff_now")).content.decode()
        attention = html[html.index("Эътибор талаб"):]
        self.assertIn(entry_url(self.second, tk(10, 8)), attention)
        self.assertIn(entry_url(self.third, tk(11, 20)), attention)
        self.assertNotIn(entry_url(self.first, tk(11, 8)), attention)
        self.assertNotIn(entry_url(self.first, datetime(2026, 9, 1, 8, tzinfo=TZ)), attention)


class DailyTests(StaffTestCase):
    def fill(self):
        """11.10: both periods ended at NOW."""
        night, day = periods.ends_for(date(2026, 10, 11))
        self.entry(self.first, night, unloaded=2, arrived=1, calc=9, override=4)
        self.entry(self.first, day, unloaded=7, arrived=0, calc=-3)
        self.entry(self.third, night, unloaded=5, arrived=3, calc=8)
        return night, day

    def assertTotal(self, html, name, *values):
        cells = "".join(f'<td class="num" {label}>{v}</td>' for label, v in zip(LABELS, values, strict=True))
        self.assertInHTML(f'<tr class="total"><td></td><th scope="row">{name}</th>{cells}</tr>', html)

    def test_cells_show_override_missing_negative_and_carried(self):
        night, day = self.fill()
        self.login(self.user_a)
        html = self.client.get(reverse("staff_daily"), {"d": "2026-10-11"}).content.decode()

        def cell(classes, station, pe, value, label):
            self.assertInHTML(
                f'<td class="{classes}" {label}><a class="cell" href="{entry_url(station, pe)}">{value}</a></td>', html
            )

        cell("num is-override", self.first, night, 4, LABELS[2])
        cell("num is-negative", self.first, day, -3, LABELS[5])
        cell("num is-missing", self.second, night, "—", LABELS[0])
        cell("num is-missing", self.second, night, "—", LABELS[1])
        cell("num is-missing is-carried", self.second, night, 10, LABELS[2])  # the opening, carried
        cell("num is-missing is-carried", self.third, day, 8, LABELS[5])
        self.assertInHTML(f'<td class="num" {LABELS[6]}>9</td>', html)  # first: both periods in
        self.assertInHTML(f'<td class="num is-missing" {LABELS[6]}>—</td>', html)  # second: nothing at all
        self.assertInHTML(f'<td class="num" {LABELS[6]}>5*</td>', html)  # third: the day period is missing
        self.assertIn("Бир давр киритилмаган", html)
        self.assertNotIn("кўрсатилмади", html)  # no row was left out
        self.assertInHTML(
            '<a class="btn no-print" href="/xodim/eksport/?d=2026-10-11">'
            '<svg class="ico" aria-hidden="true"><use href="#i-download"/></svg>Excel юклаб олиш</a>',
            html,
        )
        self.assertNotIn("<tfoot>", html)

    def test_totals_per_mtu_and_a_grand_total_across_mtus(self):
        self.fill()
        self.login(self.boss)
        html = self.client.get(reverse("staff_daily"), {"d": "2026-10-11"}).content.decode()
        self.assertTotal(html, "Тошкент МТУ жами", 7, 4, 4 + 10 + 8, 7, 0, -3 + 10 + 8, "14*")
        self.assertTotal(html, "Қўқон МТУ жами", 0, 0, 10, 0, 0, 10, "0*")
        self.assertTotal(html, "Жами", 7, 4, 32, 7, 0, 25, "14*")
        self.assertIn("<tfoot>", html)

        response = self.client.get(reverse("staff_daily"), {"d": "2026-10-11", "mtu": self.mtu_a.pk})
        self.assertNotContains(response, "Бета-бекат")
        self.assertNotContains(response, "<tfoot>")
        self.assertContains(response, f'href="/xodim/eksport/?d=2026-10-11&amp;mtu={self.mtu_a.pk}"')

    def test_a_switched_off_station_without_data_is_left_out_and_the_totals_add_up(self):
        night, _ = self.fill()
        gone = Station.objects.create(mtu=self.mtu_a, name="Ёпиқ", sort=4, active_from=tk(1, 8), active_to=tk(5, 8))
        # Switched off this morning before its night period ended, so it expected nothing; staff entered the night.
        today = Station.objects.create(mtu=self.mtu_a, name="Бугун", sort=5, active_from=tk(1, 8), active_to=tk(11, 7))
        self.entry(today, night, unloaded=4, arrived=2, calc=6)
        # Switched off long ago and still holding wagons: its stock is data too.
        Station.objects.create(
            mtu=self.mtu_a, name="Тўла", sort=6, active_from=tk(1, 8), active_to=tk(5, 8),
            opening_remaining=6, opening_at=tk(1, 8),
        )  # fmt: skip
        self.login(self.boss)
        response = self.client.get(reverse("staff_daily"), {"d": "2026-10-11"})
        html = response.content.decode()
        self.assertNotContains(response, "Ёпиқ")
        self.assertNotContains(response, f"/xodim/yozuv/{gone.pk}/")
        self.assertContains(response, "Бугун")
        self.assertContains(response, "Тўла")
        self.assertContains(response, "Ўчирилган 1 та станция кўрсатилмади")
        # The totals sum exactly the rows shown: «Бугун» and «Тўла» add their wagons, «Ёпиқ» is not one of them.
        tashkent = (2 + 5 + 4, 1 + 3 + 2, 4 + 10 + 8 + 6 + 6, 7, 0, -3 + 10 + 8 + 6 + 6, "18*")
        self.assertTotal(html, "Тошкент МТУ жами", *tashkent)
        self.assertTotal(html, "Қўқон МТУ жами", 0, 0, 10, 0, 0, 10, "0*")
        self.assertTotal(html, "Жами", 11, 6, 44, 7, 0, 37, "18*")
        self.assertEqual(response.context["grand"].stations, 6)
        for _, lines, total in response.context["groups"]:
            self.assertEqual(total, reports.totals([row for row, _ in lines]))

        # An МТУ whose stations are all left out says so instead of «Станциялар йўқ».
        closed = Mtu.objects.create(name="Термиз", sort=6)
        Station.objects.create(mtu=closed, name="Термиз-2", active_from=tk(1, 8), active_to=tk(5, 8))
        response = self.client.get(reverse("staff_daily"), {"d": "2026-10-11", "mtu": closed.pk})
        self.assertInHTML('<p class="empty">Ўчирилган 1 та станция кўрсатилмади</p>', response.content.decode())

    def test_a_period_the_station_does_not_expect_is_quieter_than_a_missing_one(self):
        night, day = periods.ends_for(date(2026, 10, 11))
        # Switched off at noon: the night period of 11.10 was still expected, the day period was not.
        noon = Station.objects.create(mtu=self.mtu_a, name="Янги", sort=9, active_from=tk(1, 8), active_to=tk(11, 12))
        self.login(self.user_a)
        html = self.client.get(reverse("staff_daily"), {"d": "2026-10-11"}).content.decode()
        cell = '<td class="num {}" {}><a class="cell" href="{}">—</a></td>'
        for i, label in enumerate(LABELS[:3]):
            self.assertInHTML(cell.format("is-missing", label, entry_url(noon, night)), html)
            self.assertInHTML(cell.format("is-off", LABELS[3 + i], entry_url(noon, day)), html)
        self.assertInHTML(f'<td class="num is-missing" {LABELS[6]}>—</td>', html)  # its daily total: night missing
        self.assertInHTML('<li><span class="is-off">—</span>Ўчирилган станция</li>', html)  # the legend says why
        # The day before, it expected both periods: no such cell, so the legend leaves the item out.
        html = self.client.get(reverse("staff_daily"), {"d": "2026-10-10"}).content.decode()
        self.assertIn("Янги", html)
        self.assertNotIn("Ўчирилган станция", html)

    def test_the_date_defaults_to_the_latest_report_date(self):
        self.login(self.user_a)
        for d in ["", "abc", "2026-13-01", "2026-10-13", "12.10.2026", "1999-12-31"]:
            with self.subTest(d=d):
                response = self.client.get(reverse("staff_daily"), {"d": d})
                self.assertEqual(response.context["day"], date(2026, 10, 12))
        # The night period has ended and links to its form; the running day period stays blank.
        self.assertContains(response, f'href="{entry_url(self.first, PE)}"')
        self.assertNotContains(response, entry_url(self.first, tk(12, 20)))
        self.assertNotContains(response, "Ўчирилган станция")  # its blank cells are no switched-off station
        self.assertContains(response, 'href="?d=2026-10-11"')
        self.assertInHTML('<span class="btn no-print" aria-disabled="true"></span>', response.content.decode())


class EntryTests(StaffTestCase):
    def test_staff_saves_a_period_three_days_old(self):
        pe = tk(9, 8)
        self.assertFalse(periods.station_can_write(pe, NOW))
        url = entry_url(self.first, pe)
        self.login(self.user_a)
        response = self.client.get(url)
        self.assertContains(response, "Олдинги давр киритилмаган")
        self.assertContains(response, "Давр бошида")

        response = self.client.post(url, {"unloaded": 2, "arrived": 5, "version": 0, "prev": 10})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        saved = Entry.objects.get(station=self.first, period_end=pe)
        self.assertEqual((saved.unloaded, saved.arrived, saved.remaining_calc), (2, 5, 13))
        change = Change.objects.get(entry=saved)
        self.assertEqual((change.user, change.ip), (self.user_a, "127.0.0.1"))
        self.assertContains(self.client.get(url), "Сақланди")

    def test_a_period_that_has_not_ended_is_not_open(self):
        self.login(self.user_a)
        url = entry_url(self.first, tk(12, 20))
        self.assertRedirects(self.client.get(url), reverse("staff_now"), fetch_redirect_response=False)
        response = self.client.post(url, {"unloaded": 1, "arrived": 1, "version": 0, "prev": 10})
        self.assertRedirects(response, reverse("staff_now"), fetch_redirect_response=False)
        self.assertFalse(Entry.objects.exists())

    def test_the_first_entry_of_a_station_asks_for_the_opening(self):
        fresh = Station.objects.create(mtu=self.mtu_a, name="Янги", active_from=tk(1, 8))
        url = entry_url(fresh, PE)
        self.login(self.user_a)
        response = self.client.post(url, {"unloaded": 1, "arrived": 2, "version": 0, "prev": ""})
        self.assertContains(response, "Давр бошидаги қолдиқ")
        self.assertContains(response, "Майдонни тўлдиринг")
        self.assertFalse(Entry.objects.exists())

        response = self.client.post(url, {"opening": 4, "unloaded": 1, "arrived": 2, "version": 0, "prev": ""})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertEqual(Entry.objects.get().remaining_calc, 5)

    def test_a_refused_save_says_why_and_keeps_the_typed_numbers(self):
        url = entry_url(self.first, PE)
        self.login(self.user_a)
        response = self.client.post(url, {"unloaded": 30, "arrived": 0, "version": 0, "prev": 10})
        self.assertContains(response, balance.MESSAGES["negative"])
        self.assertContains(response, 'value="30"')
        response = self.client.post(url, {"unloaded": 1, "arrived": 0, "prev": 10})  # a broken page
        self.assertContains(response, "Саҳифани янгилаб, қайта уриниб кўринг")
        self.assertFalse(Entry.objects.exists())

    def test_a_stale_form_is_shown_again_with_the_stored_numbers(self):
        url = entry_url(self.first, PE)
        self.login(self.user_a)
        self.client.post(url, {"unloaded": 1, "arrived": 1, "version": 0, "prev": 10})  # another tab saved first
        response = self.client.post(url, {"unloaded": 4, "arrived": 0, "version": 0, "prev": 10})
        self.assertContains(response, STALE)
        form = response.context["form"]
        self.assertEqual((form["unloaded"].value(), form["version"].value()), (1, 1))
        self.assertEqual(Entry.objects.get().unloaded, 1)

    def test_no_movement_saves_zeros(self):
        url = entry_url(self.first, PE)
        self.login(self.user_a)
        self.client.post(url, {"unloaded": "", "arrived": "7", "version": 0, "prev": 10, "harakatsiz": "1"})
        saved = Entry.objects.get()
        self.assertEqual((saved.unloaded, saved.arrived, saved.remaining_calc), (0, 0, 10))

    def test_history_lists_the_direct_change_and_a_cascade_row_with_its_cause(self):
        p1, p2 = tk(10, 8), tk(10, 20)
        for pe, prev, unloaded, arrived in [(p1, 10, 1, 2), (p2, 11, 3, 4)]:  # the station, through its PIN
            balance.save_entry(
                station_id=self.first.pk, period_end=pe, unloaded=unloaded, arrived=arrived,
                form_version=0, form_prev=prev, actor=None, ip="10.0.0.9", now=pe + timedelta(hours=1),
            )
        self.login(self.user_a)
        response = self.client.post(entry_url(self.first, p1), {"unloaded": 0, "arrived": 2, "version": 1, "prev": 10})
        self.assertContains(self.client.get(response.url), "Кейинги 1 та давр қайта ҳисобланди")

        response = self.client.get(entry_url(self.first, p2))
        cascade, direct = [change for change, *_ in response.context["history"]]
        self.assertEqual((cascade.user, cascade.cause.entry.period_end), (self.user_a, p1))
        self.assertEqual((direct.user, direct.cause), (None, None))
        self.assertEqual([who for _, who, _ in response.context["history"]], ["Тошкент МТУ", "Станция (ПИН)"])
        html = response.content.decode()
        self.assertNotIn("toshkent", html)  # the Latin login is typed, never shown
        self.assertInHTML(
            '<div><dt>Қолган (ҳисобланган)</dt><dd><span class="was">12</span> → <b>13</b></dd></div>', html
        )
        self.assertInHTML(
            f'<div class="muted small">сабаб: <a href="{entry_url(self.first, p1)}">10.10.2026 тунги давр</a>'
            " тузатилди</div>",
            html,
        )
        self.assertInHTML('<div><dt>Туширилган</dt><dd><span class="was">—</span> → <b>3</b></dd></div>', html)
        self.assertInHTML('<div><dt>Кириб келган</dt><dd><span class="was">—</span> → <b>4</b></dd></div>', html)

    def test_a_period_before_the_opening_is_refused_before_the_form(self):
        # The stations opened at 01.10 08:00: the night period 30.09 20:00 – 01.10 08:00 starts before that.
        url = entry_url(self.first, tk(1, 8))
        back = reverse("staff_daily") + "?d=2026-10-01"
        self.login(self.user_a)
        response = self.client.get(url)
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], [balance.MESSAGES["before_opening"]])
        response = self.client.post(url, {"unloaded": 1, "arrived": 2, "version": 0, "prev": 10})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertFalse(Entry.objects.exists())
        # The first period that starts at the opening is open.
        self.assertEqual(self.client.get(entry_url(self.first, tk(1, 20))).status_code, 200)

    def test_back_keeps_the_boshqarma_on_the_stations_mtu_and_the_nav_marks_the_daily_table(self):
        self.login(self.boss)
        html = self.client.get(entry_url(self.first, PE)).content.decode()
        self.assertInHTML(
            f'<a class="back" href="/xodim/jadval/?d=2026-10-12&amp;mtu={self.mtu_a.pk}">Орқага</a>', html
        )
        self.assertInHTML('<a href="/xodim/jadval/" aria-current="page">Кунлик жадвал</a>', html)
        self.login(self.user_a)
        html = self.client.get(entry_url(self.first, PE)).content.decode()
        self.assertInHTML('<a class="back" href="/xodim/jadval/?d=2026-10-12">Орқага</a>', html)


class PhoneWidthTests(StaffTestCase):
    def test_rows_keep_only_narrow_items_beside_the_station_name(self):
        # A wide badge beside the name squeezes it to a letter a line at 360px: only the time goes beside it,
        # the numbers go on a line of their own.
        self.entry(self.first, PE, unloaded=12, arrived=34, calc=56)
        self.entry(self.second, PE, unloaded=1, arrived=1, calc=9, override=7)
        self.login(self.user_a)
        html = self.client.get(reverse("staff_now")).content.decode()
        self.assertNotIn('<span class="badge-warn">Топширилмаган</span>', html)
        saved = periods.local(Entry.objects.get(station=self.first).updated_at)
        self.assertInHTML(
            f'<li><a href="{entry_url(self.first, PE)}"><span class="done-top"><b>Чуқурсой</b>'
            f'<span class="chip chip-ok"><svg class="ico" aria-hidden="true"><use href="#i-check"/></svg>'
            f"{saved:%H:%M}</span></span>"
            '<dl class="done-stats"><div><dt>Туширилган</dt><dd>12</dd></div>'
            "<div><dt>Кириб келган</dt><dd>34</dd></div><div><dt>Қолган</dt><dd>56</dd></div></dl></a></li>",
            html,
        )
        self.assertInHTML("<span>ҳисобланган 9</span>", html)


class TrendTests(SimpleTestCase):
    def test_a_day_without_numbers_cuts_the_line_and_the_scale_covers_every_value(self):
        series = {
            "labels": ["01.10.2026", "02.10.2026", "03.10.2026", "04.10.2026"],
            "unloaded": [250, None, 400, 380],
            "arrived": [None, None, None, None],
            "missing": [0, 3, 0, 0],
        }
        trend = views_staff._trend(series, [False] * 4)
        self.assertEqual([value for value, _ in trend["ticks"]], [400, 350, 300, 250])
        # The lone first day is a dot (a zero-length line); then 03.10 to 04.10, x and y in % of the plot.
        self.assertEqual(trend["lines"]["unloaded"]["runs"], ["0.0,100.0 0.0,100.0", "66.67,0.0 100.0,13.33"])
        self.assertEqual(trend["lines"]["unloaded"]["end"], {"x": 100.0, "y": 13.33, "value": 380, "above": True})
        self.assertEqual(trend["lines"]["unloaded"]["total"], 1030)
        self.assertEqual(trend["lines"]["arrived"], {"runs": [], "end": None, "total": None})
        self.assertEqual(trend["average"], 343)
        self.assertEqual(trend["days"][1], ("02.10", 33.33, None, None, 3))
        self.assertIsNone(trend["partial"])
        self.assertIsNone(views_staff._trend({**series, "unloaded": [None] * 4}, [False] * 4))

    def test_a_day_stations_may_still_enter_with_entries_missing_is_left_out_of_lines_scale_and_average(self):
        series = {
            "labels": ["01.10.2026", "02.10.2026", "03.10.2026", "04.10.2026"],
            "unloaded": [300, 340, 320, 100],
            "arrived": [310, 330, 300, 90],
            "missing": [2, 0, 0, 150],  # 01.10 is short of two for good; 04.10 is still being entered
        }
        trend = views_staff._trend(series, [False, False, False, True])
        self.assertEqual([value for value, _ in trend["ticks"]], [340, 330, 320, 310, 300])  # not down to 90
        self.assertEqual(trend["lines"]["unloaded"]["runs"], ["0.0,100.0 33.33,0.0 66.67,50.0"])
        self.assertEqual(trend["lines"]["arrived"]["runs"], ["0.0,75.0 33.33,25.0 66.67,100.0"])
        self.assertEqual(trend["lines"]["unloaded"]["end"], {"x": 66.67, "y": 50.0, "value": 320, "above": True})
        self.assertEqual(trend["lines"]["unloaded"]["total"], 1060)  # what is entered so far still counts
        self.assertEqual(trend["average"], 320)  # 300, 340, 320: the closed day short of two counts
        self.assertEqual(trend["partial"], {"unloaded": 100, "arrived": 90, "missing": 150})
        # Open but complete is not partial.
        trend = views_staff._trend({**series, "missing": [0] * 4}, [True] * 4)
        self.assertEqual((trend["lines"]["unloaded"]["end"]["x"], trend["partial"]), (100.0, None))
        # A partial day before the last one (never at the real clock) is a gap like a day without numbers.
        trend = views_staff._trend({**series, "missing": [2, 5, 0, 0]}, [False, True, False, False])
        self.assertEqual(trend["lines"]["unloaded"]["runs"], ["0.0,25.0 0.0,25.0", "66.67,20.0 100.0,75.0"])
        self.assertIsNone(trend["partial"])
        # The first day of data: nothing else to compare it with, so it is drawn.
        first = {**series, "unloaded": [None, None, None, 100], "arrived": [None, None, None, 90]}
        trend = views_staff._trend(first, [False, False, False, True])
        self.assertEqual(trend["lines"]["unloaded"]["runs"], ["100.0,0.0 100.0,0.0"])
        self.assertEqual((trend["average"], trend["partial"]["missing"]), (None, 150))

    def test_the_stock_of_one_mtu_names_six_stations_or_five_and_the_rest(self):
        def stock(*values):
            return [(Station(name=f"С{i}"), v) for i, v in enumerate(values)]

        self.assertEqual(views_staff._top(stock(1, 5, 0, -2, 5)), [("С1", 5), ("С4", 5), ("С0", 1)])
        self.assertEqual(views_staff._top(stock(*range(1, 7))), [(f"С{i}", i + 1) for i in reversed(range(6))])
        self.assertEqual(
            views_staff._top(stock(*range(1, 9))),
            [("С7", 8), ("С6", 7), ("С5", 6), ("С4", 5), ("С3", 4), ("бошқа 3 станция", 6)],
        )

    def test_the_scale_is_round_steps_from_at_or_below_the_lowest_to_at_or_above_the_highest(self):
        for lo, hi, ticks in [
            (0, 0, [0, 1]),
            (3, 3, [3, 4]),
            (250, 400, [250, 300, 350, 400]),
            (161, 604, [0, 200, 400, 600, 800]),
        ]:
            with self.subTest(lo=lo, hi=hi):
                self.assertEqual(views_staff._ticks(lo, hi), ticks)
