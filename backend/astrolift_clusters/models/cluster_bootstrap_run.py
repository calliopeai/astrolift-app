"""
ClusterBootstrapRun — one row per ``astro cluster bootstrap`` invocation.

The CLI calls ``recordClusterBootstrapRun`` after the helm install /
upgrade pass settles (success or failure) so the control plane has a
durable, queryable record of how a cluster was last brought up. The
row is the source of truth for the "Last bootstrap" card on the
cluster detail page; the matching ``cluster.bootstrap_run`` event
emitted by the mutation feeds the audit-trail surfaces (webhook
fan-out, in-app activity feed).

History is kept indefinitely — bootstraps are infrequent and the
audit value of "the chart version installed three months ago"
outlasts the operator memory of it. Cascade-deletes with the parent
cluster; we never hard-delete clusters in the platform so the cascade
only fires on the rare admin tear-down path.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ClusterBootstrapRun(BaseCoreModel):
    """One operator-initiated bootstrap pass against a cluster."""

    class Status(models.TextChoices):
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        on_delete=models.CASCADE,
        related_name="bootstrap_runs",
    )
    # The org that recorded the run. A shared cluster (organization NULL)
    # resolves for every org, so the cluster alone does not say whose run
    # this is (#1955).
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    chart_version = models.CharField(max_length=64, blank=True, default="")
    installed_releases = models.JSONField(default=list, blank=True)
    """List of ``{name, version}`` (and optional ``status``) dicts —
    one entry per Helm release the bootstrap created or upgraded.
    Free-form JSON; the GraphQL surface returns it as scalar JSON so
    the UI can render whatever the CLI version chose to report."""

    cli_version = models.CharField(max_length=64, blank=True, default="")
    host_info = models.JSONField(default=dict, blank=True)
    """OS / arch / kubectl-version / helm-version, etc. — captured for
    debugging "it worked on my machine" reports without forcing the
    operator to copy-paste from their terminal."""

    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(
                fields=["tenant_cluster", "-ended_at"],
                name="cbr_cluster_ended_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"ClusterBootstrapRun({self.tenant_cluster_id}, {self.status})"
