from django.contrib import admin

from apps.payments.models import Payment, WebhookEvent


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "reference", "booking", "amount", "status", "method",
        "provider_reference", "created_at", "processed_at",
    ]
    list_filter = ["status", "method", "created_at"]
    search_fields = ["reference", "provider_reference", "booking__reference", "idempotency_key"]
    list_select_related = ["booking"]
    readonly_fields = [
        "reference", "booking", "amount", "currency", "provider_reference",
        "idempotency_key", "provider_payload", "created_at", "updated_at", "processed_at",
    ]


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = [
        "event_id", "event_type", "status", "result", "attempts", "received_at",
    ]
    list_filter = ["status", "event_type"]
    search_fields = ["event_id", "provider_reference", "result", "last_error"]
    readonly_fields = [
        "event_id", "event_type", "provider_reference", "payload", "signature",
        "attempts", "result", "last_error", "received_at", "processed_at",
    ]

    def has_add_permission(self, request) -> bool:
        return False
