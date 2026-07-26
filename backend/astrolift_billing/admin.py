from __future__ import annotations

from django.contrib import admin

from astrolift_billing.models import (
    Budget,
    CostSnapshot,
    Quota,
    QuotaIncreaseRequest,
    QuotaUsageSnapshot,
)


@admin.register(Quota)
class QuotaAdmin(admin.ModelAdmin):
    list_display = ("organization", "scope_kind", "scope_id", "resource", "current_usage", "hard_limit")
    list_filter = ("scope_kind", "resource")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "scope_kind",
        "scope_id",
        "amount_cents",
        "currency",
        "period",
        "current_spend_cents",
    )
    list_filter = ("scope_kind", "period", "currency")


@admin.register(CostSnapshot)
class CostSnapshotAdmin(admin.ModelAdmin):
    list_display = ("organization", "project", "registered_app", "taken_at", "by", "amount_cents", "source")
    list_filter = ("by", "source")
    readonly_fields = ("guid", "taken_at", "by", "amount_cents", "currency", "source")

    def has_add_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False

    def has_change_permission(self, *args, **kwargs):
        return False


@admin.register(QuotaUsageSnapshot)
class QuotaUsageSnapshotAdmin(admin.ModelAdmin):
    list_display = ("organization", "quota", "captured_at", "used", "limit")
    list_filter = ("captured_at",)
    readonly_fields = ("guid", "organization", "quota", "captured_at", "used", "limit")

    def has_add_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False

    def has_change_permission(self, *args, **kwargs):
        return False


@admin.register(QuotaIncreaseRequest)
class QuotaIncreaseRequestAdmin(admin.ModelAdmin):
    list_display = (
        "quota",
        "organization",
        "requested_factor",
        "status",
        "requested_by",
        "decided_by",
        "decided_at",
    )
    list_filter = ("status",)
    search_fields = ("reason", "decision_note")
    readonly_fields = ("guid", "created_at", "updated_at", "deleted_at")
