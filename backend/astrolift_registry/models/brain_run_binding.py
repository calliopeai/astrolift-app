"""An agent run that reads a brain (#2432): the grant behind its read tokens.

Made at launch for every brain the run reads (its agent's own and the ones
the launch asked for), once the person launching it could read each one. The
run's tokens are issued as that person and signed by a key of this binding's
own (``BrainTokenKey.run_binding``), published only while the run is in
flight. Ending or cancelling the run, archiving the brain, or the person
losing read access revokes the binding and deletes its key, so the edge
stops admitting the run's token.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class BrainRunBinding(BaseCoreModel):
    class Source(models.TextChoices):
        # Declared by the agent (``brains`` in its manifest).
        DEFAULT = "default"
        # Asked for by the launch (``brainSlugs``).
        REQUESTED = "requested"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="brain_run_bindings",
        on_delete=models.CASCADE,
    )
    agent_task = models.ForeignKey(
        "astrolift_agents.AgentTask",
        related_name="brain_bindings",
        on_delete=models.CASCADE,
    )
    brain = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="agent_run_bindings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Frozen at launch, so the run's history reads the same after the brain changes.
    slug = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=16, choices=Source.choices)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent_task", "slug"], name="brain_run_binding_one_per_slug"),
        ]

    def __str__(self) -> str:
        return f"BrainRunBinding({self.agent_task_id} -> {self.slug})"
