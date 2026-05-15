"""Azure Blob Storage object_store managed-service driver (#47).

Storage accounts are operator-provisioned via the install workflow;
the driver creates a container per Astrolift service-handle within
the account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)


KIND = "object_store"


@dataclass(frozen=True)
class BlobStorageConfig:
    storage_account: str
    container_name_prefix: str = "astrolift"
    versioning_enabled: bool = True
    blob_service_client: Any | None = None


class BlobStorageDriver(ManagedServiceDriver):
    def __init__(self, *, config: BlobStorageConfig) -> None:
        self._config = config
        if config.blob_service_client is not None:
            self._client = config.blob_service_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            self._client = BlobServiceClient(
                account_url=(
                    f"https://{config.storage_account}.blob.core.windows.net"
                ),
                credential=DefaultAzureCredential(),
            )

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        container_name = self._container_name_for(spec=spec)
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            try:
                container_client.create_container(
                    metadata={
                        "astrolift_io_managed_by": "platform",
                        "astrolift_io_organization": spec.organization_slug,
                        "astrolift_io_app": spec.app_slug,
                        "astrolift_io_environment": spec.environment_name,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                # ResourceExistsError = 409; idempotent
                if type(exc).__name__ != "ResourceExistsError":
                    raise
        except Exception as exc:  # noqa: BLE001
            return ProvisionResult(
                ok=False, handle="",
                message=f"provision failed: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{container_name}",
            message=f"Blob container {container_name} provisioned",
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        return UpdateResult(
            ok=True, handle=spec.handle,
            message="Blob container access tier / lifecycle policies "
                    "are operator-managed",
        )

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        # force_destroy: Azure Blob has soft-delete + immutability
        # policies as guards. A real impl would disable both and
        # delete; this stub accepts the flag for Protocol symmetry.
        del force_destroy
        _, _, container_name = spec.handle.partition("/")
        if not delete_data:
            return DeprovisionResult(
                ok=True, handle=spec.handle,
                message=f"container {container_name} retained",
            )
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            container_client.delete_container()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=True, handle=spec.handle,
                    message="already gone",
                )
            return DeprovisionResult(
                ok=False, handle=spec.handle,
                message=f"delete failed: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True, handle=spec.handle,
            message=f"container {container_name} deleted with data",
        )

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, container_name = handle.handle.partition("/")
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            container_client.get_container_properties()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                return ServiceStatus(
                    handle=handle.handle, state="deprovisioned",
                    message=f"container {container_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle, state="error", message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle, state="available",
            message=f"container {container_name} reachable",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, container_name = handle.handle.partition("/")
        return Binding(
            env_vars={
                "AZURE_STORAGE_ACCOUNT": ValueRef(
                    literal=self._config.storage_account,
                ),
                "AZURE_BLOB_CONTAINER": ValueRef(literal=container_name),
                "AZURE_BLOB_ENDPOINT": ValueRef(
                    literal=(
                        f"https://{self._config.storage_account}"
                        f".blob.core.windows.net/{container_name}"
                    ),
                ),
            },
            iam_grants=[
                Grant(
                    resource=(
                        f"/subscriptions/SUB_ID/resourceGroups/RG"
                        f"/providers/Microsoft.Storage"
                        f"/storageAccounts/{self._config.storage_account}"
                        f"/blobServices/default/containers/{container_name}"
                    ),
                    actions=["Storage Blob Data Contributor"],
                ),
            ],
            notes=(
                "Workload Identity grants Storage Blob Data Contributor "
                "via Microsoft.Authorization/roleAssignments."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from datetime import UTC, datetime

        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"v-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}",
            created_at=datetime.now(tz=UTC).isoformat(),
        )

    def restore(self, snapshot, target):
        return ProvisionResult(
            ok=False, handle="",
            message="Blob restore via cross-account copy not implemented",
            errors=["not_implemented"],
        )

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "access_tier": {
                    "type": "string",
                    "enum": ["Hot", "Cool", "Archive"],
                },
            },
        }

    def binding_schema(self):
        return BindingSchema(env_vars={
            "AZURE_STORAGE_ACCOUNT": "Account name",
            "AZURE_BLOB_CONTAINER": "Container name",
            "AZURE_BLOB_ENDPOINT": "Container HTTPS endpoint",
        })

    def _container_name_for(self, *, spec: ProvisionSpec) -> str:
        # Container names: 3-63 chars, lowercase alphanumeric +
        # dashes, no consecutive dashes, no leading/trailing dash.
        parts = [
            self._config.container_name_prefix,
            spec.organization_slug, spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(
            c if (c.isalnum() or c == "-") else "-" for c in raw
        )
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]
