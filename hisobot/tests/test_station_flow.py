"""The station's way through the site: MTU → station → PIN → the two open periods → the entry form."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from hisobot import periods
from hisobot.balance import MESSAGES
from hisobot.models import Change, Entry, LoginFailure, Mtu, Station, User
from hisobot.tests.test_periods import tk

PIN = "12345678"
NOW = tk(12, 9, 5)  # the night period of 12.10 ended at 08:00; the day period of 11.10 is still open
NIGHT, DAY_BEFORE = tk(12, 8), tk(11, 20)  # the two open periods, newest first
TONIGHT = tk(12, 20)  # not ended at NOW
GAP = "Олдинги давр киритилмаган"
STALE = "Маълумот ўзгарган, қайта текширинг"


def at(moment):
    """Freeze "now" for the views, balance, access and the session machinery."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher", "hisobot.access.PinHasher"])
class StationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mtu = Mtu.objects.create(name="Тошкент", sort=1)
        cls.other_mtu = Mtu.objects.create(name="Қарши", sort=2)
        # Opened at the start of the day period of 11.10: the night period of 12.10 starts from 10.
        cls.station = cls.make("Чуқурсой", opening_remaining=10, opening_at=tk(11, 8), active_from=tk(11, 8))
        cls.fresh = cls.make("Хамза")  # no entry yet: its first one asks for the opening balance
        cls.other = cls.make(
            "Нишон", mtu=cls.other_mtu, opening_remaining=7, opening_at=tk(11, 8), active_from=tk(11, 8)
        )

    @classmethod
    def make(cls, name, mtu=None, **fields):
        fields.setdefault("active_from", tk(1, 8))
        fields.setdefault("pin_hash", make_password(PIN))
        return Station.objects.create(mtu=mtu or cls.mtu, name=name, pin_version=1, **fields)

    def setUp(self):
        clock = at(NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def login(self, station=None, client=None, pin=PIN):
        station = station or self.station
        return (client or self.client).post(reverse("station_login", args=[station.pk]), {"pin": pin})

    def entry_url(self, pe):
        return reverse("station_entry", args=[periods.to_slug(pe)])

    def submit(self, pe, *, client=None, follow=False, **data):
        """POST the entry form as rendered for a station opened with 10 and no entry for `pe` yet."""
        data = {"version": "0", "prev": "10", **data}
        return (client or self.client).post(self.entry_url(pe), data, follow=follow)

    # --- the way in ------------------------------------------------------------------

    def test_happy_path_from_the_mtu_list_to_a_first_entry(self):
        home = self.client.get(reverse("home"))
        self.assertContains(home, f'href="{reverse("mtu_stations", args=[self.mtu.pk])}"')
        self.assertContains(home, f'href="{reverse("staff_login")}"')
        self.assertContains(home, "МТУ / Бошқарма учун кириш")

        stations = self.client.get(reverse("mtu_stations", args=[self.mtu.pk]))
        self.assertContains(stations, f'href="{reverse("station_login", args=[self.fresh.pk])}"')
        self.assertNotContains(stations, "Нишон")  # another MTU's station

        pin_page = self.client.get(reverse("station_login", args=[self.fresh.pk]))
        self.assertContains(pin_page, "Хамза")
        for attribute in ('name="pin"', 'inputmode="numeric"', 'autocomplete="off"', 'maxlength="9"'):
            self.assertContains(pin_page, attribute)

        # Typed as printed on the PIN sheet.
        self.assertRedirects(self.login(self.fresh, pin="1234 5678"), reverse("station_home"))

        page = self.client.get(reverse("station_home"))
        self.assertContains(page, "Топширилмаган", count=2)
        self.assertContains(page, f'href="{self.entry_url(NIGHT)}"')

        form = self.client.get(self.entry_url(NIGHT))
        self.assertContains(form, "Давр бошидаги қолдиқ")
        self.assertContains(form, 'name="opening"')
        self.assertContains(form, '<input type="hidden" name="prev" id="id_prev">', html=True)  # no start yet
        self.assertContains(form, "novalidate")

        response = self.submit(NIGHT, opening="10", unloaded="5", arrived="3", prev="", follow=True)

        self.assertRedirects(response, reverse("station_home"))
        self.assertContains(response, '<div class="notice-ok" role="status">Қабул қилинди</div>', html=True)
        self.assertContains(response, "Қабул қилинди 09:05")
        self.assertContains(response, "Тузатиш")
        entry = Entry.objects.get()
        self.assertEqual(
            (entry.station, entry.period_end, entry.unloaded, entry.arrived, entry.remaining),
            (self.fresh, NIGHT, 5, 3, 8),
        )
        self.assertEqual(Station.objects.get(pk=self.fresh.pk).opening_remaining, 10)
        self.assertEqual(set(Change.objects.values_list("kind", "user")), {("opening", None), ("entry", None)})
        self.assertRedirects(self.client.get(reverse("home")), reverse("station_home"))

    def test_wrong_pin(self):
        response = self.login(pin="00000000")

        self.assertContains(response, "ПИН нотўғри")
        self.assertNotIn("station_id", self.client.session)

    def test_a_locked_station_is_told_until_when(self):
        Station.objects.filter(pk=self.station.pk).update(locked_until=NOW + timedelta(minutes=15))
        self.assertContains(
            self.login(), "Кириш вақтинча ёпилган. 09:20 дан кейин уриниб кўринг ёки МТУга мурожаат қилинг"
        )
        # A lock that ends on another day names the day as well.
        Station.objects.filter(pk=self.station.pk).update(locked_until=NOW + timedelta(hours=24))
        self.assertContains(self.login(), "13.10.2026 09:05 дан кейин уриниб кўринг")
        self.assertNotIn("station_id", self.client.session)

    def test_a_throttled_address_is_told_to_wait(self):
        LoginFailure.objects.bulk_create(LoginFailure(at=NOW, ip="127.0.0.1", station=self.other) for _ in range(30))
        self.assertContains(self.login(), "Жуда кўп уриниш. Бироздан сўнг қайта уриниб кўринг")
        self.assertNotIn("station_id", self.client.session)

    def test_a_station_without_a_pin(self):
        blank = self.make("ПИНсиз", pin_hash="")
        self.assertContains(self.login(blank), "Бу станцияга ПИН ҳали берилмаган. МТУга мурожаат қилинг")

    def test_an_inactive_or_unknown_station_has_no_login_page(self):
        gone = self.make("Эски", active_to=tk(5, 8))
        url = reverse("station_login", args=[gone.pk])

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"pin": PIN}).status_code, 404)
        self.assertEqual(self.client.get(reverse("station_login", args=[999999])).status_code, 404)
        self.assertNotIn("station_id", self.client.session)
        self.assertIsNone(Station.objects.get(pk=gone.pk).last_login_at)
        self.assertNotContains(self.client.get(reverse("mtu_stations", args=[self.mtu.pk])), "Эски")

    def test_mtu_page_lists_its_active_stations_and_filters_by_name(self):
        url = reverse("mtu_stations", args=[self.mtu.pk])
        page = self.client.get(url)
        self.assertContains(page, "Чуқурсой")
        self.assertContains(page, "Хамза")

        # Without JavaScript the search box is a GET form.
        filtered = self.client.get(url, {"q": "хам"})
        self.assertContains(filtered, "Хамза")
        self.assertNotContains(filtered, "Чуқурсой")
        self.assertEqual(self.client.get(reverse("mtu_stations", args=[999999])).status_code, 404)

    def test_staff_login(self):
        User.objects.create_user("toshkent", password="secret-pass", mtu=self.mtu)
        url = reverse("staff_login")

        bad = self.client.post(url, {"username": "toshkent", "password": "wrong"})
        self.assertContains(bad, "Логин ёки парол нотўғри")
        self.assertContains(bad, 'value="toshkent"')

        ok = self.client.post(url, {"username": "toshkent", "password": "secret-pass"})
        self.assertRedirects(ok, reverse("staff_now"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["staff_login_at"], NOW.timestamp())

    def test_home_sends_a_signed_in_principal_to_its_own_start_page(self):
        self.client.force_login(User.objects.create_user("toshkent", mtu=self.mtu))
        self.assertRedirects(self.client.get(reverse("home")), reverse("staff_now"), fetch_redirect_response=False)

        self.login()  # replaces the staff session
        self.assertRedirects(self.client.get(reverse("home")), reverse("station_home"))

    def test_logout_is_post_only(self):
        self.login()

        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.get(reverse("station_home")).status_code, 200)

        self.assertRedirects(self.client.post(reverse("logout")), reverse("home"))
        self.assertRedirects(self.client.get(reverse("station_home")), reverse("home"))

    def test_station_pages_need_a_station_session(self):
        for url in (reverse("station_home"), self.entry_url(NIGHT)):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse("home"))

    # --- the station's home page -------------------------------------------------------

    def test_station_home_shows_the_open_periods_newest_first(self):
        self.login()
        page = self.client.get(reverse("station_home")).content.decode()

        self.assertIn("Сервер вақти: 12.10.2026 09:05", page)
        self.assertLess(page.index("11.10 20:00 – 12.10 08:00"), page.index("11.10 08:00 – 11.10 20:00"))
        self.assertLess(page.index(self.entry_url(NIGHT)), page.index(self.entry_url(DAY_BEFORE)))
        self.assertLess(page.index("13.10 08:00 гача"), page.index("12.10 20:00 гача"))
        self.assertIn("20:00 да очилади", page)
        self.assertNotIn(self.entry_url(TONIGHT), page)

    def test_station_home_shows_saved_numbers_and_the_last_seven_days(self):
        self.login()
        self.submit(NIGHT, unloaded="5", arrived="3")

        response = self.client.get(reverse("station_home"))

        self.assertContains(response, "Қабул қилинди 09:05", count=1)
        self.assertContains(response, "Топширилмаган", count=1)
        # Report days 12.10 back to 06.10; the day period of 12.10 has not ended, so it is blank, not missing.
        self.assertContains(response, '<th scope="row"', count=7)
        self.assertContains(response, "06.10.2026")
        self.assertNotContains(response, "05.10.2026")
        self.assertInHTML(
            """<tr><th scope="row" class="nowrap">12.10.2026</th>
            <td class="num">5</td><td class="num">3</td><td class="num">8</td>
            <td class="num">—</td><td class="num">—</td><td class="num">—</td>
            <td class="num">5</td></tr>""",
            response.content.decode(),
        )

    def test_a_period_before_the_opening_cannot_be_entered(self):
        # The first entry was the night period of 12.10, so the day period of 11.10 lies before the opening.
        late = self.make("Кечки", opening_remaining=4, opening_at=DAY_BEFORE, active_from=DAY_BEFORE)
        self.login(late)

        page = self.client.get(reverse("station_home"))
        self.assertContains(page, f'href="{self.entry_url(NIGHT)}"')
        self.assertNotContains(page, f'href="{self.entry_url(DAY_BEFORE)}"')
        self.assertContains(page, MESSAGES["before_opening"])

        response = self.client.get(self.entry_url(DAY_BEFORE), follow=True)
        self.assertRedirects(response, reverse("station_home"))
        self.assertContains(response, MESSAGES["before_opening"])

    # --- the entry form ----------------------------------------------------------------

    def test_entry_form_shows_the_start_balance_and_the_gap_warning(self):
        self.login()

        night = self.client.get(self.entry_url(NIGHT))
        self.assertContains(night, GAP)  # the day period of 11.10 has no entry
        self.assertContains(night, "Давр бошида")
        self.assertContains(night, 'name="prev" value="10"')
        self.assertContains(night, 'name="version" value="0"')
        self.assertNotContains(night, 'name="opening"')
        self.assertContains(night, "Қолдиқни қўлда киритиш")
        self.assertContains(night, 'id="qolgan"')
        self.assertContains(night, 'name="harakatsiz"')
        self.assertContains(night, "Ҳаракат бўлмади (0 / 0)")
        self.assertNotContains(self.client.get(self.entry_url(DAY_BEFORE)), GAP)

        self.submit(DAY_BEFORE, unloaded="4", arrived="1")  # 10 + 1 - 4
        night = self.client.get(self.entry_url(NIGHT))
        self.assertNotContains(night, GAP)
        self.assertContains(night, 'name="prev" value="7"')

    def test_a_saved_period_can_be_corrected(self):
        self.login()
        self.submit(NIGHT, unloaded="5", arrived="3")

        form = self.client.get(self.entry_url(NIGHT))
        self.assertContains(form, 'name="version" value="1"')
        self.assertContains(form, 'name="unloaded" value="5"')
        self.assertRedirects(self.submit(NIGHT, version="1", unloaded="6", arrived="3"), reverse("station_home"))

        entry = Entry.objects.get()
        self.assertEqual((entry.unloaded, entry.version, entry.remaining), (6, 2, 7))

    def test_bad_period_slug_is_404(self):
        self.login()
        for slug in ("2026101209", "abc", "20261012"):
            with self.subTest(slug=slug):
                self.assertEqual(self.client.get(f"/stansiya/davr/{slug}/").status_code, 404)

    def test_a_period_that_has_not_ended_cannot_be_entered(self):
        self.login()

        response = self.client.get(self.entry_url(TONIGHT), follow=True)
        self.assertRedirects(response, reverse("station_home"))
        self.assertContains(response, MESSAGES["window"])

        self.assertRedirects(self.submit(TONIGHT, unloaded="1", arrived="1"), reverse("station_home"))
        self.assertFalse(Entry.objects.exists())

    def test_a_period_closes_for_the_station_24_hours_after_its_end(self):
        self.login()
        with at(tk(13, 7, 59, 59)):
            self.assertEqual(self.client.get(self.entry_url(NIGHT)).status_code, 200)
        with at(tk(13, 8)):
            self.assertRedirects(self.client.get(self.entry_url(NIGHT)), reverse("station_home"))
            self.assertRedirects(self.submit(NIGHT, unloaded="1", arrived="1"), reverse("station_home"))
        self.assertRedirects(self.client.get(self.entry_url(tk(11, 8))), reverse("station_home"))  # ended 25 h ago
        self.assertFalse(Entry.objects.exists())

    def test_no_movement_button_saves_zeros(self):
        self.login()

        response = self.submit(NIGHT, unloaded="", arrived="", harakatsiz="1")

        self.assertRedirects(response, reverse("station_home"))
        entry = Entry.objects.get()
        self.assertEqual((entry.unloaded, entry.arrived, entry.remaining), (0, 0, 10))

    def test_empty_fields_are_refused(self):
        self.login()

        response = self.submit(NIGHT, unloaded="", arrived="3")

        self.assertContains(response, "Майдонни тўлдиринг")
        self.assertFalse(Entry.objects.exists())

    def test_a_negative_remaining_is_refused_and_nothing_is_saved(self):
        self.login()

        response = self.submit(NIGHT, unloaded="11", arrived="0")

        self.assertContains(response, MESSAGES["negative"])
        self.assertContains(response, 'name="unloaded" value="11"')  # what was typed stays
        self.assertFalse(Entry.objects.exists())
        self.assertFalse(Change.objects.exists())

        # Counted by hand: the override is the remaining, and the same numbers go through.
        self.assertRedirects(self.submit(NIGHT, unloaded="11", arrived="0", override="0"), reverse("station_home"))
        self.assertEqual(Entry.objects.get().remaining, 0)

    def test_a_second_device_with_a_stale_form_is_told_to_check_again(self):
        desk = Client()
        self.login()
        self.login(client=desk)

        self.assertRedirects(self.submit(NIGHT, unloaded="5", arrived="3"), reverse("station_home"))
        # The desk still shows the form it loaded before the phone saved (version 0).
        response = self.submit(NIGHT, client=desk, unloaded="4", arrived="2")

        self.assertContains(response, STALE)
        entry = Entry.objects.get()
        self.assertEqual((entry.unloaded, entry.arrived, entry.version), (5, 3, 1))
        # The form now shows what is saved, so the next submit is a deliberate correction.
        self.assertContains(response, 'name="version" value="1"')
        self.assertContains(response, 'name="unloaded" value="5"')

    def test_a_station_id_in_the_form_body_is_ignored(self):
        self.login()

        response = self.submit(NIGHT, unloaded="1", arrived="2", station_id=self.other.pk, station=self.other.pk)

        self.assertRedirects(response, reverse("station_home"))
        self.assertEqual(list(Entry.objects.values_list("station", flat=True)), [self.station.pk])
        self.assertFalse(Change.objects.filter(station=self.other).exists())
