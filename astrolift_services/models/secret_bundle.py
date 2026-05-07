"""
SecretBundle — a named bag of secrets in the platform secrets backend.

Bundles live at the team level; an app references zero or more bundles
per environment via ``AppSecretBundleRef`` with an optional key prefix.
The actual values live in the secrets backend (Vault / SecretsManager
/ GSM / KeyVault) — the model only stores the reference path.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel, BaseCoreModel


class SecretBundle(NamedBaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="secret_bundles",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="secret_bundles",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    backend_ref = models.CharField(max_length=512)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="secret_bundle_slug_unique_active_per_team",
            ),
        ]


class AppSecretBundleRef(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="secret_bundle_refs",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="secret_bundle_refs",
        on_delete=models.CASCADE,
    )
    secret_bundle = models.ForeignKey(
        "astrolift_services.SecretBundle",
        related_name="app_refs",
        on_delete=models.PROTECT,
    )
    prefix = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "app_environment", "secret_bundle"],
                condition=models.Q(deleted_at__isnull=True),
                name="appsecret_ref_unique_active",
            ),
        ]
