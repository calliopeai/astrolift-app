"""
ApiToken — bearer credential for humans (CLI), bots, scripts.

Tokens carry the user's role bindings, optionally narrowed by per-token
``scopes`` (a subset of the user's permission set). Stored hashed
(SHA-256) so a row leak isn't usable; plaintext is shown once at
creation.

Deploy tokens are a separate model (``DeployToken`` in T4) because
they have a different lifecycle and a fixed scope (one app).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ApiToken(BaseCoreModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="api_tokens",
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="api_tokens",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="api_tokens",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    name = models.CharField(max_length=200)
    token_hash = models.CharField(max_length=128, db_index=True)
    token_last_4 = models.CharField(max_length=4, blank=True, default="")
    scopes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_revoked = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "user"], name="apitoken_org_user_idx"),
        ]
