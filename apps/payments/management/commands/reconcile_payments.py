"""Fail payments that never reached a terminal state.

A PENDING payment older than the cutoff means we charged and never learned
the result; left alone it pins the booking in PENDING and blocks a retry.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.payments.services import reconcile_stale_payments


class Command(BaseCommand):
    help = "Mark stuck PENDING payments as FAILED and release their bookings."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=30,
            help="How long a payment may stay PENDING before it is failed.",
        )

    def handle(self, *args, **options) -> None:
        count = reconcile_stale_payments(options["older_than_minutes"])
        self.stdout.write(self.style.SUCCESS(f"Reconciled {count} stale payment(s)."))
