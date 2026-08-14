"""Shared Secret Manager storage for GCP managed-service drivers.

Drivers expose logical secret references while the GCP API stores physical
secret ids.  This adapter is the single mapping and raw-value lifecycle used
by both database and cache drivers, preventing an apparently valid binding
from pointing at a secret the deployment resolver cannot read.
"""

from __future__ import annotations

from typing import Any

from gcp.secrets import secret_id_for


class ManagedSecretStoreError(RuntimeError):
    pass


class ManagedSecretStore:
    def __init__(
        self,
        *,
        project_id: str,
        secret_id_prefix: str,
        client: Any,
    ) -> None:
        self._project_id = project_id
        self._secret_id_prefix = secret_id_prefix
        self._client = client

    def secret_id(self, path: str) -> str:
        return secret_id_for(path, prefix=self._secret_id_prefix)

    def resource_name(self, path: str) -> str:
        return f"projects/{self._project_id}/secrets/{self.secret_id(path)}"

    def get(self, path: str) -> str | None:
        try:
            response = self._client.access_secret_version(
                request={"name": f"{self.resource_name(path)}/versions/latest"},
            )
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise ManagedSecretStoreError(f"read secret {path}: {exc}") from exc
        payload = getattr(getattr(response, "payload", None), "data", None)
        if payload is None and isinstance(response, dict):
            payload = (response.get("payload") or {}).get("data")
        if not isinstance(payload, bytes):
            raise ManagedSecretStoreError(f"read secret {path}: response has no byte payload")
        return payload.decode("utf-8")

    def upsert(self, path: str, value: str, *, labels: dict[str, str] | None = None) -> str:
        current = self.get(path)
        if current == value:
            return path

        parent = f"projects/{self._project_id}"
        if current is None:
            try:
                self._client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": self.secret_id(path),
                        "secret": {
                            "replication": {"automatic": {}},
                            "labels": dict(labels or {}),
                        },
                    },
                )
            except Exception as exc:
                # A concurrent retry may have won the create.  Only that
                # conflict is safe to ignore; permission/network failures are
                # surfaced instead of being hidden by a broad suppress().
                if not _is_already_exists(exc):
                    raise ManagedSecretStoreError(f"create secret {path}: {exc}") from exc

        try:
            self._client.add_secret_version(
                request={
                    "parent": self.resource_name(path),
                    "payload": {"data": value.encode("utf-8")},
                },
            )
        except Exception as exc:
            raise ManagedSecretStoreError(f"write secret {path}: {exc}") from exc
        return path

    def delete(self, path: str) -> None:
        try:
            self._client.delete_secret(request={"name": self.resource_name(path)})
        except Exception as exc:
            if not _is_not_found(exc):
                raise ManagedSecretStoreError(f"delete secret {path}: {exc}") from exc


def _is_not_found(exc: Exception) -> bool:
    return type(exc).__name__ == "NotFound" or "not found" in str(exc).lower() or "404" in str(exc)


def _is_already_exists(exc: Exception) -> bool:
    return type(exc).__name__ == "AlreadyExists" or "alreadyexists" in str(exc).replace(" ", "").lower()


__all__ = ["ManagedSecretStore", "ManagedSecretStoreError"]
