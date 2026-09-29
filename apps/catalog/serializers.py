"""Catalogue serializers."""
from __future__ import annotations

from rest_framework import serializers

from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest


class DiagnosticTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = [
            "id", "code", "name", "description", "category",
            "preparation", "report_eta_hours", "is_active",
        ]

    def validate_code(self, value: str) -> str:
        code = value.strip().upper()
        qs = DiagnosticTest.objects.filter(code=code)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A test with this code already exists.")
        return code


class TestSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = ["id", "code", "name", "category", "report_eta_hours"]


class CentreSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticCentre
        fields = ["id", "name", "city", "state"]


class OfferingSerializer(serializers.ModelSerializer):
    """An offering as seen from inside a centre (the centre is implied)."""

    test = TestSummarySerializer(read_only=True)

    class Meta:
        model = CentreTest
        fields = ["id", "test", "price", "is_available", "is_bookable"]


class CentreTestSerializer(serializers.ModelSerializer):
    """A fully qualified offering: which test, at which centre, for how much."""

    centre = CentreSummarySerializer(read_only=True)
    test = TestSummarySerializer(read_only=True)

    class Meta:
        model = CentreTest
        fields = ["id", "centre", "test", "price", "is_available", "is_bookable"]


class CentreTestWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CentreTest
        fields = ["id", "centre", "test", "price", "is_available"]

    def validate(self, attrs: dict) -> dict:
        centre = attrs.get("centre") or getattr(self.instance, "centre", None)
        test = attrs.get("test") or getattr(self.instance, "test", None)
        qs = CentreTest.objects.filter(centre=centre, test=test)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"test": "This centre already offers that test; update the price instead."}
            )
        return attrs


class DiagnosticCentreSerializer(serializers.ModelSerializer):
    location = serializers.CharField(read_only=True)
    tests_offered = serializers.IntegerField(read_only=True)

    class Meta:
        model = DiagnosticCentre
        fields = [
            "id", "name", "address_line", "city", "state", "pincode", "phone",
            "opens_at", "closes_at", "is_active", "location", "tests_offered",
        ]


class DiagnosticCentreDetailSerializer(DiagnosticCentreSerializer):
    offerings = OfferingSerializer(many=True, read_only=True)

    class Meta(DiagnosticCentreSerializer.Meta):
        fields = DiagnosticCentreSerializer.Meta.fields + ["offerings"]
