"""
Quota — hard / soft limit on a resource at a tenant scope.

Quotas are checked at admission (mutation entry) and surfaced in the
UI well before the hard limit. ``current_usage`` is a cached value
updated by a periodic reconciliation; the source of truth is always
the underlying enumeration (``RegisteredApp.objects.filter(...)``,
etc.).
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Quota(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"

    class Resource(models.TextChoices):
        APPS = "apps"
        PREVIEW_ENVS = "preview_envs"
        MANAGED_SERVICES = "managed_services"
        CPU = "cpu"
        GPU = "gpu"
        MEMORY = "memory"
        STORAGE = "storage"
        EGRESS_GB = "egress_gb"
        REQUESTS_PER_MONTH = "requests_per_month"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="quotas",
        on_delete=models.CASCADE,
    )
    scope_kind = models.CharField(max_length=16, choices=ScopeKind.choices)
    scope_id = models.BigIntegerField()
    resource = models.CharField(max_length=32, choices=Resource.choices)
    hard_limit = models.DecimalField(max_digits=20, decimal_places=4)
    soft_limit = models.DecimalField(max_digits=20, decimal_places=4)
    current_usage = models.DecimalField(max_digits=20, decimal_places=4, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "scope_kind", "scope_id", "resource"],
                condition=models.Q(deleted_at__isnull=True),
                name="quota_unique_active",
            ),
        ]
