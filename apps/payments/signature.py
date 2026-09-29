"""HMAC signing for the simulated provider's webhooks.

Real providers sign their callbacks; an unauthenticated public endpoint that
flips bookings to CONFIRMED would otherwise be an open door. The scheme here
mirrors the common ``t=<unix>,v1=<hex>`` header: the timestamp is inside the
signed payload, so an attacker cannot replay an old body with a fresh
timestamp, and deliveries older than the tolerance window are rejected.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from rest_framework import status

from apps.common.exceptions import DomainError

SIGNATURE_HEADER = "X-Webhook-Signature"
META_KEY = "HTTP_X_WEBHOOK_SIGNATURE"


class InvalidSignature(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_signature"
    message = "Webhook signature verification failed."


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def build_header(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """Produce the header value a provider would send. Used by tests and by
    the ``send_test_webhook`` management command."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    return f"t={timestamp},v1={compute_signature(secret, timestamp, body)}"


def _parse_header(header: str) -> tuple[int, str]:
    parts = dict(
        segment.split("=", 1) for segment in header.split(",") if "=" in segment
    )
    if "t" not in parts or "v1" not in parts:
        raise InvalidSignature("Signature header must be of the form 't=...,v1=...'.")
    try:
        timestamp = int(parts["t"])
    except ValueError as exc:
        raise InvalidSignature("Signature timestamp is not an integer.") from exc
    return timestamp, parts["v1"]


def verify_signature(
    secret: str, body: bytes, header: str | None, tolerance_seconds: int
) -> None:
    """Raise :class:`InvalidSignature` unless ``header`` authenticates ``body``."""
    if not header:
        raise InvalidSignature(f"Missing {SIGNATURE_HEADER} header.")

    timestamp, provided = _parse_header(header)

    if tolerance_seconds > 0 and abs(int(time.time()) - timestamp) > tolerance_seconds:
        raise InvalidSignature(
            "Webhook timestamp is outside the tolerance window.",
            details={"tolerance_seconds": tolerance_seconds},
        )

    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected, provided):
        raise InvalidSignature()
