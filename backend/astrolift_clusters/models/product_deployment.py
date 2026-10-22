"""A Calliope AI product the Deployment Manager runs on a tenant cluster (#2465).

The installer puts the Workbench hub and Zentinelle on a cluster with
Terraform; they never pass through Astrolift's projects or apps. Each
product namespace carries the installer's contract, a fixed set of labels
and annotations (calliope-installer
``docs/contracts/astrolift-product-deployments.md``), and the product scan
(``astrolift_clusters.product_deployments``) keeps one row per namespace
from them.

Read-only from Astrolift: every field the cluster supplies is the scan's to
write, and nothing here reaches the cluster. ``project`` is Astrolift's own
record about the deployment, kept across a reinstall of the same one.

The organization is not stored. It is the cluster's, or on a shared cluster
the assigned project's; an unassigned row on a shared cluster belongs to no
organization.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ProductDeployment(BaseCoreModel):
    """One product namespace the scan found on one cluster."""

    class Kind(models.TextChoices):
        WORKBENCH = "workbench"
        ZENTINELLE = "zentinelle"

    class Health(models.TextChoices):
        HEALTHY = "healthy"
        """Every declared workload has all its replicas ready."""
        DEGRADED = "degraded"
        """Every declared workload has a replica ready, not all have every one."""
        DOWN = "down"
        """A declared workload has no replica ready, or does not exist."""
        UNKNOWN = "unknown"
        """No workload data: none declared, or none could be read."""

    cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="product_deployments",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    deployment_name = models.CharField(
        max_length=63, blank=True, default="", help_text="calliope.ai/deployment: the install's name."
    )
    namespace = models.CharField(max_length=63)
    namespace_uid = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Tells a reinstall from the same install: a recreated namespace gets a new uid.",
    )
    managed_by = models.CharField(max_length=63, blank=True, default="", help_text="calliope.ai/managed-by.")
    contract_version = models.PositiveIntegerField(
        default=0, help_text="calliope.ai/product-contract; 0 when the label is not a number."
    )
    contract_supported = models.BooleanField(
        default=False,
        help_text="Whether this Astrolift reads that contract version. When false only the kind, "
        "deployment and namespace are read.",
    )
    product_version = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="calliope.ai/version, a release such as 0.1.60. Reserved by contract 1, so empty "
        "until the installer writes it.",
    )
    images = models.JSONField(
        default=dict, blank=True, help_text="Declared: component to image reference (calliope.ai/images)."
    )
    running_images = models.JSONField(
        default=dict,
        blank=True,
        help_text="Observed: component to the digests its declared workloads' containers run. Empty "
        "when the workloads could not be read; a component with no matching container maps to [].",
    )
    endpoints = models.JSONField(
        default=dict, blank=True, help_text="Name to absolute http(s) URL (calliope.ai/endpoints)."
    )
    workloads = models.JSONField(
        default=list,
        blank=True,
        help_text="The declared Deployments (calliope.ai/workloads), each with name, found, "
        "desired_replicas, ready_replicas and restart_count_24h. found is null when the "
        "workloads could not be read and false when the Deployment does not exist.",
    )
    integrations = models.JSONField(
        default=list,
        blank=True,
        help_text="Zentinelle only: the install record's enabled integration flags as of the last "
        "apply (calliope.ai/integrations). Informational.",
    )
    health = models.CharField(
        max_length=16,
        choices=Health.choices,
        default=Health.UNKNOWN,
        help_text="As of last_seen_at: a scan that cannot list the cluster leaves it as it was.",
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="product_deployments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Astrolift's own link, set in Astrolift, never from the cluster.",
    )
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(null=True, blank=True)
    gone_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a successful scan last stopped finding the namespace; cleared if it returns.",
    )
    last_scan_error = models.TextField(
        blank=True,
        default="",
        help_text="What the last scan could not read for this row; empty after a clean read.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("cluster", "namespace"),
                condition=models.Q(deleted_at__isnull=True),
                name="product_deployment_one_live_per_namespace",
            ),
        ]

    def __str__(self) -> str:
        return f"ProductDeployment({self.kind}, {self.namespace}, {self.health})"
