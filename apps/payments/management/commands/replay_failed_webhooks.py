"""Re-process webhook events that failed or were never handled.

Every delivery is persisted before it is acted on, so nothing is lost when
processing crashes. This command is the manual counterpart to the Celery
retry task.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.payments.models import WebhookEvent, WebhookEventStatus
from apps.payments.services import mark_webhook_failed, process_webhook_event


class Command(BaseCommand):
    help = "Replay FAILED / unprocessed webhook events."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--dry-run", action="store_true", help="List what would be replayed."
        )

    def handle(self, *args, **options) -> None:
        events = WebhookEvent.objects.filter(
            status__in=[WebhookEventStatus.FAILED, WebhookEventStatus.RECEIVED]
        ).order_by("received_at")[: options["limit"]]

        if not events:
            self.stdout.write("Nothing to replay.")
            return

        for event in events:
            if options["dry_run"]:
                self.stdout.write(f"  would replay {event.event_id} ({event.status})")
                continue
            try:
                result = process_webhook_event(event)
                self.stdout.write(self.style.SUCCESS(f"  {event.event_id}: {result}"))
            except Exception as exc:
                mark_webhook_failed(event, f"{exc.__class__.__name__}: {exc}")
                self.stdout.write(self.style.ERROR(f"  {event.event_id}: {exc}"))
