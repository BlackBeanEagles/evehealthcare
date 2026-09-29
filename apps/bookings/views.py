"""Booking endpoints.

Authorisation rule for the whole module: a patient can only ever see and act
on their own bookings. The queryset is filtered by ``request.user`` before
any lookup happens, so another user's booking reference resolves to 404 -
the API never confirms that someone else's booking exists.
"""
from __future__ import annotations

import logging

from django.db.models import Prefetch, QuerySet
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.bookings.models import Booking, BookingStatus
from apps.bookings.serializers import (
    BookingCancelSerializer,
    BookingCreateSerializer,
    BookingDetailSerializer,
    BookingSerializer,
)
from apps.bookings.services import cancel_booking, create_booking
from apps.common.exceptions import ValidationFailed
from apps.payments.models import Payment

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(
        tags=["bookings"],
        summary="List your bookings",
        parameters=[
            OpenApiParameter("status", str, description="PENDING, CONFIRMED, FAILED or CANCELLED."),
            OpenApiParameter("from", str, description="ISO-8601 lower bound on scheduled_for."),
            OpenApiParameter("to", str, description="ISO-8601 upper bound on scheduled_for."),
            OpenApiParameter("ordering", str, description="-created_at, created_at, scheduled_for, -scheduled_for."),
        ],
    ),
    retrieve=extend_schema(
        tags=["bookings"],
        summary="Retrieve a booking, with its full status history",
    ),
    create=extend_schema(
        tags=["bookings"],
        summary="Book a diagnostic test",
        description=(
            "Creates a PENDING booking and snapshots the current price onto it. "
            "Call POST /api/v1/payments/ next to pay for it."
        ),
        examples=[
            OpenApiExample(
                "By offering id",
                value={"offering": 3, "scheduled_for": "2026-10-04T09:30:00+05:30"},
                request_only=True,
            ),
            OpenApiExample(
                "By centre and test",
                value={
                    "centre": 1,
                    "test": 2,
                    "scheduled_for": "2026-10-04T09:30:00+05:30",
                    "patient_notes": "Fasting since 10pm.",
                },
                request_only=True,
            ),
        ],
    ),
)
class BookingViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    lookup_field = "reference"
    lookup_value_regex = "[A-Za-z0-9-]+"

    ORDERING_FIELDS = {"created_at", "-created_at", "scheduled_for", "-scheduled_for"}

    def get_serializer_class(self):
        if self.action == "create":
            return BookingCreateSerializer
        if self.action == "cancel":
            return BookingCancelSerializer
        if self.action == "retrieve":
            return BookingDetailSerializer
        return BookingSerializer

    def get_queryset(self) -> QuerySet[Booking]:
        queryset = (
            Booking.objects.filter(user=self.request.user)
            .select_related("centre_test__centre", "centre_test__test")
            .prefetch_related(
                Prefetch("payments", queryset=Payment.objects.order_by("-created_at"))
            )
        )
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("status_changes")

        status_filter = self.request.query_params.get("status")
        if status_filter:
            wanted = status_filter.upper()
            if wanted not in BookingStatus.values:
                raise ValidationFailed(
                    "Unknown booking status.",
                    details={"status": status_filter, "allowed": BookingStatus.values},
                )
            queryset = queryset.filter(status=wanted)

        for param, lookup in (("from", "scheduled_for__gte"), ("to", "scheduled_for__lte")):
            raw = self.request.query_params.get(param)
            if raw:
                parsed = parse_datetime(raw)
                if parsed is None:
                    raise ValidationFailed(
                        f"'{param}' must be an ISO-8601 datetime.", details={param: raw}
                    )
                queryset = queryset.filter(**{lookup: parsed})

        ordering = self.request.query_params.get("ordering", "-created_at")
        if ordering not in self.ORDERING_FIELDS:
            raise ValidationFailed(
                "Unsupported ordering.",
                details={"ordering": ordering, "allowed": sorted(self.ORDERING_FIELDS)},
            )
        return queryset.order_by(ordering)

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = create_booking(
            user=request.user,
            centre_test=serializer.validated_data["offering"],
            scheduled_for=serializer.validated_data["scheduled_for"],
            patient_notes=serializer.validated_data.get("patient_notes", ""),
        )
        booking = self.get_queryset().get(pk=booking.pk)
        return Response(
            BookingSerializer(booking).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["bookings"],
        summary="Cancel a booking",
        request=BookingCancelSerializer,
        responses={200: BookingSerializer},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, reference: str | None = None) -> Response:
        booking = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = cancel_booking(booking, reason=serializer.validated_data.get("reason", ""))
        booking = self.get_queryset().get(pk=booking.pk)
        return Response(BookingSerializer(booking).data, status=status.HTTP_200_OK)
