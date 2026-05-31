from __future__ import annotations

from django.contrib import admin

from astrolift_pipelines.models import (
    Artifact,
    Job,
    JobRun,
    Pipeline,
    PipelineRun,
    Step,
    StepRun,
    Trigger,
)


class _AllObjectsAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(Pipeline)
class PipelineAdmin(_AllObjectsAdmin):
    list_display = ("name", "organization", "repo_url", "default_branch", "deleted_at")
    list_filter = ("default_branch",)
    search_fields = ("name", "repo_url")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(PipelineRun)
class PipelineRunAdmin(_AllObjectsAdmin):
    list_display = ("pipeline", "run_number", "trigger_kind", "status", "started_at", "finished_at")
    list_filter = ("trigger_kind", "status")
    search_fields = ("trigger_ref", "trigger_actor", "temporal_workflow_id")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Job)
class JobAdmin(_AllObjectsAdmin):
    list_display = ("pipeline", "job_id", "name", "runs_on", "deleted_at")
    search_fields = ("job_id", "name")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(JobRun)
class JobRunAdmin(_AllObjectsAdmin):
    list_display = ("pipeline_run", "job", "status", "started_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Step)
class StepAdmin(_AllObjectsAdmin):
    list_display = ("job", "position", "step_id", "uses", "deleted_at")
    search_fields = ("step_id", "uses")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(StepRun)
class StepRunAdmin(_AllObjectsAdmin):
    list_display = ("job_run", "step", "status", "exit_code", "started_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Artifact)
class ArtifactAdmin(_AllObjectsAdmin):
    list_display = ("name", "pipeline_run", "job_run", "size_bytes", "content_type")
    search_fields = ("name", "blob_key")
    readonly_fields = ("guid", "created_at", "updated_at", "version")


@admin.register(Trigger)
class TriggerAdmin(_AllObjectsAdmin):
    list_display = ("pipeline", "kind", "deleted_at")
    list_filter = ("kind",)
    readonly_fields = ("guid", "created_at", "updated_at", "version")
