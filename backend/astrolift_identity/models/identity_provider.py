"""
IdentityProvider — the org's bound IdP.

Each Organization configures exactly one primary IdP; the kind drives
which login flow runs (OIDC PKCE, SAML SP-init, …). The provider
secret lives in the platform secrets backend; this row stores only the
reference (``client_secret_ref``).

See ``specs/03`` §3.1 and ``specs/04`` §3.4.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class IdentityProvider(BaseCoreModel):
    class Kind(models.TextChoices):
        OIDC = "oidc", "Generic OIDC"
        SAML = "saml", "SAML 2.0"
        COGNITO = "cognito", "Amazon Cognito"
        AUTH0 = "auth0", "Auth0"
        OKTA = "okta", "Okta"
        AZURE_AD = "azure_ad", "Azure AD / Entra ID"
        GOOGLE = "google", "Google Workspace"
        GITHUB = "github", "GitHub OAuth"
        LOCAL = "local", "Local accounts"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="identity_providers",
        on_delete=models.CASCADE,
    )
    # Multiple IdPs of the same kind are allowed (e.g. auth0-staging +
    # auth0-prod, or oidc-okta + oidc-keycloak as a fallback) so we
    # carry a human-readable label distinct from the enum.
    display_name = models.CharField(max_length=200, blank=True, default="")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    config = models.JSONField(default=dict, blank=True)
    metadata_url = models.URLField(blank=True, default="")
    oidc_discovery_url = models.URLField(blank=True, default="")
    client_id = models.CharField(max_length=256, blank=True, default="")
    client_secret_ref = models.CharField(max_length=512, blank=True, default="")
    is_default = models.BooleanField(default=False)

    @property
    def name(self) -> str:
        """Best-effort display label: explicit display_name → kind."""
        return self.display_name or self.kind

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(deleted_at__isnull=True, is_default=True),
                name="identity_provider_one_default_per_org",
            ),
        ]
