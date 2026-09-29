"""Booking domain logic.

Views stay thin: they authenticate, deserialise, and call into this module.
Everything that can change a booking's state goes through
:func:`transition_booking`, which is the single choke point where the state
machine is enforced and the audit trail is written.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bookings.models import ACTIVE_STATUSES, Booking, BookingStatus, BookingStatusChange
from apps.catalog.models import CentreTest
from apps.common.exceptions import (
    ConflictError,
    DuplicateResource,
    InvalidStateTransition,
    ValidationFailed,
)

logger = logging.getLogger(__name__)

#: The complete, allowed booking state machine.
#:
#: PENDING   -> CONFIRMED | FAILED | CANCELLED
#: FAILED    -> PENDING   (a new payment attempt) | CANCELLED
#: CONFIRMED -> CANCELLED
#: CANCELLED -> (terminal)
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    BookingStatus.PENDING: frozenset(
        {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED}
    ),
    BookingStatus.FAILED: frozenset({BookingStatus.PENDING, BookingStatus.CANCELLED}),
    BookingStatus.CONFIRMED: frozenset({BookingStatus.CANCELLED}),
    BookingStatus.CANCELLED: frozenset(),
}


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


@transaction.atomic
def transition_booking(
    booking: Booking,
    to_status: str,
    *,
    source: str = BookingStatusChange.Source.SYSTEM,
    reason: str = "",
) -> Booking:
    """Move a booking to ``to_status``, or explain why that is not allowed.

    Transitioning to the status the booking already has is a **no-op, not an
    error**. That is deliberate: a replayed webhook that re-asserts an
    outcome already applied must succeed quietly rather than 4xx back to the
    provider and trigger another retry.
    """
    from_status = booking.status

    if from_status == to_status:
        logger.info(
            "booking_transition_noop",
            extra={
                "booking_reference": booking.reference,
                "status": from_status,
                "source": source,
            },
        )
        return booking

    if not can_transition(from_status, to_status):
        raise InvalidStateTransition(
            f"A booking that is {from_status} cannot become {to_status}.",
            details={"from": from_status, "to": to_status, "booking": booking.reference},
        )

    now = timezone.now()
    booking.status = to_status
    update_fields = ["status", "updated_at"]
    if to_status == BookingStatus.CONFIRMED:
        booking.confirmed_at = now
        update_fields.append("confirmed_at")
    elif to_status == BookingStatus.CANCELLED:
        booking.cancelled_at = now
        update_fields.append("cancelled_at")
    booking.save(update_fields=update_fields)

    BookingStatusChange.objects.create(
        booking=booking,
        from_status=from_status,
        to_status=to_status,
        source=source,
        reason=reason[:255],
    )
    logger.info(
        "booking_status_changed",
        extra={
            "booking_reference": booking.reference,
            "from_status": from_status,
            "to_status": to_status,
            "source": source,
            "reason": reason,
        },
    )
    return booking


def validate_appointment_time(centre_test: CentreTest, scheduled_for) -> None:
    """Reject appointment times that the centre cannot honour."""
    now = timezone.now()
    lead = timedelta(minutes=settings.BOOKING_MIN_LEAD_TIME_MINUTES)
    horizon = timedelta(days=settings.BOOKING_MAX_ADVANCE_DAYS)

    if scheduled_for <= now:
        raise ValidationFailed(
            "Appointments must be in the future.",
            details={"scheduled_for": scheduled_for.isoformat()},
        )
    if scheduled_for < now + lead:
        raise ValidationFailed(
            f"Appointments must be booked at least "
            f"{settings.BOOKING_MIN_LEAD_TIME_MINUTES} minutes ahead.",
            details={"earliest": (now + lead).isoformat()},
        )
    if scheduled_for > now + horizon:
        raise ValidationFailed(
            f"Appointments cannot be booked more than "
            f"{settings.BOOKING_MAX_ADVANCE_DAYS} days in advance.",
            details={"latest": (now + horizon).isoformat()},
        )

    local_time = timezone.localtime(scheduled_for).time()
    centre = centre_test.centre
    if not (centre.opens_at <= local_time <= centre.closes_at):
        raise ValidationFailed(
            f"{centre.name} accepts appointments between "
            f"{centre.opens_at:%H:%M} and {centre.closes_at:%H:%M}.",
            details={
                "opens_at": centre.opens_at.isoformat(),
                "closes_at": centre.closes_at.isoformat(),
                "requested": local_time.isoformat(),
            },
        )


@transaction.atomic
def create_booking(
    *, user, centre_test: CentreTest, scheduled_for, patient_notes: str = ""
) -> Booking:
    """Create a PENDING booking with the price snapshotted onto it."""
    if not centre_test.is_bookable:
        raise ConflictError(
            "That test is not currently bookable at this centre.",
            code="offering_unavailable",
            details={"offering": centre_test.id},
        )

    validate_appointment_time(centre_test, scheduled_for)

    duplicate = Booking.objects.filter(
        user=user,
        centre_test=centre_test,
        scheduled_for=scheduled_for,
        status__in=list(ACTIVE_STATUSES),
    ).first()
    if duplicate is not None:
        raise DuplicateResource(
            "You already have a booking for this test at this time.",
            code="duplicate_booking",
            details={"booking": duplicate.reference, "status": duplicate.status},
        )

    try:
        booking = Booking.objects.create(
            user=user,
            centre_test=centre_test,
            scheduled_for=scheduled_for,
            amount=centre_test.price,
            currency=settings.PAYMENT_CURRENCY,
            status=BookingStatus.PENDING,
            patient_notes=patient_notes,
        )
    except IntegrityError as exc:
        # The partial unique index caught a race the SELECT above missed.
        raise DuplicateResource(
            "You already have a booking for this test at this time.",
            code="duplicate_booking",
        ) from exc

    BookingStatusChange.objects.create(
        booking=booking,
        from_status="",
        to_status=BookingStatus.PENDING,
        source=BookingStatusChange.Source.API,
        reason="Booking created",
    )
    logger.info(
        "booking_created",
        extra={
            "booking_reference": booking.reference,
            "user_id": user.id,
            "offering_id": centre_test.id,
            "amount": str(booking.amount),
        },
    )
    return booking


def cancel_booking(booking: Booking, *, reason: str = "") -> Booking:
    """Cancel a booking, respecting the cancellation cutoff."""
    if booking.status == BookingStatus.CANCELLED:
        raise InvalidStateTransition(
            "This booking has already been cancelled.",
            details={"booking": booking.reference},
        )

    cutoff = timedelta(hours=settings.BOOKING_CANCELLATION_CUTOFF_HOURS)
    if (
        booking.status == BookingStatus.CONFIRMED
        and booking.scheduled_for - cutoff <= timezone.now()
    ):
        raise ConflictError(
            f"Confirmed bookings cannot be cancelled within "
            f"{settings.BOOKING_CANCELLATION_CUTOFF_HOURS} hours of the appointment.",
            code="cancellation_window_closed",
            details={"scheduled_for": booking.scheduled_for.isoformat()},
        )

    return transition_booking(
        booking,
        BookingStatus.CANCELLED,
        source=BookingStatusChange.Source.API,
        reason=reason or "Cancelled by patient",
    )
