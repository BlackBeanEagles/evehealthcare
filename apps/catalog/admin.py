from django.contrib import admin

from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest


class OfferingInline(admin.TabularInline):
    model = CentreTest
    extra = 0
    autocomplete_fields = ["test"]


@admin.register(DiagnosticCentre)
class DiagnosticCentreAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "state", "pincode", "is_active", "created_at"]
    list_filter = ["is_active", "city", "state"]
    search_fields = ["name", "city", "address_line", "pincode"]
    inlines = [OfferingInline]


@admin.register(DiagnosticTest)
class DiagnosticTestAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "category", "report_eta_hours", "is_active"]
    list_filter = ["category", "is_active"]
    search_fields = ["code", "name", "description"]


@admin.register(CentreTest)
class CentreTestAdmin(admin.ModelAdmin):
    list_display = ["centre", "test", "price", "is_available"]
    list_filter = ["is_available", "centre__city"]
    search_fields = ["centre__name", "test__name", "test__code"]
    autocomplete_fields = ["centre", "test"]
    list_select_related = ["centre", "test"]
