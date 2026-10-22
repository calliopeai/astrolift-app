"""Typed read tokens for one brain (#2470): staged, then issued.

Read tokens carry the typed contract-1 claims (``brain_tokens.typed_claims``)
only for a brain a platform operator moved through both steps here, with
``manage.py brain_typed_tokens``; every other brain keeps the legacy shapes.

A live row stages the brain's consumer: its next deploy stamps the typed
verifier settings on its workloads (``brain_tokens.stamp_mcp_verification_env``),
and the issuer still mints legacy tokens. ``issuance_enabled_at`` makes every
read token for the brain typed; it is set only once each environment serving
the brain runs a deployment made after staging, with the operator's
``attestation`` that every serving replica accepts both shapes. Unstaging
soft-deletes the row. ``created_at`` and ``created_by`` record the staging.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class BrainTypedTokenRollout(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="brain_typed_token_rollouts",
        on_delete=models.CASCADE,
    )
    brain = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="typed_token_rollouts",
        on_delete=models.CASCADE,
    )
    issuance_enabled_at = models.DateTimeField(null=True, blank=True)
    issuance_enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    attestation = models.CharField(max_length=500, blank=True, default="")
    # When issuance last stopped: typed tokens minted before it live out their TTL.
    issuance_disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["brain"],
                condition=models.Q(deleted_at__isnull=True),
                name="brain_typed_token_rollout_one_live",
            ),
        ]

    def __str__(self) -> str:
        return f"BrainTypedTokenRollout({self.brain_id})"
