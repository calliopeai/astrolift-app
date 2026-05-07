from __future__ import annotations

from django.contrib import admin

from astrolift_operations.models import (
    AuditEvent,
    Event,
    Notification,
    WebhookSubscription,
    WorkflowRun,
    WorkloadIdentityRole,
)


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Append-only models: admin shows them, never mutates them."""

    def has_add_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False

    def has_change_permission(self, *args, **kwargs):
        return False


@admin.register(Event)
class EventAdmin(_ReadOnlyAdmin):
    list_display = ("event_type", "organization", "occurred_at", "registered_app")
    list_filter = ("event_type",)
    readonly_fields = (
        "guid",
        "organization",
        "team",
        "project",
        "registered_app",
        "event_type",
        "payload",
        "occurred_at",
    )


@admin.register(AuditEvent)
class AuditEventAdmin(_ReadOnlyAdmin):
    list_display = ("action", "decision", "actor_kind", "organization", "occurred_at")
    list_filter = ("decision", "actor_kind")
    readonly_fields = (
        "guid",
        "organization",
        "occurred_at",
        "actor_kind",
        "actor_id",
        "actor_display",
        "action",
        "decision",
        "target_kind",
        "target_id",
        "target_slug",
        "target_parent_chain",
        "request_id",
        "request_ip",
        "request_user_agent",
        "request_session_age_seconds",
        "data",
        "reasoning",
    )


@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("url", "organization", "is_active", "failure_count", "last_delivery_at")
    list_filter = ("is_active",)


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ("workflow_kind", "workflow_id", "run_id", "status", "started_at")
    list_filter = ("status", "workflow_kind")
    search_fields = ("workflow_id", "run_id")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "read_at", "created_at")
    list_filter = ("kind",)


@admin.register(WorkloadIdentityRole)
class WorkloadIdentityRoleAdmin(admin.ModelAdmin):
    list_display = ("registered_app", "tenant_cluster", "service_account", "namespace")
    search_fields = ("role_arn", "service_account", "namespace")
