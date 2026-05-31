from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Pipeline(BaseCoreModel):
    """A pipeline definition scoped to an organization.

    The slug is unique per organization (soft-delete scoped).

    ``registered_app`` is an optional association between a pipeline and
    a specific app — enables the app detail page to surface recent pipeline
    runs and auto-populates the workload parameter in astrolift-deploy@v1
    steps when set. Pipelines that do pure CI with no deploy step leave
    this null.
    """

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="pipelines",
        on_delete=models.CASCADE,
    )
    # Optional binding to a RegisteredApp. When the app is soft-deleted the
    # FK stays pointing at the deleted row (SET_NULL only fires on hard
    # delete, which never happens on Tracking models). If the app is
    # hard-deleted by accident the FK nulls out gracefully.
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="pipelines",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    name = models.SlugField(max_length=200)
    repo_url = models.CharField(max_length=512, blank=True, default="")
    default_branch = models.CharField(max_length=128, default="main")
    toml_path = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        ordering = ["organization", "name"]
        indexes = [
            models.Index(fields=["organization", "name"], name="pipeline_org_name_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}/{self.name}"

    def save(self, *args, **kwargs):
        if not self.toml_path:
            self.toml_path = f".astrolift/pipelines/{self.name}.toml"
        super().save(*args, **kwargs)
