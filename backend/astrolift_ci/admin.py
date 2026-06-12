from __future__ import annotations

from django.contrib import admin

from astrolift_ci.models import CiJob, CiPipeline, CiRun, CiStep


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(CiPipeline)
class CiPipelineAdmin(_AllObjectsAdmin):
    list_display = ("slug", "organization", "status", "last_synced_at", "deleted_at")
    list_filter = ("status",)
    search_fields = ("name", "slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(CiRun)
class CiRunAdmin(_AllObjectsAdmin):
    list_display = ("guid", "pipeline", "status", "trigger_kind", "trigger_ref", "started_at", "ended_at")
    list_filter = ("status", "trigger_kind")
    search_fields = ("trigger_ref",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(CiJob)
class CiJobAdmin(_AllObjectsAdmin):
    list_display = ("name", "run", "status", "runner_pool", "started_at", "ended_at")
    list_filter = ("status",)
    search_fields = ("name",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(CiStep)
class CiStepAdmin(_AllObjectsAdmin):
    list_display = ("name", "job", "order", "status", "exit_code")
    list_filter = ("status",)
    search_fields = ("name",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")
