"""Root URL configuration.

Everything lives under ``/api/v1/``. The assignment names two endpoints
literally (``POST /payments/`` and ``POST /payments/webhook/``), so those two
paths are also mounted at the project root as thin aliases onto the same views.
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.common.views import HealthView
from apps.payments.views import PaymentViewSet, PaymentWebhookView

api_v1 = [
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.bookings.urls")),
    path("", include("apps.payments.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", HealthView.as_view(), name="health"),
    path("api/v1/", include((api_v1, "v1"), namespace="v1")),
    # OpenAPI / Swagger
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    # Literal paths from the assignment brief.
    path(
        "payments/",
        PaymentViewSet.as_view({"post": "create", "get": "list"}),
        name="payments-alias",
    ),
    path(
        "payments/webhook/",
        PaymentWebhookView.as_view(),
        name="payments-webhook-alias",
    ),
]
