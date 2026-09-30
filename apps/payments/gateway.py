"""A simulated payment provider.

No real gateway is integrated. This module is the seam where one would go:
everything above it talks to :class:`SimulatedPaymentGateway` through
:meth:`charge`, so swapping in Razorpay or Stripe means implementing the same
two methods, not rewriting the payment service.

Outcomes are controllable. ``PAYMENT_DEFAULT_OUTCOME=random`` (the default)
uses ``PAYMENT_SUCCESS_RATE``; a request may override it with
``"outcome": "success" | "failure" | "pending"``. ``pending`` models an
asynchronous confirmation (a UPI collect request, a 3-D Secure challenge):
the provider accepts the charge and settles it later through the webhook. Deterministic outcomes are what make
the test suite meaningful, and they let a reviewer exercise the failure path
on demand rather than by retrying until the dice cooperate.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from django.conf import settings

from apps.common.exceptions import ValidationFailed
from apps.payments.models import PaymentStatus

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_PENDING = "pending"
OUTCOME_RANDOM = "random"
VALID_OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_PENDING, OUTCOME_RANDOM)

#: Which payment status each webhook event type asserts.
EVENT_TYPE_TO_STATUS: dict[str, str] = {
    "payment.succeeded": PaymentStatus.SUCCESS,
    "payment.captured": PaymentStatus.SUCCESS,
    "payment.failed": PaymentStatus.FAILED,
    "payment.cancelled": PaymentStatus.FAILED,
    "payment.pending": PaymentStatus.PENDING,
    "payment.created": PaymentStatus.PENDING,
}

FAILURE_MODES: list[tuple[str, str]] = [
    ("insufficient_funds", "The account does not have sufficient funds."),
    ("card_declined", "The issuing bank declined the transaction."),
    ("gateway_timeout", "The payment provider timed out."),
    ("risk_blocked", "The transaction was blocked by risk checks."),
]


@dataclass(frozen=True)
class GatewayResult:
    provider_reference: str
    status: str
    failure_code: str = ""
    failure_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == PaymentStatus.SUCCESS


class SimulatedPaymentGateway:
    def __init__(
        self,
        *,
        success_rate: float | None = None,
        default_outcome: str | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.success_rate = (
            settings.PAYMENT_SUCCESS_RATE if success_rate is None else success_rate
        )
        self.default_outcome = default_outcome or settings.PAYMENT_DEFAULT_OUTCOME
        self.rng = rng or random.Random()

    def resolve_outcome(self, requested: str | None) -> str:
        outcome = (requested or self.default_outcome or OUTCOME_RANDOM).lower()
        if outcome not in VALID_OUTCOMES:
            raise ValidationFailed(
                "outcome must be one of: success, failure, pending, random.",
                details={"outcome": requested, "allowed": list(VALID_OUTCOMES)},
            )
        if outcome == OUTCOME_RANDOM:
            outcome = (
                OUTCOME_SUCCESS if self.rng.random() < self.success_rate else OUTCOME_FAILURE
            )
        return outcome

    def charge(self, *, payment, outcome: str | None = None) -> GatewayResult:
        """Attempt to collect ``payment.amount``. Never raises for a decline:
        a declined payment is a normal business outcome, not an exception."""
        resolved = self.resolve_outcome(outcome)
        provider_reference = f"sim_pay_{uuid.uuid4().hex[:20]}"
        now = datetime.now(tz=timezone.utc).isoformat()

        if resolved == OUTCOME_PENDING:
            return GatewayResult(
                provider_reference=provider_reference,
                status=PaymentStatus.PENDING,
                raw={
                    "id": provider_reference,
                    "status": "created",
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                    "method": payment.method,
                    "awaiting": "customer approval; the outcome arrives by webhook",
                    "created_at": now,
                    "simulated": True,
                },
            )

        if resolved == OUTCOME_SUCCESS:
            return GatewayResult(
                provider_reference=provider_reference,
                status=PaymentStatus.SUCCESS,
                raw={
                    "id": provider_reference,
                    "status": "captured",
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                    "method": payment.method,
                    "captured_at": now,
                    "simulated": True,
                },
            )

        failure_code, failure_reason = self.rng.choice(FAILURE_MODES)
        return GatewayResult(
            provider_reference=provider_reference,
            status=PaymentStatus.FAILED,
            failure_code=failure_code,
            failure_reason=failure_reason,
            raw={
                "id": provider_reference,
                "status": "failed",
                "amount": str(payment.amount),
                "currency": payment.currency,
                "method": payment.method,
                "error": {"code": failure_code, "description": failure_reason},
                "failed_at": now,
                "simulated": True,
            },
        )

    @staticmethod
    def build_event(payment, *, event_type: str | None = None, event_id: str | None = None) -> dict:
        """Build the callback body the provider would POST to our webhook.

        Used by the ``send_test_webhook`` command and by the tests, so the
        webhook contract is exercised through exactly one definition.
        """
        if event_type is None:
            event_type = {
                PaymentStatus.SUCCESS: "payment.succeeded",
                PaymentStatus.FAILED: "payment.failed",
            }.get(payment.status, "payment.pending")
        return {
            "id": event_id or f"evt_{uuid.uuid4().hex[:24]}",
            "type": event_type,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "data": {
                "payment_reference": payment.reference,
                "provider_reference": payment.provider_reference,
                "booking_reference": payment.booking.reference,
                "status": payment.status,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "failure_code": payment.failure_code,
                "failure_reason": payment.failure_reason,
            },
        }


def get_gateway() -> SimulatedPaymentGateway:
    """Single place to swap the implementation (or inject a fake in tests)."""
    return SimulatedPaymentGateway()
