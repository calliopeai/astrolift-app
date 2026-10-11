"""Immutable builder archives, independent of Kubernetes object capacity."""

from django.db import models

from core.models.base import BaseCoreModel


class BuilderArtifact(BaseCoreModel):
    organization = models.ForeignKey("astrolift_identity.Organization", on_delete=models.CASCADE)
    dev_environment = models.ForeignKey("astrolift_lifecycle.DevEnvironment", on_delete=models.CASCADE)
    key = models.CharField(max_length=255, unique=True)
    content = models.BinaryField()
