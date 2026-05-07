"""
Container — one container under a workload.

Each workload has at least one container; exactly one carries
``is_primary=True``. Build context, ports, env, command/args, and the
healthcheck description live here. Probes (``startup_probe`` etc.) are
stored as full JSON snippets so the manifest schema can evolve without
churning columns.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Container(BaseCoreModel):
    class HealthcheckKind(models.TextChoices):
        NONE = "none"
        HTTP = "http"
        TCP = "tcp"
        EXEC = "exec"

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="containers",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=128)
    is_primary = models.BooleanField(default=False)
    image_ref = models.CharField(max_length=512, blank=True, default="")
    dockerfile_path = models.CharField(max_length=255, default="Dockerfile")
    build_context = models.CharField(max_length=255, default=".")
    port = models.PositiveIntegerField(default=0)
    command = models.JSONField(default=list, blank=True)
    args = models.JSONField(default=list, blank=True)
    env = models.JSONField(default=dict, blank=True)

    healthcheck_kind = models.CharField(
        max_length=16,
        choices=HealthcheckKind.choices,
        default=HealthcheckKind.NONE,
    )
    healthcheck_value = models.CharField(max_length=255, blank=True, default="")
    healthcheck_port = models.PositiveIntegerField(null=True, blank=True)

    startup_probe = models.JSONField(null=True, blank=True)
    readiness_probe = models.JSONField(null=True, blank=True)
    liveness_probe = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workload", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="container_name_unique_active_per_workload",
            ),
            models.UniqueConstraint(
                fields=["workload"],
                condition=models.Q(deleted_at__isnull=True, is_primary=True),
                name="container_one_primary_per_workload",
            ),
        ]
