"""Signup, login, refresh, logout and profile."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from tests.factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db

User = get_user_model()

SIGNUP = reverse("v1:accounts:signup")
LOGIN = reverse("v1:accounts:login")
REFRESH = reverse("v1:accounts:refresh")
LOGOUT = reverse("v1:accounts:logout")
ME = reverse("v1:accounts:me")

VALID_SIGNUP = {
    "email": "Asha.Menon@Example.com",
    "full_name": "Asha Menon",
    "phone": "+919876543210",
    "password": "Str0ngPassphrase!",
    "password_confirm": "Str0ngPassphrase!",
}


class TestSignup:
    def test_creates_an_account(self, api_client):
        response = api_client.post(SIGNUP, VALID_SIGNUP)

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "asha.menon@example.com"  # normalised
        assert "password" not in body

        user = User.objects.get(email="asha.menon@example.com")
        assert user.check_password("Str0ngPassphrase!")
        assert user.password != "Str0ngPassphrase!"

    def test_rejects_a_duplicate_email_case_insensitively(self, api_client):
        UserFactory(email="asha.menon@example.com")

        response = api_client.post(SIGNUP, VALID_SIGNUP)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_rejects_mismatched_passwords(self, api_client):
        payload = VALID_SIGNUP | {"password_confirm": "SomethingElse1!"}

        response = api_client.post(SIGNUP, payload)

        assert response.status_code == 400
        assert "password_confirm" in response.json()["error"]["details"]

    @pytest.mark.parametrize("password", ["short1!", "password", "12345678"])
    def test_rejects_weak_passwords(self, api_client, password):
        payload = VALID_SIGNUP | {"password": password, "password_confirm": password}

        response = api_client.post(SIGNUP, payload)

        assert response.status_code == 400

    def test_rejects_a_malformed_email(self, api_client):
        response = api_client.post(SIGNUP, VALID_SIGNUP | {"email": "not-an-email"})

        assert response.status_code == 400

    def test_rejects_an_invalid_phone_number(self, api_client):
        response = api_client.post(SIGNUP, VALID_SIGNUP | {"phone": "not a phone"})

        assert response.status_code == 400

    def test_requires_the_mandatory_fields(self, api_client):
        response = api_client.post(SIGNUP, {})

        assert response.status_code == 400
        details = response.json()["error"]["details"]
        assert {"email", "full_name", "password"} <= set(details)


class TestLogin:
    def test_returns_a_token_pair_and_the_user(self, api_client, user):
        response = api_client.post(
            LOGIN, {"email": user.email, "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["access"] and body["refresh"]
        assert body["user"]["email"] == user.email

    def test_accepts_a_differently_cased_email(self, api_client, user):
        response = api_client.post(
            LOGIN, {"email": user.email.upper(), "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 200

    def test_rejects_a_wrong_password(self, api_client, user):
        response = api_client.post(LOGIN, {"email": user.email, "password": "nope"})

        assert response.status_code == 401

    def test_rejects_an_unknown_email(self, api_client):
        response = api_client.post(
            LOGIN, {"email": "ghost@example.com", "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 401

    def test_rejects_a_deactivated_account(self, api_client, user):
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = api_client.post(
            LOGIN, {"email": user.email, "password": DEFAULT_PASSWORD}
        )

        assert response.status_code == 401


class TestRefreshAndLogout:
    def test_refresh_returns_a_new_access_token(self, api_client, user):
        tokens = api_client.post(
            LOGIN, {"email": user.email, "password": DEFAULT_PASSWORD}
        ).json()

        response = api_client.post(REFRESH, {"refresh": tokens["refresh"]})

        assert response.status_code == 200
        assert response.json()["access"]

    def test_logout_blacklists_the_refresh_token(self, api_client, user):
        tokens = api_client.post(
            LOGIN, {"email": user.email, "password": DEFAULT_PASSWORD}
        ).json()
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        assert api_client.post(LOGOUT, {"refresh": tokens["refresh"]}).status_code == 205

        # The blacklisted token can no longer be exchanged.
        assert api_client.post(REFRESH, {"refresh": tokens["refresh"]}).status_code == 401

    def test_logout_rejects_a_garbage_token(self, auth_client):
        response = auth_client.post(LOGOUT, {"refresh": "not-a-token"})

        assert response.status_code == 400


class TestProfile:
    def test_requires_authentication(self, api_client):
        assert api_client.get(ME).status_code == 401

    def test_rejects_a_forged_token(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer forged.token.value")

        assert api_client.get(ME).status_code == 401

    def test_returns_the_caller(self, auth_client, user):
        response = auth_client.get(ME)

        assert response.status_code == 200
        assert response.json()["email"] == user.email

    def test_allows_updating_the_name(self, auth_client, user):
        response = auth_client.patch(ME, {"full_name": "Asha M Menon"})

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.full_name == "Asha M Menon"

    def test_ignores_attempts_to_self_promote(self, auth_client, user):
        auth_client.patch(ME, {"is_staff": True})

        user.refresh_from_db()
        assert user.is_staff is False


def test_errors_use_the_shared_envelope(api_client):
    response = api_client.post(SIGNUP, {})

    body = response.json()
    assert set(body["error"]) >= {"code", "message"}
    assert "request_id" in body
