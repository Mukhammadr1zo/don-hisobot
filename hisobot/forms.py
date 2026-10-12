from django import forms

REQUIRED = "Майдонни тўлдиринг"
STALE = "Саҳифани янгилаб, қайта уриниб кўринг"


def _count(label, top, required=True):
    out_of_range = f"0 дан {top} гача сон киритинг"
    return forms.IntegerField(
        label=label,
        min_value=0,
        max_value=top,
        required=required,
        error_messages={
            "required": REQUIRED,
            "invalid": out_of_range,
            "min_value": out_of_range,
            "max_value": out_of_range,
        },
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "autocomplete": "off", "class": "num-input"}),
    )


def _hidden(**kwargs):
    # These come from the page, not from the person: a bad value means a stale or broken page.
    messages = dict.fromkeys(("required", "invalid", "min_value"), STALE)
    return forms.IntegerField(widget=forms.HiddenInput, error_messages=messages, **kwargs)


class EntryForm(forms.Form):
    """One period's numbers. `version` and `prev` echo what the page showed (spec 5.6.4):
    version is 0 for a new entry; prev is empty while the opening balance is still unknown."""

    unloaded = _count("Туширилган", 999)
    arrived = _count("Кириб келган", 999)
    override = _count("Қолдиқни қўлда киритиш", 9999, required=False)
    opening = _count("Давр бошидаги қолдиқ", 9999, required=False)
    version = _hidden(min_value=0)
    prev = _hidden(required=False)

    def __init__(self, *args, require_opening=False, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        self.fields["opening"].required = require_opening
