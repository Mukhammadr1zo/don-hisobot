# Handoff: where the build stands

Updated 2026-10-12. Branch `feat/tizim`, last commit "feat: qoldiq hisobi, kirish va PIN, …". 132 tests green.

Read in this order: this file, the plan (`docs/superpowers/plans/2026-10-12-don-hisobot.md`), then the spec
(`docs/superpowers/specs/2026-10-12-don-hisobot-design.md`, Uzbek) for the sections your task names.

## Done (reviewed, committed)

| Task | What | Files |
|---|---|---|
| 1 | Scaffold, models, DB guards, seed, time logic | `config/`, `hisobot/models.py`, `migrations/`, `periods.py`, `management/commands/seed.py` |
| 2 | Balance write side | `hisobot/balance.py` |
| 3 | PIN / staff auth, scoping | `hisobot/access.py` |
| 4 | Read side | `hisobot/reports.py` |
| 5 | URLs, stub views, form, filters, base layout, CSS | `hisobot/urls.py`, `forms.py`, `templatetags/`, `templates/hisobot/base.html`, `_styleguide.html`, `static/hisobot/app.css`, `views_*.py` (stubs) |

## To do, in order

1. Tasks 6, 7, 8, 9 of the plan (station flow; staff now/daily/entry; stations management + import + PINs;
   summary + charts + export). File ownership is disjoint, so they can run in parallel. Each replaces its stub `views_*.py`.
2. Task 10 (isolation test over every `/xodim/` URL), Task 11 (Dockerfile, compose, Caddy, backup, README in Uzbek Latin).
3. Whole-branch review, then a real run in the browser: seed, import a roster, print PINs, station enters two
   periods on a phone-width viewport, MTU and boshqarma views, Excel export opened and checked.

## Facts the plan does not contain (decided during Tasks 1–5)

Contract changes:
- `reports.daily_rows(stations, report_date, *, now)`: `now` is required. A period not ended at `now` is a blank
  `Cell(None, False, None, False)`, never "missing". `daily_rows` and `submission` need a queryset, not a list.
  `submission` returns no item for an MTU with no stations in scope.
- `balance.SaveError` has a ninth code, `scope` (an MTU user writing another MTU's station). Show `error.message`
  to the user; do not branch on codes except `stale` and `opening_required`.
- `balance.set_opening`: unchanged value returns 0 and logs nothing; a station without an opening raises
  `SaveError("opening_required")`. `Change(kind="opening")` payload is `{"opening_remaining": N}`.
- Cascaded entries keep `updated_at`: it is the last submission time shown as «Қабул қилинди 08:07».
- Station PINs are hashed with `access.PinHasher` (`pbkdf2_sha256_pin`, 20 000 iterations). Any test that overrides
  `PASSWORD_HASHERS` must keep `"hisobot.access.PinHasher"` in the list.
- `validate_station_name` rejects leading or trailing whitespace: the roster import must strip names first.
- `periods.label` / `to_slug` raise `ValueError` for a non-boundary; `from_slug` accepts years 2000–2099 only.

For views and tests:
- Staff tests: log in by POSTing to `staff_login`, or `client.force_login(user)` and then set
  `session["staff_login_at"] = timezone.now().timestamp()`; without the stamp `staff_required` treats the session as expired.
- Station session keys: `station_id`, `pin_version`. `station_required` sets `request.station`.
- `views_public.station_login` must 404 an inactive station before calling `access.station_login` (which does not check `active_to`).
  The PIN input must accept `1234 5678` (max length 9); `access.station_login` strips spaces.
- Anything that writes `Station` must use `save(update_fields=[...])`, or it clobbers PIN and lock state.
- "Locked" means `locked_until > now`; an expired lock time stays in the column.
- Keep `healthz` and `styleguide` when replacing `views_public.py`.
- Markup rules from the styleguide (`/_uslub/` with DEBUG): station name cell is `<th scope="row">` and its header
  cell has `class="key"`; staff forms sit in `<div class="narrow stack">`; forms carry `novalidate` and print
  hidden-field errors as a notice; PIN strips are not wrapped in `.card`.
- Feature tasks must not edit `app.css`, `base.html`, `forms.py`, `urls.py`, `templatetags/`, `models.py`.
- Parallel test runs: each sets its own `TEST_DB_NAME`.

For Task 11:
- `compose.yaml` must set `DEBUG=0`, `SECRET_KEY`, `TRUST_PROXY=1`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`; give it a
  compose project name other than `hisobot-dev`. Caddy must be the only way in and must overwrite
  `X-Forwarded-For` / `X-Forwarded-Proto`. `.gitattributes` already forces LF for `*.sh`.

## Open minor findings (triage at the final review)

- Phone nav for boshqarma scrolls sideways with a hidden scrollbar (`app.css` ~244).
- `test_access`: no case with a station both locked and IP-throttled (order LOCKED before THROTTLED is unpinned).
- `test_shell` healthz test does not assert the DB round trip.
- `test_balance` thread joins have no timeout; the `last is None` half of the gap flag is untested.
- `client_ip` falls back to `0.0.0.0` silently when the address cannot be parsed.
- `seed` help text is Latin-script Uzbek.

## Still needed from the customer

The station roster (МТУ | Станция номи), a server and domain, and where backups are kept.
