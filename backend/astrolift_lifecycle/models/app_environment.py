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
    previewed_environment = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="previews",
        help_text=(
            "For a preview environment, the environment it is a preview OF "
            "(#1578 feature 2). Null on every non-preview environment, and "
            "null on a preview whose app has no non-preview environment on "
            "the same cluster to point at."
            "\n\n"
            "Exists because a preview could not previously answer that "
            "question at all: `PreviewEnvironment` carries the app, the PR "
            "number, the branch and its own `AppEnvironment`, and nothing "
            "linked back to a primary. So there was no declared source for "
            "which managed services a preview should get -- a preview owns "
            "zero `ManagedService` rows and inherits none, and the deploy "
            "render only synthesizes the bindings Secret when that set is "
            "non-empty, so a preview workload boots with no DB / redis / "
            "queue envelope."
            "\n\n"
            "`SET_NULL` rather than `CASCADE`: deleting a production "
            "environment must not delete the previews pointing at it. They "
            "become orphaned previews, which is recoverable; cascading "
            "would silently destroy live PR environments as a side effect "
            "of retiring an environment."
        ),
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
