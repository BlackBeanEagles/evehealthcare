"""Payments and inbound webhook events.

Idempotency is enforced in the schema, not only in code:

* ``Payment.idempotency_key`` is unique, so a client retrying ``POST /payments/``
  with the same key cannot create a second charge.
* A partial unique index allows at most one *open* (PENDING or SUCCESS)
  payment per booking, so a booking can never be double-charged - while still
  allowing a fresh attempt after a FAILED one.
* ``WebhookEvent.event_id`` is unique, so a redelivered webhook collides at
  the database level rather than relying on a check-then-insert that races.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.bookings.models import Booking
from apps.common.models import TimeStampedModel, generate_reference


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SUCCESS = "SUCCESS", "Succeeded"
    FAILED = "FAILED", "Failed"


#: Once a payment reaches one of these it does not change again.
TERMINAL_PAYMENT_STATUSES = frozenset({PaymentStatus.SUCCESS, PaymentStatus.FAILED})
OPEN_PAYMENT_STATUSES = frozenset({PaymentStatus.PENDING, PaymentStatus.SUCCESS})


class PaymentMethod(models.TextChoices):
    UPI = "UPI", "UPI"
    CARD = "CARD", "Card"
    NETBANKING = "NETBANKING", "Net banking"
    WALLET = "WALLET", "Wallet"


class Payment(TimeStampedModel):
    reference = models.CharField(
        max_length=24, unique=True, editable=False, db_index=True,
        help_text="Our public payment id, e.g. PAY-9C22D1A0F3.",
    )
    booking = models.ForeignKey(
        Booking, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(
        max_length=10, choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING, db_index=True,
    )
    method = models.CharField(
        max_length=12, choices=PaymentMethod.choices, default=PaymentMethod.UPI
    )
    provider_reference = models.CharField(
        max_length=64, null=True, blank=True, unique=True,
        help_text="Identifier assigned by the (simulated) payment provider.",
    )
    idempotency_key = models.CharField(
        max_length=64, null=True, blank=True,
        help_text="Client-supplied Idempotency-Key header; replaying it returns "
        "the original payment instead of charging again.",
    )
    failure_code = models.CharField(max_length=40, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=Decimal("0")), name="payment_amount_positive"
            ),
            # At most one payment that is still open (pending) or already paid
            # per booking. A FAILED payment leaves the slot free for a retry.
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(status__in=["PENDING", "SUCCESS"]),
                name="uniq_open_payment_per_booking",
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_payment_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(fields=["booking", "status"], name="idx_payment_booking_status"),
            models.Index(fields=["status", "created_at"], name="idx_payment_status_time"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.status} {self.amount}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_reference("PAY")
        super().save(*args, **kwargs)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_PAYMENT_STATUSES


class WebhookEventStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    PROCESSED = "PROCESSED", "Processed"
    DUPLICATE = "DUPLICATE", "Duplicate"
    IGNORED = "IGNORED", "Ignored"
    FAILED = "FAILED", "Failed"


class WebhookEvent(models.Model):
    """Every inbound webhook delivery, stored before it is acted on.

    Persisting first and processing second means a crash mid-processing does
    not lose the event: it stays RECEIVED/FAILED and can be replayed by the
    retry task or the ``replay_failed_webhooks`` command.
    """

    event_id = models.CharField(
        max_length=128, unique=True, db_index=True,
        help_text="Provider's event id. The uniqueness of this column is what "
        "makes redelivery safe.",
    )
    event_type = models.CharField(max_length=64)
    provider_reference = models.CharField(max_length=64, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    signature = models.CharField(max_length=256, blank=True)
    status = models.CharField(
        max_length=12, choices=WebhookEventStatus.choices,
        default=WebhookEventStatus.RECEIVED, db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    result = models.CharField(max_length=255, blank=True)
    last_error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhook_events"
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status", "received_at"], name="idx_webhook_status_time"),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} ({self.status})"
