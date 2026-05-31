from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Artifact(BaseCoreModel):
    """A file artifact produced by a pipeline run and stored in tenant blob store."""

    pipeline_run = models.ForeignKey(
        "astrolift_pipelines.PipelineRun",
        related_name="artifacts",
        on_delete=models.CASCADE,
    )
    job_run = models.ForeignKey(
        "astrolift_pipelines.JobRun",
        related_name="artifacts",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    blob_key = models.CharField(max_length=1024)
    size_bytes = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["pipeline_run", "name"]

    def __str__(self) -> str:
        return f"PipelineRun({self.pipeline_run_id})/{self.name}"
