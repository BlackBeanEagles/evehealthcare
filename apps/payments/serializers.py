"""Payment serializers."""
from __future__ import annotations

from rest_framework import serializers

from apps.payments.gateway import VALID_OUTCOMES
from apps.payments.models import Payment, PaymentMethod


class PaymentSerializer(serializers.ModelSerializer):
    booking = serializers.CharField(source="booking.reference", read_only=True)
    booking_status = serializers.CharField(source="booking.status", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "reference", "booking", "booking_status", "amount", "currency",
            "status", "method", "provider_reference", "idempotency_key",
            "failure_code", "failure_reason", "processed_at", "created_at",
        ]
        read_only_fields = fields


class PaymentCreateSerializer(serializers.Serializer):
    booking = serializers.CharField(
        max_length=24, help_text="Booking reference, e.g. BKG-3F91A0C2D5."
    )
    method = serializers.ChoiceField(
        choices=PaymentMethod.choices, default=PaymentMethod.UPI
    )
    outcome = serializers.ChoiceField(
        choices=[(value, value) for value in VALID_OUTCOMES],
        required=False,
        help_text=(
            "Simulation control. 'success' or 'failure' settle immediately; "
            "'pending' leaves the payment awaiting a webhook (like a UPI "
            "collect request); omitted falls back to PAYMENT_DEFAULT_OUTCOME."
        ),
    )

    def validate_booking(self, value: str) -> str:
        return value.strip().upper()


class WebhookAcknowledgementSerializer(serializers.Serializer):
    """Documents the webhook response shape for the OpenAPI schema."""

    received = serializers.BooleanField()
    duplicate = serializers.BooleanField()
    event_id = serializers.CharField()
    status = serializers.CharField()
    result = serializers.CharField()
