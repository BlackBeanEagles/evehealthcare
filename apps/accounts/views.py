"""Authentication endpoints."""
from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.serializers import (
    LoginSerializer,
    LogoutSerializer,
    RefreshSerializer,
    SignupSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["auth"],
    summary="Create an account",
    examples=[
        OpenApiExample(
            "Signup",
            value={
                "email": "asha@example.com",
                "full_name": "Asha Menon",
                "phone": "+919876543210",
                "password": "Str0ngPassphrase!",
                "password_confirm": "Str0ngPassphrase!",
            },
            request_only=True,
        )
    ],
)
class SignupView(generics.CreateAPIView):
    serializer_class = SignupSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def create(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        logger.info("user_signed_up", extra={"user_id": user.id})
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["auth"],
    summary="Log in and receive a JWT pair",
    examples=[
        OpenApiExample(
            "Login",
            value={"email": "asha@example.com", "password": "Str0ngPassphrase!"},
            request_only=True,
        )
    ],
)
class LoginView(generics.GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        logger.info("user_logged_in", extra={"user_id": serializer.user.id})
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


@extend_schema(tags=["auth"], summary="Exchange a refresh token for a new access token")
class RefreshView(TokenRefreshView):
    serializer_class = RefreshSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


@extend_schema(
    tags=["auth"],
    summary="Log out",
    description="Blacklists the supplied refresh token so it cannot be reused.",
    request=LogoutSerializer,
    responses={205: None},
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info("user_logged_out", extra={"user_id": request.user.id})
        return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(tags=["auth"], summary="Current user profile")
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user
