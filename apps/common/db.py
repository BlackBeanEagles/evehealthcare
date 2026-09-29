"""Database helpers."""
from __future__ import annotations

from typing import TypeVar

from django.db import connection, models

QS = TypeVar("QS", bound=models.QuerySet)


def lock_rows(queryset: QS) -> QS:
    """Apply ``SELECT ... FOR UPDATE`` where the backend supports it.

    Postgres (the target database) takes a real row lock, which is what makes
    concurrent webhook deliveries for the same payment serialise. SQLite - used
    only for the zero-setup local test run - has no row-level locking and
    serialises writes at the database level instead, so the call is skipped
    there rather than raising ``NotSupportedError``.
    """
    if connection.features.has_select_for_update:
        return queryset.select_for_update()
    return queryset
