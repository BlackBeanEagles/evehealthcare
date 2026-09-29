"""The booking state machine and the database constraints behind it."""
from __future__ import annotations

import itertools

import pytest
from django.db import IntegrityError, transaction

from apps.bookings.models import Booking, BookingStatus, BookingStatusChange
from apps.bookings.services import ALLOWED_TRANSITIONS, can_transition, transition_booking
from apps.common.exceptions import InvalidStateTransition
from apps.payments.models import Payment, PaymentStatus
from tests.factories import BookingFactory, PaymentFactory

pytestmark = pytest.mark.django_db

ALL_STATUSES = list(BookingStatus.values)

LEGAL = {
    (BookingStatus.PENDING, BookingStatus.CONFIRMED),
    (BookingStatus.PENDING, BookingStatus.FAILED),
    (BookingStatus.PENDING, BookingStatus.CANCELLED),
    (BookingStatus.FAILED, BookingStatus.PENDING),
    (BookingStatus.FAILED, BookingStatus.CANCELLED),
    (BookingStatus.CONFIRMED, BookingStatus.CANCELLED),
}


@pytest.mark.parametrize(
    "from_status,to_status", list(itertools.product(ALL_STATUSES, ALL_STATUSES))
)
def test_the_transition_table_is_exactly_as_documented(from_status, to_status):
    expected = (from_status, to_status) in LEGAL

    assert can_transition(from_status, to_status) is expected


def test_cancelled_is_terminal():
    assert ALLOWED_TRANSITIONS[BookingStatus.CANCELLED] == frozenset()


class TestTransitioning:
    def test_applies_a_legal_transition_and_stamps_the_time(self, booking):
        transition_booking(booking, BookingStatus.CONFIRMED, source="system")

        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.confirmed_at is not None

    def test_writes_an_audit_row(self, booking):
        transition_booking(
            booking, BookingStatus.FAILED, source="payment", reason="Card declined"
        )

        change = BookingStatusChange.objects.get(booking=booking)
        assert change.from_status == BookingStatus.PENDING
        assert change.to_status == BookingStatus.FAILED
        assert change.reason == "Card declined"

    def test_refuses_an_illegal_transition(self, booking):
        transition_booking(booking, BookingStatus.CANCELLED, source="api")

        with pytest.raises(InvalidStateTransition) as excinfo:
            transition_booking(booking, BookingStatus.CONFIRMED, source="api")

        assert excinfo.value.status_code == 409
        assert excinfo.value.details["from"] == BookingStatus.CANCELLED

    def test_a_transition_to_the_current_status_is_a_silent_no_op(self, booking):
        """This is what makes a replayed webhook safe."""
        transition_booking(booking, BookingStatus.CONFIRMED, source="webhook")

        transition_booking(booking, BookingStatus.CONFIRMED, source="webhook")

        assert BookingStatusChange.objects.filter(booking=booking).count() == 1

    def test_a_long_reason_is_truncated_not_rejected(self, booking):
        transition_booking(
            booking, BookingStatus.CANCELLED, source="api", reason="x" * 500
        )

        assert len(BookingStatusChange.objects.get(booking=booking).reason) == 255


class TestDatabaseConstraints:
    def test_two_active_bookings_cannot_share_a_slot(self, user, offering, future_slot):
        BookingFactory(user=user, centre_test=offering, scheduled_for=future_slot)

        with pytest.raises(IntegrityError), transaction.atomic():
            Booking.objects.create(
                user=user,
                centre_test=offering,
                scheduled_for=future_slot,
                amount=offering.price,
                status=BookingStatus.PENDING,
            )

    def test_a_cancelled_booking_frees_the_slot(self, user, offering, future_slot):
        BookingFactory(
            user=user, centre_test=offering, scheduled_for=future_slot,
            status=BookingStatus.CANCELLED,
        )

        replacement = Booking.objects.create(
            user=user,
            centre_test=offering,
            scheduled_for=future_slot,
            amount=offering.price,
            status=BookingStatus.PENDING,
        )

        assert replacement.pk

    def test_only_one_open_payment_per_booking(self, booking):
        PaymentFactory(booking=booking, status=PaymentStatus.PENDING)

        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                booking=booking, amount=booking.amount, status=PaymentStatus.PENDING
            )

    def test_a_failed_payment_leaves_room_for_a_retry(self, booking):
        PaymentFactory(booking=booking, status=PaymentStatus.FAILED)

        retry = Payment.objects.create(
            booking=booking, amount=booking.amount, status=PaymentStatus.PENDING
        )

        assert retry.pk

    def test_an_idempotency_key_cannot_be_reused(self, booking, user, offering):
        PaymentFactory(booking=booking, idempotency_key="key-1")
        other_booking = BookingFactory(user=user, centre_test=offering)

        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.create(
                booking=other_booking,
                amount=other_booking.amount,
                idempotency_key="key-1",
            )

    def test_many_payments_may_have_no_idempotency_key(self, booking, user, offering):
        PaymentFactory(booking=booking, status=PaymentStatus.FAILED, idempotency_key=None)
        PaymentFactory(booking=booking, status=PaymentStatus.FAILED, idempotency_key=None)

        assert Payment.objects.filter(idempotency_key__isnull=True).count() == 2

    def test_a_webhook_event_id_is_unique(self, booking):
        from apps.payments.models import WebhookEvent

        WebhookEvent.objects.create(event_id="evt_1", event_type="payment.succeeded")

        with pytest.raises(IntegrityError), transaction.atomic():
            WebhookEvent.objects.create(event_id="evt_1", event_type="payment.failed")

    def test_a_booking_blocks_deletion_of_its_offering(self, booking):
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            booking.centre_test.delete()
