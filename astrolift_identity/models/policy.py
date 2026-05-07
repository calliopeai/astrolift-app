"""
ABAC Policy.

ABAC complements RBAC with runtime predicates evaluated against the
request attributes (env, region, time of day, IP, MFA freshness, …).
ABAC can only deny — it never grants beyond the RBAC result.

The exact predicate language lives in ``conditions`` JSON; the
evaluator is implemented in ``core.permissions`` extensions in P4.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Policy(NamedBaseCoreModel):
    class ScopeLevel(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"
        APP = "APP"

    class Effect(models.TextChoices):
        ALLOW = "ALLOW"
        DENY = "DENY"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="policies",
        on_delete=models.CASCADE,
    )
    scope_level = models.CharField(max_length=16, choices=ScopeLevel.choices)
    scope_id = models.BigIntegerField(null=True, blank=True)
    effect = models.CharField(max_length=8, choices=Effect.choices, default=Effect.DENY)
    action_pattern = models.CharField(max_length=128, default="*")
    resource_pattern = models.JSONField(default=dict, blank=True)
    conditions = models.JSONField(default=list, blank=True)
    actor_pattern = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="policy_slug_unique_active_per_org",
            ),
        ]
