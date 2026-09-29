"""Operational endpoints."""
from __future__ import annotations

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness/readiness probe: checks the database and the cache."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    @extend_schema(
        tags=["ops"],
        summary="Health check",
        responses={200: dict, 503: dict},
        auth=[],
    )
    def get(self, request: Request) -> Response:
        checks = {"database": self._check_database(), "cache": self._check_cache()}
        healthy = all(value == "ok" for value in checks.values())
        return Response(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _check_database() -> str:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as exc:  # pragma: no cover - failure path
            return f"error: {exc.__class__.__name__}"
        return "ok"

    @staticmethod
    def _check_cache() -> str:
        try:
            cache.set("health:ping", "pong", 5)
            if cache.get("health:ping") != "pong":
                return "error: readback mismatch"
        except Exception as exc:  # pragma: no cover - failure path
            return f"error: {exc.__class__.__name__}"
        return "ok"
