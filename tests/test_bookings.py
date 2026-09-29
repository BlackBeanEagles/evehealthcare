"""Creating, listing and cancelling bookings."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from tests.factories import BookingFactory, CentreTestFactory, DiagnosticTestFactory

pytestmark = pytest.mark.django_db

BOOKINGS = reverse("v1:bookings:booking-list")


def detail_url(booking) -> str:
    return reverse("v1:bookings:booking-detail", args=[booking.reference])


def cancel_url(booking) -> str:
    return reverse("v1:bookings:booking-cancel", args=[booking.reference])


class TestCreatingABooking:
    def test_requires_authentication(self, api_client, offering, future_slot):
        response = api_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 401

    def test_creates_a_pending_booking(self, auth_client, user, offering, future_slot):
        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == BookingStatus.PENDING
        assert body["reference"].startswith("BKG-")
        assert body["centre"]["id"] == offering.centre_id
        assert body["test"]["code"] == offering.test.code
        assert Booking.objects.filter(user=user).count() == 1

    def test_snapshots_the_price_onto_the_booking(self, auth_client, offering, future_slot):
        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.json()["amount"] == "500.00"

    def test_a_later_price_change_does_not_move_an_existing_booking(
        self, auth_client, offering, future_slot
    ):
        reference = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        ).json()["reference"]

        offering.price = Decimal("9999.00")
        offering.save(update_fields=["price"])

        booking = Booking.objects.get(reference=reference)
        assert booking.amount == Decimal("500.00")

    def test_accepts_a_centre_and_test_pair(self, auth_client, offering, future_slot):
        response = auth_client.post(
            BOOKINGS,
            {
                "centre": offering.centre_id,
                "test": offering.test_id,
                "scheduled_for": future_slot.isoformat(),
                "patient_notes": "Fasting since 10pm.",
            },
        )

        assert response.status_code == 201
        assert response.json()["offering_id"] == offering.id

    def test_rejects_both_forms_at_once(self, auth_client, offering, future_slot):
        response = auth_client.post(
            BOOKINGS,
            {
                "offering": offering.id,
                "centre": offering.centre_id,
                "test": offering.test_id,
                "scheduled_for": future_slot.isoformat(),
            },
        )

        assert response.status_code == 400

    def test_rejects_neither_form(self, auth_client, future_slot):
        response = auth_client.post(BOOKINGS, {"scheduled_for": future_slot.isoformat()})

        assert response.status_code == 400

    def test_rejects_a_test_the_centre_does_not_offer(
        self, auth_client, offering, future_slot
    ):
        elsewhere = DiagnosticTestFactory()

        response = auth_client.post(
            BOOKINGS,
            {
                "centre": offering.centre_id,
                "test": elsewhere.id,
                "scheduled_for": future_slot.isoformat(),
            },
        )

        assert response.status_code == 400

    def test_rejects_an_unknown_offering(self, auth_client, future_slot):
        response = auth_client.post(
            BOOKINGS, {"offering": 999999, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 400


class TestAppointmentTimeValidation:
    def test_rejects_a_time_in_the_past(self, auth_client, offering):
        past = timezone.now() - timedelta(days=1)

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": past.isoformat()}
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_rejects_a_time_inside_the_lead_window(self, auth_client, offering):
        too_soon = timezone.now() + timedelta(minutes=5)

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": too_soon.isoformat()}
        )

        assert response.status_code == 400

    def test_rejects_a_time_beyond_the_booking_horizon(self, auth_client, offering):
        far_future = timezone.localtime(timezone.now() + timedelta(days=200)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": far_future.isoformat()}
        )

        assert response.status_code == 400

    def test_rejects_a_time_outside_opening_hours(self, auth_client, offering):
        midnight = timezone.localtime(timezone.now() + timedelta(days=1)).replace(
            hour=3, minute=0, second=0, microsecond=0
        )

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": midnight.isoformat()}
        )

        assert response.status_code == 400
        assert "opens_at" in response.json()["error"]["details"]

    def test_rejects_a_malformed_datetime(self, auth_client, offering):
        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": "yesterday"}
        )

        assert response.status_code == 400


class TestAvailability:
    def test_rejects_an_unavailable_offering(self, auth_client, offering, future_slot):
        offering.is_available = False
        offering.save(update_fields=["is_available"])

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "offering_unavailable"

    def test_rejects_an_offering_at_a_deactivated_centre(
        self, auth_client, offering, future_slot
    ):
        offering.centre.is_active = False
        offering.centre.save(update_fields=["is_active"])

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 409

    def test_rejects_a_duplicate_of_an_active_booking(
        self, auth_client, user, offering, future_slot
    ):
        BookingFactory(user=user, centre_test=offering, scheduled_for=future_slot)

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "duplicate_booking"

    def test_allows_rebooking_a_slot_that_was_cancelled(
        self, auth_client, user, offering, future_slot
    ):
        BookingFactory(
            user=user, centre_test=offering, scheduled_for=future_slot,
            status=BookingStatus.CANCELLED,
        )

        response = auth_client.post(
            BOOKINGS, {"offering": offering.id, "scheduled_for": future_slot.isoformat()}
        )

        assert response.status_code == 201

    def test_two_patients_may_book_the_same_slot(
        self, auth_client, other_client, offering, future_slot
    ):
        payload = {"offering": offering.id, "scheduled_for": future_slot.isoformat()}

        assert auth_client.post(BOOKINGS, payload).status_code == 201
        assert other_client.post(BOOKINGS, payload).status_code == 201


class TestListingAndIsolation:
    def test_lists_only_your_own_bookings(self, auth_client, booking, other_user, offering):
        BookingFactory(user=other_user, centre_test=offering)

        body = auth_client.get(BOOKINGS).json()

        assert body["count"] == 1
        assert body["results"][0]["reference"] == booking.reference

    def test_another_users_booking_is_a_404_not_a_403(self, other_client, booking):
        response = other_client.get(detail_url(booking))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_an_unknown_reference_is_a_404(self, auth_client):
        response = auth_client.get(
            reverse("v1:bookings:booking-detail", args=["BKG-DOESNOTEXIST"])
        )

        assert response.status_code == 404

    def test_detail_includes_the_status_history(self, auth_client, booking):
        body = auth_client.get(detail_url(booking)).json()

        assert body["reference"] == booking.reference
        assert isinstance(body["status_changes"], list)

    def test_filters_by_status(self, auth_client, user, offering):
        BookingFactory(user=user, centre_test=offering, status=BookingStatus.CONFIRMED)
        BookingFactory(
            user=user, centre_test=offering, status=BookingStatus.PENDING,
            scheduled_for=timezone.now() + timedelta(days=3),
        )

        body = auth_client.get(BOOKINGS, {"status": "confirmed"}).json()

        assert body["count"] == 1
        assert body["results"][0]["status"] == BookingStatus.CONFIRMED

    def test_rejects_an_unknown_status_filter(self, auth_client):
        response = auth_client.get(BOOKINGS, {"status": "ALMOST"})

        assert response.status_code == 400
        assert response.json()["error"]["details"]["status"] == "ALMOST"

    def test_rejects_an_unparseable_date_filter(self, auth_client):
        assert auth_client.get(BOOKINGS, {"from": "soon"}).status_code == 400


class TestCancellation:
    def test_cancels_a_pending_booking(self, auth_client, booking):
        response = auth_client.post(cancel_url(booking), {"reason": "Changed my mind"})

        assert response.status_code == 200
        assert response.json()["status"] == BookingStatus.CANCELLED
        booking.refresh_from_db()
        assert booking.cancelled_at is not None

    def test_records_the_transition_in_the_audit_trail(self, auth_client, booking):
        auth_client.post(cancel_url(booking), {})

        change = booking.status_changes.first()
        assert (change.from_status, change.to_status) == (
            BookingStatus.PENDING,
            BookingStatus.CANCELLED,
        )
        assert change.source == "api"

    def test_cancelling_twice_is_a_conflict(self, auth_client, booking):
        auth_client.post(cancel_url(booking), {})

        response = auth_client.post(cancel_url(booking), {})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_state_transition"

    def test_cannot_cancel_another_users_booking(self, other_client, booking):
        response = other_client.post(cancel_url(booking), {})

        assert response.status_code == 404
        booking.refresh_from_db()
        assert booking.status == BookingStatus.PENDING

    def test_cannot_cancel_a_confirmed_booking_inside_the_cutoff(
        self, auth_client, user, offering
    ):
        imminent = timezone.localtime(timezone.now() + timedelta(minutes=45))
        booking = BookingFactory(
            user=user, centre_test=offering, scheduled_for=imminent,
            status=BookingStatus.CONFIRMED,
        )

        response = auth_client.post(cancel_url(booking), {})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "cancellation_window_closed"

    def test_can_cancel_a_confirmed_booking_before_the_cutoff(
        self, auth_client, user, offering, future_slot
    ):
        booking = BookingFactory(
            user=user, centre_test=offering, scheduled_for=future_slot,
            status=BookingStatus.CONFIRMED,
        )

        response = auth_client.post(cancel_url(booking), {})

        assert response.status_code == 200


def test_booking_references_are_unique(db, user, offering):
    references = {
        BookingFactory(
            user=user,
            centre_test=CentreTestFactory(),
            scheduled_for=timezone.now() + timedelta(days=index + 1),
        ).reference
        for index in range(20)
    }

    assert len(references) == 20
