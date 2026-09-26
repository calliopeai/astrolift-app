"""
HostnameClaim: the ledger of rendered hostnames (#2012).

``astrolift_registry.hostname_claims.hostname_label_refusal`` (#1930) refuses
a new app label that collides with another org's app *label* in a shared
zone. It never renders the hostname, so it cannot see a multi-workload app's
suffixed form (``<label>-<workload>.<zone>``) colliding with another org's
plain label, and it does nothing for a collision that predates the check.

This table is the ledger of what has actually been rendered: one row per
(managed zone, hostname) a live app/workload/environment holds. Unlike the
label check, its uniqueness is a real database constraint on the rendered
string, so a multi-workload suffix collides exactly like a plain label would.

Deliberately a new table rather than reusing ``astrolift_lifecycle.IngressRule``
(see ``astrolift_registry.hostname_claims`` module docstring for the reasoning):
IngressRule has no ``environment`` column, is exposed over GraphQL, and is read
by the cluster-decommission DNS sweep on the assumption that every row is a
real, rendered ingress rule; bending it into a claim ledger would either
starve that sweep of its historical (already-dormant) input shape or start
deleting DNS records nobody asked this change to touch.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class HostnameClaim(BaseCoreModel):
    managed_domain = models.ForeignKey(
        "astrolift_clusters.ManagedDomain",
        related_name="hostname_claims",
        on_delete=models.CASCADE,
    )
    hostname = models.CharField(max_length=255, db_index=True)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="hostname_claims",
        on_delete=models.CASCADE,
    )
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="hostname_claims",
        on_delete=models.CASCADE,
    )
    environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="hostname_claims",
        on_delete=models.CASCADE,
    )
    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="hostname_claims",
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["managed_domain", "hostname"],
                condition=models.Q(deleted_at__isnull=True),
                name="hostname_claim_unique_active_per_zone",
            ),
        ]
        indexes = [
            models.Index(fields=["registered_app"], name="hostname_claim_app_idx"),
            models.Index(fields=["environment"], name="hostname_claim_env_idx"),
        ]
