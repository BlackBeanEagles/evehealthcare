"""drf-spectacular hooks."""
from __future__ import annotations

from typing import Any

#: Paths that are documented. The assignment's literal `/payments/` and
#: `/payments/webhook/` routes are aliases of the versioned ones; documenting
#: both would duplicate every operation in the schema.
DOCUMENTED_PREFIXES = ("/api/v1/", "/health/")


def exclude_alias_paths(endpoints: list[tuple[str, Any, str, Any]], **kwargs):
    return [
        endpoint
        for endpoint in endpoints
        if endpoint[0].startswith(DOCUMENTED_PREFIXES)
    ]
