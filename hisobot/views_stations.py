"""Stub (Task 5). Task 8 replaces this module."""

from django.http import HttpResponse


def _stub(request, *args, **kwargs):
    return HttpResponse("Ҳали тайёр эмас", status=501)


index = edit = toggle = regenerate_pin = unlock = import_roster = pin_sheet = _stub
password_change = mtu_passwords = _stub
