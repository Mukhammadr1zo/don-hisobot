from datetime import date, datetime, timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from hisobot import access, balance, periods, reports
from hisobot.models import Change, Entry, Mtu, Station, User
from hisobot.periods import TZ
from hisobot.tests.test_periods import tk

NOW = tk(12, 9)  # 12.10 09:00: the night period of 12.10 has ended, its day period is running
PE = tk(12, 8)  # the last ended period
SLUG = "2026101208"
STALE = "Маълумот ўзгарган, қайта текширинг"


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
        self.assertContains(response, f"{sub.submitted}<small> / {sub.expected}</small>")
        self.assertContains(response, "топширди")
        html = response.content.decode()
        self.assertLess(html.index("Сергели"), html.index("Тошкент-Товар"))
        self.assertLess(html.index("Тошкент-Товар"), html.index("Чуқурсой"))  # submitted, though it sorts first
        for station in (self.second, self.third):
            self.assertInHTML(f'<a class="btn btn-sm btn-primary" href="{entry_url(station, PE)}">Киритиш</a>', html)
        self.assertContains(response, f'href="{entry_url(self.first, PE)}"')
        self.assertContains(response, "5 · 3 · ")

    def test_boshqarma_gets_one_card_per_mtu_linking_to_its_list(self):
        empty = Mtu.objects.create(name="Бухоро", sort=3)
        self.entry(self.first, PE)
        self.login(self.boss)
        response = self.client.get(reverse("staff_now"))

        subs = reports.submission(access.stations_for(self.boss), PE)
        self.assertEqual([(s.mtu, s.submitted, s.expected) for s in subs], [(self.mtu_a, 1, 3), (self.mtu_b, 0, 1)])
        for mtu, counts in [(self.mtu_a, "1<small> / 3</small>"), (self.mtu_b, "0<small> / 1</small>"), (empty, "0<small> / 0</small>")]:
            with self.subTest(mtu=mtu.name):
                self.assertContains(response, f'href="?mtu={mtu.pk}&amp;pe={SLUG}"')
                self.assertContains(response, counts)
        self.assertNotContains(response, "Киритиш")

        response = self.client.get(reverse("staff_now"), {"mtu": self.mtu_b.pk})
        self.assertContains(response, "0<small> / 1</small>")
        self.assertInHTML(f'<a class="btn btn-sm btn-primary" href="{entry_url(self.foreign, PE)}">Киритиш</a>', response.content.decode())
        self.assertNotContains(response, "Чуқурсой")

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

    def test_cells_show_override_missing_negative_and_carried(self):
        night, day = self.fill()
        self.login(self.user_a)
        html = self.client.get(reverse("staff_daily"), {"d": "2026-10-11"}).content.decode()

        def cell(classes, station, pe, value):
            self.assertInHTML(
                f'<td class="{classes}"><a class="cell" href="{entry_url(station, pe)}">{value}</a></td>', html
            )

        cell("num is-override", self.first, night, 4)
        cell("num is-negative", self.first, day, -3)
        cell("num is-missing", self.second, night, "—")
        cell("num is-missing is-carried", self.second, night, 10)  # the opening, carried
        cell("num is-missing is-carried", self.third, day, 8)
        self.assertInHTML('<td class="num">9</td>', html)  # first: both periods in
        self.assertInHTML('<td class="num is-missing">—</td>', html)  # second: nothing at all
        self.assertInHTML('<td class="num">5*</td>', html)  # third: the day period is missing
        self.assertIn("Бир давр киритилмаган", html)
        self.assertInHTML('<a class="btn no-print" href="/xodim/eksport/?d=2026-10-11">Excel юклаб олиш</a>', html)
        self.assertNotIn("<tfoot>", html)

    def test_totals_per_mtu_and_a_grand_total_across_mtus(self):
        self.fill()
        self.login(self.boss)
        html = self.client.get(reverse("staff_daily"), {"d": "2026-10-11"}).content.decode()

        def total(name, *values):
            cells = "".join(f'<td class="num">{v}</td>' for v in values)
            self.assertInHTML(f'<tr class="total"><td></td><th scope="row">{name}</th>{cells}</tr>', html)

        total("Тошкент МТУ жами", 7, 4, 4 + 10 + 8, 7, 0, -3 + 10 + 8, "14*")
        total("Қўқон МТУ жами", 0, 0, 10, 0, 0, 10, "0*")
        total("Жами", 7, 4, 32, 7, 0, 25, "14*")
        self.assertIn("<tfoot>", html)

        response = self.client.get(reverse("staff_daily"), {"d": "2026-10-11", "mtu": self.mtu_a.pk})
        self.assertNotContains(response, "Бета-бекат")
        self.assertNotContains(response, "<tfoot>")
        self.assertContains(response, f'href="/xodim/eksport/?d=2026-10-11&amp;mtu={self.mtu_a.pk}"')

    def test_the_date_defaults_to_the_latest_report_date(self):
        self.login(self.user_a)
        for d in ["", "abc", "2026-13-01", "2026-10-13", "12.10.2026", "1999-12-31"]:
            with self.subTest(d=d):
                response = self.client.get(reverse("staff_daily"), {"d": d})
                self.assertEqual(response.context["day"], date(2026, 10, 12))
        # The night period has ended and links to its form; the running day period stays blank.
        self.assertContains(response, f'href="{entry_url(self.first, PE)}"')
        self.assertNotContains(response, entry_url(self.first, tk(12, 20)))
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
        self.assertInHTML("<div>Қолган (ҳисобланган): 12 → 13</div>", html)
        self.assertInHTML(
            f'<div class="muted small">сабаб: <a href="{entry_url(self.first, p1)}">10.10.2026 тунги давр</a>'
            " тузатилди</div>",
            html,
        )
        self.assertInHTML("<div>Туширилган: — → 3</div>", html)
        self.assertInHTML("<div>Кириб келган: — → 4</div>", html)

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
    def test_list_rows_keep_only_narrow_items_beside_the_station_name(self):
        # A wide badge beside the name squeezes it to a letter a line at 360px (.list lets only the name shrink).
        self.entry(self.first, PE, unloaded=12, arrived=34, calc=56)
        self.entry(self.second, PE, unloaded=1, arrived=1, calc=9, override=7)
        self.login(self.user_a)
        html = self.client.get(reverse("staff_now")).content.decode()
        self.assertNotIn('<span class="badge-warn">Топширилмаган</span>', html)
        self.assertInHTML(
            f'<li><a href="{entry_url(self.first, PE)}"><span>Чуқурсой<br><small class="muted">12 · 34 · '
            f'<span>56</span></small></span><span class="badge-ok">{periods.local(Entry.objects.get(station=self.first).updated_at):%H:%M}</span></a></li>',
            html,
        )
        self.assertIn("· ҳисобланган: 9</small>", html)
