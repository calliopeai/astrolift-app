"""ActionSafetyRule: one scope's say on one sensitive action (#2202).

Every gated action is on by default (see ``astrolift_identity.action_safety``):
it asks for confirmation and a fresh step-up verification, and an org admin may
approve their own. A row changes that for one scope. The organization's row can
turn a gate off or back on; team, app and user rows can only add to it, because
the strictest requirement from any applicable scope wins (Leo, 2026-10-11).
``None`` means "this row says nothing about it".
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class ActionSafetyRule(BaseCoreModel):
    class ScopeKind(models.TextChoices):
        ORG = "ORG"
        TEAM = "TEAM"
        APP = "APP"
        USER = "USER"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="action_safety_rules",
        on_delete=models.CASCADE,
    )
    scope_kind = models.CharField(max_length=8, choices=ScopeKind.choices)
    # The team, app or user the row is about; null for the organization itself.
    scope_id = models.BigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=64)
    enabled = models.BooleanField(null=True, blank=True)
    allow_self_approval = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "scope_kind", "scope_id", "action"],
                condition=models.Q(deleted_at__isnull=True),
                name="actionsafety_unique_scope_action_active",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover — admin display only
        return f"{self.action} {self.scope_kind}:{self.scope_id} enabled={self.enabled}"
