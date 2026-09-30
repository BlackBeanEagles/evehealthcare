"""Payment domain logic, including idempotent webhook processing.

Both the synchronous charge (``POST /payments/``) and the asynchronous
callback (``POST /payments/webhook/``) converge on one function,
:func:`apply_payment_outcome`. That function is idempotent and
first-outcome-wins, which is what makes the two paths safe to combine:

* the same outcome applied twice is a no-op;
* a contradictory later outcome is refused and logged, not applied;
* the booking is only ever moved through the state machine in
  :mod:`apps.bookings.services`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus, BookingStatusChange
from apps.bookings.services import transition_booking
from apps.common.db import lock_rows
from apps.common.exceptions import (
    ConflictError,
    InvalidStateTransition,
    ResourceNotFound,
)
from apps.payments.gateway import EVENT_TYPE_TO_STATUS, get_gateway
from apps.payments.models import (
    OPEN_PAYMENT_STATUSES,
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)

logger = logging.getLogger(__name__)

#: Which booking status each terminal payment status implies.
PAYMENT_TO_BOOKING_STATUS = {
    PaymentStatus.SUCCESS: BookingStatus.CONFIRMED,
    PaymentStatus.FAILED: BookingStatus.FAILED,
}


@dataclass
class OutcomeResult:
    """What actually happened when an outcome was applied."""

    changed: bool
    booking_updated: bool
    detail: str


# --------------------------------------------------------------------- charging
def _replay_for_key(idempotency_key: str | None, booking: Booking) -> Payment | None:
    """The payment already made under this Idempotency-Key, if any.

    A key belongs to one booking; presenting it for another is a client bug,
    reported as a conflict rather than silently returning someone else's
    payment.
    """
    if not idempotency_key:
        return None
    existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
    if existing is None:
        return None
    if existing.booking_id != booking.pk:
        raise ConflictError(
            "This Idempotency-Key was already used for a different booking.",
            code="idempotency_key_reused",
            details={"payment": existing.reference},
        )
    logger.info(
        "payment_idempotent_replay",
        extra={"payment_reference": existing.reference, "booking_reference": booking.reference},
    )
    return existing


@transaction.atomic
def initiate_payment(
    *,
    booking: Booking,
    method: str,
    outcome: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[Payment, bool]:
    """Charge a booking through the simulated provider.

    Returns ``(payment, created)``. ``created=False`` means an
    ``Idempotency-Key`` replay returned the original payment untouched.
    """
    # Fast path: a plain retry after the original request finished.
    if (existing := _replay_for_key(idempotency_key, booking)) is not None:
        return existing, False

    # Re-read the booking under a row lock: two concurrent charge attempts for
    # the same booking must not both get past the checks below.
    booking = lock_rows(Booking.objects.filter(pk=booking.pk)).get()

    # Check the key again now that we hold the lock. A parallel request with
    # the same key may have committed its payment while this one waited; the
    # client must get that payment back, not a 409. (Found by
    # tests/test_concurrency.py: 7 of 8 simultaneous retries got 409.)
    if (existing := _replay_for_key(idempotency_key, booking)) is not None:
        return existing, False

    if booking.status == BookingStatus.CANCELLED:
        raise InvalidStateTransition(
            "This booking was cancelled and can no longer be paid for.",
            code="booking_cancelled",
            details={"booking": booking.reference},
        )
    if booking.status == BookingStatus.CONFIRMED:
        raise ConflictError(
            "This booking has already been paid for.",
            code="booking_already_paid",
            details={"booking": booking.reference},
        )

    open_payment = Payment.objects.filter(
        booking=booking, status__in=list(OPEN_PAYMENT_STATUSES)
    ).first()
    if open_payment is not None:
        raise ConflictError(
            "A payment for this booking is already in progress.",
            code="payment_in_progress",
            details={"payment": open_payment.reference, "status": open_payment.status},
        )

    # Retrying after a decline puts the booking back into PENDING first, so the
    # state machine sees PENDING -> CONFIRMED rather than FAILED -> CONFIRMED.
    if booking.status == BookingStatus.FAILED:
        transition_booking(
            booking,
            BookingStatus.PENDING,
            source=BookingStatusChange.Source.PAYMENT,
            reason="New payment attempt started",
        )

    try:
        payment = Payment.objects.create(
            booking=booking,
            amount=booking.amount,
            currency=booking.currency,
            method=method,
            idempotency_key=idempotency_key or None,
            status=PaymentStatus.PENDING,
        )
    except IntegrityError as exc:
        # Lost a race on either unique index; surface the winner (or report a
        # key that was used for a different booking).
        if (existing := _replay_for_key(idempotency_key, booking)) is not None:
            return existing, False
        raise ConflictError(
            "A payment for this booking is already in progress.",
            code="payment_in_progress",
        ) from exc

    logger.info(
        "payment_initiated",
        extra={
            "payment_reference": payment.reference,
            "booking_reference": booking.reference,
            "amount": str(payment.amount),
            "method": method,
        },
    )

    result = get_gateway().charge(payment=payment, outcome=outcome)
    apply_payment_outcome(
        payment,
        result.status,
        source=BookingStatusChange.Source.PAYMENT,
        provider_reference=result.provider_reference,
        failure_code=result.failure_code,
        failure_reason=result.failure_reason,
        provider_payload=result.raw,
    )
    payment.refresh_from_db()
    return payment, True


# ------------------------------------------------------------- outcome handling
@transaction.atomic
def apply_payment_outcome(
    payment: Payment,
    new_status: str,
    *,
    source: str,
    provider_reference: str | None = None,
    failure_code: str = "",
    failure_reason: str = "",
    provider_payload: dict[str, Any] | None = None,
) -> OutcomeResult:
    """Apply a terminal payment outcome exactly once.

    Idempotency rules, in order:

    1. Same status as the payment already has -> no-op (this is the replayed
       webhook case).
    2. Payment already terminal with a *different* status -> refused. The
       first terminal outcome wins; the contradiction is logged loudly rather
       than allowed to flip a confirmed booking.
    3. A non-terminal (PENDING) assertion never overwrites anything.
    """
    payment = lock_rows(Payment.objects.filter(pk=payment.pk)).get()

    if payment.status == new_status:
        logger.info(
            "payment_outcome_noop",
            extra={
                "payment_reference": payment.reference,
                "status": new_status,
                "source": source,
            },
        )
        return OutcomeResult(False, False, f"already_{new_status.lower()}")

    if payment.is_terminal:
        logger.warning(
            "payment_outcome_conflict",
            extra={
                "payment_reference": payment.reference,
                "current_status": payment.status,
                "rejected_status": new_status,
                "source": source,
            },
        )
        return OutcomeResult(False, False, "conflicting_outcome_ignored")

    if new_status not in PAYMENT_TO_BOOKING_STATUS:
        return OutcomeResult(False, False, "non_terminal_outcome_ignored")

    payment.status = new_status
    payment.processed_at = timezone.now()
    fields = ["status", "processed_at", "updated_at"]
    if provider_reference and not payment.provider_reference:
        payment.provider_reference = provider_reference
        fields.append("provider_reference")
    if failure_code:
        payment.failure_code = failure_code[:40]
        payment.failure_reason = failure_reason[:255]
        fields += ["failure_code", "failure_reason"]
    if provider_payload:
        payment.provider_payload = provider_payload
        fields.append("provider_payload")
    payment.save(update_fields=fields)

    logger.info(
        "payment_settled",
        extra={
            "payment_reference": payment.reference,
            "status": new_status,
            "source": source,
            "failure_code": payment.failure_code,
        },
    )

    booking = lock_rows(Booking.objects.filter(pk=payment.booking_id)).get()
    target = PAYMENT_TO_BOOKING_STATUS[new_status]
    try:
        transition_booking(
            booking,
            target,
            source=source,
            reason=f"Payment {payment.reference} {new_status.lower()}",
        )
    except InvalidStateTransition:
        # The booking moved on while the payment was in flight - almost always
        # a cancellation racing a late callback. The payment record stays
        # accurate; a successful charge against a cancelled booking is flagged
        # for refund rather than silently confirming a cancelled appointment.
        needs_refund = new_status == PaymentStatus.SUCCESS
        logger.warning(
            "payment_booking_not_updated",
            extra={
                "payment_reference": payment.reference,
                "booking_reference": booking.reference,
                "booking_status": booking.status,
                "payment_status": new_status,
                "requires_refund": needs_refund,
            },
        )
        return OutcomeResult(
            True,
            False,
            "refund_required" if needs_refund else "booking_already_terminal",
        )

    return OutcomeResult(True, True, f"booking_{target.lower()}")


# ---------------------------------------------------------------------- webhooks
def ingest_webhook_event(
    *, event_id: str, event_type: str, payload: dict[str, Any], signature: str = ""
) -> tuple[WebhookEvent, bool]:
    """Persist a delivery before acting on it. Returns ``(event, created)``.

    The unique index on ``event_id`` is the idempotency anchor: a redelivery
    loses the insert race and is reported as a duplicate. This is deliberately
    *not* a ``filter(...).exists()`` check followed by a create, which two
    simultaneous deliveries would both pass.
    """
    provider_reference = (payload.get("data") or {}).get("provider_reference") or ""
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                event_id=event_id,
                event_type=event_type,
                provider_reference=provider_reference[:64],
                payload=payload,
                signature=signature[:256],
                status=WebhookEventStatus.RECEIVED,
            )
        return event, True
    except IntegrityError:
        existing = WebhookEvent.objects.get(event_id=event_id)
        logger.info(
            "webhook_duplicate_ignored",
            extra={
                "event_id": event_id,
                "original_status": existing.status,
                "original_result": existing.result,
            },
        )
        return existing, False


def _resolve_payment(data: dict[str, Any]) -> Payment | None:
    provider_reference = data.get("provider_reference")
    if provider_reference:
        payment = Payment.objects.filter(provider_reference=provider_reference).first()
        if payment is not None:
            return payment
    payment_reference = data.get("payment_reference")
    if payment_reference:
        return Payment.objects.filter(reference=payment_reference).first()
    return None


def _finish(event: WebhookEvent, status: str, result: str) -> str:
    event.status = status
    event.result = result[:255]
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "result", "processed_at", "attempts", "last_error"])
    return result


@transaction.atomic
def process_webhook_event(event: WebhookEvent) -> str:
    """Act on a stored webhook event. Safe to call more than once.

    Returns a short machine-readable description of what was done, which is
    stored on the event and echoed to the provider.
    """
    event = lock_rows(WebhookEvent.objects.filter(pk=event.pk)).get()

    if event.status == WebhookEventStatus.PROCESSED:
        return "already_processed"

    event.attempts += 1
    event.last_error = ""

    data = event.payload.get("data") or {}
    target_status = EVENT_TYPE_TO_STATUS.get(event.event_type)

    if target_status is None:
        logger.info(
            "webhook_unsupported_type",
            extra={"event_id": event.event_id, "event_type": event.event_type},
        )
        return _finish(event, WebhookEventStatus.IGNORED, "unsupported_event_type")

    payment = _resolve_payment(data)
    if payment is None:
        # Not retryable: the event does not refer to anything we know about.
        logger.warning(
            "webhook_unknown_payment",
            extra={"event_id": event.event_id, "data": data},
        )
        return _finish(event, WebhookEventStatus.IGNORED, "unknown_payment")

    claimed_amount = data.get("amount")
    if claimed_amount is not None:
        try:
            if Decimal(str(claimed_amount)) != payment.amount:
                logger.error(
                    "webhook_amount_mismatch",
                    extra={
                        "event_id": event.event_id,
                        "payment_reference": payment.reference,
                        "expected": str(payment.amount),
                        "claimed": str(claimed_amount),
                    },
                )
                return _finish(event, WebhookEventStatus.IGNORED, "amount_mismatch")
        except (InvalidOperation, ValueError):
            return _finish(event, WebhookEventStatus.IGNORED, "invalid_amount")

    outcome = apply_payment_outcome(
        payment,
        target_status,
        source=BookingStatusChange.Source.WEBHOOK,
        failure_code=data.get("failure_code") or "",
        failure_reason=data.get("failure_reason") or "",
        provider_payload=event.payload,
    )
    logger.info(
        "webhook_processed",
        extra={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "payment_reference": payment.reference,
            "result": outcome.detail,
            "changed": outcome.changed,
        },
    )
    return _finish(event, WebhookEventStatus.PROCESSED, outcome.detail)


def mark_webhook_failed(event: WebhookEvent, error: str) -> None:
    """Record a processing failure so the retry machinery can pick it up.

    ``process_webhook_event`` runs in a transaction, so its own attempt
    counter is rolled back with everything else when it raises; the increment
    is therefore applied here, atomically, against the stored value.
    """
    WebhookEvent.objects.filter(pk=event.pk).update(
        status=WebhookEventStatus.FAILED,
        last_error=error[:2000],
        attempts=F("attempts") + 1,
    )
    logger.error(
        "webhook_processing_failed",
        extra={"event_id": event.event_id, "error": error},
    )


# ------------------------------------------------------------------ housekeeping
def reconcile_stale_payments(older_than_minutes: int = 30) -> int:
    """Fail payments that never reached a terminal state.

    A payment stuck in PENDING means we called the provider and never learned
    the result. Leaving it there pins the booking in PENDING forever and, via
    the partial unique index, blocks any retry. This is the sweeper; in
    production it would also re-query the provider before giving up.
    """
    cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
    stale = Payment.objects.filter(status=PaymentStatus.PENDING, created_at__lt=cutoff)
    count = 0
    for payment in stale.iterator():
        try:
            apply_payment_outcome(
                payment,
                PaymentStatus.FAILED,
                source=BookingStatusChange.Source.SYSTEM,
                failure_code="reconciliation_timeout",
                failure_reason=(
                    f"No terminal outcome received within {older_than_minutes} minutes."
                ),
            )
            count += 1
        except ResourceNotFound:  # pragma: no cover - row vanished mid-sweep
            continue
    if count:
        logger.warning("stale_payments_reconciled", extra={"count": count})
    return count
