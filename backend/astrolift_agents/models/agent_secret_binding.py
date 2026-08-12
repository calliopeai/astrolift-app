"""Operator-owned agent secret bindings and reusable bundle attachments."""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AgentSecretBindingOverride(BaseCoreModel):
    """Persistent override/tombstone layered over manifest secret refs.

    Manifest sync owns ``AgentEnvironmentSpec.secret_refs``. UI/GraphQL CRUD
    writes this layer so a repo re-sync cannot silently erase an operator's
    binding change. ``removed`` is a tombstone for a manifest-owned env var.
    """

    environment_spec = models.ForeignKey(
        "astrolift_agents.AgentEnvironmentSpec",
        related_name="secret_binding_overrides",
        on_delete=models.CASCADE,
    )
    env_var = models.CharField(max_length=255)
    uri = models.CharField(max_length=512, blank=True, default="")
    removed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["environment_spec", "env_var"],
                condition=models.Q(deleted_at__isnull=True),
                name="agent_secret_binding_override_unique_active",
            ),
        ]
        indexes = [
            models.Index(fields=["environment_spec", "env_var"], name="agent_secret_override_idx"),
        ]


class AgentSecretBundleRef(BaseCoreModel):
    """Attach a shared SecretBundle to an agent environment recipe."""

    environment_spec = models.ForeignKey(
        "astrolift_agents.AgentEnvironmentSpec",
        related_name="secret_bundle_refs",
        on_delete=models.CASCADE,
    )
    secret_bundle = models.ForeignKey(
        "astrolift_services.SecretBundle",
        related_name="agent_refs",
        on_delete=models.PROTECT,
    )
    environment = models.CharField(max_length=64, blank=True, default="default")
    prefix = models.CharField(max_length=64, blank=True, default="")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["environment_spec", "environment", "secret_bundle"],
                condition=models.Q(deleted_at__isnull=True),
                name="agent_secret_bundle_ref_unique_active",
            ),
        ]
        indexes = [
            models.Index(
                fields=["environment_spec", "environment", "position"],
                name="agent_secret_bundle_order_idx",
            ),
        ]
