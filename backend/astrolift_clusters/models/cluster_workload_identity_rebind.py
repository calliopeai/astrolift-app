"""Moving a re-pointed cluster's per-app role trust to its new OIDC issuer (#2472).

``register_tenant_cluster`` upserts a cluster row by slug. When the upsert
points the row at a cluster with another OIDC issuer (the installer's region
change registers the target region's cluster under the old slug), every
per-app role minted for the row still trusts the old cluster's OIDC provider,
and pods on the new cluster cannot assume it. The command records one of these
rows and starts ``RebindClusterWorkloadIdentityWorkflow``, which moves each
role's trust and records here how far it got and what failed. The newest row
of a cluster is its current state; a newer row supersedes an older one still
running.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ClusterWorkloadIdentityRebind(BaseCoreModel):
    class State(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        on_delete=models.PROTECT,
        related_name="workload_identity_rebinds",
    )
    issuer = models.CharField(max_length=512)
    """The issuer the roles are moved to, host and path, no scheme."""

    previous_issuers = models.JSONField(default=list)
    """Every issuer the row named before, including the targets of earlier
    requests that never finished, since their roles may still trust them."""

    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    apps_total = models.PositiveIntegerField(default=0)
    apps_reconciled = models.PositiveIntegerField(default=0)
    failures = models.JSONField(default=list)
    """``{"app": "<org>/<app>", "role": ..., "error": ...}`` per app whose role was not moved."""

    error = models.TextField(blank=True, default="")
    """Why the run could not start or stopped before reaching every app."""

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    @property
    def workflow_id(self) -> str:
        return f"RebindClusterWorkloadIdentityWorkflow-{self.guid}"
