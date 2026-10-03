from __future__ import annotations

from django.contrib import admin

from astrolift_agents.models import (
    AgentTask,
    Brief,
    BriefSkillRef,
    DispatcherInstance,
    Skill,
    TaskToolDef,
    ToolDef,
    WorkloadToolDef,
)


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(Brief)
class BriefAdmin(_AllObjectsAdmin):
    list_display = ("content_hash", "organization", "status", "assembled_at", "deleted_at")
    list_filter = ("status",)
    search_fields = ("content_hash", "storage_key")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "content_hash")


@admin.register(Skill)
class SkillAdmin(_AllObjectsAdmin):
    list_display = ("slug", "organization", "version", "is_global", "is_active", "deleted_at")
    list_filter = ("is_global", "is_active")
    search_fields = ("name", "slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "content_hash")


@admin.register(ToolDef)
class ToolDefAdmin(_AllObjectsAdmin):
    list_display = ("slug", "skill", "adapter", "deleted_at")
    list_filter = ("adapter",)
    search_fields = ("name", "slug", "handler_ref")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(BriefSkillRef)
class BriefSkillRefAdmin(_AllObjectsAdmin):
    list_display = ("brief", "skill", "skill_version")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(WorkloadToolDef)
class WorkloadToolDefAdmin(_AllObjectsAdmin):
    list_display = ("workload", "tool_def")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(TaskToolDef)
class TaskToolDefAdmin(_AllObjectsAdmin):
    list_display = ("agent_run", "tool_def")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(DispatcherInstance)
class DispatcherInstanceAdmin(_AllObjectsAdmin):
    list_display = (
        "slug",
        "organization",
        "cloud",
        "backend",
        "status",
        "last_heartbeat_at",
        "deleted_at",
    )
    list_filter = ("cloud", "backend", "status")
    search_fields = ("name", "slug", "endpoint")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "api_key_hash")


@admin.register(AgentTask)
class AgentTaskAdmin(_AllObjectsAdmin):
    list_display = (
        "guid",
        "organization",
        "status",
        "dispatcher",
        "queued_at",
        "ended_at",
        "deleted_at",
    )
    list_filter = ("status",)
    search_fields = ("external_id", "telemetry_key")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "dispatch_execution")
