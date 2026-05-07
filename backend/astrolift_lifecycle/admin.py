from __future__ import annotations

from django.contrib import admin

from astrolift_lifecycle.models import (
    AppEnvironment,
    CommandRun,
    CustomDomain,
    Deployment,
    DeploymentLog,
    DeployToken,
    IngressRule,
    PreviewEnvironment,
    ProjectIngress,
    ScheduledJobRun,
)


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        if hasattr(self.model, "all_objects"):
            return self.model.all_objects.all()
        return super().get_queryset(request)


@admin.register(AppEnvironment)
class AppEnvironmentAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "name", "tenant_cluster", "deploys_paused")
    list_filter = ("deploys_paused",)
    search_fields = ("name", "registered_app__slug")


@admin.register(Deployment)
class DeploymentAdmin(_AllObjectsAdmin):
    list_display = (
        "registered_app",
        "app_environment",
        "trigger_kind",
        "status",
        "started_at",
        "ended_at",
    )
    list_filter = ("status", "trigger_kind")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(DeploymentLog)
class DeploymentLogAdmin(admin.ModelAdmin):
    list_display = ("deployment", "status", "occurred_at")
    list_filter = ("status",)
    readonly_fields = (
        "guid",
        "deployment",
        "status",
        "message",
        "detail",
        "by_user",
        "by_token_kind",
        "by_token_id",
        "occurred_at",
    )

    def has_add_permission(self, *args, **kwargs):
        return False

    def has_delete_permission(self, *args, **kwargs):
        return False

    def has_change_permission(self, *args, **kwargs):
        return False


@admin.register(DeployToken)
class DeployTokenAdmin(_AllObjectsAdmin):
    list_display = (
        "name",
        "registered_app",
        "is_revoked",
        "expires_at",
        "last_used_at",
    )
    list_filter = ("is_revoked",)
    search_fields = ("name",)


@admin.register(PreviewEnvironment)
class PreviewEnvironmentAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "pr_number", "branch", "status", "torn_down_at")
    list_filter = ("status",)
    search_fields = ("registered_app__slug", "branch", "commit_sha")


@admin.register(IngressRule)
class IngressRuleAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "subdomain", "hostname", "is_active")
    list_filter = ("is_active",)


@admin.register(ProjectIngress)
class ProjectIngressAdmin(_AllObjectsAdmin):
    list_display = ("project", "hostname", "is_active")
    list_filter = ("is_active",)


@admin.register(CustomDomain)
class CustomDomainAdmin(_AllObjectsAdmin):
    list_display = ("hostname", "registered_app", "validation_status", "is_active")
    list_filter = ("validation_status", "validation_method", "is_active")
    search_fields = ("hostname",)


@admin.register(ScheduledJobRun)
class ScheduledJobRunAdmin(_AllObjectsAdmin):
    list_display = ("workload", "app_environment", "status", "started_at", "exit_code")
    list_filter = ("status",)


@admin.register(CommandRun)
class CommandRunAdmin(_AllObjectsAdmin):
    list_display = ("registered_app", "invoked_by", "started_at", "exit_code")
    list_filter = ("registered_app",)
