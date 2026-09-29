"""Request-scoped logging middleware."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from django.http import HttpRequest, HttpResponse

from apps.common.logging import set_request_id

logger = logging.getLogger("apps.request")

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"


class RequestLogMiddleware:
    """Assigns a request id and emits one structured line per request.

    The id is taken from an inbound ``X-Request-ID`` header when present (so a
    gateway or a load test can correlate), otherwise generated. It is echoed
    back on the response and attached to every log record and error envelope
    produced while handling the request.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        set_request_id(request_id)
        request.request_id = request_id

        started = time.perf_counter()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        response[RESPONSE_HEADER] = request_id

        user = getattr(request, "user", None)
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "user_id": getattr(user, "id", None) if user else None,
            },
        )
        return response
