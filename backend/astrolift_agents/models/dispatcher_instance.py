"""
DispatcherInstance — a registered Dispatch Service endpoint.

One DispatcherInstance per cluster/cloud/region combination.  Unlike
TenantCluster (which is for app deployments), a DispatcherInstance is
the routing target for agent task dispatch.  It may be co-located on a
TenantCluster or run independently.

See issue #43 (Agent Dispatch Layer — DispatcherInstance model).
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class DispatcherInstance(BaseCoreModel):
    class Cloud(models.TextChoices):
        AWS = "aws"
        GCP = "gcp"
        AZURE = "azure"
        K8S_NATIVE = "k8s_native"
        LOCAL = "local"

    class Backend(models.TextChoices):
        K8S_JOB = "k8s_job"
        ECS_TASK = "ecs_task"
        LOCAL_DOCKER = "local_docker"

    class Status(models.TextChoices):
        PENDING = "pending"
        ACTIVE = "active"
        SUSPENDED = "suspended"
        REJECTED = "rejected"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="dispatcher_instances",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    # URL of the Dispatch Service's registration/heartbeat endpoint.
    endpoint = models.URLField()
    # SHA-256 of the scoped API key issued at registration.  Plaintext
    # is surfaced exactly once at registration; only the hash persists.
    api_key_hash = models.CharField(max_length=64, blank=True)
    cloud = models.CharField(max_length=32, choices=Cloud.choices)
    region = models.CharField(max_length=64, blank=True, default="")
    backend = models.CharField(max_length=32, choices=Backend.choices)
    # Key-value tags used by the routing function to match task requirements.
    capability_labels = models.JSONField(default=dict)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    # Duration (seconds) before the controller marks the instance SUSPENDED
    # when no heartbeat is received.
    heartbeat_ttl_seconds = models.IntegerField(default=30)
    # Nullable FK — a DispatcherInstance may or may not be co-located with
    # a TenantCluster.  They serve different routing purposes.
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="dispatcher_instances",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "status"],
                name="dispatcher_org_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"DispatcherInstance {self.slug} ({self.status})"
