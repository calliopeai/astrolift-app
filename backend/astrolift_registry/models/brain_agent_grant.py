"""A semantic layer shared with an agent (#2449), like a team share, viewer only.

A run nobody launched (a schedule, a loop, a webhook, a workflow stage) reads
the semantic layers its agent is granted here and also lists in its
``brains`` or ``brains_allowed``, through ``BrainRunBinding`` with the agent,
not a person, as the token's subject (``brain_runs``). Unsharing soft-deletes
the grant and revokes the bindings made from it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class BrainAgentGrant(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="brain_agent_grants",
        on_delete=models.CASCADE,
    )
    brain = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="agent_grants",
        on_delete=models.CASCADE,
    )
    agent = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="brain_grants",
        on_delete=models.CASCADE,
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["brain", "agent"],
                condition=models.Q(deleted_at__isnull=True),
                name="brain_agent_grant_one_live",
            ),
        ]

    def __str__(self) -> str:
        return f"BrainAgentGrant({self.brain_id} -> agent {self.agent_id})"
