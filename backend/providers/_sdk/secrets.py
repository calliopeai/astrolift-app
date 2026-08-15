"""SecretsBackend protocol -- read/write/list secret material out-of-cluster."""

from __future__ import annotations

from typing import Protocol


class SecretReferenceError(ValueError):
    """A secret reference cannot safely resolve to one value."""


def resolve_secret_reference(
    backend: SecretsBackend,
    reference: str,
    *,
    default_key: str | None = None,
    allow_single_value: bool = True,
) -> str | None:
    """Resolve ``path[#field]`` through a portable secrets backend.

    Field selection belongs to the portable reference contract rather than
    any one backend.  Cloud stores disagree on whether ``#`` is legal in a
    physical secret name, so passing the whole reference to ``get`` makes a
    bundle work on AWS while silently missing on Vault, GCP, and Azure.
    """

    if not isinstance(reference, str) or not reference:
        raise SecretReferenceError("secret reference must be a non-empty string")
    path, separator, field = reference.partition("#")
    if not path or (separator and (not field or "#" in field)):
        raise SecretReferenceError("secret reference must use path[#field]")

    payload = backend.get(path)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        if separator:
            raise SecretReferenceError("cannot select a field from a scalar secret")
        return str(payload)

    selected_key = field if separator else default_key
    if selected_key:
        if selected_key in payload:
            return str(payload[selected_key])
        if separator:
            return None
    if allow_single_value and len(payload) == 1:
        return str(next(iter(payload.values())))
    if not payload:
        return None
    raise SecretReferenceError("secret bundle contains multiple fields; select one with #field")


class SecretsBackend(Protocol):
    """Protocol for managing secrets in an external secrets store.

    Implementations: aws_secrets_manager, gcp_secret_manager,
    azure_key_vault, hashicorp_vault, kubernetes_secrets,
    external_secrets_operator.
    """

    # Operator-facing disclosure metadata. Secret values may be revealed only
    # when the provider explicitly opts in; write-only stores (for example,
    # GitHub Actions secrets) set ``supports_value_reveal = False`` and explain
    # the limitation. Older/custom drivers without these attributes retain the
    # structural ``get`` fallback in the control plane for compatibility.
    provider_id: str
    supports_value_reveal: bool
    value_reveal_limitation: str | None

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
