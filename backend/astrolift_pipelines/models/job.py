from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Job(BaseCoreModel):
    """A job definition within a pipeline, parsed from TOML.

    ``needs`` holds a JSON array of job_id strings that must complete
    before this job starts (DAG ordering).
    """

    pipeline = models.ForeignKey(
        "astrolift_pipelines.Pipeline",
        related_name="jobs",
        on_delete=models.CASCADE,
    )
    job_id = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    runs_on = models.CharField(max_length=200, blank=True, default="")
    container_image = models.CharField(max_length=512, blank=True, default="")
    needs = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["pipeline", "job_id"]

    def __str__(self) -> str:
        return f"{self.pipeline_id}/{self.job_id}"
