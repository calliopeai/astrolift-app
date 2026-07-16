"""SecretsBackend protocol -- read/write/list secret material out-of-cluster."""

from __future__ import annotations

from typing import Protocol


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

    def list_keys(self, path: str) -> list[str]:
        """Key names stored at ``path`` — never the values.

        Default implementation fetches the payload once via :meth:`get`
        and returns its keys sorted; a ``None`` payload normalises to
        ``[]``. Backends with a cheaper native key-listing API should
        override. Backs the secret-bundle known-keys refresh (#441 B).
        """
        payload = self.get(path)
        return sorted(payload) if payload else []

    def ensure_initialized(self) -> dict | None:
        """Bootstrap the secrets backend if it requires out-of-band setup
        (e.g. creating a KMS key, enabling a Vault secrets engine).

        Backends that require no initialisation (AWS Secrets Manager,
        GCP Secret Manager, Azure Key Vault) should raise
        ``NotImplementedError``; the cluster-bring workflow treats that as
        a successful no-op and stamps ``secrets_backend_provisioned_at``.

        Backends that do perform setup (Vault, custom) should return a
        ``dict`` with any relevant output, or ``None``.
        """
        raise NotImplementedError(f"{type(self).__name__} does not require out-of-band initialisation")
