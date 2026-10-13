# Bento redesign (direction C, «Rangli») Implementation Plan

**Goal:** Re-skin every page of the app in the bento design the customer chose, without changing behaviour.

**Why:** On 2026-10-13 the customer called the shipped UI plain ("musor") and chose direction C out of three bento mockups.

**Source of truth for the look:** `design-mockups/c-rangli/` (git-ignored, local): `home.html`, `mtu.html`,
`dashboard.html`, `station.html`, `style.css`, `fonts/` (Onest, OFL) and `shots/*.png`. Pages without a mockup extend
the same language: same tokens, tile radii, type scale, pill buttons and chips, МТУ tints.

## Global constraints

- Behaviour, URLs, forms, permissions, messages and the plan's glossary stay as they are
  (`docs/superpowers/plans/2026-10-12-don-hisobot.md`, "Global Constraints" and glossary). Only presentation changes,
  plus the extra read-only context the new dashboard needs.
- Every МТУ has one tint, keyed by `Mtu.sort` (1 Тошкент, 2 Қўқон, 3 Бухоро, 4 Қўнғирот, 5 Қарши, 6 Термиз):
  markup `class="mtu-{{ mtu.sort }}"`, CSS variables `--tint --soft --mark --deep` as in the mockup. Colour carries
  meaning (which МТУ), never decoration only.
- Light theme only (`color-scheme: light`). WCAG AA contrast. 44px touch targets on phone screens.
- Font: Onest, self-hosted from `hisobot/static/hisobot/fonts/` with its OFL licence file. No CDN, no other host.
- No new Python dependencies. No JS framework. Small vanilla JS only where a page already has it or needs a
  live number. Chart.js (vendored) for charts.
- Shared design system lives in `hisobot/static/hisobot/app.css` (Task 1 only). Page-specific CSS goes in
  `hisobot/static/hisobot/pages/<area>.css`, loaded through `{% block head %}` in that area's templates.
- Tests stay green: `uv run python manage.py test hisobot --noinput` (274+). Change a test only where it asserted
  old markup; never weaken a behaviour test. Parallel workers set their own `TEST_DB_NAME`.
- Visual check is mandatory: `uv run python .superpowers/shot.py <url> --as <who> --width 1440|390 --out <png>`
  (see the script's docstring), then Read the PNG. The dev server on 127.0.0.1:8000 serves the static files.
  Do not POST to the dev server; it holds the demo data the customer is looking at.

## Tasks

| # | Area | Owns |
|---|---|---|
| 1 | Design system | `app.css`, `fonts/`, `base.html`, `_styleguide.html`, additions to `templatetags/hisobot_extras.py`, `views_public.styleguide`, `tests/test_shell.py` |
| 2 | Public + station | `public/*.html`, `station/*.html`, `views_public.py` (not healthz/styleguide), `views_station.py`, `pages/public.css`, `pages/station.css`, `tests/test_station_flow.py` |
| 3 | Staff: Ҳозир, Кунлик жадвал, entry | `staff/now.html`, `staff/daily.html`, `staff/entry.html`, `views_staff.py`, `pages/staff.css`, `tests/test_staff.py` |
| 4 | Stations, import, PIN sheet, passwords, error pages | `staff/stations.html`, `station_edit.html`, `import.html`, `pin_sheet.html`, `password_change.html`, `mtu_passwords.html`, `templates/40x.html`, `500.html`, `403_csrf.html`, `views_stations.py`, `pages/admin.css`, `tests/test_stations.py` |
| 5 | Report and charts | `staff/report.html`, `views_reports.py`, `static/hisobot/charts.js`, `pages/report.css`, `tests/test_summary_export.py` |
| 6 | Whole-app visual QA and fixes | any template or CSS file |

Task 1 runs first; Tasks 2–5 run in parallel on disjoint files; Task 6 runs last.

## Task 3 dashboard («Ҳозир») content, boshqarma scope (mirror `dashboard.html`)

Hero tile: «Топширди N / M», percent, per-МТУ dot rows. Tiles «Туширилган» and «Кириб келган» with the period's sum
and a per-МТУ segment bar. Tile «Қолган» (stock at the period end, `reports.remaining_at`) with segment bar and legend.
Six МТУ tiles: ring N / M, туширилган / кириб келган / қолган, «N станция топширмаган» or «Ҳаммаси топширди».
«Топширмаганлар» tile: tinted column per МТУ, each station with «Киритиш». «Охирги 14 сутка» chart tile.
«Эътибор талаб» tile. An МТУ user sees the same language scoped to its own МТУ (its stations take the place of
the six МТУ tiles). Query count must not grow with the number of stations.
