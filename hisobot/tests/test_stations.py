import re
import zipfile
from datetime import timedelta
from io import BytesIO

from django.contrib.auth.hashers import check_password
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from hisobot import access, balance, periods, roster
from hisobot.models import Change, Mtu, Station, User

HEADER = ("МТУ", "Станция номи")
PIN_ON_SHEET = re.compile(r"<small>ПИН</small>\s*(\d{4} \d{4})")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx(*rows, header=HEADER):
    workbook = Workbook()
    sheet = workbook.active
    if header:
        sheet.append(header)
    for row in rows:
        sheet.append(row)
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # typed as text, the way a person would type a name, not a formula
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("stansiyalar.xlsx", buffer.getvalue(), content_type=XLSX)


def dimension(ref):
    """A with_sheet change that sets the sheet's stored <dimension>, or drops it when ref is None."""

    def change(xml):
        xml, found = re.subn(r'<dimension ref="[^"]*"\s*/>', "" if ref is None else f'<dimension ref="{ref}"/>', xml)
        assert found == 1, xml[:300]
        return xml

    return change


def with_sheet(upload, change, extra=None):
    """The same upload with xl/worksheets/sheet1.xml passed through `change`, plus `extra` {name: bytes} entries."""
    source, target = zipfile.ZipFile(BytesIO(upload.read())), BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = source.read(item.filename)
            out.writestr(item, change(data.decode()) if item.filename == "xl/worksheets/sheet1.xml" else data)
        for name, data in (extra or {}).items():
            out.writestr(name, data)
    return SimpleUploadedFile("stansiyalar.xlsx", target.getvalue(), content_type=XLSX)


def messages_of(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher", "hisobot.access.PinHasher"])
class StationsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mtu_a = Mtu.objects.create(name="Тошкент", sort=1)
        cls.mtu_b = Mtu.objects.create(name="Қўқон", sort=2)
        cls.boss = User.objects.create_user("boshqarma", password="boss-secret-1")
        cls.user_a = User.objects.create_user("toshkent", password="a-secret-123", mtu=cls.mtu_a)
        cls.user_b = User.objects.create_user("qoqon", password="b-secret-123", mtu=cls.mtu_b)
        cls.station = Station.objects.create(
            mtu=cls.mtu_a, name="Чуқурсой", sort=1, active_from=timezone.now() - timedelta(days=10)
        )
        cls.foreign = Station.objects.create(mtu=cls.mtu_b, name="Фарғона-2", sort=1)

    def login(self, user, client=None):
        client = client or self.client
        client.force_login(user)
        session = client.session
        session["staff_login_at"] = timezone.now().timestamp()
        session.save()

    def stored(self, station):
        return Station.objects.get(pk=station.pk)

    def names(self, mtu):
        return list(Station.objects.filter(mtu=mtu).order_by("sort", "name").values_list("name", "sort"))


class RosterTests(StationsTestCase):
    def test_imports_rows_of_two_mtus_in_row_order_after_the_existing_ones(self):
        Station.objects.filter(pk=self.station.pk).update(sort=5)
        upload = xlsx(
            ("Тошкент", "Янгийўл"),
            ("Қўқон", "Наманган"),
            (None, None),
            ("  тошкент ", " Бекобод "),
            ("ҚЎҚОН", "Андижон"),
        )

        added = roster.import_roster(upload, user=self.boss)

        self.assertEqual(added, 4)
        self.assertEqual(self.names(self.mtu_a), [("Чуқурсой", 5), ("Янгийўл", 6), ("Бекобод", 7)])
        self.assertEqual(self.names(self.mtu_b), [("Фарғона-2", 1), ("Наманган", 2), ("Андижон", 3)])
        new = Station.objects.exclude(pk__in=[self.station.pk, self.foreign.pk])
        self.assertEqual(set(new.values_list("pin_hash", "active_to")), {("", None)})

    def test_mtu_user_imports_its_own_rows(self):
        self.assertEqual(roster.import_roster(xlsx(("Тошкент", "Янгийўл")), user=self.user_a), 1)
        self.assertEqual(self.names(self.mtu_a), [("Чуқурсой", 1), ("Янгийўл", 2)])

    def assertRejected(self, upload, user=None, errors=None):
        before = list(Station.objects.order_by("pk").values_list("pk", "name"))
        with self.assertRaises(roster.RosterError) as caught:
            roster.import_roster(upload, user=user or self.boss)
        self.assertEqual(list(Station.objects.order_by("pk").values_list("pk", "name")), before)
        if errors is not None:
            self.assertEqual(caught.exception.errors, errors)
        return caught.exception.errors

    def test_wrong_header(self):
        errors = self.assertRejected(xlsx(("Тошкент", "Янгийўл"), header=("Станция номи", "МТУ")))
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("1-қатор: "), errors)
        self.assertRejected(xlsx(header=None))

    def test_unknown_mtu(self):
        self.assertRejected(
            xlsx(("Тошкент", "Янгийўл"), ("Навоий", "Кармана"), ("", "Зарафшон")),
            errors=["3-қатор: «Навоий» номли МТУ топилмади", "4-қатор: МТУ кўрсатилмаган"],
        )

    def test_mtu_user_cannot_import_a_row_of_another_mtu(self):
        errors = self.assertRejected(xlsx(("Тошкент", "Янгийўл"), ("Қўқон", "Наманган")), user=self.user_a)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("3-қатор: "), errors)

    def test_formula_like_and_empty_names_are_rejected(self):
        errors = self.assertRejected(
            xlsx(
                ("Тошкент", "=1+1"),
                ("Тошкент", "@SUM(A1)"),
                ("Тошкент", "Янгийўл"),
                ("Тошкент", "  "),
                ("Тошкент", "Б" * 101),
            )
        )
        self.assertEqual([error.split(":")[0] for error in errors], ["2-қатор", "3-қатор", "5-қатор", "6-қатор"])

    def test_duplicate_inside_the_file(self):
        errors = self.assertRejected(
            xlsx(("Тошкент", "Янгийўл"), ("Қўқон", "Янгийўл"), ("тошкент", " ЯНГИЙЎЛ "))
        )
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("4-қатор: "), errors)
        self.assertIn("2-қатор", errors[0])

    def test_every_error_is_reported_at_once(self):
        errors = self.assertRejected(xlsx(("Навоий", "А"), ("Тошкент", "=А"), ("Тошкент", "Б"), ("Термиз", "В")))
        self.assertEqual([error.split(":")[0] for error in errors], ["2-қатор", "3-қатор", "5-қатор"])

    def test_reimport_adds_only_new_rows_and_leaves_pins_alone(self):
        access.set_pin(self.station, actor=self.boss, ip="10.0.0.1")
        pin_state = Station.objects.filter(pk=self.station.pk).values_list("pin_hash", "pin_version").get()
        changes = Change.objects.count()
        upload = xlsx(("Тошкент", "ЧУҚУРСОЙ"), ("Тошкент", "Янгийўл"))

        self.assertEqual(roster.import_roster(upload, user=self.boss), 1)
        upload.seek(0)
        self.assertEqual(roster.import_roster(upload, user=self.boss), 0)

        self.assertEqual(self.names(self.mtu_a), [("Чуқурсой", 1), ("Янгийўл", 2)])
        pin_state_after = Station.objects.filter(pk=self.station.pk).values_list("pin_hash", "pin_version").get()
        self.assertEqual(pin_state_after, pin_state)
        self.assertEqual(Change.objects.count(), changes)

    def test_up_to_a_thousand_rows_blank_rows_not_counted(self):
        rows = [("Тошкент", f"Станция {n}") for n in range(1000)]
        rows[500:500] = [(None, None)] * 5
        self.assertEqual(roster.import_roster(xlsx(*rows), user=self.boss), 1000)

    def test_more_than_a_thousand_rows_are_rejected(self):
        self.assertRejected(xlsx(*[("Тошкент", f"Станция {n}") for n in range(1001)]))

    def test_a_file_that_is_not_xlsx_is_rejected(self):
        csv = SimpleUploadedFile("stansiyalar.xlsx", "МТУ;Станция номи\nТошкент;Янгийўл\n".encode())
        self.assertRejected(csv)
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("word/document.xml", "<w:document/>")
        self.assertRejected(SimpleUploadedFile("stansiyalar.xlsx", archive.getvalue()))

    def test_a_file_over_one_megabyte_is_rejected(self):
        errors = self.assertRejected(SimpleUploadedFile("stansiyalar.xlsx", b"x" * (1024 * 1024 + 1)))
        self.assertIn("1 МБ", errors[0])

    def test_a_small_file_that_unpacks_to_a_huge_one_is_rejected_before_parsing(self):
        bomb = with_sheet(xlsx(("Тошкент", "Янгийўл")), str, {"xl/media/pad.bin": b"\0" * (roster.MAX_UNZIPPED + 1)})
        self.assertLess(bomb.size, 1024 * 1024)
        self.assertRejected(bomb, errors=["Файлни ўқиб бўлмади. Excel (.xlsx) файлини юкланг."])

    def test_a_stale_dimension_does_not_hide_rows(self):
        # Read-only openpyxl stops at the stored <dimension>; some exporters write a header-only one.
        upload = with_sheet(xlsx(("Тошкент", "Янгийўл"), ("Навоий", "Кармана")), dimension("A1:B1"))
        self.assertRejected(upload, errors=["3-қатор: «Навоий» номли МТУ топилмади"])

    def test_a_row_number_beyond_excel_is_not_read(self):
        # Without the bound openpyxl yields every blank row up to the number the file claims.
        far = '<row r="1048577"><c r="A1048577" t="inlineStr"><is><t>Тошкент</t></is></c>' \
              '<c r="B1048577" t="inlineStr"><is><t>Узоқ</t></is></c></row>'
        upload = with_sheet(
            xlsx(("Тошкент", "Янгийўл")), lambda xml: dimension(None)(xml).replace("</sheetData>", far + "</sheetData>")
        )
        self.assertEqual(roster.import_roster(upload, user=self.boss), 1)
        self.assertFalse(Station.objects.filter(name="Узоқ").exists())

    def test_whitespace_inside_a_cell_becomes_one_space(self):
        upload = xlsx(("Тошкент", "Тошкент\xa0 Товар"), ("Тошкент\n", "Янги\tйўл"))
        self.assertEqual(roster.import_roster(upload, user=self.boss), 2)
        self.assertEqual(self.names(self.mtu_a), [("Чуқурсой", 1), ("Тошкент Товар", 2), ("Янги йўл", 3)])


class ImportViewTests(StationsTestCase):
    def setUp(self):
        self.login(self.user_a)

    def test_get_explains_the_format(self):
        response = self.client.get(reverse("stations_import"))
        self.assertContains(response, "Станция номи")
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_post_imports_and_reports_the_count(self):
        response = self.client.post(reverse("stations_import"), {"file": xlsx(("Тошкент", "А"), ("Тошкент", "Б"))})

        self.assertRedirects(response, reverse("stations"), fetch_redirect_response=False)
        self.assertEqual(messages_of(response), ["2 та станция қўшилди"])
        self.assertEqual(Station.objects.filter(mtu=self.mtu_a).count(), 3)

    def test_post_with_errors_lists_them_and_saves_nothing(self):
        response = self.client.post(reverse("stations_import"), {"file": xlsx(("Тошкент", "А"), ("Қўқон", "Б"))})

        self.assertContains(response, "ҳеч нарса сақланмади")
        self.assertContains(response, "3-қатор: ")
        self.assertEqual(Station.objects.count(), 2)

    def test_post_without_a_file(self):
        self.assertContains(self.client.post(reverse("stations_import")), "Файлни танланг")


class PinSheetTests(StationsTestCase):
    def setUp(self):
        self.login(self.user_a)
        self.given = Station.objects.create(mtu=self.mtu_a, name="Янгийўл", sort=2)
        self.old_pin = access.set_pin(self.given, actor=self.boss, ip="10.0.0.1")
        self.used = Station.objects.create(mtu=self.mtu_a, name="Бекобод", sort=3)
        access.set_pin(self.used, actor=self.boss, ip="10.0.0.1")
        Station.objects.filter(pk=self.used.pk).update(last_login_at=timezone.now())
        self.off = Station.objects.create(mtu=self.mtu_a, name="Ўчган", sort=4, active_to=timezone.now())

    def sheet(self, **data):
        return self.client.post(reverse("pin_sheet"), data)

    def test_sheet_shows_each_new_pin_once_and_no_page_shows_it_again(self):
        used_hash = self.stored(self.used).pin_hash

        response = self.sheet()

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        content = response.content.decode()
        pins = PIN_ON_SHEET.findall(content)
        self.assertEqual(len(pins), 2)
        for station in (self.station, self.given):
            matches = [pin for pin in pins if check_password(pin.replace(" ", ""), self.stored(station).pin_hash)]
            self.assertEqual(len(matches), 1, station)
            self.assertIn(f"http://testserver/s/{station.pk}/", content)
        for pin in pins:
            self.assertEqual(content.count(pin), 1)
            self.assertNotIn(pin.replace(" ", ""), content)
        self.assertIn("бир марта", content)
        self.assertIn("Тошкент МТУ", content)
        self.assertNotIn(self.foreign.name, content)
        # Used and inactive stations are left alone, and so is the other MTU.
        self.assertEqual(self.stored(self.used).pin_hash, used_hash)
        self.assertEqual(self.stored(self.off).pin_hash, "")
        self.assertEqual(self.stored(self.foreign).pin_hash, "")

        later = self.client.get(reverse("stations")).content.decode()
        for pin in pins + [access.format_pin(self.old_pin)]:
            self.assertNotIn(pin, later)
            self.assertNotIn(pin.replace(" ", ""), later)

    def test_checked_stations_only(self):
        response = self.sheet(station=[str(self.given.pk), str(self.used.pk)])

        pins = PIN_ON_SHEET.findall(response.content.decode())
        self.assertEqual(len(pins), 1)
        self.assertTrue(check_password(pins[0].replace(" ", ""), self.stored(self.given).pin_hash))
        self.assertEqual(self.stored(self.station).pin_hash, "")

    def test_nothing_to_issue_redirects_with_a_notice(self):
        response = self.sheet(station=[str(self.used.pk)])

        self.assertRedirects(response, reverse("stations"), fetch_redirect_response=False)
        self.assertEqual(len(messages_of(response)), 1)

    def test_a_foreign_or_malformed_id_is_404_and_issues_nothing(self):
        changes = Change.objects.count()
        self.assertEqual(self.sheet(station=[str(self.station.pk), str(self.foreign.pk)]).status_code, 404)
        self.assertEqual(self.sheet(station=["x"]).status_code, 404)
        self.assertEqual(Change.objects.count(), changes)
        self.assertEqual(self.stored(self.station).pin_hash, "")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("pin_sheet")).status_code, 405)

    def test_regenerate_pin_shows_one_strip_and_ends_the_station_session(self):
        station_client = Client()
        session = station_client.session
        session["station_id"], session["pin_version"] = self.given.pk, self.stored(self.given).pin_version
        session.save()
        request = RequestFactory().get("/")
        request.session = station_client.session
        self.assertEqual(access.current_station(request), self.given)

        response = self.client.post(reverse("station_pin", args=[self.given.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        pins = PIN_ON_SHEET.findall(response.content.decode())
        self.assertEqual(len(pins), 1)
        self.assertTrue(check_password(pins[0].replace(" ", ""), self.stored(self.given).pin_hash))
        self.assertIsNone(access.current_station(request))

    def test_regenerate_pin_works_for_a_used_pin_but_not_for_an_inactive_station(self):
        response = self.client.post(reverse("station_pin", args=[self.used.pk]))
        self.assertEqual(len(PIN_ON_SHEET.findall(response.content.decode())), 1)
        self.assertIsNone(self.stored(self.used).last_login_at)

        response = self.client.post(reverse("station_pin", args=[self.off.pk]))
        self.assertRedirects(response, reverse("station_edit", args=[self.off.pk]), fetch_redirect_response=False)
        self.assertEqual(self.stored(self.off).pin_hash, "")


class StationIndexTests(StationsTestCase):
    def test_lists_only_the_users_mtu_with_pin_and_lock_state(self):
        now = timezone.now()
        locked = Station.objects.create(mtu=self.mtu_a, name="Блокли", sort=2, locked_until=now + timedelta(hours=1))
        expired = Station.objects.create(mtu=self.mtu_a, name="Эскиблок", sort=3)
        access.set_pin(expired, actor=self.boss, ip="10.0.0.1")
        Station.objects.filter(pk=expired.pk).update(locked_until=now - timedelta(minutes=1))  # set_pin clears it
        self.login(self.user_a)

        response = self.client.get(reverse("stations"))

        self.assertContains(response, "Чуқурсой")
        self.assertNotContains(response, self.foreign.name)
        self.assertContains(response, "Берилмаган")
        self.assertContains(response, "Берилган, ишлатилмаган")
        self.assertContains(response, reverse("station_unlock", args=[locked.pk]))
        self.assertNotContains(response, reverse("station_unlock", args=[expired.pk]))
        self.assertContains(response, "Блокдан чиқариш", count=1)
        self.assertContains(response, "ПИН яратиш")
        self.assertContains(response, reverse("stations_import"))

    def test_boshqarma_sees_every_mtu(self):
        self.login(self.boss)
        response = self.client.get(reverse("stations"))
        self.assertContains(response, "Чуқурсой")
        self.assertContains(response, self.foreign.name)

    def test_boshqarma_can_filter_one_mtu_and_its_pin_sheet_stays_in_it(self):
        self.login(self.boss)
        response = self.client.get(reverse("stations"), {"mtu": self.mtu_b.pk})
        self.assertContains(response, self.foreign.name)
        self.assertNotContains(response, "Чуқурсой")
        pin_url = f"{reverse('pin_sheet')}?mtu={self.mtu_b.pk}"
        self.assertContains(response, f'action="{pin_url}"')

        sheet = self.client.post(pin_url).content.decode()

        self.assertEqual(len(PIN_ON_SHEET.findall(sheet)), 1)
        self.assertIn(self.foreign.name, sheet)
        # Тошкент's printed, unused sheet keeps working.
        self.assertEqual(self.stored(self.station).pin_hash, "")

    def test_the_mtu_filter_is_ignored_for_an_mtu_user(self):
        self.login(self.user_a)
        response = self.client.get(reverse("stations"), {"mtu": self.mtu_b.pk})
        self.assertContains(response, "Чуқурсой")
        self.assertNotContains(response, self.foreign.name)
        self.assertContains(response, f'action="{reverse("pin_sheet")}"')

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("stations"))
        self.assertRedirects(response, reverse("staff_login"), fetch_redirect_response=False)

    def test_mtu_user_adding_with_another_mtu_id_adds_to_its_own(self):
        self.login(self.user_a)

        response = self.client.post(reverse("stations"), {"name": " Янгийўл ", "mtu": self.mtu_b.pk})

        self.assertRedirects(response, reverse("stations"), fetch_redirect_response=False)
        added = Station.objects.get(name="Янгийўл")
        self.assertEqual((added.mtu, added.sort), (self.mtu_a, 2))
        self.assertFalse(Station.objects.filter(mtu=self.mtu_b, name="Янгийўл").exists())

    def test_boshqarma_chooses_the_mtu(self):
        self.login(self.boss)
        self.client.post(reverse("stations"), {"name": "Наманган", "mtu": self.mtu_b.pk})
        self.assertEqual(self.names(self.mtu_b), [("Фарғона-2", 1), ("Наманган", 2)])

    def test_bad_names_are_refused(self):
        self.login(self.user_a)
        for name in ["чуқурсой", "=1+1", "", "Альфа\x0bбекат"]:
            with self.subTest(name=name):
                response = self.client.post(reverse("stations"), {"name": name})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'aria-invalid="true"')
        self.assertEqual(Station.objects.count(), 2)

    def test_unlock(self):
        Station.objects.filter(pk=self.station.pk).update(
            locked_until=timezone.now() + timedelta(hours=1), fail_streak=5
        )
        self.login(self.user_a)

        response = self.client.post(reverse("station_unlock", args=[self.station.pk]))

        self.assertRedirects(response, reverse("stations"), fetch_redirect_response=False)
        self.assertEqual((self.stored(self.station).locked_until, self.stored(self.station).fail_streak), (None, 5))
        self.assertEqual(Change.objects.get().kind, "unlock")
        self.assertEqual(self.client.get(reverse("station_unlock", args=[self.station.pk])).status_code, 405)


class ToggleTests(StationsTestCase):
    def setUp(self):
        self.login(self.user_a)

    def test_deactivate_then_activate(self):
        before = timezone.now()
        response = self.client.post(reverse("station_toggle", args=[self.station.pk]), {"active": "0"})
        after = timezone.now()

        self.assertRedirects(response, reverse("station_edit", args=[self.station.pk]), fetch_redirect_response=False)
        station = self.stored(self.station)
        self.assertTrue(before <= station.active_to <= after)
        # Periods that ended while it was active are still expected; later ones are not.
        last = periods.last_ended(before)
        self.assertTrue(station.expects(last))
        self.assertFalse(station.expects(periods.next_end(last)))
        change = Change.objects.get()
        self.assertEqual((change.kind, change.user, change.old), ("station", self.user_a, {"active_to": None}))

        self.client.post(reverse("station_toggle", args=[self.station.pk]), {"active": "1"})

        station = self.stored(self.station)
        self.assertIsNone(station.active_to)
        self.assertTrue(after <= station.active_from <= timezone.now())
        self.assertEqual(Change.objects.filter(kind="station").count(), 2)

    def test_a_second_click_or_a_stale_page_changes_nothing(self):
        active_from = self.stored(self.station).active_from
        url = reverse("station_toggle", args=[self.station.pk])
        self.client.post(url, {"active": "0"})
        active_to = self.stored(self.station).active_to

        response = self.client.post(url, {"active": "0"})  # the same button again

        self.assertRedirects(response, reverse("station_edit", args=[self.station.pk]), fetch_redirect_response=False)
        self.assertEqual(messages_of(response)[-1], "Станция аллақачон ўчирилган.")
        station = self.stored(self.station)
        self.assertEqual((station.active_from, station.active_to), (active_from, active_to))
        self.assertEqual(Change.objects.filter(kind="station").count(), 1)

    def test_a_request_without_the_wanted_state_is_refused(self):
        for data in [{}, {"active": "2"}]:
            with self.subTest(data=data):
                response = self.client.post(reverse("station_toggle", args=[self.station.pk]), data)
                self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.stored(self.station).active_to)
        self.assertFalse(Change.objects.exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("station_toggle", args=[self.station.pk])).status_code, 405)
        self.assertIsNone(self.stored(self.station).active_to)


class EditTests(StationsTestCase):
    def setUp(self):
        self.login(self.user_a)
        self.url = reverse("station_edit", args=[self.station.pk])

    def test_rename_and_sort_write_a_station_change(self):
        response = self.client.post(self.url, {"form": "station", "name": " Чуқурсой-2 ", "sort": "7"})

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual((self.stored(self.station).name, self.stored(self.station).sort), ("Чуқурсой-2", 7))
        change = Change.objects.get()
        self.assertEqual(change.kind, "station")
        self.assertEqual(change.old, {"name": "Чуқурсой", "sort": 1})
        self.assertEqual(change.new, {"name": "Чуқурсой-2", "sort": 7})

    def test_unchanged_form_writes_nothing(self):
        self.client.post(self.url, {"form": "station", "name": "Чуқурсой", "sort": "1"})
        self.assertFalse(Change.objects.exists())

    def test_rename_to_a_taken_name_is_refused_but_a_case_change_is_not(self):
        Station.objects.create(mtu=self.mtu_a, name="Янгийўл", sort=2)
        response = self.client.post(self.url, {"form": "station", "name": "ЯНГИЙЎЛ", "sort": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertEqual(self.stored(self.station).name, "Чуқурсой")

        self.client.post(self.url, {"form": "station", "name": "ЧУҚУРСОЙ", "sort": "1"})
        self.assertEqual(self.stored(self.station).name, "ЧУҚУРСОЙ")

    def test_opening_balance_goes_through_set_opening(self):
        pe = periods.last_ended(timezone.now())
        balance.save_entry(
            station_id=self.station.pk, period_end=pe, unloaded=2, arrived=3, opening=10,
            form_version=0, form_prev=None, actor=self.user_a, ip="10.0.0.1",
        )
        self.assertContains(self.client.get(self.url), 'name="opening"')

        response = self.client.post(self.url, {"form": "opening", "opening": "20"})

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self.stored(self.station).opening_remaining, 20)
        self.assertEqual(self.station.entries.get().remaining_calc, 21)
        self.assertEqual(Change.objects.filter(kind="opening").count(), 2)

    def test_opening_before_the_first_entry_is_refused_with_the_save_error(self):
        self.assertNotContains(self.client.get(self.url), 'name="opening"')

        response = self.client.post(self.url, {"form": "opening", "opening": "20"})

        self.assertContains(response, "биринчи ёзув билан бирга киритилади")
        self.assertIsNone(self.stored(self.station).opening_remaining)

    def test_history_lists_station_events_newest_first(self):
        access.set_pin(self.station, actor=self.boss, ip="10.0.0.1")
        access.unlock(self.station, actor=self.user_a, ip="10.0.0.1")
        self.client.post(self.url, {"form": "station", "name": "Чуқурсой-2", "sort": "1"})
        balance.save_entry(
            station_id=self.station.pk, period_end=periods.last_ended(timezone.now()), unloaded=2, arrived=3,
            opening=10, form_version=0, form_prev=None, actor=self.user_a, ip="10.0.0.1",
        )

        content = self.client.get(self.url).content.decode()

        events = ["Давр бошидаги қолдиқ", "Номи: Чуқурсой → Чуқурсой-2", "Блокдан чиқарилди", "ПИН янгиланди"]
        positions = [content.index(event, content.index("Тарих")) for event in events]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("boshqarma", content)


class ScopeTests(StationsTestCase):
    def test_a_foreign_station_is_404_on_every_endpoint_and_nothing_changes(self):
        self.login(self.user_a)
        state = Station.objects.filter(pk=self.foreign.pk).values().get()
        calls = [
            ("get", "station_edit", {}),
            ("post", "station_edit", {"form": "station", "name": "Бошқа", "sort": "3"}),
            ("post", "station_edit", {"form": "opening", "opening": "5"}),
            ("post", "station_toggle", {}),
            ("post", "station_pin", {}),
            ("post", "station_unlock", {}),
        ]
        for method, name, data in calls:
            with self.subTest(method=method, name=name, data=data):
                response = getattr(self.client, method)(reverse(name, args=[self.foreign.pk]), data)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(Station.objects.filter(pk=self.foreign.pk).values().get(), state)
        self.assertFalse(Change.objects.exists())


class PasswordTests(StationsTestCase):
    def test_mtu_passwords_is_404_for_an_mtu_user(self):
        self.login(self.user_a)
        self.assertEqual(self.client.get(reverse("mtu_passwords")).status_code, 404)
        self.assertEqual(self.client.post(reverse("mtu_passwords"), {"user": self.user_b.pk}).status_code, 404)
        self.assertTrue(User.objects.get(pk=self.user_b.pk).check_password("b-secret-123"))

    def test_boshqarma_resets_an_mtu_password_shown_once(self):
        self.login(self.boss)
        listing = self.client.get(reverse("mtu_passwords"))
        self.assertContains(listing, "toshkent")
        self.assertContains(listing, "qoqon")

        response = self.client.post(reverse("mtu_passwords"), {"user": self.user_b.pk})

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        password = re.search(r'<span class="secret">([^<]+)</span>', response.content.decode()).group(1)
        self.assertEqual(len(password), 16)
        self.assertTrue(User.objects.get(pk=self.user_b.pk).check_password(password))
        self.assertNotContains(self.client.get(reverse("mtu_passwords")), password)

    def test_boshqarma_password_cannot_be_reset_here(self):
        self.login(self.boss)
        self.assertEqual(self.client.post(reverse("mtu_passwords"), {"user": self.boss.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("mtu_passwords"), {"user": "x"}).status_code, 404)

    def change(self, current, new, again):
        return self.client.post(reverse("password_change"), {"current": current, "new": new, "again": again})

    def test_password_change_validates_in_cyrillic(self):
        self.login(self.user_a)
        self.assertContains(self.change("wrong", "long-enough-1", "long-enough-1"), "Жорий парол нотўғри")
        self.assertContains(self.change("a-secret-123", "short", "short"), "камида 10")
        self.assertContains(self.change("a-secret-123", "long-enough-1", "long-enough-2"), "бир хил эмас")
        self.assertContains(self.change("", "", ""), "Майдонни тўлдиринг")
        self.assertTrue(User.objects.get(pk=self.user_a.pk).check_password("a-secret-123"))

    def test_password_change_keeps_the_session(self):
        self.login(self.user_a)

        response = self.change("a-secret-123", "long-enough-1", "long-enough-1")

        self.assertRedirects(response, reverse("staff_now"), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(pk=self.user_a.pk).check_password("long-enough-1"))
        self.assertEqual(self.client.get(reverse("stations")).status_code, 200)
