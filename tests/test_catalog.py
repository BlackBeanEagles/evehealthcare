"""Diagnostic centres, tests and per-centre pricing."""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from tests.factories import CentreTestFactory, DiagnosticCentreFactory, DiagnosticTestFactory

pytestmark = pytest.mark.django_db

CENTRES = reverse("v1:catalog:centre-list")
TESTS = reverse("v1:catalog:test-list")
OFFERINGS = reverse("v1:catalog:offering-list")


def centre_detail(centre) -> str:
    return reverse("v1:catalog:centre-detail", args=[centre.id])


class TestReadingTheCatalogue:
    def test_requires_authentication(self, api_client):
        assert api_client.get(CENTRES).status_code == 401

    def test_lists_centres_with_pagination_metadata(self, auth_client):
        DiagnosticCentreFactory.create_batch(3)

        response = auth_client.get(CENTRES)

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 3
        assert {"count", "page", "pages", "page_size", "results"} <= set(body)

    def test_honours_the_page_size_parameter(self, auth_client):
        DiagnosticCentreFactory.create_batch(5)

        body = auth_client.get(CENTRES, {"page_size": 2}).json()

        assert len(body["results"]) == 2
        assert body["pages"] == 3

    def test_filters_by_city(self, auth_client):
        DiagnosticCentreFactory(city="Mumbai")
        DiagnosticCentreFactory(city="Bengaluru")

        body = auth_client.get(CENTRES, {"city": "mumbai"}).json()

        assert body["count"] == 1
        assert body["results"][0]["city"] == "Mumbai"

    def test_filters_centres_by_test_code(self, auth_client):
        wanted = DiagnosticTestFactory(code="CBC")
        offered_here = CentreTestFactory(test=wanted)
        DiagnosticCentreFactory()  # a centre that does not offer it

        body = auth_client.get(CENTRES, {"test": "cbc"}).json()

        assert [row["id"] for row in body["results"]] == [offered_here.centre_id]

    def test_hides_deactivated_centres(self, auth_client):
        DiagnosticCentreFactory(is_active=False)

        assert auth_client.get(CENTRES).json()["count"] == 0

    def test_include_inactive_is_staff_only(self, auth_client, staff_client):
        DiagnosticCentreFactory(is_active=False)

        forbidden = auth_client.get(CENTRES, {"include_inactive": "true"})
        allowed = staff_client.get(CENTRES, {"include_inactive": "true"})

        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "permission_denied"
        assert allowed.json()["count"] == 1

    def test_rejects_an_unsupported_ordering(self, auth_client):
        response = auth_client.get(OFFERINGS, {"ordering": "drop table"})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"


class TestPricing:
    """Price belongs to the centre/test pair, not to the test."""

    def test_the_same_test_can_cost_different_amounts_at_different_centres(
        self, auth_client
    ):
        cbc = DiagnosticTestFactory(code="CBC")
        cheap = CentreTestFactory(test=cbc, price=Decimal("299.00"))
        dear = CentreTestFactory(test=cbc, price=Decimal("850.00"))

        body = auth_client.get(OFFERINGS, {"test": "CBC"}).json()

        prices = {row["centre"]["id"]: row["price"] for row in body["results"]}
        assert prices[cheap.centre_id] == "299.00"
        assert prices[dear.centre_id] == "850.00"

    def test_offerings_are_returned_cheapest_first(self, auth_client):
        cbc = DiagnosticTestFactory(code="CBC")
        CentreTestFactory(test=cbc, price=Decimal("850.00"))
        CentreTestFactory(test=cbc, price=Decimal("299.00"))
        CentreTestFactory(test=cbc, price=Decimal("500.00"))

        body = auth_client.get(OFFERINGS, {"test": "CBC"}).json()

        assert [row["price"] for row in body["results"]] == ["299.00", "500.00", "850.00"]

    def test_filters_by_max_price(self, auth_client):
        cbc = DiagnosticTestFactory(code="CBC")
        CentreTestFactory(test=cbc, price=Decimal("299.00"))
        CentreTestFactory(test=cbc, price=Decimal("850.00"))

        body = auth_client.get(OFFERINGS, {"test": "CBC", "max_price": "400"}).json()

        assert body["count"] == 1

    def test_rejects_a_non_numeric_max_price(self, auth_client):
        assert auth_client.get(OFFERINGS, {"max_price": "cheap"}).status_code == 400

    def test_centre_detail_includes_its_price_list(self, auth_client, offering):
        response = auth_client.get(centre_detail(offering.centre))

        assert response.status_code == 200
        body = response.json()
        assert body["offerings"][0]["price"] == "500.00"
        assert body["offerings"][0]["test"]["code"] == offering.test.code

    def test_centre_tests_endpoint_lists_available_offerings(self, auth_client, offering):
        url = reverse("v1:catalog:centre-tests", args=[offering.centre_id])

        body = auth_client.get(url).json()

        assert body["count"] == 1
        assert body["results"][0]["test"]["code"] == offering.test.code

    def test_a_centre_cannot_list_the_same_test_twice(self, staff_client, offering):
        response = staff_client.post(
            OFFERINGS,
            {"centre": offering.centre_id, "test": offering.test_id, "price": "100.00"},
        )

        assert response.status_code == 400


class TestWritePermissions:
    PAYLOAD = {
        "name": "New Centre",
        "address_line": "1 Road",
        "city": "Pune",
        "state": "Maharashtra",
        "pincode": "411001",
    }

    def test_a_patient_cannot_create_a_centre(self, auth_client):
        response = auth_client.post(CENTRES, self.PAYLOAD)

        assert response.status_code == 403

    def test_staff_can_create_a_centre(self, staff_client):
        response = staff_client.post(CENTRES, self.PAYLOAD)

        assert response.status_code == 201

    def test_a_patient_cannot_reprice_an_offering(self, auth_client, offering):
        url = reverse("v1:catalog:offering-detail", args=[offering.id])

        response = auth_client.patch(url, {"price": "1.00"})

        assert response.status_code == 403
        offering.refresh_from_db()
        assert offering.price == Decimal("500.00")

    def test_staff_can_reprice_an_offering(self, staff_client, offering):
        url = reverse("v1:catalog:offering-detail", args=[offering.id])

        response = staff_client.patch(url, {"price": "425.50"})

        assert response.status_code == 200
        offering.refresh_from_db()
        assert offering.price == Decimal("425.50")

    def test_staff_can_add_a_test_to_a_centre(self, staff_client, centre):
        new_test = DiagnosticTestFactory()

        response = staff_client.post(
            OFFERINGS, {"centre": centre.id, "test": new_test.id, "price": "750.00"}
        )

        assert response.status_code == 201

    def test_a_patient_cannot_create_a_test(self, auth_client):
        response = auth_client.post(TESTS, {"code": "X", "name": "X"})

        assert response.status_code == 403

    def test_staff_cannot_duplicate_a_test_code(self, staff_client):
        DiagnosticTestFactory(code="CBC")

        response = staff_client.post(TESTS, {"code": "cbc", "name": "Duplicate"})

        assert response.status_code == 400


class TestCaching:
    def test_a_repeated_read_is_served_from_cache(self, auth_client):
        DiagnosticCentreFactory()

        first = auth_client.get(CENTRES)
        second = auth_client.get(CENTRES)

        assert first["X-Cache"] == "MISS"
        assert second["X-Cache"] == "HIT"
        assert first.json() == second.json()

    def test_a_write_invalidates_the_cached_read(self, auth_client, staff_client):
        DiagnosticCentreFactory()
        assert auth_client.get(CENTRES).json()["count"] == 1
        assert auth_client.get(CENTRES)["X-Cache"] == "HIT"

        staff_client.post(
            CENTRES,
            {
                "name": "Fresh Centre", "address_line": "2 Road", "city": "Pune",
                "state": "Maharashtra", "pincode": "411002",
            },
        )

        refreshed = auth_client.get(CENTRES)
        assert refreshed["X-Cache"] == "MISS"
        assert refreshed.json()["count"] == 2

    def test_different_queries_do_not_share_a_cache_entry(self, auth_client):
        DiagnosticCentreFactory(city="Mumbai")
        DiagnosticCentreFactory(city="Pune")

        mumbai = auth_client.get(CENTRES, {"city": "Mumbai"}).json()
        pune = auth_client.get(CENTRES, {"city": "Pune"}).json()

        assert mumbai["results"][0]["city"] == "Mumbai"
        assert pune["results"][0]["city"] == "Pune"
