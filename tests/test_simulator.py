"""The demo-mode payment provider simulator, asynchronous ("pending")
payments, the per-payment webhook log and the public config endpoint."""
from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.throttling import ScopedRateThrottle

from apps.bookings.models import BookingStatus
from apps.payments.models import Payment, PaymentStatus, WebhookEvent
from tests.factories import BookingFactory

pytestmark = pytest.mark.django_db

PAYMENTS = reverse("v1:payments:payment-list")


def simulate_url(payment_ref: str) -> str:
    return reverse("v1:payments:payment-simulate-webhook", args=[payment_ref])


def events_url(payment_ref: str) -> str:
    return reverse("v1:payments:payment-webhook-events", args=[payment_ref])


@pytest.fixture
def pending(auth_client, booking):
    """A charge the provider accepted but hasn't confirmed (UPI-collect style)."""
    response = auth_client.post(PAYMENTS, {"booking": booking.reference, "outcome": "pending"})
    assert response.status_code == 201
    return response.json()


class TestPendingPayments:
    def test_a_pending_charge_waits_for_the_webhook(self, pending, booking):
        assert pending["status"] == PaymentStatus.PENDING
        assert pending["provider_reference"].startswith("sim_pay_")
        booking.refresh_from_db()
        assert booking.status == BookingStatus.PENDING

    def test_a_second_charge_is_refused_while_one_is_pending(self, auth_client, pending, booking):
        response = auth_client.post(PAYMENTS, {"booking": booking.reference, "outcome": "success"})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "payment_in_progress"


class TestSimulator:
    def test_three_deliveries_are_processed_once(self, auth_client, pending, booking):
        response = auth_client.post(
            simulate_url(pending["reference"]), {"outcome": "success", "deliveries": 3}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["event_type"] == "payment.succeeded"
        assert [d["duplicate"] for d in body["deliveries"]] == [False, True, True]
        assert body["deliveries"][0]["result"] == "booking_confirmed"
        assert (body["booking_status_before"], body["booking_status_after"]) == (
            BookingStatus.PENDING,
            BookingStatus.CONFIRMED,
        )
        assert body["payment"]["status"] == PaymentStatus.SUCCESS
        assert booking.status_changes.filter(to_status=BookingStatus.CONFIRMED).count() == 1

        event = WebhookEvent.objects.get()
        assert event.delivery_count == 3  # every redelivery recorded...
        assert event.attempts == 1  # ...but processed exactly once

    def test_a_late_contradictory_event_is_refused(self, auth_client, pending, booking):
        auth_client.post(simulate_url(pending["reference"]), {"outcome": "success", "deliveries": 1})

        body = auth_client.post(
            simulate_url(pending["reference"]), {"outcome": "failure", "deliveries": 1}
        ).json()

        assert body["deliveries"][0]["result"] == "conflicting_outcome_ignored"
        assert body["booking_status_after"] == BookingStatus.CONFIRMED

    def test_a_failure_event_fails_a_pending_payment(self, auth_client, pending, booking):
        body = auth_client.post(
            simulate_url(pending["reference"]), {"outcome": "failure", "deliveries": 1}
        ).json()

        assert body["payment"]["status"] == PaymentStatus.FAILED
        assert body["booking_status_after"] == BookingStatus.FAILED

    def test_deliveries_go_through_the_real_signature_check(
        self, auth_client, pending, monkeypatch
    ):
        """Proof the simulator uses the real webhook view rather than calling
        the services directly: break the signature and the view rejects it."""
        from apps.payments import views

        monkeypatch.setattr(views, "build_header", lambda secret, body: "t=1,v1=forged")

        body = auth_client.post(
            simulate_url(pending["reference"]), {"outcome": "success", "deliveries": 1}
        ).json()

        assert body["deliveries"][0]["http_status"] == 401
        assert body["booking_status_after"] == BookingStatus.PENDING

    def test_is_a_404_when_disabled(self, auth_client, pending, settings):
        settings.PAYMENT_SIMULATOR_ENABLED = False

        response = auth_client.post(simulate_url(pending["reference"]), {"outcome": "success"})

        assert response.status_code == 404
        assert Payment.objects.get().status == PaymentStatus.PENDING

    def test_cannot_target_another_users_payment(self, other_client, pending):
        response = other_client.post(simulate_url(pending["reference"]), {"outcome": "success"})

        assert response.status_code == 404
        assert WebhookEvent.objects.count() == 0

    def test_requires_authentication(self, api_client, pending):
        response = api_client.post(simulate_url(pending["reference"]), {"outcome": "success"})

        assert response.status_code == 401

    @pytest.mark.parametrize(
        "payload",
        [{"outcome": "maybe"}, {"outcome": "success", "deliveries": 0},
         {"outcome": "success", "deliveries": 6}, {}],
    )
    def test_rejects_invalid_requests(self, auth_client, pending, payload):
        assert auth_client.post(simulate_url(pending["reference"]), payload).status_code == 400


class TestWebhookEventLog:
    def test_lists_the_deliveries_for_a_payment(self, auth_client, pending):
        auth_client.post(simulate_url(pending["reference"]), {"outcome": "success", "deliveries": 3})
        auth_client.post(simulate_url(pending["reference"]), {"outcome": "failure", "deliveries": 1})

        events = auth_client.get(events_url(pending["reference"])).json()

        assert [(e["event_type"], e["delivery_count"]) for e in events] == [
            ("payment.succeeded", 3),
            ("payment.failed", 1),
        ]
        assert [e["result"] for e in events] == ["booking_confirmed", "conflicting_outcome_ignored"]

    def test_is_scoped_to_the_owner(self, other_client, pending):
        assert other_client.get(events_url(pending["reference"])).status_code == 404


class TestThrottleScopes:
    def test_only_writes_count_against_the_payments_rate(
        self, auth_client, user, offering, monkeypatch
    ):
        """Reads (e.g. a page polling payment status) must not use up the
        tight rate reserved for moving money."""
        monkeypatch.setattr(
            ScopedRateThrottle, "THROTTLE_RATES", {**ScopedRateThrottle.THROTTLE_RATES, "payments": "2/min"}
        )

        reads = [auth_client.get(PAYMENTS).status_code for _ in range(4)]
        writes = [
            auth_client.post(
                PAYMENTS, {"booking": BookingFactory(user=user, centre_test=offering).reference,
                           "outcome": "success"}
            ).status_code
            for _ in range(3)
        ]

        assert reads == [200, 200, 200, 200]
        assert writes[-1] == 429


def test_the_public_config_endpoint(api_client, settings):
    settings.PAYMENT_SIMULATOR_ENABLED = True

    body = api_client.get(reverse("v1:config")).json()

    assert body["payment_simulator"] is True
    assert body["booking"]["min_lead_minutes"] == settings.BOOKING_MIN_LEAD_TIME_MINUTES
    assert body["currency"] == "INR"
