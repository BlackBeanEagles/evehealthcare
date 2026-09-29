"""Bookings.

Two decisions worth calling out:

1. ``amount`` is a **snapshot** taken from the offering's price when the
   booking is created. Prices change; what a patient agreed to pay must not.
2. ``status`` is driven by an explicit state machine in
   :mod:`apps.bookings.services`, never assigned ad hoc from a view. Every
   change is recorded in :class:`BookingStatusChange`, which is what makes a
   duplicated webhook visible after the fact instead of invisible.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.catalog.models import CentreTest
from apps.common.models import TimeStampedModel, generate_reference


class BookingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending payment"
    CONFIRMED = "CONFIRMED", "Confirmed"
    FAILED = "FAILED", "Payment failed"
    CANCELLED = "CANCELLED", "Cancelled"


class TransitionSource(models.TextChoices):
    """What caused a booking to change state."""

    API = "api", "API request"
    PAYMENT = "payment", "Payment attempt"
    WEBHOOK = "webhook", "Provider webhook"
    SYSTEM = "system", "System"


TERMINAL_STATUSES = frozenset({BookingStatus.CANCELLED})
ACTIVE_STATUSES = frozenset({BookingStatus.PENDING, BookingStatus.CONFIRMED})


class Booking(TimeStampedModel):
    reference = models.CharField(
        max_length=24, unique=True, editable=False, db_index=True,
        help_text="Public identifier, e.g. BKG-3F91A0C2D5. The integer pk is never exposed.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
        help_text="PROTECT: a user with financial history is deactivated, not deleted.",
    )
    centre_test = models.ForeignKey(
        CentreTest,
        on_delete=models.PROTECT,
        related_name="bookings",
        help_text="The offering booked - carries both the centre and the test.",
    )
    scheduled_for = models.DateTimeField(db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(
        max_length=12,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
        db_index=True,
    )
    patient_notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bookings"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gte=Decimal("0")), name="booking_amount_non_negative"
            ),
            # One live booking per patient, offering and slot. Enforced as a
            # partial unique index so a cancelled or failed booking does not
            # block rebooking the same slot.
            models.UniqueConstraint(
                fields=["user", "centre_test", "scheduled_for"],
                condition=models.Q(status__in=["PENDING", "CONFIRMED"]),
                name="uniq_active_booking_per_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="idx_booking_user_status"),
            models.Index(fields=["status", "scheduled_for"], name="idx_booking_status_time"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_reference("BKG")
        super().save(*args, **kwargs)

    # -- convenience -------------------------------------------------------
    @property
    def centre(self):
        return self.centre_test.centre

    @property
    def test(self):
        return self.centre_test.test

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_payable(self) -> bool:
        """A booking can be paid while it is awaiting payment or retrying one."""
        return self.status in {BookingStatus.PENDING, BookingStatus.FAILED}


class BookingStatusChange(models.Model):
    """Append-only audit trail of booking state transitions."""

    #: Kept as a class attribute so call sites read
    #: ``BookingStatusChange.Source.WEBHOOK``.
    Source = TransitionSource

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="status_changes"
    )
    from_status = models.CharField(
        max_length=12, choices=BookingStatus.choices, blank=True,
        help_text="Empty for the row recording the booking's creation.",
    )
    to_status = models.CharField(max_length=12, choices=BookingStatus.choices)
    source = models.CharField(
        max_length=12, choices=TransitionSource.choices, default=TransitionSource.SYSTEM
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "booking_status_changes"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["booking", "created_at"], name="idx_status_change_booking"),
        ]

    def __str__(self) -> str:
        return f"{self.booking_id}: {self.from_status} -> {self.to_status}"
