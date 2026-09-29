"""Shared model building blocks."""
from __future__ import annotations

import uuid

from django.db import models


def generate_reference(prefix: str, length: int = 10) -> str:
    """Return a short, human-quotable public identifier, e.g. ``BKG-9F3A2C71B4``.

    Internal primary keys stay as integers; this is the only identifier the API
    exposes, so booking and payment records cannot be enumerated by guessing
    sequential ids.
    """
    return f"{prefix}-{uuid.uuid4().hex[:length].upper()}"


class TimeStampedModel(models.Model):
    """Adds ``created_at`` / ``updated_at`` to a model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
