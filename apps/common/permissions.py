"""Reusable permissions."""
from __future__ import annotations

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView


class IsAdminOrReadOnly(permissions.BasePermission):
    """Anyone authenticated may read the catalogue; only staff may change it.

    Diagnostic centres, tests and prices are operator-managed data, not
    user-generated content.
    """

    message = "Only staff users may modify the catalogue."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)
