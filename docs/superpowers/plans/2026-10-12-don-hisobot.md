# Don Hisobot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Django web system where railway stations enter the daily grain-unloading report by PIN, each of 6 MTUs sees its own stations, and the department (boshqarma) sees everything.

**Architecture:** One Django app (`hisobot`) with server-rendered templates on PostgreSQL. All time logic lives in `periods.py`; `balance.save_entry()` is the only writer of `Entry`; `access.stations_for(user)` is the base queryset of every staff view. Read-side reporting is in `reports.py`.

**Tech Stack:** Python 3.12 (uv), Django 5.2 LTS, PostgreSQL 17, openpyxl, defusedxml, gunicorn, whitenoise, Chart.js (vendored), Docker Compose + Caddy.

**Spec:** `docs/superpowers/specs/2026-10-12-don-hisobot-design.md` (Uzbek). This plan is the interface contract; where they differ, the spec's rules win and the difference is a plan bug to report.

This plan fixes file ownership, URL names and function signatures so tasks can run in parallel. It gives test cases as exact behaviours; each task writes those tests first (TDD), then the code.

## Global Constraints

- UI language: Uzbek Cyrillic only. No i18n framework: `USE_I18N = False`, every user-visible string written in Cyrillic directly, including form error messages. Use the glossary below verbatim.
- Time zone `Asia/Tashkent` (UTC+5, no DST). `USE_TZ = True`. "Now" is always `django.utils.timezone.now()`. `date.today()`, `datetime.now()` without tz, and SQL `CURRENT_DATE` are banned.
- PostgreSQL only (dev, test, prod). Dev DB: `docker compose -f compose.dev.yaml up -d` gives `127.0.0.1:5440`, db/user/password `hisobot`.
- Run everything through uv: `uv run python manage.py ...`.
- Tests: `uv run python manage.py test hisobot.tests.<module> --noinput`. When several agents test at once, each sets its own `TEST_DB_NAME` env var (settings read it) so test databases never collide.
- Only Task 1 edits `models.py` and creates migrations. Any later task that believes it needs a model change stops and reports instead.
- Only Task 5 edits `hisobot/urls.py`, `base.html`, `app.css`, `forms.py`, `templatetags/`. Feature tasks use the classes and tags that exist; a missing one is reported, not added.
- `Entry` rows are written only by `balance.save_entry()` / `balance.set_opening()`. Never `Entry.objects.create()` outside `balance.py` and tests.
- Every staff view starts from `access.stations_for(request.user)`; a foreign id is a 404. Every state change is a POST with CSRF.
- No external hosts at runtime: no CDN, no web fonts. System font stack.
- No Django admin, no REST API, no JS framework, no Node build.
- Do not run `git commit`, `git add` or any state-changing git command. The coordinator commits.
- Dependencies are exactly: `django>=5.2,<5.3`, `psycopg[binary]`, `openpyxl`, `defusedxml`, `gunicorn`, `whitenoise`. Adding another needs the coordinator's approval.

### Glossary (use verbatim)

| Concept | Text |
|---|---|
| Night period / day period | Тунги давр (20:00–08:00) / Кундузги давр (08:00–20:00) |
| Unloaded / arrived / remaining | Туширилган / Кириб келган / Қолган |
| Full column titles | Туширилган вагонлар сони / Кириб келган вагонлар сони / Юк туширишда қолган вагонлар сони |
| Daily total | Сутка давомида жами туширилган вагонлар сони |
| Opening balance | Давр бошидаги қолдиқ |
| Status | Топширилмаган / Қабул қилинди 08:07 |
| Buttons | Юбориш, Сақлаш, Бекор қилиш, Киритиш, Тузатиш, Кириш, Чиқиш |
| No movement | Ҳаракат бўлмади (0 / 0) |
| Manual remaining | Қолдиқни қўлда киритиш |
| Gap warning | Олдинги давр киритилмаган |
| Needs attention | Эътибор талаб |
| Staff nav | Ҳозир, Кунлик жадвал, Ҳисобот, Станциялар |
| PIN actions | ПИН яратиш, ПИН янгилаш, Блокдан чиқариш |
| Totals / export | Жами / Excel юклаб олиш |
| Not open yet | 08:00 да очилади |
| Roles | Бошқарма, МТУ, Станция |

Dates print as `dd.mm.yyyy`, times as `HH:MM`, both in Tashkent time.

## File Structure

```
pyproject.toml  manage.py  .gitignore  .env.example
compose.dev.yaml                dev Postgres only
compose.yaml  Dockerfile  Caddyfile  backup.sh  README.md      (Task 11)
config/settings.py urls.py wsgi.py
hisobot/
  models.py                     Mtu, User, Station, Entry, Change, LoginFailure          (Task 1)
  migrations/0001_initial.py, 0002_guards.py                                             (Task 1)
  management/commands/seed.py                                                            (Task 1)
  xlsx/shablon.xlsx             copy of the customer's template                          (Task 1)
  periods.py                    pure time logic                                          (Task 1)
  balance.py                    save_entry, set_opening, prev_for, recompute             (Task 2)
  access.py                     PIN, throttling, principals, decorators, scoping         (Task 3)
  reports.py                    read side: daily rows, submission, attention, stock      (Task 4)
  forms.py  urls.py  templatetags/hisobot_extras.py                                      (Task 5)
  templates/hisobot/base.html   static/hisobot/app.css                                   (Task 5)
  views_public.py views_station.py   templates/hisobot/public/*, station/*               (Task 6)
  views_staff.py                     templates/hisobot/staff/now|daily|entry.html        (Task 7)
  views_stations.py roster.py        templates/hisobot/staff/stations*|import|pin_sheet|passwords*.html  (Task 8)
  views_reports.py summary.py export.py  templates/hisobot/staff/report.html, static/hisobot/chart.umd.min.js, charts.js  (Task 9)
  tests/test_periods.py test_balance.py test_access.py test_reports.py
        test_station_flow.py test_staff.py test_stations.py test_summary_export.py test_isolation.py
```

## URL table (Task 5 writes `hisobot/urls.py` exactly so; view modules start as stubs)

| Name | Path | View | Principal |
|---|---|---|---|
| `home` | `/` | `views_public.home` | any |
| `mtu_stations` | `/m/<int:mtu_id>/` | `views_public.mtu_stations` | any |
| `station_login` | `/s/<int:station_id>/` | `views_public.station_login` | any |
| `staff_login` | `/kirish/` | `views_public.staff_login` | any |
| `logout` | `/chiqish/` (POST) | `views_public.logout` | any |
| `healthz` | `/healthz` | `views_public.healthz` | any |
| `station_home` | `/stansiya/` | `views_station.home` | station |
| `station_entry` | `/stansiya/davr/<str:pe>/` | `views_station.entry` | station |
| `staff_now` | `/xodim/` | `views_staff.now` | staff |
| `staff_daily` | `/xodim/jadval/` | `views_staff.daily` | staff |
| `staff_entry` | `/xodim/yozuv/<int:station_id>/<str:pe>/` | `views_staff.entry` | staff |
| `staff_report` | `/xodim/hisobot/` | `views_reports.report` | staff |
| `staff_export` | `/xodim/eksport/` | `views_reports.export` | staff |
| `stations` | `/xodim/stansiyalar/` | `views_stations.index` | staff |
| `station_edit` | `/xodim/stansiyalar/<int:station_id>/` | `views_stations.edit` | staff |
| `station_toggle` | `/xodim/stansiyalar/<int:station_id>/holat/` (POST) | `views_stations.toggle` | staff |
| `station_pin` | `/xodim/stansiyalar/<int:station_id>/pin/` (POST) | `views_stations.regenerate_pin` | staff |
| `station_unlock` | `/xodim/stansiyalar/<int:station_id>/blok/` (POST) | `views_stations.unlock` | staff |
| `stations_import` | `/xodim/stansiyalar/import/` | `views_stations.import_roster` | staff |
| `pin_sheet` | `/xodim/stansiyalar/pin-varaq/` (POST) | `views_stations.pin_sheet` | staff |
| `password_change` | `/xodim/parol/` | `views_stations.password_change` | staff |
| `mtu_passwords` | `/xodim/mtu-parol/` | `views_stations.mtu_passwords` | boshqarma |

`pe` is a period end as `YYYYMMDDHH` in Tashkent time (`2026101208`). Query params: `?pe=`, `?d=YYYY-MM-DD`, `?from=&to=`, `?mtu=<id>` (honoured for boshqarma only).

---

### Task 1: Scaffold, models, guards, seed, periods

**Files:** create `pyproject.toml`, `manage.py`, `.gitignore`, `.env.example`, `compose.dev.yaml`, `config/*`, `hisobot/__init__.py`, `apps.py`, `models.py`, `migrations/*`, `management/commands/seed.py`, `xlsx/shablon.xlsx` (copy of `Дон_махсулотлари_кунлик_маълумоти.xlsx`), `periods.py`, `tests/__init__.py`, `tests/test_periods.py`.

**Produces:**

```python
# config/settings.py reads env: SECRET_KEY, DEBUG (default 1), ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS,
# DB_HOST(127.0.0.1) DB_PORT(5440) DB_NAME DB_USER DB_PASSWORD (all default "hisobot"), TEST_DB_NAME, TRUST_PROXY(0)
# AUTH_USER_MODEL = "hisobot.User"; SESSION_COOKIE_AGE = 30 days; SESSION_SAVE_EVERY_REQUEST = True
# Tunables: PIN_LOCK_EVERY=5, PIN_LOCK_STEPS=[15min,1h,4h,24h], PIN_IP_FAILS_PER_HOUR=30,
#           STAFF_IP_FAILS=10, STAFF_IP_WINDOW=15min, STAFF_SESSION_HOURS=12
# When DEBUG is off: SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE, SECURE_HSTS_SECONDS, SECURE_PROXY_SSL_HEADER.

# hisobot/models.py
class Mtu:      name (unique, 50), sort
class User(AbstractUser):  mtu = FK(Mtu, null, PROTECT);  is_boshqarma -> bool (mtu_id is None)
class Station:  mtu FK PROTECT (related_name="stations"), name (100, validate_station_name), sort,
                active_from (default now), active_to (null), opening_remaining (null), opening_at (null),
                pin_hash ("" default), pin_version (0), last_login_at (null), fail_streak (0), locked_until (null)
                is_active -> bool (active_to is None);  expects(pe) -> bool
class Entry:    station FK PROTECT (related_name="entries"), period_end, unloaded, arrived,
                remaining_calc (int, may be negative), remaining_override (null), version (1),
                created_at, updated_at;  remaining -> int;  overridden -> bool (override not None and != calc)
class Change:   at, kind ("entry"|"opening"|"station"|"pin"|"unlock"), station FK PROTECT, entry FK PROTECT null,
                user FK PROTECT null (None = station via PIN), ip (null), cause FK self PROTECT null, old JSON, new JSON
class LoginFailure:  at, ip, station FK SET_NULL null, username ("")

# hisobot/periods.py   (all datetimes aware)
TZ = ZoneInfo("Asia/Tashkent");  HALF = timedelta(hours=12);  STATION_WINDOW = timedelta(hours=24)
def local(dt) -> datetime
def is_period_end(dt) -> bool          # local time exactly 08:00:00.000000 or 20:00:00.000000
def last_ended(now) -> datetime        # greatest period end <= now
def prev_end(pe) -> datetime;  def next_end(pe) -> datetime
def bounds(pe) -> tuple[datetime, datetime]         # (pe - 12h, pe)
def label(pe) -> tuple[date, int]                   # (report date, 1 = night ending 08:00, 2 = day ending 20:00)
def ends_for(report_date) -> tuple[datetime, datetime]   # (D 08:00, D 20:00)
def open_for_station(now) -> list[datetime]         # [last_ended(now), prev_end(last_ended(now))]
def station_can_write(pe, now) -> bool              # pe <= now < pe + 24h
def staff_can_write(pe, now) -> bool                # pe <= now
def to_slug(pe) -> str;  def from_slug(s) -> datetime     # "YYYYMMDDHH"; from_slug raises ValueError on anything else
```

Constraints: `UniqueConstraint(Lower("name"), "mtu")` on Station; check "opening fields both null or both set"; `unique(station, period_end)` on Entry; checks `unloaded <= 999`, `arrived <= 999`, `remaining_override <= 9999`. `validate_station_name`: strip, 1–100 chars, first char not in `=+-@`.

`0002_guards.py` (RunSQL with reverse): trigger rejecting UPDATE and DELETE on `hisobot_change`; trigger rejecting DELETE on `hisobot_entry`; CHECK on `hisobot_entry.period_end` that Tashkent local time is exactly 08:00:00 or 20:00:00.

`seed` command: idempotent. Creates the 6 MTUs (Тошкент, Қўқон, Бухоро, Қўнғирот, Қарши, Термиз; sort 1–6) and users `boshqarma`, `toshkent`, `qoqon`, `buxoro`, `qongirot`, `qarshi`, `termiz`. Prints a random 16-char password for each user it creates, once; existing users are left alone.

- [ ] Write `tests/test_periods.py` first. Cases, all with explicit aware datetimes:
  - `label(12.10 08:00) == (date(2026,10,12), 1)`; `label(12.10 20:00) == (date(2026,10,12), 2)`; `ends_for(date(2026,10,12)) == (12.10 08:00, 12.10 20:00)`; `bounds(12.10 08:00) == (11.10 20:00, 12.10 08:00)`.
  - `last_ended`: 12.10 07:59:59 → 11.10 20:00; 12.10 08:00:00 → 12.10 08:00; 12.10 19:59:59 → 12.10 08:00; 12.10 20:00:00 → 12.10 20:00; 13.10 00:30 → 12.10 20:00.
  - `open_for_station` at 12.10 07:59:59 → [11.10 20:00, 11.10 08:00]; at 12.10 08:00:00 → [12.10 08:00, 11.10 20:00].
  - `station_can_write(pe=12.10 08:00)`: false at 12.10 07:59:59, true at 12.10 08:00:00, true at 13.10 07:59:59, false at 13.10 08:00:00. `staff_can_write` false before the end, true a year later.
  - The same instants passed as UTC datetimes (03:00Z, 15:00Z) give identical results (process-in-UTC safety).
  - `is_period_end` false for 08:00:01 and 08:30; `from_slug(to_slug(pe)) == pe`; `from_slug("2026101209")`, `"abc"`, `"20261012"` raise `ValueError`.
- [ ] Run, watch them fail, implement `periods.py`, run until green.
- [ ] Start dev DB, `makemigrations`, write `0002_guards.py`, `migrate`, run `seed` twice (second run prints nothing new).
- [ ] Add a DB test in `test_periods.py`: inserting an `Entry` with `period_end` 08:30 raises `IntegrityError`; deleting an `Entry` raises; updating a `Change` raises.

### Task 2: Balance (`balance.py`)

**Consumes:** Task 1 models and `periods`.

**Produces:**

```python
class SaveError(Exception):
    code: str    # "period" | "window" | "inactive" | "opening_required" | "before_opening" | "negative" | "stale" | "range"
    message: str # Cyrillic, user-facing
@dataclass
class SaveResult: entry: Entry; changed: bool; cascaded: int

def prev_for(station, period_end) -> tuple[int | None, bool]
    # (effective remaining of the nearest earlier entry, else opening_remaining, else None;
    #  gap: True when the immediately preceding period has no entry and starts at/after opening_at)
def save_entry(*, station_id, period_end, unloaded, arrived, override=None, opening=None,
               form_version, form_prev, actor, ip, now=None) -> SaveResult
    # actor: User or None (None = station via PIN). now defaults to timezone.now().
def set_opening(*, station_id, value, actor, ip) -> int      # staff only; returns number of cascaded entries
def recompute(station) -> dict[int, int]                     # entry id -> calc recomputed from the opening
```

`save_entry` rules (spec section 5, one `transaction.atomic`):
1. `Station.objects.select_for_update().get(pk=station_id)`.
2. Not a period end → `period`. Window: actor None → `station_can_write`, else `staff_can_write`; fail → `window`. Actor None and station not active → `inactive`. Values outside 0–999 / override outside 0–9999 → `range`.
3. Station has no `opening_at`: `opening` is required (else `opening_required`); set `opening_remaining`, `opening_at = bounds(pe)[0]`, `active_from = opening_at`, and write `Change(kind="opening")`. Otherwise `bounds(pe)[0] < opening_at` → `before_opening`.
4. `prev`, `calc = prev + arrived - unloaded`, `effective = override if override is not None else calc`.
5. Existing entry with identical `(unloaded, arrived, remaining_override)` → `SaveResult(changed=False)`, nothing written.
6. `(existing.version if existing else 0) != form_version`, or (opening not being set in this call and `prev != form_prev`) → `stale`.
7. `effective < 0` → `negative`.
8. Create (`version=1`) or update (`version += 1`); write `Change(kind="entry", old, new)` where both dicts carry `unloaded, arrived, remaining_calc, remaining_override` (old is `{}` on create).
9. Cascade over later entries ordered by `period_end`, `running = effective`: `new = running + e.arrived - e.unloaded`; equal to stored → stop; else save (`version += 1`), write `Change(kind="entry", cause=<step 8 change>, old={"remaining_calc": …}, new={…})`; if `e.remaining_override is not None` → stop; `running = new`.

`set_opening`: lock station; requires `opening_at` already set; writes `Change(kind="opening")`; cascades from the first entry with the same loop.

- [ ] Write `tests/test_balance.py` first (helper `mk(station, pe, unloaded, arrived, override=None, actor=staff)` calls `save_entry` with correct `form_version`/`form_prev`, `now` well after `pe`). Cases:
  - First entry without `opening` → `opening_required`. With `opening=10, arrived=3, unloaded=5` → calc 8; station gets `opening_remaining=10`, `opening_at = period start`, `active_from = opening_at`; one `opening` and one `entry` change.
  - Chain of three: remainders follow `prev + arrived - unloaded`.
  - Skipped period: P1 then P3 → P3 chains from P1, `prev_for(P3)` gap flag true; then filling P2 cascades into P3 and the P3 cascade change has `cause` = the P2 change.
  - Editing P1 after P2, P3 exist recomputes both; `cascaded == 2`.
  - Override anchors: P2 has override 4; editing P1 changes P2's `remaining_calc` but not its effective value, and P3 is untouched (cascade stops after P2).
  - Override equal to calc is stored (not None) and `overridden` is False; submitting `override=None` clears it.
  - Direct save with negative effective → `negative`; same numbers plus `override=0` succeeds.
  - Upstream correction that makes a later calc negative succeeds; the later entry stores the negative calc.
  - Identical resubmit → `changed is False`, no new `Change`, even with a stale `form_version`.
  - Stale version → `stale`; stale `form_prev` (previous period edited in between) → `stale`.
  - Station (actor None): at `pe + 24h` exactly → `window`; one second earlier OK; before `pe` → `window`. Staff a year later OK. Station with `active_to` set → `inactive`.
  - Entry for a period starting before `opening_at` → `before_opening`.
  - `set_opening` cascades and logs; refused when `opening_at` is None.
  - Invariant after every scenario above: `recompute(station) == {e.id: e.remaining_calc for e in entries}`.
  - Concurrency (`TransactionTestCase`, two threads, each closing its DB connection): two different saves for the same station+period with the same `form_version` → exactly one succeeds, the other raises `stale`; exactly one `Entry` row.
- [ ] Fail, implement, green.

### Task 3: Access (`access.py`)

**Consumes:** Task 1 models, settings tunables.

**Produces:**

```python
def client_ip(request) -> str          # TRUST_PROXY: rightmost X-Forwarded-For entry, else REMOTE_ADDR
def ip_bucket(ip) -> str               # IPv4 unchanged; IPv6 collapsed to its /64 network address
def new_pin() -> str                   # 8 digits from secrets
def format_pin(pin) -> str             # "1234 5678"
def set_pin(station, *, actor, ip) -> str        # new hash, pin_version+1, last_login_at=None, fail_streak=0, locked_until=None; Change(kind="pin"); returns plaintext
def issue_unused_pins(stations, *, actor, ip) -> list[tuple[Station, str]]   # only pin_hash == "" or last_login_at is None
def unlock(station, *, actor, ip) -> None        # locked_until=None only; Change(kind="unlock")

class Login(enum.Enum): OK, BAD, LOCKED, THROTTLED, NO_PIN
def station_login(request, station, pin) -> Login
def staff_login(request, username, password) -> Login      # OK / BAD / THROTTLED
def logout(request) -> None                                # session.flush()

def current_station(request) -> Station | None   # session station_id + pin_version match + station active, else None
def station_required(view)     # sets request.station; otherwise redirect to "home"
def staff_required(view)       # authenticated and login younger than STAFF_SESSION_HOURS; otherwise logout + redirect "staff_login"
def boshqarma_required(view)   # staff_required and user.is_boshqarma, else 404
def stations_for(user) -> QuerySet[Station]      # boshqarma: all; MTU user: its own; select_related("mtu"), ordered mtu.sort, sort, name
def mtus_for(user) -> QuerySet[Mtu]
def scope(request) -> tuple[QuerySet[Station], Mtu | None]   # applies ?mtu= for boshqarma only; an unknown id is a 404
```

`station_login` order: `NO_PIN` if no hash → `LOCKED` if `locked_until > now` → `THROTTLED` if failures for this IP bucket in the last hour ≥ `PIN_IP_FAILS_PER_HOUR` → `check_password`. Failure: under `select_for_update`, `fail_streak += 1`; when `fail_streak % PIN_LOCK_EVERY == 0`, `locked_until = now + PIN_LOCK_STEPS[min(fail_streak // PIN_LOCK_EVERY, len) - 1]`; insert `LoginFailure`; delete `LoginFailure` older than 7 days. Success: `fail_streak = 0`, `locked_until = None`, `last_login_at = now`, `request.session.flush()`, store `station_id` and `pin_version`.

`staff_login`: `THROTTLED` if failures for this IP bucket within `STAFF_IP_WINDOW` ≥ `STAFF_IP_FAILS`; `authenticate`; failure inserts `LoginFailure(username=…)`; success flushes the session, `django.contrib.auth.login`, stamps `staff_login_at`.

- [ ] Write `tests/test_access.py` first. Cases:
  - `new_pin` is 8 digits; `format_pin("12345678") == "1234 5678"`; `set_pin` stores a hash (never the plaintext), bumps `pin_version`, writes a `pin` change.
  - `issue_unused_pins` skips a station that has logged in, includes never-used ones, and re-running gives new PINs for the still-unused.
  - Correct PIN → OK, session holds the station; `current_station` returns it. After `set_pin` that session yields `None`. After deactivation it yields `None`.
  - 4 wrong PINs → BAD without lock; 5th → `locked_until ≈ now + 15 min`; correct PIN while locked → LOCKED; 10th failure → 1 h; 15th → 4 h; 20th and 25th → 24 h. Success resets `fail_streak`.
  - Lock does not affect an existing session.
  - `unlock` clears `locked_until`, keeps the PIN, writes a change.
  - 30 failures from one IP across different stations → next attempt from that IP is THROTTLED even with a correct PIN; another IP is unaffected. `ip_bucket` of two addresses in one IPv6 /64 is equal.
  - Staff: wrong password ×10 from one IP → THROTTLED; other IP still OK. No per-username lock.
  - `staff_required`: a session whose `staff_login_at` is 12 h 1 min old is logged out and redirected.
  - Logging in as staff in a browser that holds a station session leaves no `station_id` in the session, and vice versa.
  - `stations_for(mtu_user)` contains only its MTU; `scope` ignores `?mtu=` for an MTU user and 404s on an unknown id for boshqarma.
  - Brute-force budget computed from settings: guesses per station per year ≤ 2000.
- [ ] Fail, implement, green.

### Task 4: Read side (`reports.py`)

**Consumes:** Task 1 models, `periods`. Tests build `Entry` rows with the ORM directly (allowed in tests).

**Produces:**

```python
@dataclass
class Cell:
    entry: Entry | None
    expected: bool            # station.expects(pe)
    remaining: int | None     # entry's effective remaining, else the last known value at or before this period, else None
    carried: bool             # True when remaining comes from an earlier period or the opening
@dataclass
class DayRow:
    station: Station; night: Cell; day: Cell
    total_unloaded: int | None    # sum of the unloaded values present; None when both periods are absent
    partial: bool                 # an expected period has no entry
@dataclass
class Totals:
    n_unloaded: int; n_arrived: int; n_remaining: int; d_unloaded: int; d_arrived: int; d_remaining: int
    total_unloaded: int; partial: bool; stations: int; missing: int

def remaining_at(stations, t) -> dict[int, int | None]     # stock rule, spec section 10
def daily_rows(stations, report_date) -> list[DayRow]      # station order as given by the queryset
def totals(rows) -> Totals                                 # None counts as 0; partial if any row partial
def by_mtu(rows) -> list[tuple[Mtu, list[DayRow], Totals]]
@dataclass
class Submission: mtu: Mtu; expected: int; submitted: int; missing: list[Station]; done: list[Entry]
def submission(stations, period_end) -> list[Submission]   # one per MTU present in `stations`
def attention(stations, since) -> list[Entry]              # overridden or effective < 0, period_end >= since, newest first
```

Query count must not grow with the number of stations (use `DISTINCT ON` for the carry-in, one query for in-range entries).

- [ ] Write `tests/test_reports.py` first. Cases: entry present → not carried; no entry but an earlier one → carried value; no entries but opening before T → opening, carried; opening after T → None; station not expected (created later, or deactivated before) → `expected False`, not counted missing; one period absent → `total_unloaded` is the present value and `partial True`; both absent → None; `totals` sums and counts missing; `submission` gives N of M per MTU and lists missing in station order; `attention` returns overridden and negative entries only; `assertNumQueries` is the same for 2 and for 20 stations.
- [ ] Fail, implement, green.

### Task 5: Shell: URLs, forms, base layout, CSS

**Consumes:** Task 1. Runs alongside Tasks 2–4, so it must not import `balance`, `access` or `reports` at module level in anything but stubs.

**Produces:**
- `hisobot/urls.py` exactly per the URL table, `config/urls.py` including it.
- Stub modules `views_public.py`, `views_station.py`, `views_staff.py`, `views_stations.py`, `views_reports.py`: every view in the table exists and returns HTTP 501 with the text «Ҳали тайёр эмас». `healthz` is implemented for real: JSON `{"ok": true, "time": <ISO Tashkent>}` after `SELECT 1`.
- `forms.py`: `EntryForm` with `unloaded`, `arrived` (0–999, required), `override` (0–9999, optional), `opening` (0–9999, optional; made required by `require_opening=True` kwarg), hidden `version`, `prev`. All error messages Cyrillic («Майдонни тўлдиринг», «0 дан 999 гача сон киритинг», …). Number inputs render with `inputmode="numeric"`, `min`, `max`.
- `templatetags/hisobot_extras.py`: filters `d` (date → `dd.mm.yyyy`), `t` (datetime → `HH:MM` Tashkent), `dt` (`dd.mm.yyyy HH:MM`), `period_range` (pe → «11.10 20:00 – 12.10 08:00»), `period_name` (pe → «Тунги давр» / «Кундузги давр»), `slug` (pe → `to_slug`), `num` (None → «—»).
- `templates/hisobot/base.html`: blocks `title`, `nav`, `content`; nav depends on the principal (station: station name + Чиқиш; staff: Ҳозир, Кунлик жадвал, Ҳисобот, Станциялар, account name, Парол, Чиқиш as a POST form; boshqarma also «МТУ пароллари»); Django messages rendered as notices.
- `static/hisobot/app.css`: design tokens as CSS variables with a dark scheme under `prefers-color-scheme`, system font stack with good Cyrillic, `font-variant-numeric: tabular-nums` for numbers. Components, each documented in a comment block at the top of the file: `.page`, `.toolbar`, `.card`, `.card-grid`, `.stat` (big number + caption), `.btn`, `.btn-primary`, `.btn-quiet`, `.btn-danger`, `.field` (label + input + error), `.num-input` (large numeric input for phones), `.notice-info|ok|warn|error`, `.badge-ok|warn|bad|muted`, `.table` and `.table-wrap` (horizontal scroll on phones, sticky first column), cell states `.is-missing` (grey), `.is-override` (yellow), `.is-negative` (red), `.is-carried` (italic), `.list` (tappable rows), `.print-only`, `.no-print`, and print rules for the PIN sheet (`.pin-strip`, cut lines, page breaks).
- `templates/hisobot/_styleguide.html` at `/_uslub/` (DEBUG only): every component once, so feature tasks can copy markup.

Design direction: a working tool for dispatchers, not a marketing page. Quiet neutral surfaces, one restrained accent, strong numerals, generous touch targets (min 44px), no gradients, no decorative shadows, no emoji. It must read well in sunlight on a cheap Android phone and on an office monitor.

- [ ] Write a test in `tests/test_shell.py`: every URL name in the table reverses; `healthz` returns 200 with `ok`; `EntryForm` rejects empty, 1000 and -1 with Cyrillic messages; each filter's output for a fixed datetime.
- [ ] Implement, green. Run `runserver` and load `/_uslub/` once to check it renders.

### Task 6: Station flow

**Files:** `views_public.py`, `views_station.py`, `templates/hisobot/public/{home,mtu,pin,staff_login}.html`, `templates/hisobot/station/{home,entry}.html`, `tests/test_station_flow.py`.

**Consumes:** `access.*`, `balance.save_entry/prev_for/SaveError`, `periods.*`, `forms.EntryForm`, `reports.daily_rows` (last 7 days).

Behaviour:
- `home`: station session → redirect `station_home`; staff → `staff_now`; else six MTU buttons and a small link «МТУ / Бошқарма учун кириш».
- `mtu_stations`: active stations of that MTU, tappable list, a filter-as-you-type box (a few lines of inline JS, works without JS too).
- `station_login`: GET shows the station name and one PIN field (`inputmode="numeric"`, `autocomplete="one-time-code"` off, max 9 chars so a pasted `1234 5678` works; spaces stripped). POST maps `Login` to messages: BAD «ПИН нотўғри», LOCKED «Кириш вақтинча ёпилган. HH:MM дан кейин уриниб кўринг ёки МТУга мурожаат қилинг», THROTTLED «Жуда кўп уриниш. Бироздан сўнг қайта уриниб кўринг», NO_PIN «Бу станцияга ПИН ҳали берилмаган. МТУга мурожаат қилинг». Inactive or unknown station → 404.
- `staff_login`: username + password; BAD «Логин ёки парол нотўғри».
- `logout`: POST only.
- `station_home`: server time; cards for `open_for_station(now)` newest first with range, status and saved numbers, deadline («13.10 08:00 гача тузатиш мумкин»), button Киритиш / Тузатиш; next opening time; last 7 report days read-only.
- `station_entry`: `from_slug` failure → 404; not writable now → redirect home with a notice. Form shows «Давр бошида: N» (or the opening field on the first entry), the gap warning, live «Қолган: N» updated by ~15 lines of inline JS, `<details>` «Қолдиқни қўлда киритиш», buttons «Юбориш» and «Ҳаракат бўлмади (0 / 0)» (a submit button that posts zeros). `SaveError` codes map to Cyrillic messages; `stale` re-renders with fresh numbers and «Маълумот ўзгарган, қайта текширинг». Success → redirect home with «Қабул қилинди».
- The station id never comes from the URL or the form on station pages.

- [ ] Tests first: full happy path with the Django test client (pick MTU → station → PIN → first entry with opening → home shows «Қабул қилинди»); wrong PIN message; locked message; entry for a period not yet ended → redirected, nothing saved; entry page for a period older than 24 h → redirected; «Ҳаракат бўлмади» saves 0/0; negative result shows the error and saves nothing; second device with a stale form gets the stale message; posting another station's id in the form body has no effect; logout is POST-only.
- [ ] Implement, green.

### Task 7: Staff: now, daily table, entry edit

**Files:** `views_staff.py`, `templates/hisobot/staff/{now,daily,entry}.html`, `tests/test_staff.py`.

**Consumes:** `access.staff_required/scope/stations_for`, `reports.*`, `balance.*`, `periods.*`, `forms.EntryForm`.

Behaviour:
- `now`: `?pe=` (default `last_ended(now)`; invalid or future → default), previous/next period links. Boshqarma: six «N / M» cards linking to `?mtu=`. One MTU in scope: «14 / 18 топширди», missing stations first with «Киритиш» (→ `staff_entry`), then submitted with their numbers. Below: «Эътибор талаб» from `attention(stations, now - 30 days)`.
- `daily`: `?d=` (default: report date of the latest ended period; invalid → default), previous/next day. Table in the template layout (two header rows, 9 columns), grouped by MTU with «Жами» per MTU and a grand total when more than one MTU is in scope. Cell classes per Task 5; a partial daily total is followed by `*` with a legend. Each station row links to `staff_entry` for night and day. A «Excel юклаб олиш» link to `staff_export` with the same params.
- `entry`: station must be in `stations_for(user)` else 404. Same form as the station's plus the history for this entry: every `Change` with `entry_id`, newest first: time, who (username, or «Станция (ПИН)»), old → new, and «сабаб: <period> тузатилди» for cascade rows. Staff may save any ended period.

- [ ] Tests first: MTU user sees only its stations on all three pages; `?mtu=<other>` is ignored for an MTU user; a foreign `station_id` → 404; missing-first ordering; counts match `submission`; daily table shows override, missing and negative classes; staff saves a period 3 days old; the history lists the direct change and a cascade row with its cause; unauthenticated → redirect to `staff_login`.
- [ ] Implement, green.

### Task 8: Stations management, import, PINs, passwords

**Files:** `views_stations.py`, `roster.py`, `templates/hisobot/staff/{stations,station_edit,import,pin_sheet,password_change,mtu_passwords}.html`, `tests/test_stations.py`.

**Produces:**

```python
# roster.py
class RosterError(Exception): errors: list[str]      # each "N-қатор: …"
def import_roster(file, *, user) -> int               # number of stations added
```

`import_roster`: reject > 1 MB or non-xlsx; `load_workbook(read_only=True, data_only=True)`; first sheet; row 1 is the header and must be `МТУ | Станция номи`; up to 1000 data rows, blank rows skipped. Per row: MTU name must match an existing MTU case-insensitively after trimming; for an MTU user the row's MTU must be its own; name passes `validate_station_name`; duplicates inside the file are an error. Existing (MTU, name) pairs are skipped silently. Any error → nothing saved, all errors reported. New stations get `sort` from row order.

Views:
- `index`: stations in scope grouped by MTU: name, status (фаол / ўчирилган), PIN status (берилмаган / берилган, ишлатилмаган / ишлатилган), last login, lock state with «Блокдан чиқариш»; add-station form (boshqarma chooses the MTU, an MTU user's is forced); buttons Импорт and «ПИН яратиш» (all never-used in scope, or the checked ones).
- `edit`: rename, sort, opening balance (only when `opening_at` is set; calls `balance.set_opening`), event history (`Change` of kinds station / pin / unlock / opening). Rename and toggle write `Change(kind="station")`.
- `toggle`: deactivate sets `active_to = now`; activate sets `active_from = now`, `active_to = None`.
- `regenerate_pin`: `access.set_pin`, responds with the one-strip PIN sheet.
- `pin_sheet`: `access.issue_unused_pins`, responds with the printable sheet: per station a strip with МТУ, station name, the absolute link to `station_login`, and the PIN as `1234 5678`. `Cache-Control: no-store`. A notice says PINs are shown once.
- `import_roster`: GET explains the format with a small example table; POST imports and shows the count or the error list.
- `password_change`: own small form (current, new ×2, min 10 chars), Cyrillic messages; `update_session_auth_hash`.
- `mtu_passwords` (boshqarma): lists MTU users; POST resets one to a random 16-char password shown once, `no-store`.

- [ ] Tests first: roster happy path (2 MTUs, order kept); wrong header; unknown MTU; MTU user importing another MTU's row → error and nothing saved; formula-like name `=1+1` rejected; duplicate in file; re-import adds only new rows and leaves PINs untouched; 1001 rows rejected; a non-xlsx upload rejected. PIN sheet contains each PIN exactly once and a later GET of the stations page contains none; `no-store` header; `pin_sheet` skips used PINs; `regenerate_pin` ends that station's session. Toggle sets the dates; a deactivated station is not expected afterwards but still is for earlier periods. MTU user: adding a station with another MTU's id in the POST creates it in its own MTU; foreign `station_id` on every POST endpoint → 404. `mtu_passwords` is 404 for an MTU user.
- [ ] Implement, green.

### Task 9: Summary, charts, Excel export

**Files:** `views_reports.py`, `summary.py`, `export.py`, `templates/hisobot/staff/report.html`, `static/hisobot/chart.umd.min.js` (Chart.js 4.4.x downloaded once from cdnjs and committed), `static/hisobot/charts.js`, `tests/test_summary_export.py`.

**Produces:**

```python
# summary.py
@dataclass
class SummaryRow: station: Station | None; mtu: Mtu; unloaded: int; arrived: int; remaining_end: int | None
                  submitted: int; expected: int; overrides: int
def summary(stations, d_from, d_to) -> tuple[list[SummaryRow], list[SummaryRow]]   # (per station, per MTU)
def daily_series(stations, d_from, d_to) -> dict    # {"labels": [...], "unloaded": [...], "arrived": [...], "remaining": [...], "missing": [...]}
def compare(stations, d_from, d_to, *, by) -> dict  # by "mtu" | "station": {"labels": [...], "unloaded": [...], "arrived": [...]}
# export.py
def export_daily(stations, report_date, *, user, now) -> bytes
```

- `report`: presets «Ҳафта» (last 7 report days), «Ой» (last 30), custom `?from=&to=` with native date inputs; range > 366 days or reversed → clamp and notice. Summary tables per MTU and per station; two charts with data passed via `json_script`; `charts.js` draws a line chart (unloaded, arrived, end-of-day remaining) and a bar chart (by MTU for boshqarma with all MTUs in scope, by station otherwise). Caption states how many station-periods are missing in the range. Load the `dataviz` skill before writing chart code.
- `export`: fills `hisobot/xlsx/shablon.xlsx` from row 4 down, copying the header's border/alignment style to data cells. One MTU in scope: its stations then «Жами». Several: an MTU title row, stations, «<МТУ> жами», and a final «Жами». Numbers as numbers, station names as explicit string cells. Missing cells blank; carried remaining in grey font; partial daily total as text `12*`; overridden remaining with yellow fill and a cell comment «Ҳисобланган: N»; negative in red. Title row gets the date («… 12.10.2026»). Footer lines: generated time, account, number of stations not submitted, legend for `*`. Filename `don-YYYY-MM-DD.xlsx`, correct content type.

- [ ] Tests first: `summary` sums over a range and reports submitted/expected and the stock at range end; `daily_series` has one point per day and the missing counts; `compare` by MTU and by station; export opens with openpyxl, header rows equal the template's, the station rows carry the right numbers, the totals row is correct, an override cell has the comment, a missing cell is None, a formula-like station name is a string cell (`data_type == "s"`), an MTU user's export contains no other MTU's station.
- [ ] Implement, green.

### Task 10: Isolation test

**Files:** `tests/test_isolation.py`.

- [ ] A registry maps **every** URL name under `/xodim/` to how to call it (method, kwargs built from MTU A's or MTU B's objects, POST data). The test asserts the registry's keys equal the staff URL names found in `hisobot.urls`, so an unlisted URL fails.
- [ ] Fixture: MTU A with stations «Альфа-бекат», MTU B with «Бета-бекат», entries, changes and overrides on both.
- [ ] Logged in as A: every endpoint called with A's own objects returns content (HTML, xlsx text extracted with openpyxl, chart JSON, history, PIN sheet, import result) that never contains «Бета-бекат» or B's ids in links; every endpoint called with B's `station_id` returns 404 and changes nothing; `?mtu=<B>` is ignored.
- [ ] Anonymous: every staff URL redirects to `staff_login`. Station session: every staff URL redirects to `staff_login`; staff session: `/stansiya/` redirects to `home`.
- [ ] Fix whatever it finds in the owning view module.

### Task 11: Deployment

**Files:** `Dockerfile`, `compose.yaml`, `Caddyfile`, `backup.sh`, `.env.example`, `README.md` (Uzbek Latin).

- [ ] `Dockerfile`: python:3.12-slim, uv sync from the lockfile, `collectstatic`, non-root user, `gunicorn config.wsgi -k gthread --workers 3 --threads 4 --timeout 30`.
- [ ] `compose.yaml`: `caddy` (80/443, volumes for data and config), `web`, `db` (postgres:17, named volume, healthcheck); only Caddy publishes ports; `restart: unless-stopped`; log size capped; migrations run on `web` start.
- [ ] `Caddyfile`: `{$DOMAIN}` → `reverse_proxy web:8000`, gzip.
- [ ] `backup.sh`: `set -euo pipefail`; `pg_dump -Fc --exclude-table-data=django_session` through `docker compose exec -T db`; verify with `pg_restore --list` and a minimum size; keep 30 days; non-zero exit on any failure. README shows the cron line for 10:00 and 22:00.
- [ ] `README.md`: local start (dev DB, migrate, seed, runserver), first-time setup walkthrough (log in as an MTU → import stations → print PIN sheet), server deployment steps, backup and restore steps, how to reset the boshqarma password (`manage.py changepassword`).
- [ ] Build the image and bring the stack up locally with `DOMAIN=localhost`; `/healthz` answers through Caddy.

## Self-review against the spec

| Spec section | Task |
|---|---|
| 3 Texnologiya | 1, 5, 11 |
| 4 Ma'lumotlar modeli | 1 |
| 5 Qoldiq algoritmi | 2 |
| 6 Vaqt qoidalari | 1 (periods), 2 (windows), 6, 7 |
| 7 Stansiya kirishi | 3, 6, 8 |
| 8 MTU va boshqarma kirishi | 3, 8, 10 |
| 9 Ekranlar | 5, 6, 7, 8, 9 |
| 10 Hisobotlar, eksport, grafiklar | 4, 7, 9 |
| 11 Joylashtirish va zaxira | 11 |
