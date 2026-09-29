"""Payment and webhook endpoints."""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.bookings.models import Booking
from apps.common.exceptions import ResourceNotFound, ValidationFailed
from apps.payments.models import Payment, PaymentStatus, WebhookEventStatus
from apps.payments.serializers import (
    PaymentCreateSerializer,
    PaymentSerializer,
    WebhookAcknowledgementSerializer,
)
from apps.payments.services import ingest_webhook_event, initiate_payment
from apps.payments.signature import META_KEY, SIGNATURE_HEADER, verify_signature
from apps.payments.tasks import dispatch_webhook_event

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"


@extend_schema_view(
    list=extend_schema(
        tags=["payments"],
        summary="List your payments",
        parameters=[
            OpenApiParameter("status", str, description="PENDING, SUCCESS or FAILED."),
            OpenApiParameter("booking", str, description="Filter by booking reference."),
        ],
    ),
    retrieve=extend_schema(tags=["payments"], summary="Retrieve a payment"),
)
class PaymentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payments"
    lookup_field = "reference"
    lookup_value_regex = "[A-Za-z0-9-]+"

    def get_serializer_class(self):
        return PaymentCreateSerializer if self.action == "create" else PaymentSerializer

    def get_queryset(self) -> QuerySet[Payment]:
        queryset = Payment.objects.filter(booking__user=self.request.user).select_related(
            "booking"
        )
        status_filter = self.request.query_params.get("status")
        if status_filter:
            wanted = status_filter.upper()
            if wanted not in PaymentStatus.values:
                raise ValidationFailed(
                    "Unknown payment status.",
                    details={"status": status_filter, "allowed": PaymentStatus.values},
                )
            queryset = queryset.filter(status=wanted)
        booking_reference = self.request.query_params.get("booking")
        if booking_reference:
            queryset = queryset.filter(booking__reference=booking_reference.upper())
        return queryset

    @extend_schema(
        tags=["payments"],
        summary="Pay for a booking (simulated)",
        description=(
            "Charges a booking through the simulated provider and settles it "
            "immediately. Send an `Idempotency-Key` header to make retries "
            "safe: replaying a key returns the original payment with 200 "
            "instead of charging again.\n\n"
            "A SUCCESS confirms the booking; a FAILED payment moves it to "
            "FAILED, from where a new payment may be attempted."
        ),
        parameters=[
            OpenApiParameter(
                "Idempotency-Key",
                str,
                location=OpenApiParameter.HEADER,
                description="Client-generated key making this charge replay-safe.",
            )
        ],
        request=PaymentCreateSerializer,
        responses={201: PaymentSerializer, 200: PaymentSerializer},
        examples=[
            OpenApiExample(
                "Force a success",
                value={"booking": "BKG-3F91A0C2D5", "method": "UPI", "outcome": "success"},
                request_only=True,
            ),
            OpenApiExample(
                "Force a decline",
                value={"booking": "BKG-3F91A0C2D5", "method": "CARD", "outcome": "failure"},
                request_only=True,
            ),
        ],
    )
    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reference = serializer.validated_data["booking"]

        # Scoped to the caller: another user's booking reference is a 404, so
        # the endpoint never reveals that it exists.
        booking = Booking.objects.filter(
            reference=reference, user=request.user
        ).select_related("centre_test").first()
        if booking is None:
            raise ResourceNotFound(
                "No booking with that reference.", details={"booking": reference}
            )

        idempotency_key = request.META.get(IDEMPOTENCY_HEADER) or None
        payment, created = initiate_payment(
            booking=booking,
            method=serializer.validated_data["method"],
            outcome=serializer.validated_data.get("outcome"),
            idempotency_key=idempotency_key,
        )
        payment.refresh_from_db()
        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PaymentWebhookView(APIView):
    """Receives payment-status callbacks from the simulated provider.

    Contract:

    * The body is signed; an unsigned or mis-signed delivery is rejected 401.
    * ``id`` is the provider's event id and is stored under a unique index.
      A redelivery returns 200 with ``duplicate: true`` and changes nothing.
    * Anything we cannot act on (unknown payment, unsupported event type,
      amount mismatch) is stored, marked IGNORED and answered 200 - retrying
      it would never succeed, and a 4xx would make the provider hammer us.
    * Genuine processing errors return 500 so the provider *does* retry, and
      the stored event is picked up by the retry task as well.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "webhook"

    @extend_schema(
        tags=["payments"],
        summary="Payment provider webhook (idempotent)",
        request={
            "application/json": {
                "type": "object",
                "required": ["id", "type", "data"],
                "properties": {
                    "id": {"type": "string", "example": "evt_9f1c2b3a4d5e6f70"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "payment.succeeded", "payment.captured",
                            "payment.failed", "payment.cancelled",
                            "payment.pending", "payment.created",
                        ],
                    },
                    "data": {
                        "type": "object",
                        "properties": {
                            "payment_reference": {"type": "string"},
                            "provider_reference": {"type": "string"},
                            "amount": {"type": "string"},
                            "failure_code": {"type": "string"},
                        },
                    },
                },
            }
        },
        parameters=[
            OpenApiParameter(
                SIGNATURE_HEADER,
                str,
                location=OpenApiParameter.HEADER,
                required=True,
                description="t=<unix-seconds>,v1=<hex hmac-sha256 of '<t>.<body>'>",
            )
        ],
        responses={200: WebhookAcknowledgementSerializer},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        # Read raw bytes before anything parses them: the signature covers the
        # exact body, not a re-serialised version of it.
        raw_body: bytes = request.body

        if settings.PAYMENT_WEBHOOK_REQUIRE_SIGNATURE:
            verify_signature(
                settings.PAYMENT_WEBHOOK_SECRET,
                raw_body,
                request.META.get(META_KEY),
                settings.PAYMENT_WEBHOOK_TOLERANCE_SECONDS,
            )

        try:
            payload = json.loads(raw_body or b"{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationFailed("Webhook body must be valid JSON.") from exc

        if not isinstance(payload, dict):
            raise ValidationFailed("Webhook body must be a JSON object.")

        event_id = str(payload.get("id") or "").strip()
        event_type = str(payload.get("type") or "").strip()
        if not event_id or not event_type:
            raise ValidationFailed(
                "Webhook payload must include 'id' and 'type'.",
                details={"id": event_id, "type": event_type},
            )

        event, created = ingest_webhook_event(
            event_id=event_id[:128],
            event_type=event_type[:64],
            payload=payload,
            signature=request.META.get(META_KEY, "") or "",
        )

        if not created:
            # The whole point of the exercise: a redelivery is acknowledged
            # and does nothing. No second payment, no second transition.
            return Response(
                {
                    "received": True,
                    "duplicate": True,
                    "event_id": event.event_id,
                    "status": event.status,
                    "result": event.result or "already_recorded",
                },
                status=status.HTTP_200_OK,
            )

        result = dispatch_webhook_event(event)
        event.refresh_from_db()

        if event.status == WebhookEventStatus.FAILED:
            # Signal the provider to retry; the event is stored either way.
            return Response(
                {
                    "received": True,
                    "duplicate": False,
                    "event_id": event.event_id,
                    "status": event.status,
                    "result": result,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "received": True,
                "duplicate": False,
                "event_id": event.event_id,
                "status": event.status,
                "result": result,
            },
            status=status.HTTP_200_OK,
        )
