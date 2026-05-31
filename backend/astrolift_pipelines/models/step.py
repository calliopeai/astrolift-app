from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Step(BaseCoreModel):
    """A step within a job, parsed from TOML.

    Either ``uses`` (built-in action ref) or ``run`` (shell command)
    must be set — mutually exclusive at the DSL level; both are nullable
    here so partial TOML states can be persisted before validation.
    """

    job = models.ForeignKey(
        "astrolift_pipelines.Job",
        related_name="steps",
        on_delete=models.CASCADE,
    )
    position = models.PositiveIntegerField()
    step_id = models.CharField(max_length=200, blank=True, default="")
    uses = models.CharField(max_length=512, null=True, blank=True)
    run = models.TextField(null=True, blank=True)
    env = models.JSONField(default=dict, blank=True)
    with_params = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["job", "position"]

    def __str__(self) -> str:
        label = self.step_id or self.uses or f"pos={self.position}"
        return f"Job({self.job_id})/{label}"
