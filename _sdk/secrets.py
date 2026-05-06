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
