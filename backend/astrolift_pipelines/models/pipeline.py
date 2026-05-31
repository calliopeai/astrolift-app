from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Pipeline(BaseCoreModel):
    """A pipeline definition scoped to an organization.

    The slug is unique per organization (soft-delete scoped).
    """

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="pipelines",
        on_delete=models.CASCADE,
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
