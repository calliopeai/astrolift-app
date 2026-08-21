from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Job(BaseCoreModel):
    """A job definition within a pipeline, parsed from TOML.

    ``needs`` holds a JSON array of job_id strings that must complete
    before this job starts (DAG ordering).

    ``pipeline_run`` decides which of the two shapes in #1531 this row is.
    Null means the row is the pipeline's living definition, shared by every
    run and rewritten in place when the TOML changes. Set means the row is
    one run's snapshot, written once and never edited. Both are reachable
    only through :mod:`astrolift_pipelines.job_sync`, which is what keeps
    the two from being confused at a query site.
    """

    pipeline = models.ForeignKey(
        "astrolift_pipelines.Pipeline",
        related_name="jobs",
        on_delete=models.CASCADE,
    )
    # Null = the pipeline's living definition; set = this run's snapshot.
    pipeline_run = models.ForeignKey(
        "astrolift_pipelines.PipelineRun",
        related_name="job_definitions",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    job_id = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    runs_on = models.CharField(max_length=200, blank=True, default="")
    container_image = models.CharField(max_length=512, blank=True, default="")
    needs = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["pipeline", "job_id"]
        # Two constraints rather than one, because SQL does not consider
        # two NULLs equal: a single unique_together over a nullable column
        # would enforce nothing at all on the shared rows.
        constraints = [
            models.UniqueConstraint(
                fields=["pipeline", "job_id"],
                condition=models.Q(pipeline_run__isnull=True, deleted_at__isnull=True),
                name="job_unique_shared_per_pipeline",
            ),
            models.UniqueConstraint(
                fields=["pipeline_run", "job_id"],
                condition=models.Q(pipeline_run__isnull=False, deleted_at__isnull=True),
                name="job_unique_snapshot_per_run",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.pipeline_id}/{self.job_id}"
