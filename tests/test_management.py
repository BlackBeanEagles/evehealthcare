"""Management commands and the Celery tasks they share code with."""
from __future__ import annotations

import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.bookings.models import BookingStatus
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from apps.payments.gateway import SimulatedPaymentGateway
from apps.payments.models import Payment, PaymentStatus, WebhookEvent, WebhookEventStatus
from apps.payments.services import ingest_webhook_event
from tests.factories import PaymentFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def pending_payment(booking):
    return PaymentFactory(
        booking=booking,
        status=PaymentStatus.PENDING,
        provider_reference=f"sim_pay_{uuid.uuid4().hex[:20]}",
    )


class TestSeedCatalog:
    def test_seeds_centres_tests_prices_and_demo_users(self):
        call_command("seed_catalog", stdout=StringIO())

        assert DiagnosticCentre.objects.count() == 4
        assert DiagnosticTest.objects.count() == 9
        assert CentreTest.objects.count() == 26
        assert User.objects.filter(email="patient@example.com").exists()
        assert User.objects.get(email="admin@example.com").is_staff

    def test_is_safe_to_run_twice(self):
        call_command("seed_catalog", stdout=StringIO())
        call_command("seed_catalog", stdout=StringIO())

        assert DiagnosticCentre.objects.count() == 4
        assert CentreTest.objects.count() == 26

    def test_can_skip_the_demo_users(self):
        call_command("seed_catalog", "--no-users", stdout=StringIO())

        assert User.objects.count() == 0


class TestSendTestWebhook:
    def test_prints_a_ready_to_run_curl_command(self, pending_payment):
        out = StringIO()

        call_command(
            "send_test_webhook", "--payment", pending_payment.reference,
            "--print-only", stdout=out,
        )

        printed = out.getvalue()
        assert "curl -X POST" in printed
        assert "X-Webhook-Signature: t=" in printed
        assert pending_payment.reference in printed

    def test_rejects_an_unknown_payment(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("send_test_webhook", "--payment", "PAY-NOPE", "--print-only")


class TestReconcilePayments:
    def test_fails_a_stuck_payment(self, pending_payment, booking):
        Payment.objects.filter(pk=pending_payment.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )
        out = StringIO()

        call_command("reconcile_payments", "--older-than-minutes", "30", stdout=out)

        assert "Reconciled 1" in out.getvalue()
        booking.refresh_from_db()
        assert booking.status == BookingStatus.FAILED


class TestReplayCommand:
    def test_reports_when_there_is_nothing_to_do(self):
        out = StringIO()

        call_command("replay_failed_webhooks", stdout=out)

        assert "Nothing to replay" in out.getvalue()

    def test_dry_run_changes_nothing(self, pending_payment):
        event_body = SimulatedPaymentGateway.build_event(pending_payment)
        event, _ = ingest_webhook_event(
            event_id=event_body["id"], event_type=event_body["type"], payload=event_body
        )
        out = StringIO()

        call_command("replay_failed_webhooks", "--dry-run", stdout=out)

        event.refresh_from_db()
        assert "would replay" in out.getvalue()
        assert event.status == WebhookEventStatus.RECEIVED


class TestCeleryTasks:
    def test_the_processing_task_settles_a_payment(self, pending_payment, booking):
        from apps.payments.tasks import process_webhook_event_task

        event_body = SimulatedPaymentGateway.build_event(
            pending_payment, event_type="payment.succeeded"
        )
        event, _ = ingest_webhook_event(
            event_id=event_body["id"], event_type=event_body["type"], payload=event_body
        )

        result = process_webhook_event_task(event.pk)

        assert result == "booking_confirmed"
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_the_processing_task_tolerates_a_missing_event(self):
        from apps.payments.tasks import process_webhook_event_task

        assert process_webhook_event_task(999999) == "missing"

    def test_the_replay_task_drains_failed_events(self, pending_payment, booking):
        from apps.payments.tasks import replay_failed_webhooks_task

        event_body = SimulatedPaymentGateway.build_event(
            pending_payment, event_type="payment.succeeded"
        )
        event, _ = ingest_webhook_event(
            event_id=event_body["id"], event_type=event_body["type"], payload=event_body
        )
        WebhookEvent.objects.filter(pk=event.pk).update(status=WebhookEventStatus.FAILED)

        assert replay_failed_webhooks_task() == 1

        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_with_a_broker_configured_the_event_is_queued_not_processed_inline(
        self, settings, pending_payment, booking, monkeypatch
    ):
        """The production path: the request returns as soon as the delivery is
        stored, and a worker settles it."""
        from apps.payments import tasks

        settings.CELERY_TASK_ALWAYS_EAGER = False
        queued = []
        monkeypatch.setattr(
            tasks.process_webhook_event_task, "delay", lambda pk: queued.append(pk)
        )
        event_body = SimulatedPaymentGateway.build_event(
            pending_payment, event_type="payment.succeeded"
        )
        event, _ = ingest_webhook_event(
            event_id=event_body["id"], event_type=event_body["type"], payload=event_body
        )

        assert tasks.dispatch_webhook_event(event) == "queued"
        assert queued == [event.pk]

        booking.refresh_from_db()
        assert booking.status == BookingStatus.PENDING  # the worker has not run yet

    def test_a_dispatch_failure_marks_the_event_for_retry(
        self, pending_payment, monkeypatch
    ):
        from apps.payments import services, tasks

        monkeypatch.setattr(
            services,
            "apply_payment_outcome",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        event_body = SimulatedPaymentGateway.build_event(
            pending_payment, event_type="payment.succeeded"
        )
        event, _ = ingest_webhook_event(
            event_id=event_body["id"], event_type=event_body["type"], payload=event_body
        )

        assert tasks.dispatch_webhook_event(event) == "processing_failed"

        event.refresh_from_db()
        assert event.status == WebhookEventStatus.FAILED
        assert "boom" in event.last_error

    def test_the_reconciliation_task_runs(self, pending_payment):
        from apps.payments.tasks import reconcile_stale_payments_task

        Payment.objects.filter(pk=pending_payment.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )

        assert reconcile_stale_payments_task(30) == 1
