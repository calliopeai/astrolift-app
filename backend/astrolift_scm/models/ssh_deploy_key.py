"""
SshDeployKey — ed25519 keypair for direct git-over-SSH access.

Lives at one of two scopes:

* **Org-scoped** (``registered_app=None``): a shared deploy key used
  across multiple apps in the org. One public key to paste into all
  the relevant repos' deploy-key settings. Easier rotation.
* **App-scoped** (``registered_app`` set): per-app keypair, smaller
  blast radius if a single repo is compromised. Standard for
  security-sensitive deployments.

The operator picks at app registration; the UI defaults to org-level
for ergonomics with an opt-in per-app toggle.

Public key is shown in the UI for the operator to paste into
GitHub/GitLab. Private key bytes go through ``core.secrets`` (same
encryption story as SourceConnection.secret_ciphertext), so the
private material never lives plaintext in the row.

Fingerprint (sha256, base64-no-padding) is computed at generation
time and indexed for "is this the key the SCM tells us was just
used?" lookups in the future.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class SshDeployKey(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="ssh_deploy_keys",
        on_delete=models.CASCADE,
    )

    # NULL → org-scoped (shared); set → per-app key.
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="ssh_deploy_keys",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    name = models.CharField(max_length=200)
    public_key = models.TextField()  # OpenSSH-formatted single line
    fingerprint_sha256 = models.CharField(max_length=128, db_index=True)

    secret_backend_kind = models.CharField(max_length=32, default="local_fernet")
    private_key_ciphertext = models.BinaryField()

    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "fingerprint_sha256"],
                condition=models.Q(deleted_at__isnull=True),
                name="ssh_key_unique_per_org_fingerprint",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "registered_app"]),
        ]
