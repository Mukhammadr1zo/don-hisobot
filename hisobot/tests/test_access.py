import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.contrib.auth.hashers import check_password, make_password
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import path
from django.utils import timezone

from hisobot import access
from hisobot.access import Login
from hisobot.models import Change, LoginFailure, Mtu, Station, User

PIN = "12345678"
WRONG = "00000000"
AUTH_KEY = "_auth_user_id"


def pin_login_view(request, station_id):
    station = Station.objects.get(pk=station_id)
    return HttpResponse(access.station_login(request, station, request.POST["pin"]).name)


def staff_login_view(request):
    return HttpResponse(access.staff_login(request, request.POST["username"], request.POST["password"]).name)


def logout_view(request):
    access.logout(request)
    return HttpResponse("out")


@access.station_required
def station_view(request):
    return HttpResponse(f"station {request.station.pk}")


@access.staff_required
def staff_view(request):
    return HttpResponse("staff")


@access.boshqarma_required
def boshqarma_view(request):
    return HttpResponse("boshqarma")


@access.staff_required
def scope_view(request):
    stations, mtu = access.scope(request)
    return JsonResponse({"stations": [s.pk for s in stations], "mtu": mtu and mtu.pk})


# The project's URLconf belongs to another task; these tests bring their own.
urlpatterns = [
    path("", lambda request: HttpResponse("home"), name="home"),
    path("kirish/", lambda request: HttpResponse("kirish"), name="staff_login"),
    path("t/pin/<int:station_id>/", pin_login_view),
    path("t/staff-login/", staff_login_view),
    path("t/logout/", logout_view),
    path("t/station/", station_view),
    path("t/staff/", staff_view),
    path("t/boshqarma/", boshqarma_view),
    path("t/scope/", scope_view),
]


def at(moment):
    """Freeze "now" for access.py and the session machinery."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


access_settings = override_settings(
    ROOT_URLCONF=__name__,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher", "hisobot.access.PinHasher"],
    TRUST_PROXY=False,
)


@access_settings
class AccessTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Sort order is the reverse of creation order, so ordering by id cannot pass for ordering by sort.
        cls.mtu_a = Mtu.objects.create(name="А", sort=2)
        cls.mtu_b = Mtu.objects.create(name="Б", sort=1)
        cls.boss = User.objects.create_user("boshqarma", password="boss-secret")
        cls.user_a = User.objects.create_user("mtu_a", password="a-secret", mtu=cls.mtu_a)
        cls.station = Station.objects.create(mtu=cls.mtu_a, name="Чуқурсой", pin_hash=make_password(PIN), pin_version=1)
        cls.other = Station.objects.create(mtu=cls.mtu_b, name="Нишон", pin_hash=make_password(PIN), pin_version=1)

    def pin_login(self, station, pin, ip="10.0.0.1", client=None):
        response = (client or self.client).post(f"/t/pin/{station.pk}/", {"pin": pin}, REMOTE_ADDR=ip)
        return Login[response.content.decode()]

    def staff_login(self, username, password, ip="10.0.0.1"):
        response = self.client.post("/t/staff-login/", {"username": username, "password": password}, REMOTE_ADDR=ip)
        return Login[response.content.decode()]

    def stored(self, station):
        return Station.objects.get(pk=station.pk)

    def current_station(self):
        request = RequestFactory().get("/")
        request.session = self.client.session
        return access.current_station(request)


class IpTests(SimpleTestCase):
    @override_settings(TRUST_PROXY=False)
    def test_client_ip_ignores_forwarded_header_without_a_proxy(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.1.1.1")
        self.assertEqual(access.client_ip(request), "10.0.0.1")

    @override_settings(TRUST_PROXY=True)
    def test_client_ip_behind_a_proxy_is_the_rightmost_forwarded_entry(self):
        request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.2", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2")
        self.assertEqual(access.client_ip(request), "2.2.2.2")
        request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.2")
        self.assertEqual(access.client_ip(request), "172.18.0.2")

    @override_settings(TRUST_PROXY=True)
    def test_client_ip_is_always_a_storable_address(self):
        request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.2", HTTP_X_FORWARDED_FOR="1.1.1.1, not-an-ip")
        self.assertEqual(access.client_ip(request), "0.0.0.0")
        request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.2", HTTP_X_FORWARDED_FOR="::ffff:1.2.3.4")
        self.assertEqual(access.client_ip(request), "1.2.3.4")

    def test_ip_bucket(self):
        self.assertEqual(access.ip_bucket("10.1.2.3"), "10.1.2.3")
        self.assertEqual(access.ip_bucket("2001:db8:1:2:aaaa::1"), "2001:db8:1:2::")
        self.assertEqual(access.ip_bucket("2001:db8:1:2:aaaa::1"), access.ip_bucket("2001:db8:1:2:bbbb::2"))
        self.assertNotEqual(access.ip_bucket("2001:db8:1:2::1"), access.ip_bucket("2001:db8:1:3::1"))


class BudgetTests(SimpleTestCase):
    def test_brute_force_budget_per_station_per_year(self):
        # Worst case: every guess is made the instant the lock allows it, from as many IPs as needed.
        elapsed, guesses = timedelta(0), 0
        while elapsed < timedelta(days=365):
            guesses += 1
            elapsed += access._lock_duration(guesses) or timedelta(0)
        self.assertLessEqual(guesses, 2000)
        self.assertGreater(guesses, 365)  # the simulation really ran the 24 h steps


class PinTests(AccessTestCase):
    def test_new_pin_is_eight_random_digits(self):
        pins = {access.new_pin() for _ in range(50)}
        for pin in pins:
            self.assertRegex(pin, r"^\d{8}$")
        self.assertGreater(len(pins), 1)

    def test_format_pin(self):
        self.assertEqual(access.format_pin("12345678"), "1234 5678")

    def test_set_pin_stores_a_hash_bumps_the_version_and_logs_a_change(self):
        Station.objects.filter(pk=self.station.pk).update(
            fail_streak=7, locked_until=timezone.now() + timedelta(hours=1), last_login_at=timezone.now()
        )

        pin = access.set_pin(self.station, actor=self.user_a, ip="10.0.0.9")

        self.assertRegex(pin, r"^\d{8}$")
        stored = self.stored(self.station)
        self.assertNotIn(pin, stored.pin_hash)
        self.assertTrue(check_password(pin, stored.pin_hash))
        self.assertFalse(check_password(PIN, stored.pin_hash))
        self.assertEqual(stored.pin_version, 2)
        self.assertEqual((stored.fail_streak, stored.locked_until, stored.last_login_at), (0, None, None))
        # The caller's object is brought up to date, so a later save() cannot write the old PIN back.
        self.assertEqual((self.station.pin_hash, self.station.pin_version), (stored.pin_hash, 2))
        change = Change.objects.get()
        self.assertEqual(
            (change.kind, change.station, change.user, change.ip), ("pin", self.station, self.user_a, "10.0.0.9")
        )
        self.assertNotIn(pin, json.dumps([change.old, change.new]))

    def test_issue_unused_pins_skips_used_and_reissues_the_still_unused(self):
        never_issued = Station.objects.create(mtu=self.mtu_a, name="Янги")
        self.assertEqual(self.pin_login(self.station, PIN), Login.OK)  # self.station is now in use
        stations = Station.objects.order_by("pk")

        first = access.issue_unused_pins(stations, actor=self.boss, ip="10.0.0.9")
        second = access.issue_unused_pins(stations, actor=self.boss, ip="10.0.0.9")

        self.assertEqual([station for station, _ in first], [self.other, never_issued])
        self.assertEqual([station for station, _ in second], [self.other, never_issued])
        for (station, old), (_, new) in zip(first, second):
            self.assertNotEqual(old, new)
            self.assertFalse(check_password(old, self.stored(station).pin_hash))
            self.assertTrue(check_password(new, self.stored(station).pin_hash))
        self.assertTrue(check_password(PIN, self.stored(self.station).pin_hash))
        self.assertEqual(self.stored(self.station).pin_version, 1)
        self.assertEqual(Change.objects.filter(kind="pin", user=self.boss).count(), 4)


class StationLoginTests(AccessTestCase):
    def test_correct_pin_opens_a_session_for_the_station(self):
        self.assertEqual(self.pin_login(self.station, PIN), Login.OK)

        self.assertEqual(self.client.session["station_id"], self.station.pk)
        self.assertEqual(self.current_station(), self.station)
        self.assertEqual(self.client.get("/t/station/").content.decode(), f"station {self.station.pk}")
        self.assertIsNotNone(self.stored(self.station).last_login_at)

    def test_pin_may_be_typed_with_the_printed_space(self):
        self.assertEqual(self.pin_login(self.station, " 1234 5678 "), Login.OK)

    def test_without_a_session_the_station_view_redirects_home(self):
        self.assertIsNone(self.current_station())
        self.assertRedirects(self.client.get("/t/station/"), "/", fetch_redirect_response=False)

    def test_a_new_pin_ends_the_session(self):
        self.pin_login(self.station, PIN)
        access.set_pin(self.station, actor=self.user_a, ip="10.0.0.9")

        self.assertIsNone(self.current_station())
        self.assertRedirects(self.client.get("/t/station/"), "/", fetch_redirect_response=False)

    def test_deactivation_ends_the_session(self):
        self.pin_login(self.station, PIN)
        Station.objects.filter(pk=self.station.pk).update(active_to=timezone.now())

        self.assertIsNone(self.current_station())
        self.assertRedirects(self.client.get("/t/station/"), "/", fetch_redirect_response=False)

    def test_station_without_a_pin(self):
        blank = Station.objects.create(mtu=self.mtu_a, name="ПИНсиз")

        self.assertEqual(self.pin_login(blank, ""), Login.NO_PIN)
        self.assertEqual(self.pin_login(blank, PIN), Login.NO_PIN)
        self.assertEqual(self.stored(blank).fail_streak, 0)
        self.assertFalse(LoginFailure.objects.exists())

    def test_four_failures_do_not_lock_and_success_resets_the_streak(self):
        for _ in range(4):
            self.assertEqual(self.pin_login(self.station, WRONG), Login.BAD)
        self.assertEqual(self.stored(self.station).fail_streak, 4)
        self.assertIsNone(self.stored(self.station).locked_until)
        self.assertNotIn("station_id", self.client.session)

        self.assertEqual(self.pin_login(self.station, PIN), Login.OK)
        self.assertEqual(self.stored(self.station).fail_streak, 0)

        for _ in range(4):
            self.assertEqual(self.pin_login(self.station, WRONG), Login.BAD)
        self.assertIsNone(self.stored(self.station).locked_until)

    def test_every_fifth_failure_locks_for_longer(self):
        locks = {
            5: timedelta(minutes=15),
            10: timedelta(hours=1),
            15: timedelta(hours=4),
            20: timedelta(hours=24),
            25: timedelta(hours=24),
        }
        now = timezone.now()
        for n in range(1, 26):
            with at(now):
                self.assertEqual(self.pin_login(self.station, WRONG), Login.BAD, n)
                stored = self.stored(self.station)
                self.assertEqual(stored.fail_streak, n)
                if n in locks:
                    self.assertEqual(stored.locked_until, now + locks[n], n)
                    self.assertEqual(self.pin_login(self.station, PIN), Login.LOCKED, n)
                    self.assertNotIn("station_id", self.client.session)
                    now = stored.locked_until  # the first instant the station accepts attempts again
                else:
                    self.assertFalse(stored.locked_until and stored.locked_until > now, n)
        with at(now):
            self.assertEqual(self.pin_login(self.station, PIN), Login.OK)
        stored = self.stored(self.station)
        self.assertEqual((stored.fail_streak, stored.locked_until), (0, None))

    def test_a_lock_does_not_affect_an_existing_session(self):
        self.pin_login(self.station, PIN)
        attacker = Client()
        for _ in range(5):
            self.pin_login(self.station, WRONG, ip="10.0.0.2", client=attacker)
        self.assertGreater(self.stored(self.station).locked_until, timezone.now())

        self.assertEqual(self.current_station(), self.station)
        self.assertEqual(self.client.get("/t/station/").status_code, 200)

    def test_unlock_clears_only_the_lock(self):
        for _ in range(5):
            self.pin_login(self.station, WRONG)
        self.assertEqual(self.pin_login(self.station, PIN), Login.LOCKED)

        access.unlock(self.station, actor=self.user_a, ip="10.0.0.9")

        stored = self.stored(self.station)
        self.assertEqual((stored.locked_until, stored.fail_streak, stored.pin_version), (None, 5, 1))
        change = Change.objects.get()
        self.assertEqual(
            (change.kind, change.station, change.user, change.ip), ("unlock", self.station, self.user_a, "10.0.0.9")
        )
        self.assertEqual(self.pin_login(self.station, PIN), Login.OK)  # the PIN was kept

    def test_thirty_failures_from_one_ip_throttle_that_ip_only(self):
        stations = [
            Station.objects.create(mtu=self.mtu_a, name=f"Станция {i}", pin_hash=make_password(PIN)) for i in range(10)
        ]
        now = timezone.now()
        with at(now):
            for station in stations:
                for _ in range(3):
                    self.assertEqual(self.pin_login(station, WRONG, ip="10.0.0.1"), Login.BAD)

            self.assertEqual(self.pin_login(self.station, PIN, ip="10.0.0.1"), Login.THROTTLED)
            self.assertNotIn("station_id", self.client.session)
            self.assertEqual(LoginFailure.objects.count(), 30)  # a throttled attempt is not another failure
            self.assertEqual(self.pin_login(self.station, PIN, ip="10.0.0.2"), Login.OK)
        with at(now + timedelta(hours=1)):
            self.assertEqual(self.pin_login(self.station, PIN, ip="10.0.0.1"), Login.OK)

    def test_a_locked_station_says_locked_even_from_a_throttled_ip(self):
        # The lock is the station's own state and the cheaper check: it is reported first.
        locked = Station.objects.create(
            mtu=self.mtu_a, name="Блокли", pin_hash=make_password(PIN), locked_until=timezone.now() + timedelta(hours=1)
        )
        others = [Station.objects.create(mtu=self.mtu_a, name=f"Бошқа {i}", pin_hash=make_password(PIN)) for i in range(6)]
        for attempt in range(30):  # 5 each, in turn: every one is a failure, none hits a lock first
            self.assertEqual(self.pin_login(others[attempt % 6], WRONG, ip="10.0.0.1"), Login.BAD)
        self.assertEqual(self.pin_login(locked, PIN, ip="10.0.0.1"), Login.LOCKED)
        self.assertEqual(self.pin_login(self.station, PIN, ip="10.0.0.1"), Login.THROTTLED)

    def test_failures_are_counted_per_ipv6_64(self):
        self.pin_login(self.station, WRONG, ip="2001:db8:1:2:aaaa::1")
        self.pin_login(self.station, WRONG, ip="2001:db8:1:2:bbbb::2")

        self.assertEqual(
            list(LoginFailure.objects.values_list("ip", "station")), [("2001:db8:1:2::", self.station.pk)] * 2
        )

    def test_a_failure_purges_rows_older_than_seven_days(self):
        now = timezone.now()
        LoginFailure.objects.create(ip="10.9.9.9", at=now - timedelta(days=7, minutes=1))
        recent = LoginFailure.objects.create(ip="10.9.9.9", at=now - timedelta(days=6))

        self.pin_login(self.station, WRONG)

        self.assertEqual(LoginFailure.objects.count(), 2)
        self.assertTrue(LoginFailure.objects.filter(pk=recent.pk).exists())


@access_settings
class ParallelGuessTests(TransactionTestCase):
    def test_parallel_guesses_cannot_outrun_the_lock(self):
        station = Station.objects.create(mtu=Mtu.objects.create(name="А"), name="Чуқурсой", pin_hash=make_password(PIN))

        def slow_check(*args):
            time.sleep(0.05)  # the real hasher's cost in miniature: keeps every guess in flight at once
            return check_password(*args)

        def guess(n):
            try:
                response = Client().post(f"/t/pin/{station.pk}/", {"pin": WRONG}, REMOTE_ADDR=f"10.0.1.{n}")
                return response.content.decode()
            finally:
                connection.close()  # this thread's own connection

        with mock.patch("hisobot.access.check_password", slow_check), ThreadPoolExecutor(8) as pool:
            results = list(pool.map(guess, range(8)))

        self.assertEqual(sorted(results), ["BAD"] * 5 + ["LOCKED"] * 3)
        self.assertEqual(Station.objects.get(pk=station.pk).fail_streak, 5)


class StaffLoginTests(AccessTestCase):
    def test_ten_wrong_passwords_throttle_the_ip_not_the_username(self):
        now = timezone.now()
        with at(now):
            for _ in range(10):
                self.assertEqual(self.staff_login("mtu_a", "wrong", ip="10.0.0.1"), Login.BAD)

            self.assertEqual(self.staff_login("mtu_a", "a-secret", ip="10.0.0.1"), Login.THROTTLED)
            self.assertNotIn(AUTH_KEY, self.client.session)
            self.assertEqual(LoginFailure.objects.count(), 10)
            self.assertEqual(self.staff_login("mtu_a", "a-secret", ip="10.0.0.2"), Login.OK)
            self.assertEqual(self.client.get("/t/staff/").status_code, 200)
        with at(now + timedelta(minutes=15)):
            self.assertEqual(self.staff_login("mtu_a", "a-secret", ip="10.0.0.1"), Login.OK)

    def test_a_failure_records_the_submitted_username_truncated(self):
        self.assertEqual(self.staff_login("x" * 200, "wrong"), Login.BAD)

        failure = LoginFailure.objects.get()
        self.assertEqual((failure.username, failure.station, failure.ip), ("x" * 150, None, "10.0.0.1"))

    def test_pin_and_staff_failures_are_throttled_separately(self):
        for _ in range(10):
            self.staff_login("mtu_a", "wrong")
        self.assertEqual(self.pin_login(self.station, PIN), Login.OK)

        LoginFailure.objects.all().delete()
        stations = [
            Station.objects.create(mtu=self.mtu_a, name=f"Станция {i}", pin_hash=make_password(PIN)) for i in range(10)
        ]
        for station in stations:
            for _ in range(3):
                self.pin_login(station, WRONG)
        self.assertEqual(self.staff_login("mtu_a", "a-secret"), Login.OK)

    def test_staff_session_ends_twelve_hours_after_login(self):
        now = timezone.now()
        with at(now):
            self.assertEqual(self.staff_login("mtu_a", "a-secret"), Login.OK)
        with at(now + timedelta(hours=11, minutes=59)):
            self.assertEqual(self.client.get("/t/staff/").status_code, 200)
        with at(now + timedelta(hours=12, minutes=1)):  # the request at 11:59 did not extend it
            self.assertRedirects(self.client.get("/t/staff/"), "/kirish/", fetch_redirect_response=False)
        self.assertNotIn(AUTH_KEY, self.client.session)

    def test_anonymous_is_sent_to_the_staff_login(self):
        self.assertRedirects(self.client.get("/t/staff/"), "/kirish/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/t/boshqarma/"), "/kirish/", fetch_redirect_response=False)

    def test_a_station_session_is_not_staff_and_survives_the_redirect(self):
        self.pin_login(self.station, PIN)

        self.assertRedirects(self.client.get("/t/staff/"), "/kirish/", fetch_redirect_response=False)
        self.assertEqual(self.current_station(), self.station)

    def test_boshqarma_required(self):
        self.staff_login("mtu_a", "a-secret")
        self.assertEqual(self.client.get("/t/boshqarma/").status_code, 404)

        self.staff_login("boshqarma", "boss-secret")
        self.assertEqual(self.client.get("/t/boshqarma/").content, b"boshqarma")

    def test_staff_login_replaces_a_station_session_and_vice_versa(self):
        self.pin_login(self.station, PIN)
        self.assertEqual(self.staff_login("mtu_a", "a-secret"), Login.OK)
        self.assertNotIn("station_id", self.client.session)
        self.assertRedirects(self.client.get("/t/station/"), "/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/t/staff/").status_code, 200)

        self.assertEqual(self.pin_login(self.station, PIN), Login.OK)
        self.assertNotIn(AUTH_KEY, self.client.session)
        self.assertNotIn("staff_login_at", self.client.session)
        self.assertRedirects(self.client.get("/t/staff/"), "/kirish/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/t/station/").status_code, 200)

    def test_logout_empties_the_session(self):
        self.staff_login("mtu_a", "a-secret")
        self.client.post("/t/logout/")
        self.assertRedirects(self.client.get("/t/staff/"), "/kirish/", fetch_redirect_response=False)

        self.pin_login(self.station, PIN)
        self.client.post("/t/logout/")
        self.assertIsNone(self.current_station())


class ScopeTests(AccessTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a_last = Station.objects.create(mtu=cls.mtu_a, name="Аа", sort=5)
        cls.a_tie_2 = Station.objects.create(mtu=cls.mtu_a, name="Вв", sort=1)
        cls.a_tie_1 = Station.objects.create(mtu=cls.mtu_a, name="Бб", sort=1)
        cls.own = [cls.station, cls.a_tie_1, cls.a_tie_2, cls.a_last]

    def scope(self, query=""):
        return self.client.get(f"/t/scope/{query}")

    def test_stations_for_an_mtu_user_is_its_own_mtu_in_order(self):
        with self.assertNumQueries(1):
            stations = list(access.stations_for(self.user_a))
            self.assertEqual({station.mtu.name for station in stations}, {"А"})
        self.assertEqual(stations, self.own)

    def test_stations_for_boshqarma_is_everything_ordered_by_mtu_sort(self):
        self.assertEqual(list(access.stations_for(self.boss)), [self.other, *self.own])

    def test_mtus_for(self):
        self.assertEqual(list(access.mtus_for(self.boss)), [self.mtu_b, self.mtu_a])
        self.assertEqual(list(access.mtus_for(self.user_a)), [self.mtu_a])

    def test_scope_ignores_the_mtu_parameter_for_an_mtu_user(self):
        self.staff_login("mtu_a", "a-secret")
        expected = {"stations": [station.pk for station in self.own], "mtu": self.mtu_a.pk}

        self.assertEqual(self.scope().json(), expected)
        self.assertEqual(self.scope(f"?mtu={self.mtu_b.pk}").json(), expected)
        self.assertEqual(self.scope("?mtu=999999").json(), expected)

    def test_scope_applies_the_mtu_parameter_for_boshqarma(self):
        self.staff_login("boshqarma", "boss-secret")
        everything = {"stations": [self.other.pk, *(station.pk for station in self.own)], "mtu": None}

        self.assertEqual(self.scope().json(), everything)
        self.assertEqual(self.scope("?mtu=").json(), everything)
        self.assertEqual(
            self.scope(f"?mtu={self.mtu_b.pk}").json(), {"stations": [self.other.pk], "mtu": self.mtu_b.pk}
        )
        for unknown in ("999999", "9" * 30, "abc", "-1", "²"):
            self.assertEqual(self.scope(f"?mtu={unknown}").status_code, 404, unknown)
