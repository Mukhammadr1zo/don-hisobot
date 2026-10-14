"""Display filters. Every datetime is printed in Tashkent time, whatever zone it arrives in.

{{ entry.updated_at|t }}   08:07               {{ pe|period_range }}  11.10 20:00 – 12.10 08:00
{{ report_date|d }}        12.10.2026          {{ pe|period_name }}   Тунги давр / Кундузги давр
{{ station.last_login_at|dt }}  12.10.2026 08:07    {{ pe|slug }}     2026101208 (for {% url %})
{{ entry.remaining|num }}  the number, or — when there is none
{{ total|thousands }}      4 716 (no-break space), or —; anything not a number unchanged
{{ n|percent:m }}          82: whole percent of n / m, rounded down (100 only when all are in), 0 when m is 0
"""

from datetime import datetime

from django import template

from hisobot import periods

register = template.Library()

DASH = "—"
# Django hands a filter "" when the variable does not resolve, e.g. row.entry.unloaded with entry None.
MISSING = (None, "")


def _format(value, pattern):
    if value in MISSING:
        return DASH
    if isinstance(value, datetime):
        value = periods.local(value)
    return value.strftime(pattern)


@register.filter
def d(value):
    return _format(value, "%d.%m.%Y")


@register.filter
def t(value):
    return _format(value, "%H:%M")


@register.filter
def dt(value):
    return _format(value, "%d.%m.%Y %H:%M")


@register.filter
def period_range(pe):
    periods.label(pe)  # rejects anything that is not a period end
    start, end = periods.bounds(periods.local(pe))
    return f"{start:%d.%m %H:%M} – {end:%d.%m %H:%M}"


@register.filter
def period_name(pe):
    return "Тунги давр" if periods.label(pe)[1] == 1 else "Кундузги давр"


@register.filter
def slug(pe):
    return periods.to_slug(pe)


@register.filter
def num(value):
    return DASH if value in MISSING else value


@register.filter
def thousands(value):
    # A plain no-break space: Onest has no glyph for the narrow one (U+202F), which fell back to a 2px system space.
    if value in MISSING:
        return DASH
    try:
        return f"{value:,}".replace(",", "\N{NO-BREAK SPACE}")
    except (TypeError, ValueError):  # not a number: printed as it is, as Django's own filters do
        return value


@register.filter
def percent(n, m):
    # Rounded down: 107 of 108 is 99, never a premature 100. Drives the ring's --p as well as the printed figure.
    return 100 * n // m if n not in MISSING and m else 0
