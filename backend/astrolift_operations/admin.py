from __future__ import annotations

from django.contrib import admin

from astrolift_operations.models import (
    AlertMute,
    AuditEvent,
    DeviceRegistration,
    Event,
    Notification,
    NotificationPreference,
    WebhookDelivery,
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
    list_display = (
        "url",
        "organization",
        "is_active",
        "format",
        "failure_count",
        "last_delivery_at",
        "secret_rotated_at",
    )
    list_filter = ("is_active", "format")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "subscription",
        "event_type",
        "status_code",
        "success",
        "is_test",
        "delivered_at",
    )
    list_filter = ("success", "is_test")
    search_fields = ("event_type", "delivery_id")
    readonly_fields = (
        "guid",
        "subscription",
        "event_type",
        "retry_attempt",
        "status_code",
        "latency_ms",
        "success",
        "is_test",
        "request_payload_excerpt",
        "response_body_excerpt",
        "error",
        "delivery_id",
        "delivered_at",
    )


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


@admin.register(AlertMute)
class AlertMuteAdmin(admin.ModelAdmin):
    list_display = ("rule", "organization", "ttl_until", "muted_by", "reason")
    list_filter = ("organization",)
    search_fields = ("rule__name", "reason")
    readonly_fields = ("guid", "created_at", "updated_at", "deleted_at")


@admin.register(DeviceRegistration)
class DeviceRegistrationAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "label", "driver", "registered_at", "stale_at")
    list_filter = ("platform", "driver")
    search_fields = ("label",)
    readonly_fields = ("guid", "device_token", "registered_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "channel", "event_kind", "enabled", "updated_at")
    list_filter = ("channel", "enabled")
    search_fields = ("event_kind",)
