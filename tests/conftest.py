"""Shared fixtures.

Requests are authenticated with real JWTs rather than
``force_authenticate``, so every test also exercises the token path.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.payments.signature import SIGNATURE_HEADER, build_header
from tests.factories import (
    BookingFactory,
    CentreTestFactory,
    DiagnosticCentreFactory,
    DiagnosticTestFactory,
    StaffUserFactory,
    UserFactory,
)

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture(autouse=True)
def _clear_cache():
    """LocMemCache is process-wide; don't let catalogue caching leak between tests."""
    cache.clear()
    yield
    cache.clear()


def access_token_for(user) -> str:
    return str(RefreshToken.for_user(user).access_token)


def authenticated_client(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token_for(user)}")
    return client


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def other_user(db):
    return UserFactory()


@pytest.fixture
def staff_user(db):
    return StaffUserFactory()


@pytest.fixture
def client_for(db):
    """Factory fixture: ``client_for(user)`` -> an authenticated APIClient."""
    return authenticated_client


@pytest.fixture
def auth_client(user):
    return authenticated_client(user)


@pytest.fixture
def other_client(other_user):
    return authenticated_client(other_user)


@pytest.fixture
def staff_client(staff_user):
    return authenticated_client(staff_user)


@pytest.fixture
def centre(db):
    return DiagnosticCentreFactory()


@pytest.fixture
def diagnostic_test(db):
    return DiagnosticTestFactory()


@pytest.fixture
def offering(centre, diagnostic_test):
    return CentreTestFactory(centre=centre, test=diagnostic_test)


@pytest.fixture
def future_slot():
    """Tomorrow at 10:00 local time - inside every centre's opening hours."""
    local_now = timezone.localtime(timezone.now())
    return (local_now + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )


@pytest.fixture
def booking(user, offering, future_slot):
    return BookingFactory(user=user, centre_test=offering, scheduled_for=future_slot)


@pytest.fixture
def webhook_url() -> str:
    return reverse("v1:payments:payment-webhook")


@pytest.fixture
def post_webhook(api_client, webhook_url):
    """Post a correctly signed webhook body.

    ``sign=False`` sends it unsigned, ``secret=`` signs it with the wrong key,
    and ``timestamp=`` forces a stale signature.
    """

    def _post(payload: dict, *, sign: bool = True, secret: str | None = None,
              timestamp: int | None = None, url: str | None = None):
        body = json.dumps(payload).encode()
        headers = {}
        if sign:
            headers[f"HTTP_{SIGNATURE_HEADER.upper().replace('-', '_')}"] = build_header(
                secret or WEBHOOK_SECRET, body, timestamp
            )
        return api_client.post(
            url or webhook_url, data=body, content_type="application/json", **headers
        )

    return _post
