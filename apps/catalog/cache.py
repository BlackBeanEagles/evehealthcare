"""Catalogue read caching.

The catalogue is read on nearly every screen and written only by staff, which
makes it the one place where caching clearly pays. Rather than deleting
individual keys on write (fiddly and easy to get wrong with query-string
permutations), every cache key embeds a *version* number that is bumped
whenever any catalogue row changes. Stale entries are then simply never read
again and expire on their own TTL.
"""
from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from rest_framework.request import Request
from rest_framework.response import Response

logger = logging.getLogger(__name__)

VERSION_KEY = "catalog:version"


def catalog_version() -> int:
    version = cache.get(VERSION_KEY)
    if version is None:
        version = 1
        cache.set(VERSION_KEY, version, None)
    return int(version)


def bump_catalog_version() -> int:
    try:
        return int(cache.incr(VERSION_KEY))
    except ValueError:
        # Key absent or evicted: start a fresh generation.
        cache.set(VERSION_KEY, 1, None)
        return 1


def cache_key_for(request: Request) -> str:
    digest = hashlib.sha256(request.get_full_path().encode()).hexdigest()[:24]
    return f"catalog:v{catalog_version()}:{digest}"


class CachedListMixin:
    """Caches list responses for catalogue endpoints.

    Catalogue reads are identical for every authenticated user, so the cache
    key needs only the path and query string. Nothing user-specific is stored.
    """

    def list(self, request: Request, *args, **kwargs) -> Response:
        key = cache_key_for(request)
        cached = cache.get(key)
        if cached is not None:
            response = Response(cached)
            response["X-Cache"] = "HIT"
            return response

        response = super().list(request, *args, **kwargs)
        response["X-Cache"] = "MISS"
        # DRF responses are rendered lazily; hook the cache write onto render.
        response.add_post_render_callback(
            lambda resp: cache.set(key, resp.data, settings.CATALOG_CACHE_TTL_SECONDS)
        )
        return response
