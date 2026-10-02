"""The single-page frontend served at /."""
from __future__ import annotations

import re

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_the_app_shell_is_served_at_the_root(api_client):
    response = api_client.get(reverse("app"))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="view"' in html
    assert "app/app.js" in html
    assert "app/styles.css" in html


def test_in_debug_asset_urls_carry_a_version_so_edits_are_never_served_stale(
    api_client, settings
):
    """Found while testing: after an edit, browsers kept running the old
    app.js because the dev URL never changed. Each URL now carries the file's
    modification time."""
    settings.DEBUG = True

    html = api_client.get(reverse("app")).content.decode()

    assert re.search(r'app/app\.js\?v=\d+"', html)
    assert re.search(r'app/styles\.css\?v=\d+"', html)


def test_without_debug_the_storage_provides_the_versioning(api_client, settings):
    settings.DEBUG = False

    html = api_client.get(reverse("app")).content.decode()

    assert "?v=" not in html  # production uses content-hashed filenames instead


def test_the_app_shell_needs_no_authentication(api_client):
    """The shell is public; the data it loads is not."""
    assert api_client.get("/").status_code == 200
    assert api_client.get("/api/v1/bookings/").status_code == 401


@pytest.mark.parametrize("asset", ["app/app.js", "app/styles.css"])
def test_the_frontend_assets_are_discoverable_by_collectstatic(asset):
    assert finders.find(asset) is not None


def test_the_root_route_does_not_shadow_the_api(api_client):
    response = api_client.get("/api/v1/centres/")

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/json")


def test_the_frontend_ships_an_escaping_helper_and_no_eval():
    """A cheap guard rail, not proof of XSS-safety: API strings rendered into
    innerHTML are meant to pass through esc(), and the classic footguns must
    never appear. Reviewing app.js is what actually establishes the former."""
    source = open(finders.find("app/app.js"), encoding="utf-8").read()

    assert "const esc =" in source
    assert "innerHTML = " in source
    # The two classic XSS footguns are absent.
    assert "eval(" not in source
    assert "document.write" not in source
