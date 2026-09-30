"""Background processing for webhook deliveries.

Webhooks are stored synchronously (so nothing is ever lost) and processed in
a task, which gives retries with backoff for free. When no broker is
configured the dispatcher runs the same function inline, so behaviour is
identical in a single-process deployment - only the timing differs.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

from apps.payments.models import WebhookEvent, WebhookEventStatus
from apps.payments.services import (
    mark_webhook_failed,
    process_webhook_event,
    reconcile_stale_payments,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, acks_late=True, ignore_result=False)
def process_webhook_event_task(self, event_pk: int) -> str:
    """Process one stored webhook event, retrying transient failures.

    The most common transient failure is the database itself being briefly
    unavailable, so nothing on the failure path may depend on it: loading the
    event sits inside the retry handling, and recording the failure is
    best-effort. Before this, a DB outage raised out of the bookkeeping and
    the retry was never scheduled.
    """
    try:
        event = WebhookEvent.objects.get(pk=event_pk)
        return process_webhook_event(event)
    except WebhookEvent.DoesNotExist:
        logger.error("webhook_event_missing", extra={"event_pk": event_pk})
        return "missing"
    except Exception as exc:
        _record_failure(event_pk, exc)
        countdown = settings.PAYMENT_WEBHOOK_RETRY_BACKOFF_SECONDS * (
            2 ** self.request.retries
        )
        logger.warning(
            "webhook_retry_scheduled",
            extra={"event_pk": event_pk, "attempt": self.request.retries + 1,
                   "countdown_s": countdown, "error": str(exc)},
        )
        raise self.retry(
            exc=exc,
            countdown=countdown,
            max_retries=settings.PAYMENT_WEBHOOK_MAX_RETRIES,
        )


def _record_failure(event_pk: int, exc: Exception) -> None:
    """Mark the event FAILED if the database will let us; never raise."""
    try:
        event = WebhookEvent.objects.only("pk", "event_id").get(pk=event_pk)
        mark_webhook_failed(event, f"{exc.__class__.__name__}: {exc}")
    except Exception:
        logger.exception("webhook_failure_not_recorded", extra={"event_pk": event_pk})


@shared_task
def replay_failed_webhooks_task(limit: int = 100) -> int:
    """Re-process events that exhausted their retries or died mid-flight."""
    events = WebhookEvent.objects.filter(
        status__in=[WebhookEventStatus.FAILED, WebhookEventStatus.RECEIVED]
    ).order_by("received_at")[:limit]
    replayed = 0
    for event in events:
        try:
            process_webhook_event(event)
            replayed += 1
        except Exception as exc:  # pragma: no cover - logged and skipped
            mark_webhook_failed(event, f"{exc.__class__.__name__}: {exc}")
    return replayed


@shared_task
def reconcile_stale_payments_task(older_than_minutes: int = 30) -> int:
    return reconcile_stale_payments(older_than_minutes)


def dispatch_webhook_event(event: WebhookEvent) -> str:
    """Hand a freshly stored event to Celery, or process it inline.

    Returning the concrete result when running inline keeps the endpoint
    useful for a reviewer poking at it with curl: the response says what the
    event actually did, not just that it was accepted.
    """
    if settings.CELERY_TASK_ALWAYS_EAGER:
        try:
            return process_webhook_event(event)
        except Exception as exc:
            mark_webhook_failed(event, f"{exc.__class__.__name__}: {exc}")
            return "processing_failed"

    process_webhook_event_task.delay(event.pk)
    return "queued"
