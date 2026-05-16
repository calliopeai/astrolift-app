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
