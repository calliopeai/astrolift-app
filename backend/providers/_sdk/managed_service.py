"""ManagedServiceDriver protocol -- provision backing services and emit binding env vars."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class ProvisionSpec:
    """Normalized request to provision a managed service.

    ``binding_id`` and ``managed_service_id`` carry the platform GUIDs
    the cost collector joins on. Drivers stamp them onto the cloud-
    side resource as ``astrolift.io/binding`` /
    ``astrolift.io/managed_service_id`` (per-cloud serialization
    handled in each driver's tag helper). Both default to empty so
    older call sites continue to compile; populated specs flow through
    from the workflow layer once #432 wiring lands.
    """

    organization_id: str
    organization_slug: str
    app_id: str
    app_slug: str
    environment_id: str
    environment_name: str
    tenant_cluster_id: str
    service_handle_hint: str
    size: str  # small | medium | large | xlarge | custom
    config: dict[str, Any] = field(default_factory=dict)
    desired_extensions: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    isolation: str = "shared"  # shared | dedicated
    binding_id: str = ""
    managed_service_id: str = ""


@dataclass(frozen=True)
class ProvisionResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)
    ready: bool = False
    """The backing resource is already active and needs no readiness
    poll. Capability-only services (e.g. Bedrock on-demand model
    access) provision no cloud resource, so they are ready the moment
    ``provision`` returns. The provision workflow skips its
    ``status``-poll wait loop when this is set. Drivers that create a
    resource with a ``creating`` -> ``available`` transition (RDS,
    ElastiCache, provisioned-throughput Bedrock) leave it ``False`` so
    the workflow waits for the endpoint."""


@dataclass(frozen=True)
class UpdateSpec:
    handle: str
    size: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)
    retryable: bool = True
    """Provider failures are retryable by default for compatibility with
    existing drivers. Permanent validation/safety refusals opt out."""


@dataclass(frozen=True)
class DeprovisionSpec:
    handle: str
    # The row's stored config, so a driver can recover origin/auxiliary
    # resource refs (e.g. the CloudFront OAC's origin bucket) on the
    # idempotent "already gone" path where the live resource that carried
    # them no longer exists. Other drivers ignore it.
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeprovisionResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)
    retryable: bool = True
    """Whether the workflow should call ``deprovision`` again.

    Retry by default for backwards compatibility: existing drivers use a
    failed result for transient provider errors. Drivers must opt out for a
    permanent validation or safety refusal so Temporal does not spend the
    cloud-delete window on an operator-actionable error.
    """


@dataclass(frozen=True)
class ServiceHandle:
    handle: str


@dataclass(frozen=True)
class ServiceStatus:
    handle: str
    state: str  # provisioning | available | updating | error | deprovisioning | deprovisioned
    message: str


@dataclass(frozen=True)
class ValueRef:
    """A value that is either a literal or a reference to a secret."""

    literal: str | None = None
    secret_ref: str | None = None


class VolumeSourceKind(StrEnum):
    """Portable Kubernetes attachment strategy for a managed filesystem."""

    EXISTING_PVC = "existing_pvc"
    CSI = "csi"
    DYNAMIC_PVC = "dynamic_pvc"


@dataclass(frozen=True)
class VolumeMount:
    """A managed filesystem attachment, never a credential container.

    Drivers emit either a claim they already provisioned or a static CSI
    volume description. ``secret_refs`` maps the key expected by the CSI
    driver to a reference in the install secrets backend; plaintext values
    are resolved only while materializing the consumer namespace Secret.
    ``secret_literals`` carries required non-secret identity fields, such as
    an Azure storage account name, that the CSI driver expects in that same
    Secret. Drivers must never place credentials in ``secret_literals``.
    """

    name: str
    mount_path: str
    sub_path: str | None = None
    source_kind: VolumeSourceKind = VolumeSourceKind.EXISTING_PVC
    protocol: str = ""
    claim_name: str = ""
    claim_namespace: str = ""
    storage_class_name: str = ""
    csi_driver: str = ""
    volume_handle: str = ""
    volume_attributes: dict[str, str] = field(default_factory=dict)
    secret_refs: dict[str, str] = field(default_factory=dict)
    secret_literals: dict[str, str] = field(default_factory=dict)
    mount_options: list[str] = field(default_factory=list)
    read_only: bool = False
    capacity: str = "1Gi"
    access_modes: tuple[str, ...] = ("ReadWriteMany",)
    workload_names: tuple[str, ...] = ()
    container_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", self.name) or len(self.name) > 63:
            raise ValueError("managed volume name must be a 1-63 character Kubernetes DNS label")
        if not self.mount_path.startswith("/"):
            raise ValueError("managed volume mount_path must be absolute")
        if self.sub_path and (self.sub_path.startswith("/") or ".." in self.sub_path.split("/")):
            raise ValueError("managed volume sub_path must be relative and cannot traverse parents")
        if not self.protocol:
            raise ValueError("managed volume protocol is required")
        if self.source_kind == VolumeSourceKind.EXISTING_PVC:
            if not self.claim_name or not self.claim_namespace:
                raise ValueError("existing_pvc managed volume requires claim_name and claim_namespace")
            if (
                self.storage_class_name
                or self.csi_driver
                or self.volume_handle
                or self.secret_refs
                or self.secret_literals
            ):
                raise ValueError("existing_pvc managed volume cannot declare dynamic or CSI fields")
        elif self.source_kind == VolumeSourceKind.CSI:
            if not self.csi_driver or not self.volume_handle:
                raise ValueError("csi managed volume requires csi_driver and volume_handle")
            if self.claim_name or self.claim_namespace or self.storage_class_name:
                raise ValueError("csi managed volume cannot declare claim or StorageClass fields")
        elif self.source_kind == VolumeSourceKind.DYNAMIC_PVC:
            if not self.storage_class_name:
                raise ValueError("dynamic_pvc managed volume requires storage_class_name")
            if self.claim_name or self.claim_namespace or self.volume_handle:
                raise ValueError("dynamic_pvc managed volume cannot declare an existing volume locator")
            if self.secret_refs or self.secret_literals:
                raise ValueError("dynamic_pvc managed volume credentials belong to its StorageClass")
        else:
            raise ValueError(f"unsupported managed volume source kind {self.source_kind!r}")
        if not self.capacity:
            raise ValueError("managed volume capacity is required")
        if not re.fullmatch(r"[1-9][0-9]*(?:[EPTGMK]i?|m)?", self.capacity):
            raise ValueError("managed volume capacity must be a positive canonical Kubernetes quantity")
        allowed_access_modes = {"ReadWriteOnce", "ReadOnlyMany", "ReadWriteMany", "ReadWriteOncePod"}
        if not self.access_modes or any(mode not in allowed_access_modes for mode in self.access_modes):
            raise ValueError("managed volume has an unsupported Kubernetes access mode")
        if self.source_kind == VolumeSourceKind.DYNAMIC_PVC and len(self.access_modes) != 1:
            raise ValueError("dynamic_pvc managed volume must request exactly one access mode")
        if any(not key or not ref for key, ref in self.secret_refs.items()):
            raise ValueError("managed volume secret_refs must map non-empty keys to backend references")
        if any(not key or not value for key, value in self.secret_literals.items()):
            raise ValueError("managed volume secret_literals must map non-empty keys to non-secret values")
        if self.secret_refs.keys() & self.secret_literals.keys():
            raise ValueError("managed volume secret refs and literals cannot define the same key")
        if any(not key or not value for key, value in self.volume_attributes.items()):
            raise ValueError("managed volume attributes must map non-empty keys to non-empty values")
        if any(not value for value in (*self.mount_options, *self.workload_names, *self.container_names)):
            raise ValueError("managed volume selectors and mount options cannot contain empty values")


@dataclass(frozen=True)
class Grant:
    resource: str
    actions: list[str]


@dataclass(frozen=True)
class Binding:
    """Connection material for a provisioned managed service."""

    env_vars: dict[str, ValueRef]
    pod_volume_mounts: list[VolumeMount] = field(default_factory=list)
    iam_grants: list[Grant] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class SnapshotHandle:
    handle: str
    snapshot_id: str
    created_at: str


@dataclass(frozen=True)
class BindingSchema:
    """Describes what env vars a driver produces."""

    env_vars: dict[str, str]  # key -> description


class ManagedServiceDriver(Protocol):
    """Protocol for provisioning and managing a backing service of a given kind.

    Each (kind, variant) tuple maps to a separate driver implementation.
    The driver is responsible for the full lifecycle: provision, update,
    deprovision, snapshot, restore.

    All lifecycle methods are idempotent. Provisioning probes existing
    resources and reconciles state.
    """

    def provision(self, spec: ProvisionSpec) -> ProvisionResult: ...

    def update(self, spec: UpdateSpec) -> UpdateResult: ...

    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        """Tear down the managed service.

        Two-axis safety design:

          ``delete_data`` controls what happens to PERSISTENT STATE:
            - False (default): retain data. Drivers do whatever the
              cloud's safest deletion path is — RDS / Aurora take a
              final snapshot; S3 / GCS / Blob keep the bucket
              contents; ElastiCache / Memorystore export a backup
              before delete; queues drain rather than purge; etc.
              Operator can restore later from the retained artifact.
            - True: irreversibly delete data. Skip final snapshot,
              empty bucket contents, purge queue, etc.

          ``force_destroy`` controls SAFETY GUARDS:
            - False (default): respect cloud-side deletion protection,
              refuse if active bindings exist, require bucket-empty
              before delete-bucket, etc. Errors out with a clear
              message the operator can act on.
            - True: bypass guards. Disable deletion-protection flags
              on the resource, empty buckets even when non-empty,
              terminate active sessions, ignore bindings (the calling
              workflow has already detached them or is consciously
              orphaning them).

        Four corners of the matrix:
          delete_data=False, force_destroy=False (default): graceful,
            keep state, respect guards. Refuses on hard cases.
          delete_data=True,  force_destroy=False: delete state, respect
            guards. The 'I want this gone but only if it's safe.'
          delete_data=False, force_destroy=True: keep state but bypass
            guards. Useful for orphan cleanup where final snapshot
            already exists.
          delete_data=True,  force_destroy=True: nuke. Equivalent to
            Terraform's ``force_destroy = true`` semantic.
        """
        ...

    def status(self, handle: ServiceHandle) -> ServiceStatus: ...

    def binding(
        self,
        handle: ServiceHandle,
        config: dict[str, Any] | None = None,
    ) -> Binding:
        """Emit connection material (env vars, IAM grants, volume mounts)
        for a provisioned managed service.

        ``handle`` carries the opaque per-driver resource identifier.

        ``config`` is the operator-supplied ``ManagedService.config``
        dict (the same shape ``provision`` / ``update`` accept). Drivers
        that surface header-style customization -- e.g. the SES driver's
        ``from_name`` / ``reply_to`` / ``return_path`` / ``env_senders``
        keys -- read those values here and fold them into the rendered
        binding. Drivers that don't need config-driven binding fields
        ignore the argument; the default of ``None`` (also tolerated as
        an empty dict via ``cfg = config or {}``) keeps every existing
        driver implementation backwards-compatible.
        """
        ...

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle: ...

    def restore(self, snapshot: SnapshotHandle, target: ProvisionSpec) -> ProvisionResult: ...

    def config_schema(self) -> dict[str, Any]: ...

    def binding_schema(self) -> BindingSchema: ...

    def editable_fields(self) -> list[str]:
        """Config keys that ``update()`` accepts without a full deprovision +
        reprovision cycle.

        Return ``["*"]`` (default) to allow any config key to be updated
        in-place. Return a specific list to restrict which keys the driver
        can apply live; keys NOT in the list require ``reprovisionManagedService``.
        Return ``[]`` if ALL config changes require full reprovision.
        """
        return ["*"]
