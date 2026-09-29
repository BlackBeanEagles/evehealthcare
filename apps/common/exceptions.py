"""Domain exceptions and a single, consistent API error envelope.

Every error response from this service looks like::

    {
      "error": {
        "code": "invalid_state_transition",
        "message": "Cannot pay for a booking that is CANCELLED.",
        "details": {"from": "CANCELLED", "to": "CONFIRMED"}
      },
      "request_id": "0f2c..."
    }

A stable machine-readable ``code`` matters more to a client than the prose.
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.logging import get_request_id

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Base class for expected, business-rule failures.

    These are not bugs: they are the API telling a client that what it asked
    for is not allowed right now. Views do not catch them individually; the
    exception handler below turns them into the error envelope.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "domain_error"
    message = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        self.status_code = status_code or self.status_code
        super().__init__(self.message)


class ValidationFailed(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "validation_error"
    message = "The request payload is invalid."


class ResourceNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource does not exist."


class PermissionDenied(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class ConflictError(DomainError):
    """The resource exists but is in a state that forbids this operation."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The resource is in a conflicting state."


class InvalidStateTransition(ConflictError):
    code = "invalid_state_transition"
    message = "That state transition is not allowed."


class DuplicateResource(ConflictError):
    code = "duplicate"
    message = "That resource already exists."


class ServiceUnavailable(DomainError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A downstream dependency is unavailable."


def error_payload(
    code: str, message: str, details: Any | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    request_id = get_request_id()
    if request_id:
        body["request_id"] = request_id
    return body


def _flatten_drf_detail(detail: Any) -> tuple[str, Any]:
    """Turn a DRF error detail into (message, details)."""
    if isinstance(detail, dict):
        first_field = next(iter(detail))
        first_value = detail[first_field]
        if isinstance(first_value, list) and first_value:
            first_value = first_value[0]
        message = f"{first_field}: {first_value}"
        return message, detail
    if isinstance(detail, list):
        return (str(detail[0]) if detail else "Invalid request."), {
            "non_field_errors": detail
        }
    return str(detail), None


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF exception handler producing the envelope documented above."""
    if isinstance(exc, DomainError):
        return Response(
            error_payload(exc.code, exc.message, exc.details or None),
            status=exc.status_code,
        )

    if isinstance(exc, Http404):
        return Response(
            error_payload("not_found", "The requested resource does not exist."),
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, DjangoPermissionDenied):
        return Response(
            error_payload(
                "permission_denied",
                "You do not have permission to perform this action.",
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, IntegrityError):
        # A unique/check constraint fired that the serializer did not catch.
        # 409 is honest here; the client may be able to retry with new input.
        logger.warning("integrity_error", extra={"error": str(exc)})
        return Response(
            error_payload(
                "conflict", "The request conflicts with the current state of the data."
            ),
            status=status.HTTP_409_CONFLICT,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        # Genuinely unexpected: log with traceback, return an opaque 500.
        logger.exception("unhandled_exception")
        return None

    code = getattr(exc, "default_code", "error") or "error"
    message, details = _flatten_drf_detail(response.data)
    if response.status_code == status.HTTP_400_BAD_REQUEST:
        code = "validation_error"
    response.data = error_payload(code, message, details)
    return response
