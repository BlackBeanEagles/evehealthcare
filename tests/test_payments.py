"""The simulated charge endpoint: POST /payments/."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.bookings.models import BookingStatus
from apps.payments.models import Payment, PaymentStatus
from tests.factories import BookingFactory, PaymentFactory

pytestmark = pytest.mark.django_db

PAYMENTS = reverse("v1:payments:payment-list")


def pay(client, booking, **extra):
    return client.post(PAYMENTS, {"booking": booking.reference, **extra})


class TestChargingABooking:
    def test_requires_authentication(self, api_client, booking):
        assert pay(api_client, booking).status_code == 401

    def test_a_successful_payment_confirms_the_booking(self, auth_client, booking):
        response = pay(auth_client, booking, outcome="success")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == PaymentStatus.SUCCESS
        assert body["reference"].startswith("PAY-")
        assert body["provider_reference"].startswith("sim_pay_")

        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.confirmed_at is not None

    def test_a_failed_payment_fails_the_booking(self, auth_client, booking):
        response = pay(auth_client, booking, outcome="failure")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == PaymentStatus.FAILED
        assert body["failure_code"]
        assert body["failure_reason"]

        booking.refresh_from_db()
        assert booking.status == BookingStatus.FAILED

    def test_the_charge_uses_the_booking_amount(self, auth_client, booking):
        response = pay(auth_client, booking, outcome="success")

        assert Decimal(response.json()["amount"]) == booking.amount

    def test_records_the_transition_source_as_payment(self, auth_client, booking):
        pay(auth_client, booking, outcome="success")

        change = booking.status_changes.filter(to_status=BookingStatus.CONFIRMED).first()
        assert change.source == "payment"

    def test_a_failed_payment_can_be_retried(self, auth_client, booking):
        assert pay(auth_client, booking, outcome="failure").status_code == 201
        booking.refresh_from_db()
        assert booking.status == BookingStatus.FAILED

        response = pay(auth_client, booking, outcome="success")

        assert response.status_code == 201
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.payments.count() == 2

    @pytest.mark.parametrize("method", ["UPI", "CARD", "NETBANKING", "WALLET"])
    def test_accepts_every_supported_method(self, auth_client, booking, method):
        response = pay(auth_client, booking, method=method, outcome="success")

        assert response.status_code == 201
        assert response.json()["method"] == method

    def test_rejects_an_unknown_method(self, auth_client, booking):
        response = pay(auth_client, booking, method="BARTER", outcome="success")

        assert response.status_code == 400

    def test_rejects_an_unknown_outcome(self, auth_client, booking):
        response = pay(auth_client, booking, outcome="maybe")

        assert response.status_code == 400


class TestLiteralAssignmentPath:
    """The brief names ``POST /payments/``; it is aliased at the root onto the
    same view as ``/api/v1/payments/``."""

    def test_post_payments_at_the_root_charges_the_booking(self, auth_client, booking):
        response = auth_client.post(
            "/payments/", {"booking": booking.reference, "outcome": "success"}
        )

        assert response.status_code == 201
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_the_root_alias_enforces_authentication(self, api_client, booking):
        response = api_client.post("/payments/", {"booking": booking.reference})

        assert response.status_code == 401


class TestRejectedCharges:
    def test_an_unknown_booking_reference_is_a_404(self, auth_client):
        response = auth_client.post(PAYMENTS, {"booking": "BKG-NOPE"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_another_users_booking_is_a_404(self, other_client, booking):
        response = pay(other_client, booking, outcome="success")

        assert response.status_code == 404
        assert Payment.objects.count() == 0

    def test_a_cancelled_booking_cannot_be_paid(self, auth_client, booking):
        booking.status = BookingStatus.CANCELLED
        booking.save(update_fields=["status"])

        response = pay(auth_client, booking, outcome="success")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "booking_cancelled"
        assert Payment.objects.count() == 0

    def test_a_confirmed_booking_cannot_be_paid_again(self, auth_client, booking):
        pay(auth_client, booking, outcome="success")

        response = pay(auth_client, booking, outcome="success")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "booking_already_paid"
        assert booking.payments.count() == 1

    def test_a_payment_already_in_flight_blocks_a_second_one(self, auth_client, booking):
        PaymentFactory(booking=booking, status=PaymentStatus.PENDING)

        response = pay(auth_client, booking, outcome="success")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "payment_in_progress"
        assert booking.payments.count() == 1


class TestIdempotencyKey:
    def test_replaying_a_key_returns_the_original_payment(self, auth_client, booking):
        first = auth_client.post(
            PAYMENTS,
            {"booking": booking.reference, "outcome": "success"},
            HTTP_IDEMPOTENCY_KEY="key-123",
        )
        second = auth_client.post(
            PAYMENTS,
            {"booking": booking.reference, "outcome": "success"},
            HTTP_IDEMPOTENCY_KEY="key-123",
        )

        assert first.status_code == 201
        assert second.status_code == 200  # replayed, not created
        assert first.json()["reference"] == second.json()["reference"]
        assert Payment.objects.count() == 1

    def test_a_replay_does_not_re_transition_the_booking(self, auth_client, booking):
        for _ in range(3):
            auth_client.post(
                PAYMENTS,
                {"booking": booking.reference, "outcome": "success"},
                HTTP_IDEMPOTENCY_KEY="key-abc",
            )

        confirmations = booking.status_changes.filter(
            to_status=BookingStatus.CONFIRMED
        ).count()
        assert confirmations == 1

    def test_reusing_a_key_for_a_different_booking_is_rejected(
        self, auth_client, user, booking, offering
    ):
        other = BookingFactory(user=user, centre_test=offering)
        auth_client.post(
            PAYMENTS,
            {"booking": booking.reference, "outcome": "success"},
            HTTP_IDEMPOTENCY_KEY="shared-key",
        )

        response = auth_client.post(
            PAYMENTS,
            {"booking": other.reference, "outcome": "success"},
            HTTP_IDEMPOTENCY_KEY="shared-key",
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_key_reused"

    def test_distinct_keys_on_one_booking_still_respect_the_state_machine(
        self, auth_client, booking
    ):
        auth_client.post(
            PAYMENTS,
            {"booking": booking.reference, "outcome": "success"},
            HTTP_IDEMPOTENCY_KEY="key-1",
        )

        response = auth_client.post(
            PAYMENTS,
            {"booking": booking.reference, "outcome": "success"},
            HTTP_IDEMPOTENCY_KEY="key-2",
        )

        assert response.status_code == 409
        assert Payment.objects.count() == 1


class TestListingPayments:
    def test_lists_only_your_own_payments(self, auth_client, other_client, booking,
                                          other_user, offering):
        pay(auth_client, booking, outcome="success")
        others = BookingFactory(user=other_user, centre_test=offering)
        pay(other_client, others, outcome="success")

        body = auth_client.get(PAYMENTS).json()

        assert body["count"] == 1
        assert body["results"][0]["booking"] == booking.reference

    def test_another_users_payment_is_a_404(self, auth_client, other_client, booking):
        reference = pay(auth_client, booking, outcome="success").json()["reference"]

        response = other_client.get(
            reverse("v1:payments:payment-detail", args=[reference])
        )

        assert response.status_code == 404

    def test_filters_by_status(self, auth_client, user, offering):
        good = BookingFactory(user=user, centre_test=offering)
        bad = BookingFactory(user=user, centre_test=offering)
        pay(auth_client, good, outcome="success")
        pay(auth_client, bad, outcome="failure")

        body = auth_client.get(PAYMENTS, {"status": "failed"}).json()

        assert body["count"] == 1
        assert body["results"][0]["status"] == PaymentStatus.FAILED

    def test_rejects_an_unknown_status_filter(self, auth_client):
        assert auth_client.get(PAYMENTS, {"status": "PAID"}).status_code == 400


class TestReconciliation:
    def test_a_stuck_pending_payment_is_failed_and_releases_the_booking(
        self, booking, settings
    ):
        from datetime import timedelta

        from django.utils import timezone

        from apps.payments.services import reconcile_stale_payments

        payment = PaymentFactory(booking=booking, status=PaymentStatus.PENDING)
        Payment.objects.filter(pk=payment.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )

        assert reconcile_stale_payments(older_than_minutes=30) == 1

        payment.refresh_from_db()
        booking.refresh_from_db()
        assert payment.status == PaymentStatus.FAILED
        assert payment.failure_code == "reconciliation_timeout"
        assert booking.status == BookingStatus.FAILED

    def test_a_recent_pending_payment_is_left_alone(self, booking):
        from apps.payments.services import reconcile_stale_payments

        PaymentFactory(booking=booking, status=PaymentStatus.PENDING)

        assert reconcile_stale_payments(older_than_minutes=30) == 0
