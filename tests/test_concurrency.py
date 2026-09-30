"""The idempotency guarantees, under genuinely parallel requests.

Every other test proves the constraints exist and the locking code runs; these
prove that N simultaneous callers still produce exactly one outcome. Each
thread holds its own database connection, and a barrier releases them at the
same instant so they really do collide.

PostgreSQL only: row locks and unique-index waits are what is under test.
SQLite serialises every write, so these would pass without proving anything.
CI runs the suite on Postgres; locally:

    DATABASE_URL=postgresql://eve:eve@localhost:5432/eve_diagnostics pytest tests/test_concurrency.py
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import Counter

import pytest
from django.db import connection, connections
from django.urls import reverse
from rest_framework.test import APIClient

from apps.bookings.models import Booking, BookingStatus
from apps.payments.models import Payment, PaymentStatus, WebhookEvent
from apps.payments.signature import build_header
from tests.conftest import WEBHOOK_SECRET, authenticated_client
from tests.factories import PaymentFactory
from tests.test_webhooks import make_event

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="needs PostgreSQL row locking; SQLite would pass vacuously",
    ),
]

PARALLEL = 8


def run_concurrently(n, fn):
    """Run fn(i) in n threads released together; return results in order."""
    barrier = threading.Barrier(n)
    results = [None] * n
    errors = []

    def worker(i):
        try:
            barrier.wait(timeout=10)
            results[i] = fn(i)
        except Exception as exc:  # surfaced below, with the thread's error
            errors.append(exc)
        finally:
            connections.close_all()  # each thread owns its connection

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    return results


@pytest.fixture
def pending_payment(booking):
    return PaymentFactory(
        booking=booking,
        status=PaymentStatus.PENDING,
        provider_reference=f"sim_pay_{uuid.uuid4().hex[:20]}",
    )


def post_signed(payload):
    body = json.dumps(payload).encode()
    return APIClient().post(
        reverse("v1:payments:payment-webhook"),
        data=body,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=build_header(WEBHOOK_SECRET, body),
    )


def confirmations(booking):
    return booking.status_changes.filter(to_status=BookingStatus.CONFIRMED).count()


class TestWebhookConcurrency:
    def test_one_event_delivered_in_parallel_is_applied_exactly_once(
        self, pending_payment, booking
    ):
        payload = make_event(pending_payment, event_id="evt_parallel")

        responses = run_concurrently(PARALLEL, lambda _: post_signed(payload))

        assert [r.status_code for r in responses] == [200] * PARALLEL
        assert sum(1 for r in responses if r.json()["duplicate"] is False) == 1
        assert WebhookEvent.objects.count() == 1
        # The F()-expression increment means no parallel redelivery is lost.
        assert WebhookEvent.objects.get().delivery_count == PARALLEL
        assert confirmations(booking) == 1
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_distinct_events_with_the_same_outcome_confirm_once(
        self, pending_payment, booking
    ):
        responses = run_concurrently(
            PARALLEL, lambda i: post_signed(make_event(pending_payment, event_id=f"evt_{i}"))
        )

        assert [r.status_code for r in responses] == [200] * PARALLEL
        assert WebhookEvent.objects.count() == PARALLEL  # every delivery is recorded...
        assert confirmations(booking) == 1  # ...but the booking moves once
        results = Counter(r.json()["result"] for r in responses)
        assert results == {"booking_confirmed": 1, "already_success": PARALLEL - 1}

    def test_contradictory_events_in_parallel_settle_on_one_consistent_outcome(
        self, pending_payment, booking
    ):
        kinds = ["payment.succeeded", "payment.failed"] * (PARALLEL // 2)

        run_concurrently(
            PARALLEL,
            lambda i: post_signed(
                make_event(pending_payment, event_type=kinds[i], event_id=f"evt_mix_{i}")
            ),
        )

        pending_payment.refresh_from_db()
        booking.refresh_from_db()
        # Exactly one terminal outcome won, and the booking agrees with it.
        expected = {
            PaymentStatus.SUCCESS: BookingStatus.CONFIRMED,
            PaymentStatus.FAILED: BookingStatus.FAILED,
        }[pending_payment.status]
        assert booking.status == expected
        terminal_moves = booking.status_changes.filter(
            to_status__in=[BookingStatus.CONFIRMED, BookingStatus.FAILED]
        ).count()
        assert terminal_moves == 1


class TestPaymentConcurrency:
    def test_parallel_charges_for_one_booking_take_money_once(self, user, booking):
        def charge(_):
            return authenticated_client(user).post(
                reverse("v1:payments:payment-list"),
                {"booking": booking.reference, "outcome": "success"},
            )

        responses = run_concurrently(PARALLEL, charge)

        statuses = Counter(r.status_code for r in responses)
        assert statuses == {201: 1, 409: PARALLEL - 1}
        assert Payment.objects.filter(booking=booking).count() == 1
        assert confirmations(booking) == 1

    def test_parallel_retries_with_one_idempotency_key_return_one_payment(
        self, user, booking
    ):
        """A client that double-submits (or retries on a timeout) with the same
        key must get the original payment back, never a 409 and never a
        second charge."""

        def charge(_):
            return authenticated_client(user).post(
                reverse("v1:payments:payment-list"),
                {"booking": booking.reference, "outcome": "success"},
                HTTP_IDEMPOTENCY_KEY="same-key",
            )

        responses = run_concurrently(PARALLEL, charge)

        assert Payment.objects.filter(booking=booking).count() == 1
        assert Counter(r.status_code for r in responses) == {201: 1, 200: PARALLEL - 1}
        assert len({r.json()["reference"] for r in responses}) == 1


class TestBookingConcurrency:
    def test_parallel_requests_for_one_slot_create_one_booking(
        self, user, offering, future_slot
    ):
        def book(_):
            return authenticated_client(user).post(
                reverse("v1:bookings:booking-list"),
                {"offering": offering.id, "scheduled_for": future_slot.isoformat()},
            )

        responses = run_concurrently(PARALLEL, book)

        assert Counter(r.status_code for r in responses) == {201: 1, 409: PARALLEL - 1}
        assert Booking.objects.filter(user=user, centre_test=offering).count() == 1
