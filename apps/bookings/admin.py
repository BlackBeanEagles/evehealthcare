from django.contrib import admin

from apps.bookings.models import Booking, BookingStatusChange


class StatusChangeInline(admin.TabularInline):
    model = BookingStatusChange
    extra = 0
    can_delete = False
    readonly_fields = ["from_status", "to_status", "source", "reason", "created_at"]

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = [
        "reference", "user", "centre_test", "scheduled_for", "amount", "status", "created_at",
    ]
    list_filter = ["status", "centre_test__centre__city", "created_at"]
    search_fields = ["reference", "user__email", "centre_test__test__code"]
    list_select_related = ["user", "centre_test__centre", "centre_test__test"]
    readonly_fields = ["reference", "amount", "created_at", "updated_at", "confirmed_at", "cancelled_at"]
    date_hierarchy = "scheduled_for"
    inlines = [StatusChangeInline]


@admin.register(BookingStatusChange)
class BookingStatusChangeAdmin(admin.ModelAdmin):
    list_display = ["booking", "from_status", "to_status", "source", "created_at"]
    list_filter = ["source", "to_status"]
    search_fields = ["booking__reference"]
    list_select_related = ["booking"]
