"""Send a correctly signed webhook to a running instance.

The point of this command is to make the idempotency guarantee easy to *see*::

    python manage.py send_test_webhook --payment PAY-9C22D1A0F3 --times 3

Three identical deliveries: the first is processed, the next two come back
``duplicate: true`` and change nothing.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.payments.gateway import EVENT_TYPE_TO_STATUS, SimulatedPaymentGateway
from apps.payments.models import Payment
from apps.payments.signature import SIGNATURE_HEADER, build_header


class Command(BaseCommand):
    help = "POST a signed payment webhook to a running server (idempotency demo)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--payment", required=True, help="Payment reference, e.g. PAY-...")
        parser.add_argument(
            "--type",
            default=None,
            choices=sorted(EVENT_TYPE_TO_STATUS),
            help="Event type. Defaults to one matching the payment's status.",
        )
        parser.add_argument(
            "--url",
            default="http://127.0.0.1:8000/payments/webhook/",
            help="Webhook URL of the running server.",
        )
        parser.add_argument(
            "--times", type=int, default=1, help="Deliver the same event N times."
        )
        parser.add_argument(
            "--event-id", default=None, help="Reuse a specific event id."
        )
        parser.add_argument(
            "--print-only", action="store_true", help="Print a curl command instead of sending."
        )

    def handle(self, *args, **options) -> None:
        try:
            payment = Payment.objects.select_related("booking").get(
                reference=options["payment"].upper()
            )
        except Payment.DoesNotExist as exc:
            raise CommandError(f"No payment with reference {options['payment']!r}.") from exc

        event_id = options["event_id"] or f"evt_{uuid.uuid4().hex[:24]}"
        payload = SimulatedPaymentGateway.build_event(
            payment, event_type=options["type"], event_id=event_id
        )
        body = json.dumps(payload).encode()
        header = build_header(settings.PAYMENT_WEBHOOK_SECRET, body)

        if options["print_only"]:
            self.stdout.write(
                f"curl -X POST {options['url']} \\\n"
                f"  -H 'Content-Type: application/json' \\\n"
                f"  -H '{SIGNATURE_HEADER}: {header}' \\\n"
                f"  -d '{body.decode()}'"
            )
            return

        for attempt in range(1, options["times"] + 1):
            request = urllib.request.Request(
                options["url"],
                data=body,
                headers={"Content-Type": "application/json", SIGNATURE_HEADER: header},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    parsed = json.loads(response.read().decode())
                    status_code = response.status
            except urllib.error.HTTPError as exc:
                parsed = exc.read().decode()
                status_code = exc.code
            except urllib.error.URLError as exc:
                raise CommandError(
                    f"Could not reach {options['url']}: {exc.reason}. Is the server running?"
                ) from exc

            style = self.style.SUCCESS if status_code < 400 else self.style.ERROR
            self.stdout.write(
                style(f"delivery {attempt}/{options['times']}: HTTP {status_code} {parsed}")
            )
