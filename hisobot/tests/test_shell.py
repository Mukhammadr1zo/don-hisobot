import re
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.cookie import CookieStorage
from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse

from hisobot import views_public
from hisobot.forms import EntryForm
from hisobot.models import Mtu, Station, User
from hisobot.periods import TZ

# The URL table of the plan, verbatim: name, kwargs, path, view.
URL_TABLE = [
    ("home", {}, "/", "views_public.home"),
    ("mtu_stations", {"mtu_id": 3}, "/m/3/", "views_public.mtu_stations"),
    ("station_login", {"station_id": 7}, "/s/7/", "views_public.station_login"),
    ("staff_login", {}, "/kirish/", "views_public.staff_login"),
    ("logout", {}, "/chiqish/", "views_public.logout"),
    ("healthz", {}, "/healthz", "views_public.healthz"),
    ("station_home", {}, "/stansiya/", "views_station.home"),
    ("station_entry", {"pe": "2026101208"}, "/stansiya/davr/2026101208/", "views_station.entry"),
    ("staff_now", {}, "/xodim/", "views_staff.now"),
    ("staff_daily", {}, "/xodim/jadval/", "views_staff.daily"),
    ("staff_entry", {"station_id": 7, "pe": "2026101208"}, "/xodim/yozuv/7/2026101208/", "views_staff.entry"),
    ("staff_report", {}, "/xodim/hisobot/", "views_reports.report"),
    ("staff_export", {}, "/xodim/eksport/", "views_reports.export"),
    ("stations", {}, "/xodim/stansiyalar/", "views_stations.index"),
    ("station_edit", {"station_id": 7}, "/xodim/stansiyalar/7/", "views_stations.edit"),
    ("station_toggle", {"station_id": 7}, "/xodim/stansiyalar/7/holat/", "views_stations.toggle"),
    ("station_pin", {"station_id": 7}, "/xodim/stansiyalar/7/pin/", "views_stations.regenerate_pin"),
    ("station_unlock", {"station_id": 7}, "/xodim/stansiyalar/7/blok/", "views_stations.unlock"),
    ("stations_import", {}, "/xodim/stansiyalar/import/", "views_stations.import_roster"),
    ("pin_sheet", {}, "/xodim/stansiyalar/pin-varaq/", "views_stations.pin_sheet"),
    ("password_change", {}, "/xodim/parol/", "views_stations.password_change"),
    ("mtu_passwords", {}, "/xodim/mtu-parol/", "views_stations.mtu_passwords"),
]


class UrlTests(SimpleTestCase):
    def test_every_name_reverses_to_its_path_and_resolves_to_its_view(self):
        for name, kwargs, path, view in URL_TABLE:
            with self.subTest(name=name):
                self.assertEqual(reverse(name, kwargs=kwargs), path)
                module, attr = view.split(".")
                match = resolve(path)
                self.assertEqual(match.url_name, name)
                self.assertIs(match.func, getattr(import_module(f"hisobot.{module}"), attr))

    def test_styleguide_is_routed_only_in_debug(self):
        try:
            routed = resolve("/_uslub/").url_name == "styleguide"
        except Resolver404:
            routed = False
        self.assertEqual(routed, settings.DEBUG)


class HealthzTests(TestCase):
    def test_healthz_reports_ok_and_tashkent_time(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIs(data["ok"], True)
        self.assertEqual(datetime.fromisoformat(data["time"]).utcoffset(), timedelta(hours=5))


REQUIRED = "Майдонни тўлдиринг"
UP_TO_999 = "0 дан 999 гача сон киритинг"
UP_TO_9999 = "0 дан 9999 гача сон киритинг"


class EntryFormTests(SimpleTestCase):
    def form(self, require_opening=False, **data):
        data = {"unloaded": "5", "arrived": "3", "version": "0", "prev": "12", **data}
        return EntryForm(data, require_opening=require_opening)

    def test_valid_form_cleans_to_integers_and_none(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data,
            {"unloaded": 5, "arrived": 3, "override": None, "opening": None, "version": 0, "prev": 12},
        )

    def test_zero_is_a_value_not_an_empty_field(self):
        form = self.form(unloaded="0", arrived="0", override="0")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["override"], 0)

    def test_counts_reject_empty_and_out_of_range_in_cyrillic(self):
        cases = [("", REQUIRED), ("1000", UP_TO_999), ("-1", UP_TO_999), ("1.5", UP_TO_999), ("abc", UP_TO_999)]
        for field in ("unloaded", "arrived"):
            for value, message in cases:
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.form(**{field: value}).errors, {field: [message]})

    def test_missing_keys_are_rejected_like_empty_ones(self):
        self.assertEqual(EntryForm({"version": "0"}).errors, {"unloaded": [REQUIRED], "arrived": [REQUIRED]})

    def test_override_and_opening_are_optional_up_to_9999(self):
        for field in ("override", "opening"):
            self.assertTrue(self.form(**{field: "9999"}).is_valid())
            for value in ("10000", "-1", "x"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.form(**{field: value}).errors, {field: [UP_TO_9999]})

    def test_require_opening_makes_opening_mandatory(self):
        self.assertEqual(self.form(require_opening=True).errors, {"opening": [REQUIRED]})
        form = self.form(require_opening=True, opening="10", prev="")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["opening"], 10)
        self.assertIsNone(form.cleaned_data["prev"])

    def test_hidden_version_is_required_and_prev_may_be_negative(self):
        form = self.form(prev="-3")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["prev"], -3)
        for field, value in [("version", ""), ("version", "-1"), ("version", "x"), ("prev", "x")]:
            with self.subTest(field=field, value=value):
                (message,) = self.form(**{field: value}).errors[field]
                self.assertRegex(message, r"^[А-Яа-яЎўҚқҒғҲҳ ,.]+$")

    def test_number_inputs_carry_numeric_keyboard_and_bounds(self):
        form = EntryForm()
        for name, top in [("unloaded", 999), ("arrived", 999), ("override", 9999), ("opening", 9999)]:
            with self.subTest(name=name):
                html = str(form[name])
                self.assertIn('type="number"', html)
                self.assertIn('inputmode="numeric"', html)
                self.assertIn('min="0"', html)
                self.assertIn(f'max="{top}"', html)
        self.assertEqual([f.name for f in form.hidden_fields()], ["version", "prev"])


class FilterTests(SimpleTestCase):
    night = datetime(2026, 10, 12, 8, tzinfo=TZ)  # period end of the night period of 12.10
    day = datetime(2026, 10, 12, 20, tzinfo=TZ)
    late_utc = datetime(2026, 10, 11, 20, 7, tzinfo=UTC)  # 12.10.2026 01:07 in Tashkent

    def render(self, expression, value):
        template = Template("{% load hisobot_extras %}{{ value|" + expression + " }}")
        return template.render(Context({"value": value}))

    def test_date_and_time_filters_print_tashkent_time(self):
        self.assertEqual(self.render("d", date(2026, 3, 5)), "05.03.2026")
        self.assertEqual(self.render("d", self.late_utc), "12.10.2026")
        self.assertEqual(self.render("t", self.late_utc), "01:07")
        self.assertEqual(self.render("dt", self.late_utc), "12.10.2026 01:07")
        self.assertEqual(self.render("dt", self.night), "12.10.2026 08:00")

    def test_period_filters(self):
        self.assertEqual(self.render("period_range", self.night), "11.10 20:00 – 12.10 08:00")
        self.assertEqual(self.render("period_range", self.day), "12.10 08:00 – 12.10 20:00")
        self.assertEqual(self.render("period_name", self.night), "Тунги давр")
        self.assertEqual(self.render("period_name", self.day), "Кундузги давр")
        self.assertEqual(self.render("slug", self.night), "2026101208")
        self.assertEqual(self.render("slug", self.day.astimezone(UTC)), "2026101220")

    def test_num_prints_a_dash_only_for_none(self):
        self.assertEqual(self.render("num", None), "—")
        self.assertEqual(self.render("num", 0), "0")
        self.assertEqual(self.render("num", -4), "-4")
        self.assertEqual(self.render("num", 137), "137")

    def test_missing_values_print_a_dash(self):
        # "" is what Django passes to a filter when the variable does not resolve (entry is None).
        for name in ("d", "t", "dt", "num"):
            for missing in (None, ""):
                with self.subTest(name=name, missing=missing):
                    self.assertEqual(self.render(name, missing), "—")

    def test_period_filters_accept_utc_datetimes_from_the_database(self):
        night_utc = self.night.astimezone(UTC)
        self.assertEqual(self.render("period_range", night_utc), "11.10 20:00 – 12.10 08:00")
        self.assertEqual(self.render("period_name", night_utc), "Тунги давр")


class BaseLayoutTests(TestCase):
    def request(self, user=None, station=None, path="/"):
        request = RequestFactory().get(path)
        request.user = user or AnonymousUser()
        request._messages = CookieStorage(request)
        if station is not None:
            request.station = station
        return request

    def page(self, request):
        return render_to_string("hisobot/base.html", request=request)

    def test_anonymous_visitor_gets_the_stylesheet_and_no_account_controls(self):
        html = self.page(self.request())
        self.assertIn('<html lang="uz-Cyrl">', html)
        self.assertIn('href="/static/hisobot/app.css"', html)
        self.assertIn('name="viewport"', html)
        for text in ("Чиқиш", "Ҳозир", "Парол"):
            self.assertNotIn(text, html)

    def test_station_sees_its_name_and_a_post_logout(self):
        station = Station.objects.create(mtu=Mtu.objects.create(name="Тошкент"), name="Чуқурсой")
        html = self.page(self.request(station=station))
        self.assertIn("Чуқурсой", html)
        self.assertIn('<form method="post" action="/chiqish/"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn("Чиқиш", html)
        for text in ("Кунлик жадвал", "Станциялар", "Парол"):
            self.assertNotIn(text, html)

    def test_mtu_user_sees_staff_links_and_its_mtu_name(self):
        user = User.objects.create_user("toshkent", mtu=Mtu.objects.create(name="Тошкент"))
        html = self.page(self.request(user=user))
        for href, text in [
            ("/xodim/", "Ҳозир"),
            ("/xodim/jadval/", "Кунлик жадвал"),
            ("/xodim/hisobot/", "Ҳисобот"),
            ("/xodim/stansiyalar/", "Станциялар"),
            ("/xodim/parol/", "Парол"),
        ]:
            with self.subTest(text=text):
                self.assertRegex(html, rf'<a href="{href}"[^>]*>{text}</a>')
        self.assertIn("Тошкент МТУ", html)
        self.assertIn('<form method="post" action="/chiqish/"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertNotIn("МТУ пароллари", html)
        self.assertNotIn("Бошқарма", html)

    def test_boshqarma_also_sees_mtu_passwords(self):
        html = self.page(self.request(user=User.objects.create_user("boshqarma")))
        self.assertIn("Бошқарма", html)
        self.assertRegex(html, r'<a href="/xodim/mtu-parol/"[^>]*>МТУ пароллари</a>')
        self.assertRegex(html, r'<a href="/xodim/"[^>]*>Ҳозир</a>')

    def test_the_current_section_is_marked(self):
        request = self.request(user=User.objects.create_user("boshqarma"), path="/xodim/jadval/")
        request.resolver_match = resolve("/xodim/jadval/")
        html = self.page(request)
        self.assertIn('<a href="/xodim/jadval/" aria-current="page">Кунлик жадвал</a>', html)
        self.assertEqual(html.count("aria-current"), 1)

    def test_messages_are_rendered_as_notices(self):
        request = self.request()
        messages.info(request, "Маълумот")
        messages.success(request, "Қабул қилинди")
        messages.warning(request, "Олдинги давр киритилмаган")
        messages.error(request, "ПИН нотўғри")
        html = self.page(request)
        for cls, text in [
            ("notice-info", "Маълумот"),
            ("notice-ok", "Қабул қилинди"),
            ("notice-warn", "Олдинги давр киритилмаган"),
            ("notice-error", "ПИН нотўғри"),
        ]:
            with self.subTest(cls=cls):
                self.assertInHTML(f'<div class="{cls}" role="status">{text}</div>', html)


# Every class the plan promises to the feature tasks.
COMPONENTS = [
    "page", "toolbar", "card", "card-grid", "stat", "btn", "btn-primary", "btn-quiet", "btn-danger",
    "field", "num-input", "notice-info", "notice-ok", "notice-warn", "notice-error",
    "badge-ok", "badge-warn", "badge-bad", "badge-muted", "table", "table-wrap",
    "is-missing", "is-override", "is-negative", "is-carried", "list", "print-only", "no-print", "pin-strip",
]  # fmt: skip


class StyleguideTests(SimpleTestCase):
    def test_every_promised_component_is_styled_documented_and_shown(self):
        css = Path(finders.find("hisobot/app.css")).read_text(encoding="utf-8")
        header = css[: css.index("*/")]
        request = RequestFactory().get("/_uslub/")
        request.user = AnonymousUser()
        response = views_public.styleguide(request)
        self.assertEqual(response.status_code, 200)
        classes = set(" ".join(re.findall(r'class="([^"]*)"', response.content.decode())).split())
        for name in COMPONENTS:
            with self.subTest(name=name):
                self.assertRegex(css, rf"\.{name}(?![\w-])[^{{}}]*\{{")
                self.assertIn(f".{name}", header)
                self.assertIn(name, classes)

    def test_the_stylesheet_has_a_dark_scheme_and_loads_nothing_external(self):
        css = Path(finders.find("hisobot/app.css")).read_text(encoding="utf-8")
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn("tabular-nums", css)
        self.assertNotRegex(css, r"@import|url\(|https?:")


class ErrorPageTests(TestCase):
    """With DEBUG off (as in tests) Django's own error pages are English; these replace them."""

    def test_not_found_is_in_cyrillic(self):
        response = self.client.get("/yoq/")
        self.assertContains(response, "Саҳифа топилмади", status_code=404)

    def test_csrf_failure_is_in_cyrillic(self):
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(reverse("staff_login"), {"username": "x", "password": "y"})
        self.assertContains(response, "Саҳифа эскирган", status_code=403)

    def test_server_error_page_needs_neither_request_nor_database(self):
        with self.assertNumQueries(0):
            html = render_to_string("500.html")
        self.assertIn("Хатолик юз берди", html)
