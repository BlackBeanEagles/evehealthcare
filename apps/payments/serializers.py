"""Payment serializers."""
from __future__ import annotations

from rest_framework import serializers

from apps.payments.gateway import VALID_OUTCOMES
from apps.payments.models import Payment, PaymentMethod, WebhookEvent


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


class WebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEvent
        fields = [
            "event_id", "event_type", "status", "result",
            "delivery_count", "attempts", "received_at", "processed_at",
        ]
        read_only_fields = fields


class SimulateWebhookSerializer(serializers.Serializer):
    EVENT_TYPES = {"success": "payment.succeeded", "failure": "payment.failed"}

    outcome = serializers.ChoiceField(
        choices=[("success", "success"), ("failure", "failure")],
        help_text="Which outcome the simulated provider reports.",
    )
    deliveries = serializers.IntegerField(
        min_value=1, max_value=5, default=3,
        help_text="Deliver the same event this many times, as a provider "
        "retrying on a timeout would.",
    )


class SimulatedDeliverySerializer(serializers.Serializer):
    attempt = serializers.IntegerField()
    http_status = serializers.IntegerField()
    duplicate = serializers.BooleanField()
    status = serializers.CharField()
    result = serializers.CharField()


class SimulationResultSerializer(serializers.Serializer):
    """Documents the simulator response for the OpenAPI schema."""

    event_id = serializers.CharField()
    event_type = serializers.CharField()
    deliveries = SimulatedDeliverySerializer(many=True)
    booking_status_before = serializers.CharField()
    booking_status_after = serializers.CharField()
    payment = PaymentSerializer()
