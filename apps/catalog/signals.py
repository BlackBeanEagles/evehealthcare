"""Invalidate cached catalogue reads whenever catalogue data changes."""
from __future__ import annotations

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.catalog.cache import bump_catalog_version
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest

logger = logging.getLogger(__name__)


@receiver(post_save, sender=DiagnosticCentre)
@receiver(post_save, sender=DiagnosticTest)
@receiver(post_save, sender=CentreTest)
@receiver(post_delete, sender=DiagnosticCentre)
@receiver(post_delete, sender=DiagnosticTest)
@receiver(post_delete, sender=CentreTest)
def invalidate_catalog_cache(sender, **kwargs) -> None:
    version = bump_catalog_version()
    logger.debug(
        "catalog_cache_invalidated",
        extra={"model": sender.__name__, "catalog_version": version},
    )
