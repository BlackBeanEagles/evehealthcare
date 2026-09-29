"""Booking serializers."""
from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.bookings.models import Booking, BookingStatusChange
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from apps.catalog.serializers import CentreSummarySerializer, TestSummarySerializer


class BookingStatusChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingStatusChange
        fields = ["from_status", "to_status", "source", "reason", "created_at"]


class BookingSerializer(serializers.ModelSerializer):
    centre = CentreSummarySerializer(read_only=True)
    test = TestSummarySerializer(read_only=True)
    offering_id = serializers.IntegerField(source="centre_test_id", read_only=True)
    can_be_paid = serializers.BooleanField(source="is_payable", read_only=True)
    latest_payment = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = [
            "reference", "status", "amount", "currency", "scheduled_for",
            "centre", "test", "offering_id", "patient_notes",
            "can_be_paid", "latest_payment",
            "confirmed_at", "cancelled_at", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_latest_payment(self, obj: Booking) -> dict[str, Any] | None:
        # ``payments`` is prefetched by the viewset; avoid a query per row.
        payments = list(obj.payments.all())
        if not payments:
            return None
        latest = max(payments, key=lambda p: (p.created_at, p.id))
        return {
            "reference": latest.reference,
            "status": latest.status,
            "amount": str(latest.amount),
            "created_at": latest.created_at,
        }


class BookingDetailSerializer(BookingSerializer):
    status_changes = BookingStatusChangeSerializer(many=True, read_only=True)

    class Meta(BookingSerializer.Meta):
        fields = BookingSerializer.Meta.fields + ["status_changes"]
        read_only_fields = fields


class BookingCreateSerializer(serializers.Serializer):
    """Accepts either the offering id, or the centre/test pair.

    ``offering`` is the precise form (it is the row that carries the price);
    ``centre`` + ``test`` is the form a client naturally has after browsing
    the catalogue. Supporting both costs little and avoids a round trip.
    """

    offering = serializers.PrimaryKeyRelatedField(
        queryset=CentreTest.objects.all(), required=False,
        help_text="Id of the centre/test offering being booked.",
    )
    centre = serializers.PrimaryKeyRelatedField(
        queryset=DiagnosticCentre.objects.all(), required=False
    )
    test = serializers.PrimaryKeyRelatedField(
        queryset=DiagnosticTest.objects.all(), required=False
    )
    scheduled_for = serializers.DateTimeField()
    patient_notes = serializers.CharField(
        required=False, allow_blank=True, max_length=2000, default=""
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        offering = attrs.get("offering")
        centre = attrs.get("centre")
        test = attrs.get("test")

        if offering is None and not (centre and test):
            raise serializers.ValidationError(
                "Provide either 'offering', or both 'centre' and 'test'."
            )
        if offering is not None and (centre or test):
            raise serializers.ValidationError(
                "Provide 'offering' or the 'centre'/'test' pair, not both."
            )

        if offering is None:
            offering = (
                CentreTest.objects.select_related("centre", "test")
                .filter(centre=centre, test=test)
                .first()
            )
            if offering is None:
                raise serializers.ValidationError(
                    {"test": "This centre does not offer that test."}
                )
        attrs["offering"] = offering
        return attrs


class BookingCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
