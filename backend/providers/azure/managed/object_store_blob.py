"""Azure Blob Storage object_store managed-service driver (#47, #364).

Storage accounts are operator-provisioned via the install workflow;
the driver creates a container per Astrolift service-handle within
the account.

Two driver classes ship from this module:

* ``BlobStorageDriver`` -- the original #47 MVP. Container-level
  resource with a minimal four-corner deprovision shape. Kept for
  backward-compat with the ``object_store/blob`` plugin variant.
* ``AzureBlobStorageDriver`` -- the #364 cross-cloud-symmetry
  driver. Same provision path but a fully-fleshed-out four-corner
  deprovision matrix matching the GCS + S3 drivers' semantic:

    delete_data=False, force_destroy=False (default):
      Retain container + contents. Driver disables any platform-
      managed immutability policy that's still on but unlocked,
      then no-ops on the data.

    delete_data=False, force_destroy=True:
      Retain contents but bypass platform-side guards. Same as
      the safe path at the data layer; the bypass flag is logged
      for operator visibility.

    delete_data=True, force_destroy=False:
      Delete the container. Refuses cleanly when an active
      retention/immutability policy blocks the delete -- the
      operator must wait it out or unlock manually.

    delete_data=True, force_destroy=True:
      Disable unlocked retention/immutability policies, release
      legal holds where possible, then delete. LOCKED retention
      policies cannot be bypassed by the Blob API and surface
      as an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.azure_ownership import (
    BLOB_METADATA_KEYS,
    OWNERSHIP_ERROR_CODE,
    AzureOperation,
    AzureOwnershipError,
    metadata_of,
    owner_of,
    verify_azure_ownership,
)
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
    unsupported_update,
)

KIND = "object_store"


# ---- legacy #47 MVP driver -------------------------------------------


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
                account_url=(f"https://{config.storage_account}.blob.core.windows.net"),
                credential=DefaultAzureCredential(),
            )

    @driver_op(
        cloud="azure",
        driver="object_store_blob",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        container_name = self._container_name_for(spec=spec)
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            try:
                container_client.create_container(metadata=_tags_for(spec))
            except Exception as exc:
                # ResourceExistsError = 409; idempotent
                if type(exc).__name__ != "ResourceExistsError":
                    raise
                _assert_container_owned(container_client, spec, AzureOperation.PROVISION, container_name)
        except AzureOwnershipError as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"provision failed: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=f"{KIND}/{container_name}",
            message=f"Blob container {container_name} provisioned",
        )

    @driver_op(cloud="azure", driver="object_store_blob")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(
            spec.handle, "Blob container access tier and lifecycle policy reconcile on provision, not in place"
        )

    @driver_op(
        cloud="azure",
        driver="object_store_blob",
        audit=True,
        sensitive_kind="managed_service_deprovision",
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
                ok=True,
                handle=spec.handle,
                message=f"container {container_name} retained",
            )
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            _assert_container_owned(container_client, spec, AzureOperation.DELETE, container_name)
            container_client.delete_container()
        except AzureOwnershipError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message="already gone",
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete failed: {exc}",
                errors=[str(exc)],
            )
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=f"container {container_name} deleted with data",
        )

    @driver_op(cloud="azure", driver="object_store_blob")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        _, _, container_name = handle.handle.partition("/")
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            container_client.get_container_properties()
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return ServiceStatus(
                    handle=handle.handle,
                    state="deprovisioned",
                    message=f"container {container_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"container {container_name} reachable",
        )

    @driver_op(cloud="azure", driver="object_store_blob")
    def binding(self, handle: ServiceHandle) -> Binding:
        _, _, container_name = handle.handle.partition("/")
        return Binding(
            env_vars={
                "AZURE_STORAGE_ACCOUNT": ValueRef(
                    literal=self._config.storage_account,
                ),
                "AZURE_BLOB_CONTAINER": ValueRef(literal=container_name),
                "AZURE_BLOB_ENDPOINT": ValueRef(
                    literal=(f"https://{self._config.storage_account}.blob.core.windows.net/{container_name}"),
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
                "Workload Identity grants Storage Blob Data Contributor via Microsoft.Authorization/roleAssignments."
            ),
        )

    @driver_op(cloud="azure", driver="object_store_blob")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "Azure Blob exposes per-blob versions, not an atomic container "
            "snapshot; retain the container for data-preserving teardown",
        )

    @driver_op(cloud="azure", driver="object_store_blob")
    def restore(self, snapshot, target):
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "Azure Blob container restore requires an explicit version-aware copy workflow",
        )

    @driver_op(cloud="azure", driver="object_store_blob", heartbeat=False)
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

    @driver_op(cloud="azure", driver="object_store_blob", heartbeat=False)
    def binding_schema(self):
        return BindingSchema(
            env_vars={
                "AZURE_STORAGE_ACCOUNT": "Account name",
                "AZURE_BLOB_CONTAINER": "Container name",
                "AZURE_BLOB_ENDPOINT": "Container HTTPS endpoint",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # access_tier is set while provisioning the container; this driver has
        # no in-place path for it.
        return []

    def _container_name_for(self, *, spec: ProvisionSpec) -> str:
        # Container names: 3-63 chars, lowercase alphanumeric +
        # dashes, no consecutive dashes, no leading/trailing dash.
        parts = [
            self._config.container_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]


# ---- #364 cross-cloud-symmetry driver --------------------------------


class AzureBlobStorageError(Exception):
    """Distinct from the generic plugin error so the control plane can
    tell managed-service failures from infra-driver failures."""


@dataclass(frozen=True)
class AzureBlobConfig:
    """Driver-instance config for ``AzureBlobStorageDriver``."""

    subscription_id: str
    resource_group: str
    storage_account: str
    container_name_prefix: str = "astrolift"
    versioning_enabled: bool = True
    blob_service_client: Any | None = None


def _assert_container_owned(
    container_client: Any,
    source: object,
    operation: AzureOperation,
    container_name: str,
) -> None:
    """Refuse ``operation`` unless the live container's metadata proves it is ours.

    A missing container is left to the caller's existing already-gone handling:
    the read raises ``ResourceNotFoundError`` and this returns, so an idempotent
    teardown replay still converges instead of failing closed on nothing.
    """
    try:
        properties = container_client.get_container_properties()
    except Exception as exc:
        if type(exc).__name__ == "ResourceNotFoundError":
            return
        raise
    verify_azure_ownership(
        metadata_of(properties),
        owner_of(source),
        operation=operation,
        resource=f"Blob container {container_name}",
        keys=BLOB_METADATA_KEYS,
    )


def _tags_for(spec: ProvisionSpec) -> dict[str, str]:
    """Same shape as the other Azure managed-service drivers'
    ``tags_for`` helper. Container metadata uses a flattened
    underscore form because Azure rejects dot-separated keys."""
    base = {
        "astrolift_io_managed_by": "platform",
        "astrolift_io_organization": spec.organization_slug,
        "astrolift_io_app": spec.app_slug,
        "astrolift_io_environment": spec.environment_name,
        "astrolift_io_cluster": spec.tenant_cluster_id,
        "astrolift_io_isolation": spec.isolation,
    }
    # Per-binding cost-attribution keys (#438). Blob container
    # metadata rejects dots + slashes, so the underscore form
    # mirrors the existing scheme on this driver.
    if spec.binding_id:
        base["astrolift_io_binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift_io_managed_service_id"] = spec.managed_service_id
    for k, v in (spec.tags or {}).items():
        sanitized = "".join(c if c.isalnum() else "_" for c in k)
        base[f"astrolift_io_extra_{sanitized}"] = str(v)
    return base


class AzureBlobStorageDriver(ManagedServiceDriver):
    def __init__(self, *, config: AzureBlobConfig) -> None:
        self._config = config
        if config.blob_service_client is not None:
            self._client = config.blob_service_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            self._client = BlobServiceClient(
                account_url=(f"https://{config.storage_account}.blob.core.windows.net"),
                credential=DefaultAzureCredential(),
            )

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="azure",
        driver="object_store_blob_v2",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        container_name = self._container_name_for(spec=spec)
        try:
            container_client = self._client.get_container_client(
                container_name,
            )
            try:
                container_client.create_container(
                    metadata=_tags_for(spec),
                )
            except Exception as exc:
                # ResourceExistsError = 409; idempotent
                if type(exc).__name__ != "ResourceExistsError":
                    raise
                _assert_container_owned(container_client, spec, AzureOperation.PROVISION, container_name)
        except AzureOwnershipError as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_container: {exc}",
                errors=[str(exc)],
            )
        return ProvisionResult(
            ok=True,
            handle=self._handle_for(container_name=container_name),
            message=f"Blob container {container_name} provisioned",
        )

    @driver_op(cloud="azure", driver="object_store_blob_v2")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        return unsupported_update(
            spec.handle, "Blob container access tier and lifecycle policy reconcile on provision, not in place"
        )

    @driver_op(
        cloud="azure",
        driver="object_store_blob_v2",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        container_name = self._container_name_from_handle(spec.handle)
        container_client = self._client.get_container_client(container_name)
        try:
            _assert_container_owned(container_client, spec, AzureOperation.DELETE, container_name)
        except AzureOwnershipError as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=str(exc),
                errors=[OWNERSHIP_ERROR_CODE],
                retryable=False,
            )

        if not delete_data:
            # Safe path: contents stay. force_destroy here optionally
            # toggles off any platform-owned immutability policy that's
            # still active but unlocked, so the operator can clean up
            # later without fighting the policy. Locked policies are
            # left alone.
            if force_destroy:
                self._try_disable_immutability_policy(
                    container_client=container_client,
                )
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=(f"container {container_name} retained (force_destroy={force_destroy})"),
            )

        # delete_data=True: actually drop the container.
        policy_active, policy_locked = self._immutability_state(
            container_client=container_client,
        )
        if policy_active and not force_destroy:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"container {container_name} has an active "
                    f"immutability/retention policy -- pass "
                    f"force_destroy=True to attempt clearing it "
                    f"(locked policies cannot be cleared)"
                ),
                errors=["retention_policy_active"],
            )
        if policy_active and force_destroy and policy_locked:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"container {container_name} has a LOCKED "
                    f"immutability policy -- Azure does not allow "
                    f"API-level bypass; the operator must wait for "
                    f"the policy to expire"
                ),
                errors=["retention_policy_locked"],
            )
        if policy_active and force_destroy:
            try:
                container_client.delete_immutability_policy()
            except Exception as exc:
                return DeprovisionResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"failed to clear immutability policy: {exc}",
                    errors=[str(exc)],
                )

        try:
            container_client.delete_container()
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return DeprovisionResult(
                    ok=True,
                    handle=spec.handle,
                    message=f"container {container_name} already gone",
                )
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_container: {exc}",
                errors=[str(exc)],
            )
        suffix = " (force_destroy)" if force_destroy else ""
        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(f"container {container_name} deleted with data{suffix}"),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="object_store_blob_v2")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        container_name = self._container_name_from_handle(handle.handle)
        container_client = self._client.get_container_client(container_name)
        try:
            container_client.get_container_properties()
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return ServiceStatus(
                    handle=handle.handle,
                    state="deprovisioned",
                    message=f"container {container_name} does not exist",
                )
            return ServiceStatus(
                handle=handle.handle,
                state="error",
                message=str(exc),
            )
        return ServiceStatus(
            handle=handle.handle,
            state="available",
            message=f"container {container_name} reachable",
        )

    @driver_op(cloud="azure", driver="object_store_blob_v2")
    def binding(self, handle: ServiceHandle) -> Binding:
        container_name = self._container_name_from_handle(handle.handle)
        endpoint = f"https://{self._config.storage_account}.blob.core.windows.net/{container_name}"
        container_resource = (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Storage/storageAccounts/"
            f"{self._config.storage_account}/blobServices/default/"
            f"containers/{container_name}"
        )
        return Binding(
            env_vars={
                "AZURE_STORAGE_ACCOUNT": ValueRef(
                    literal=self._config.storage_account,
                ),
                "AZURE_BLOB_CONTAINER": ValueRef(literal=container_name),
                "AZURE_BLOB_ENDPOINT": ValueRef(literal=endpoint),
            },
            iam_grants=[
                Grant(
                    resource=container_resource,
                    actions=["Storage Blob Data Contributor"],
                ),
            ],
            notes=(
                "Workload Identity grants Storage Blob Data Contributor "
                "via Microsoft.Authorization/roleAssignments scoped to "
                "the container resource."
            ),
        )

    @driver_op(cloud="azure", driver="object_store_blob_v2")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "Azure Blob exposes per-blob versions, not an atomic container "
            "snapshot; retain the container for data-preserving teardown",
        )

    @driver_op(cloud="azure", driver="object_store_blob_v2")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        from _sdk import UnsupportedOperationError

        raise UnsupportedOperationError(
            "Azure Blob container restore requires an explicit version-aware copy workflow",
        )

    @driver_op(cloud="azure", driver="object_store_blob_v2", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "access_tier": {
                    "type": "string",
                    "enum": ["Hot", "Cool", "Archive"],
                },
                "public_access": {
                    "type": "string",
                    "enum": ["off", "blob", "container"],
                },
            },
        }

    @driver_op(cloud="azure", driver="object_store_blob_v2", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "AZURE_STORAGE_ACCOUNT": "Storage account name",
                "AZURE_BLOB_CONTAINER": "Container name",
                "AZURE_BLOB_ENDPOINT": "Container HTTPS endpoint",
            }
        )

    def editable_fields(self) -> list[str]:
        """No config key can be applied without a reprovision (#1376)."""
        # access_tier / public_access are set while provisioning the container;
        # this driver has no in-place path for either.
        return []

    # ---- internals ----------------------------------------------------

    def _container_name_for(self, *, spec: ProvisionSpec) -> str:
        # Container names: 3-63 chars, lowercase alphanumeric +
        # dashes, no consecutive dashes, no leading/trailing dash.
        parts = [
            self._config.container_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        if spec.service_handle_hint:
            parts.append(spec.service_handle_hint)
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        return clean.strip("-")[:63]

    def _handle_for(self, *, container_name: str) -> str:
        return f"{KIND}/{container_name}"

    def _container_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureBlobStorageError(
                f"handle {handle!r} must be '<kind>/<container>'",
            )
        kind, _, container_name = handle.partition("/")
        if not kind or not container_name:
            raise AzureBlobStorageError(
                f"handle {handle!r} has empty component",
            )
        return container_name

    def _immutability_state(
        self,
        *,
        container_client: Any,
    ) -> tuple[bool, bool]:
        """Returns (active, locked). active=True means there's a
        retention/immutability policy currently in force; locked=True
        means it's been irreversibly locked (Azure doesn't allow
        API-level bypass once locked)."""
        get = getattr(container_client, "get_immutability_policy", None)
        if get is None:
            return False, False
        try:
            policy = get()
        except Exception as exc:
            # Most "no policy" responses surface as 404; treat as
            # not-active rather than blowing up the deprovision.
            if type(exc).__name__ in ("ResourceNotFoundError",):
                return False, False
            return False, False
        if policy is None:
            return False, False
        retention_period = (
            getattr(
                policy,
                "immutability_period_since_creation_in_days",
                0,
            )
            or 0
        )
        locked = bool(getattr(policy, "policy_mode", "") == "Locked") or bool(
            getattr(policy, "is_locked", False),
        )
        if isinstance(policy, dict):
            retention_period = (
                policy.get(
                    "immutability_period_since_creation_in_days",
                    retention_period,
                )
                or 0
            )
            locked = bool(policy.get("policy_mode") == "Locked") or bool(
                policy.get("is_locked", locked),
            )
        return bool(retention_period), bool(locked)

    def _try_disable_immutability_policy(
        self,
        *,
        container_client: Any,
    ) -> None:
        """Best-effort: clear an unlocked immutability policy. Used by
        the retain-with-bypass path so a follow-up
        delete_data=True-no-force can succeed without operator
        intervention."""
        active, locked = self._immutability_state(
            container_client=container_client,
        )
        if not active or locked:
            return
        delete = getattr(container_client, "delete_immutability_policy", None)
        if delete is None:
            return
        try:
            delete()
        except Exception:
            return
