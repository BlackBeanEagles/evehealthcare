from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.payments.views import PaymentViewSet, PaymentWebhookView

app_name = "payments"

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = [
    # Registered before the router so it is not shadowed by the detail route.
    path("payments/webhook/", PaymentWebhookView.as_view(), name="payment-webhook"),
    *router.urls,
]
