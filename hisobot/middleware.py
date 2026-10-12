from django.http import HttpResponseNotAllowed
from django.shortcuts import render


def method_not_allowed(get_response):
    """require_POST answers a GET with an empty 405 (Enter in the PIN sheet's address bar, a typed /chiqish/).

    Keep the status and the Allow header, and show an Uzbek page instead of a blank one.
    """

    def middleware(request):
        response = get_response(request)
        if isinstance(response, HttpResponseNotAllowed):
            page = render(request, "405.html", status=405)
            page["Allow"] = response["Allow"]
            return page
        return response

    return middleware
