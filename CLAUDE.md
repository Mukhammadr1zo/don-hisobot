# Don hisobot

Daily grain-unloading report for Uzbekistan Railways: stations enter data by PIN, 6 MTUs see their own stations,
the department sees all. Django 5.2, PostgreSQL, server-rendered templates, UI in Uzbek Cyrillic.

- Current status and next steps: `docs/superpowers/HANDOFF.md` (read first, keep it updated).
- Plan and binding rules ("Global Constraints", glossary, URL table): `docs/superpowers/plans/2026-10-12-don-hisobot.md`.
- Spec (Uzbek): `docs/superpowers/specs/2026-10-12-don-hisobot-design.md`.

## Commands

```
docker compose -f compose.dev.yaml up -d        # dev PostgreSQL on 127.0.0.1:5440
uv run python manage.py migrate
uv run python manage.py seed                    # 6 MTUs + 7 accounts, passwords printed once
uv run python manage.py test hisobot --noinput
uv run python manage.py runserver               # /_uslub/ shows every UI component
```

## Rules that are easy to break

- Only `balance.save_entry()` / `set_opening()` write `Entry`. Every staff view starts from `access.stations_for(user)`.
- All time logic goes through `hisobot/periods.py`; never `date.today()` or naive datetimes.
- User-visible text is Uzbek Cyrillic, using the plan's glossary verbatim.
- No new dependencies, no CDN, no Django admin.
