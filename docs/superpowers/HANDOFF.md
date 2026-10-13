# Handoff: where the build stands

Updated 2026-10-12. Branch `claude/youthful-cori-fjo0it` (pushed). All 11 plan tasks are built, reviewed and
audited for production; 274 tests green; `ruff check` clean. Ready to deploy: follow `README.md` (Uzbek Latin).

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
| — | Uzbek 404 / 405 / 500 / CSRF pages; Caddy's own 413 and 502–504 | `templates/40x.html`, `500.html`, `403_csrf.html`, `middleware.py`, `Caddyfile` |

Verification, each round with independent finders and an adversarial verifier per finding; every fix has a test
that fails on the old code:
1. Whole-branch review (8 dimensions): 25 of 26 confirmed, fixed in `3a23305`.
2. Check of that commit (regressions, completeness, phone/print in a browser, security): 12 of 14, fixed in `5e8cd0a`.
   Rejected and kept: pre-opening cells of the daily table still link to the entry page, which refuses with the
   reason (hiding the link needs a per-cell flag and would also hide the carried opening value).
3. Production-readiness audit (scale with 200 stations × 2 years, the README followed literally on a real stack,
   failure modes, security of the running stack, every visible string): 12 of 21, fixed in `0794060`.
4. Check of that commit: 3 README issues confirmed and fixed after it (the restore is one `&&` chain that checks
   the dump first, so a typo can no longer start web on an empty database; a 50-byte key recipe for `check
   --deploy`; the port-recovery command on its own line). Both restore paths were run on the stack.

Real runs:
- Browser (Playwright, 360px and 1280px): seed, roster import, PIN sheet on screen and as PDF, station login and
  two periods, MTU and boshqarma pages, report with both charts, history, Excel export; print of the daily table
  and the report.
- **The Docker stack** (built here; Caddy + web + db, `DOMAIN=localhost`): `up --build --wait` healthy, the whole
  flow over HTTPS through Caddy, HSTS/nosniff/DENY/Secure cookies, a spoofed `X-Forwarded-For` ignored,
  `backup.sh` against the stack (owner-only files), the README restore (drop, create, single-transaction
  restore, migrate), Caddy's Uzbek 413 and 503, `check --deploy` clean.

## Running without Docker

A plain PostgreSQL works: role `hisobot` / password `hisobot` with `CREATEDB`, database `hisobot`, then
`export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=hisobot DB_USER=hisobot DB_PASSWORD=hisobot`.
Parallel test runs each need their own `TEST_DB_NAME`.

Building the image behind a TLS-intercepting proxy (as in the cloud sandbox) needs a base that trusts the proxy's
CA; production servers do not.

## Decisions taken in Tasks 6–11 and the reviews (beyond the plan)

- Station: a card for an open period that starts before `opening_at` shows the `before_opening` text and no
  button; staff `entry` refuses such a period the same way. A lock message gives the date too when the lock
  ends on another day. Station search folds ў/қ/ғ/ҳ/ё to у/к/г/х/е (Russian keyboards), server and JS alike.
- Staff entry: after a save it redirects to itself with «Сақланди» (+ cascaded count); stale shows the stored
  numbers; «Орқага» goes to the daily table of that date (boshqarma: the station's MTU).
- Accounts are shown as `User.display_name` («Бошқарма», «Тошкент МТУ») in the top bar, histories and the
  Excel footer; the Latin login only where it is typed (sign-in, МТУ пароллари).
- Stations: no `Change` row on creation; `pin_sheet` 404s a foreign or malformed checked id; the boshqarma's
  `?mtu=` filters the page, «ПИН яратиш» and unlock; toggle posts `active=0|1` and a repeat is a no-op.
- Station names: the roster import and the add/edit forms both collapse whitespace (`" ".join(value.split())`,
  a no-break space included); `validate_station_name` also rejects what is still unprintable (no migration).
- Roster import: the upload is re-packed with bounded reads (`roster._unpacked`), counting the bytes that really
  come out: ≤ 2 MB in all, `xl/styles.xml` ≤ 256 KB. Rows read up to Excel's 1 048 576 with `reset_dimensions()`.
- Passwords: `AUTH_PASSWORD_VALIDATORS` (similarity, 10 chars, common, numeric) for the «Парол» form (one
  Cyrillic message) and `changepassword`. The «Парол» page's current-password check goes through the staff IP
  throttle (`access.staff_recheck`). No per-username lock (plan: the 7 logins are public).
- Summary: `summary()` / `daily_series()` take `now`; data nobody entered is `None`, never 0; they read entries
  as tuples (a year of 200 stations ≈ 140 000 rows) and use `reports._latest` / `reports._stock` (private).
- Report and export dates are clamped to the latest report date. Export «Жами» rows are blank for a period that
  has not ended. Grey (`is-missing`) on the report only where something was expected.
- Chart.js 4.4.7 UMD comes from the npm registry tarball (shasum matched the registry). Dark chart colours pass
  the dataviz validator. Charts redraw with light tokens for print and scale to the paper.
- `LOGGING` puts an ERROR stderr handler (DEBUG off only) on the root logger; configuring the `django` logger
  itself would wipe runserver's request log. `check --deploy` W005/W008/W021 silenced with reasons.
- Gunicorn runs with `--no-control-socket`; the web healthcheck is a TCP connect (no access-log noise);
  `TZ=Asia/Tashkent` for log lines.
- Phone nav wraps to a second row instead of scrolling sideways.

## Open minor items (none blocking)

- Station edit page's links back to Станциялар do not carry the boshqarma's `?mtu=`.
- `client_ip` falls back to `0.0.0.0` silently when the address cannot be parsed.
- `seed` help text is Latin-script Uzbek (CLI only).
- Opening-balance corrections on a station with years of history cascade with two queries per later entry
  (about 2 s at two years); PIN sheet for 200 stations takes about 4 s. Both judged acceptable for rare actions.
- New UI wording not in the glossary (worth a native speaker's look): «Ўзгаришлар тарихи», «Барча МТУ»,
  «Қолган (ҳисобланган)», «Қолган (қўлда)», «Бошланиш санаси», «Охирги сана», «Кўрсатиш», «Ҳаммаси»,
  «Саҳифа эскирган», «Бу саҳифа тугма орқали очилади», «Тизим қайта ишга тушмоқда», «Парол жуда оддий ёки
  логинга ўхшаш», the export footer lines.

## Next steps

1. Bring the work onto the main line: `feat/tizim` is an ancestor of `claude/youthful-cori-fjo0it`, so it
   fast-forwards (`git checkout feat/tizim && git merge --ff-only claude/youthful-cori-fjo0it && git push`).
2. Deploy by README «Serverga joylashtirish»; then the post-start checks there (healthz, client IP, a hand-run
   `backup.sh`, the cron line).
3. With the customer's roster: «Станциялар» → Импорт, «ПИН яратиш», print and hand out the sheets.
4. Have a native speaker read the wording listed under "Open minor items".
5. Any code change: tests first (`uv run python manage.py test hisobot --noinput`), `ruff check hisobot config`,
   and keep this file current.

## Still needed from the customer

The station roster (МТУ | Станция номи), a server and domain, and where backups are kept off the server.
