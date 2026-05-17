"""SecretsBackend protocol -- read/write/list secret material out-of-cluster."""

from __future__ import annotations

from typing import Any, Protocol


class SecretsBackend(Protocol):
    """Protocol for managing secrets in an external secrets store.

    Implementations: aws_secrets_manager, gcp_secret_manager,
    azure_key_vault, hashicorp_vault, kubernetes_secrets,
    external_secrets_operator.
    """

    def get(self, path: str) -> dict[str, str] | None: ...

    def upsert(self, path: str, kvs: dict[str, str]) -> None: ...

    def delete(self, path: str) -> None: ...

    def list(self, prefix: str) -> list[str]: ...

    # ----- Bundle key enumeration (#441) --------------------------
    # ``list`` (above) enumerates secret *paths* underneath a prefix.
    # ``list_keys`` enumerates the *keys inside a single secret
    # payload* (the JSON envelope every backend stores under one
    # path).  Returned for UI/metadata only -- values are never
    # surfaced through this call.
    #
    # The default implementation reads the payload and returns its
    # keys; concrete drivers that have a cheaper metadata-only
    # primitive should override.  A driver that genuinely can't
    # enumerate (e.g. an opaque external store) should raise
    # ``NotImplementedError`` so callers can fall back to the cached
    # ``SecretBundle.last_known_keys`` snapshot.
    def list_keys(self, path: str) -> list[str]:
        """Return the key names projected by the bundle at ``path``.

        Values are never returned.  An empty list means the bundle is
        empty or the payload has no key map (single-value payload).
        ``None`` from :py:meth:`get` is normalised to ``[]`` so the
        caller doesn't need to distinguish "not yet written" from
        "empty bundle" for the purpose of a key-count display.
        """
        payload = self.get(path)
        if not payload:
            return []
        return sorted(payload.keys())

    # TODO(#379): Implement on concrete drivers that need out-of-band setup
    # (CSI driver Helm install for GCP Secret Manager + Azure Key Vault,
    # KMS key creation for AWS Secrets Manager scoped per-cluster, Vault
    # auth-backend + policy setup for HashiCorp Vault). AWS Secrets
    # Manager and the kubernetes_secrets backend need no init and should
    # leave the default NotImplementedError in place -- the
    # provision_secrets_backend activity catches that and records a
    # ``skipped`` result without failing the workflow.
    def ensure_initialized(self) -> dict[str, Any]:
        """One-time bootstrap: CSI driver install, KMS key creation,
        Vault token setup. Idempotent. Default raises
        NotImplementedError so AWS Secrets Manager (which needs no
        init) degrades cleanly."""
        raise NotImplementedError("ensure_initialized not required for this backend")
