"""
Budget — currency-denominated spend limit.

Distinct from Quota (resource-counted). The platform fires
notifications when ``current_spend_cents`` crosses each percentage in
``alerts_at_pct``.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Budget(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        PROJECT = "PROJECT"

    class Period(models.TextChoices):
        MONTHLY = "monthly"
        QUARTERLY = "quarterly"
        ANNUAL = "annual"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="budgets",
        on_delete=models.CASCADE,
    )
    scope_kind = models.CharField(max_length=16, choices=ScopeKind.choices)
    scope_id = models.BigIntegerField()
    amount_cents = models.BigIntegerField()
    currency = models.CharField(max_length=8, default="USD")
    period = models.CharField(max_length=16, choices=Period.choices, default=Period.MONTHLY)
    alerts_at_pct = models.JSONField(default=list, blank=True)
    current_spend_cents = models.BigIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["organization", "scope_kind", "scope_id"],
                name="budget_org_scope_idx",
            ),
        ]
