"""Authentication serializers."""
from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "is_staff", "date_joined"]
        read_only_fields = ["id", "email", "is_staff", "date_joined"]


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, min_length=8, max_length=128
    )
    password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "password", "password_confirm"]
        read_only_fields = ["id"]
        # The model's automatic UniqueValidator is case-sensitive and words its
        # error as "user with this email already exists."; validate_email
        # below replaces it with a case-insensitive check and a clearer message.
        extra_kwargs = {"email": {"validators": []}}

    def validate_email(self, value: str) -> str:
        normalised = value.strip().lower()
        if User.objects.filter(email=normalised).exists():
            # Deliberate: signup is the one place where confirming that an
            # account exists is unavoidable, so the message is explicit.
            raise serializers.ValidationError("An account with this email already exists.")
        return normalised

    def validate_full_name(self, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise serializers.ValidationError("Enter your full name.")
        return cleaned

    def validate_password(self, value: str) -> str:
        # Field-level, so a weak password is reported in the same response as
        # any other field error rather than only after everything else passes.
        candidate = User(
            email=str(self.initial_data.get("email", "")),
            full_name=str(self.initial_data.get("full_name", "")),
        )
        try:
            validate_password(value, user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Any:
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class LoginSerializer(TokenObtainPairSerializer):
    """Email + password to a JWT pair, with the user inlined in the response."""

    username_field = User.USERNAME_FIELD

    @classmethod
    def get_token(cls, user: Any) -> RefreshToken:
        token = super().get_token(user)
        token["email"] = user.email
        token["is_staff"] = user.is_staff
        return token

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs[self.username_field] = attrs.get(self.username_field, "").strip().lower()
        data = super().validate(attrs)
        return {
            "access": data["access"],
            "refresh": data["refresh"],
            "user": UserSerializer(self.user).data,
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)

    def validate_refresh(self, value: str) -> str:
        try:
            self.token = RefreshToken(value)
        except TokenError as exc:
            raise serializers.ValidationError("Refresh token is invalid or expired.") from exc
        return value

    def save(self, **kwargs: Any) -> None:
        # Blacklisting makes logout meaningful: the refresh token cannot be
        # exchanged for a new access token afterwards.
        self.token.blacklist()


class RefreshSerializer(TokenRefreshSerializer):
    """Token refresh with a short reuse window.

    Refresh tokens rotate: each use returns a new pair and blacklists the old
    token. On its own that races badly with real browsers. If a page reloads
    while a refresh is in flight, the server rotates the token but the
    response is never received, so the reloaded page presents the old,
    now-blacklisted token and the user is logged out. Two tabs refreshing at
    once hit the same wall.

    So a token presented again within ``JWT_REFRESH_REUSE_SECONDS`` of being
    rotated gets the *same* pair it was rotated to, instead of a 401. After
    the window, reuse is rejected as before. This is the "reuse interval"
    trade-off identity providers make: a replay inside a few seconds yields
    nothing the legitimate client didn't already receive.

    The cache must be shared across processes (Redis in docker-compose) for
    the window to hold between gunicorn workers.
    """

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        window = settings.JWT_REFRESH_REUSE_SECONDS
        key = "jwt:rotated:" + hashlib.sha256(attrs["refresh"].encode()).hexdigest()
        if window > 0:
            previous = cache.get(key)
            if previous is not None:
                return previous
        data = super().validate(attrs)
        if window > 0:
            cache.set(key, data, window)
        return data
