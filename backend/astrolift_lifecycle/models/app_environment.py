"""
AppEnvironment — a deploy target for one app on one cluster.

Each app has 1+ environments (production, staging, …). The env binds
the app to a TenantCluster + ManagedDomain, holds the snapshotted
deploy config, and gates promotion via ``required_approvals`` and
optional ABAC ``policy``.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AppEnvironment(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="environments",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=128)
    tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="environments",
        on_delete=models.PROTECT,
    )
    managed_domain = models.ForeignKey(
        "astrolift_clusters.ManagedDomain",
        related_name="environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    url = models.URLField(blank=True, default="")
    deploy_config = models.JSONField(default=dict, blank=True)
    deploys_paused = models.BooleanField(default=False)
    ingress_paused = models.BooleanField(default=False)
    required_approvals = models.PositiveIntegerField(default=0)
    approval_role_ids = models.JSONField(default=list, blank=True)
    abac_policy = models.ForeignKey(
        "astrolift_identity.Policy",
        related_name="environments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "name"],
                condition=models.Q(deleted_at__isnull=True),
                name="appenv_name_unique_active_per_app",
            ),
        ]
