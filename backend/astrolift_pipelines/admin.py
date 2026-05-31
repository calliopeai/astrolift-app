from __future__ import annotations

from django.contrib import admin

from astrolift_pipelines.models import JobRun, Runner


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(Runner)
class RunnerAdmin(_AllObjectsAdmin):
    list_display = (
        "slug",
        "name",
        "organization",
        "status",
        "os",
        "arch",
        "version_string",
        "last_heartbeat_at",
        "deleted_at",
    )
    list_filter = ("status", "os", "arch", "organization")
    search_fields = ("slug", "name", "organization__slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version", "registration_token_hash", "api_key_hash")


@admin.register(JobRun)
class JobRunAdmin(_AllObjectsAdmin):
    list_display = (
        "guid",
        "job_name",
        "organization",
        "status",
        "claimed_by_runner",
        "started_at",
        "finished_at",
        "deleted_at",
    )
    list_filter = ("status", "organization")
    search_fields = ("job_name", "organization__slug")
    readonly_fields = ("guid", "created_at", "updated_at", "version")
