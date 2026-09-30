"""Health, documentation, logging and throttling."""
from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestHealth:
    def test_is_public_and_reports_its_dependencies(self, api_client):
        response = api_client.get(reverse("health"))

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["checks"] == {"database": "ok", "cache": "ok"}


class TestDocumentation:
    def test_the_openapi_schema_builds(self, api_client):
        response = api_client.get(reverse("schema"))

        assert response.status_code == 200

    def test_swagger_ui_is_served(self, api_client):
        assert api_client.get(reverse("swagger-ui")).status_code == 200


class TestRequestTracing:
    def test_every_response_carries_a_request_id(self, api_client):
        response = api_client.get(reverse("health"))

        assert response["X-Request-ID"]

    def test_an_inbound_request_id_is_echoed_back(self, api_client):
        response = api_client.get(reverse("health"), HTTP_X_REQUEST_ID="trace-me-123")

        assert response["X-Request-ID"] == "trace-me-123"

    def test_the_request_id_appears_in_error_bodies(self, api_client):
        response = api_client.post(
            reverse("v1:accounts:signup"), {}, HTTP_X_REQUEST_ID="trace-me-456"
        )

        assert response.json()["request_id"] == "trace-me-456"


class TestThrottling:
    def test_login_attempts_are_rate_limited(self, api_client, user, monkeypatch):
        from rest_framework.throttling import ScopedRateThrottle

        # monkeypatch restores the class attribute afterwards; assigning it
        # directly would leak a 3/min login limit into every later test.
        monkeypatch.setattr(
            ScopedRateThrottle,
            "THROTTLE_RATES",
            {**ScopedRateThrottle.THROTTLE_RATES, "auth": "3/min"},
        )

        url = reverse("v1:accounts:login")
        payload = {"email": user.email, "password": "wrong-password"}
        statuses = [api_client.post(url, payload).status_code for _ in range(5)]

        assert 429 in statuses


class TestJsonLogging:
    def test_a_record_renders_as_one_json_object(self):
        import json
        import logging

        from apps.common.logging import JSONFormatter, set_request_id

        set_request_id("req-abc")
        record = logging.LogRecord(
            name="apps.payments", level=logging.INFO, pathname=__file__, lineno=1,
            msg="payment_settled", args=(), exc_info=None,
        )
        record.payment_reference = "PAY-1"

        payload = json.loads(JSONFormatter().format(record))

        assert payload["event"] == "payment_settled"
        assert payload["level"] == "INFO"
        assert payload["request_id"] == "req-abc"
        assert payload["payment_reference"] == "PAY-1"
