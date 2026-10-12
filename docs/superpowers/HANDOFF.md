# Handoff: where the build stands

Updated 2026-10-12. Branch `claude/youthful-cori-fjo0it` (pushed). All 11 plan tasks are built; 270 tests green.

Read in this order: this file, the plan (`docs/superpowers/plans/2026-10-12-don-hisobot.md`), then the spec
(`docs/superpowers/specs/2026-10-12-don-hisobot-design.md`, Uzbek) for the sections your task names.

## Done

| Task | What | Files |
|---|---|---|
| 1–5 | Scaffold, models, guards, seed, periods; balance; access; read side; shell (URLs, form, filters, CSS) | see the plan's File Structure |
| 6 | Station flow: MTU → station → PIN → two open periods, last 7 days | `views_public.py`, `views_station.py`, `templates/hisobot/public/*`, `station/*` |
| 7 | Staff: Ҳозир, Кунлик жадвал, entry form with history | `views_staff.py`, `staff/now|daily|entry.html` |
| 8 | Stations, roster import, PIN sheet, passwords | `views_stations.py`, `roster.py`, `staff/stations*|import|pin_sheet|*password*.html` |
| 9 | Summary, charts, Excel export | `views_reports.py`, `summary.py`, `export.py`, `staff/report.html`, `static/hisobot/charts.js`, `chart.umd.min.js` |
| 10 | Isolation test over every `/xodim/` URL | `tests/test_isolation.py` |
| 11 | Dockerfile, compose, Caddy, backup, README (Uzbek Latin) | `Dockerfile`, `.dockerignore`, `compose.yaml`, `Caddyfile`, `backup.sh`, `.env.example`, `README.md` |
| — | Uzbek 404 / 500 / CSRF pages | `templates/404.html`, `500.html`, `403_csrf.html` |

Then a whole-branch review: 8 reviewers, each finding checked by an adversarial verifier; 25 of 26 confirmed and
fixed in `3a23305` (the commit message lists them), each with a test that fails on the old code. A second
round (regressions, completeness, phone and print in a real browser, security) checked that commit: 12 of 14
confirmed and fixed in the commit after it. The one rejected: pre-opening cells of the daily table still link
to the entry page, which now refuses with the reason (kept deliberately: hiding the link needs a per-cell flag
and would also hide the carried opening value).

A real browser run (Playwright, 360px phone and 1280px desktop) went through: seed, roster import (a foreign
MTU row refused, then 4 stations), PIN sheet on screen and as PDF, station login with a wrong and the right PIN,
two periods entered (opening, live «Қолган», negative refused), MTU and boshqarma Ҳозир, daily table, report
with both charts, entry history, Excel export opened with openpyxl.

## Not done / not verifiable here

- **The Docker image was never built** and the stack never ran: this environment has no Docker daemon.
  `docker compose config` passes; `collectstatic` and gunicorn with `DEBUG=0` were run directly (errors reach
  stderr with a traceback); the web healthcheck is a TCP connect (gunicorn listens only after `migrate`); `backup.sh` ran against a local PostgreSQL through a
  stand-in `docker`; `pg_restore --clean --if-exists` over a live copy worked. First thing on a machine with
  Docker: `DOMAIN=localhost` stack up, `/healthz` through Caddy (plan Task 11 last step).

## Running without Docker

A plain PostgreSQL works: role `hisobot` / password `hisobot` with `CREATEDB`, database `hisobot`, then
`export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=hisobot DB_USER=hisobot DB_PASSWORD=hisobot`.
Parallel test runs each need their own `TEST_DB_NAME`.

## Decisions taken in Tasks 6–11 (beyond the plan)

- Station: a card for an open period that starts before `opening_at` shows the `before_opening` text and no
  button; staff `entry` refuses such a period the same way. A lock message gives the date too when the lock
  ends on another day.
- Staff entry: after a save it redirects to itself with «Сақланди» (+ cascaded count); stale shows the stored
  numbers; «Орқага» goes to the daily table of that date (boshqarma: the station's MTU).
- Stations: no `Change` row on creation; `pin_sheet` 404s a foreign or malformed checked id; the boshqarma's
  `?mtu=` filters the page and limits «ПИН яратиш» to that MTU; toggle posts `active=0|1` and a repeat is a no-op.
- Station names: the roster import and the add/edit forms both collapse whitespace (`" ".join(value.split())`,
  a no-break space included); `validate_station_name` now also rejects what is still unprintable (`\x01`,
  zero-width space; no migration: the validator is referenced by path).
- Roster import: the upload is re-packed with bounded reads (`roster._unpacked`), counting the bytes that really
  come out: ≤ 2 MB in all, `xl/styles.xml` ≤ 256 KB (openpyxl's cost grows up to 100× the XML). Rows are read up
  to Excel's 1 048 576 with `reset_dimensions()`.
- Summary: `summary()` / `daily_series()` take `now`; data nobody entered is `None`, never 0; `daily_series` uses
  `reports._latest` / `reports._stock` (private) to keep a fixed query count — make them public if touched again.
- Report and export dates are clamped to the latest report date. Export «Жами» rows are blank for a period that
  has not ended. Grey (`is-missing`) on the report only where something was expected.
- Chart.js 4.4.7 UMD comes from the npm registry tarball (shasum matched the registry); cdnjs is blocked here.
  Dark chart colours were re-stepped to pass the dataviz validator. Charts redraw with light tokens for print.
- `LOGGING` puts an ERROR stderr handler (DEBUG off only) on the root logger, so 500s reach `docker compose logs
  web`; configuring the `django` logger itself would wipe runserver's request log.
- Gunicorn runs with `--no-control-socket` (the `app` user has no home).

## Open minor items (none blocking)

- Phone nav for boshqarma scrolls sideways with a hidden scrollbar (`app.css` ~244).
- Station edit page's links back to Станциялар do not carry the boshqarma's `?mtu=` (the list's unlock does).
- `test_access`: no case with a station both locked and IP-throttled (order LOCKED before THROTTLED is unpinned).
- `test_shell` healthz test does not assert the DB round trip.
- `test_balance` thread joins have no timeout; the `last is None` half of the gap flag is untested.
- `client_ip` falls back to `0.0.0.0` silently when the address cannot be parsed.
- `seed` help text is Latin-script Uzbek.
- Pre-existing lint (default ruff rules here): RUF012 on model `Meta`/migrations, I001 in `access.py` and
  `test_access.py`, DTZ001 in `test_periods.py` (deliberate naive datetime), C408 in `test_balance.py`.
- `_styleguide.html` wraps its sample PIN strips in `.card`, against the HANDOFF rule the real sheet follows.
- New UI wording not in the glossary (worth a native speaker's look): «Ўзгаришлар тарихи», «Барча МТУ»,
  «Қолган (ҳисобланган)», «Қолган (қўлда)», «Бошланиш санаси», «Охирги сана», «Кўрсатиш», «Ҳаммаси»,
  «Саҳифа эскирган», the export footer lines.

## Still needed from the customer

The station roster (МТУ | Станция номи), a server and domain, and where backups are kept off the server.
