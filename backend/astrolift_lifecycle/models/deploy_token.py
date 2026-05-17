"""
DeployToken — bearer credential bound to one app, scoped narrowly.

Used by CI runners. Default 1y TTL, max 5y. Stored hashed; plaintext
is shown to the user once on creation. Default scope is
``["app.deploy"]``; rotation creates a new row and revokes the old.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class DeployToken(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="deploy_tokens",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=200)
    token_hash = models.CharField(max_length=128, db_index=True)
    token_last_4 = models.CharField(max_length=4, blank=True, default="")
    scopes = models.JSONField(default=list, blank=True)

    created_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="created_deploy_tokens",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Forensic columns (#425). Populated on every successful
    # ``alft_dt_`` bearer auth by the deploy-token middleware so an
    # operator investigating a leaked token can correlate it with the
    # last CI runner / IP that used it. Mirrors the ApiToken
    # last_used_ip / last_used_agent fields from #428.
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)
    last_used_agent = models.CharField(max_length=512, blank=True, default="")
    is_revoked = models.BooleanField(default=False)

    # Rotation grace window (#143). When ``rotate_deploy_token``
    # issues a new secret, the previous SHA-256 hash is parked here
    # for a configurable grace period so CI runners holding the old
    # token keep working until they're updated. ``last_rotated_at``
    # is the rotation event timestamp; ``previous_token_expires_at``
    # is the moment the old hash stops being accepted.
    previous_token_hash = models.CharField(max_length=128, blank=True, default="")
    previous_token_expires_at = models.DateTimeField(null=True, blank=True)
    last_rotated_at = models.DateTimeField(null=True, blank=True)
