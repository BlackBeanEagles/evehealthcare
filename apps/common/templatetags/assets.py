"""Cache-safe URLs for the frontend's own static files."""
from __future__ import annotations

import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path: str) -> str:
    """Like ``{% static %}``, but a changed file always gets a new URL.

    In production the manifest storage already serves content-hashed names
    (``app.01b3e510607d.js``). In DEBUG the plain name is served with no
    cache headers, so browsers kept running stale JavaScript after an edit.
    There the file's modification time is appended as a version instead.
    """
    url = static(path)
    if settings.DEBUG:
        found = finders.find(path)
        if found:
            url = f"{url}?v={int(os.path.getmtime(found))}"
    return url
