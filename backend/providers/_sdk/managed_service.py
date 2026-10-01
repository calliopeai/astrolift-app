"""ManagedServiceDriver protocol -- provision backing services and emit binding env vars."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelConsumer:
    subscription_id: str
    namespace: str
    app_slug: str
    environment_name: str
    credential_ref: str
    workload_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClusterModelPlacement:
    organization_id: str
    cluster_id: str
    managed_service_id: str
    revision: int = 0
    consumers: tuple[ModelConsumer, ...] = ()


@dataclass(frozen=True)
class ProvisionSpec:
    """Normalized request to provision a managed service.

    ``managed_service_id`` is the platform GUID cost attribution joins
    on. ``build_provision_spec`` populates it, drivers stamp it onto the
    cloud resource, and all three billing collectors group by it.

    ``binding_id`` does not work that way and has never been populated.
    The sentence here used to promise it would "flow through from the
    workflow layer once #432 wiring lands"; #432 is closed, and #1419
    moved attribution onto the managed-service id instead. Sixty-eight
    driver sites still stamp ``astrolift.io/binding`` when it is
    non-empty, and it never is, so no live resource carries that tag
    (#1470).

    It is left in place rather than removed because the ownership
    verifier and the adoption path both branch on it, and both branches
    wake together the day something populates it — but nothing does
    today, and a reader should not take its presence for a working
    join key.
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
    recorded_handle: str = ""
    """The handle the platform already records for this service (the row's
    ``backend_ref``), empty for one that never finished provisioning.

    A re-provision reaches the driver as a fresh spec, so a driver whose name
    derivation changed would otherwise derive a new name for a live service and
    create a second, empty resource beside it. Drivers that changed one keep a
    service on the name its record holds (#2086)."""
    recorded_handle_exclusive: bool = False
    """Whether ``recorded_handle`` is this service's and no other live one's.
    See ``UpdateSpec.recorded_handle_exclusive``."""
    cluster_model: ClusterModelPlacement | None = None
    """Verified third-owner placement and desired subscriber credential snapshot."""
    recorded_container_exclusive: bool = False
    """Internal platform proof that no other live/unreconciled service can occupy
    this exact driver/project/container. A provider must independently check
    its actual complete child-resource set before resizing/deleting a container."""


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
    # The same identity ``ProvisionSpec`` stamps onto the cloud resource, so a
    # driver can prove the thing behind ``handle`` is still the thing it
    # created before mutating it (#1365). A handle is only a name, and names
    # collide.
    binding_id: str = ""
    managed_service_id: str = ""
    recorded_handle_exclusive: bool = False
    """Whether ``handle`` is recorded for this service and for no other live one (#2086).

    ``handle`` is the platform's record of which resource the service owns. For a
    resource that predates a driver's identity marker, that record is the only
    ownership evidence there is, and it is evidence only while it is unique: two
    live rows recording one handle is the cross-tenant collision #2086 describes,
    and the squatter's row was written on its own request. The lifecycle sets this
    after checking every live service the same GCP driver resolves to in the same
    project. ``False`` means "not established", never "contested", so a caller that
    leaves it unset gets a refusal rather than a pass."""
    cluster_model: ClusterModelPlacement | None = None

    organization_id: str = ""
    recorded_container_exclusive: bool = False
    """See ProvisionSpec.recorded_container_exclusive. Unknown never grants authority."""


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    handle: str
    message: str
    errors: list[str] = field(default_factory=list)
    retryable: bool = True
    """Provider failures are retryable by default for compatibility with
    existing drivers. Permanent validation/safety refusals opt out."""


UPDATE_NOT_SUPPORTED_IN_PLACE = "update_not_supported_in_place"
"""``UpdateResult.errors`` marker for a driver that cannot update in place."""


def unsupported_update(handle: str, reason: str) -> UpdateResult:
    """The refusal a driver returns when it cannot apply a config change
    in place (#1376).

    ``ok=True`` means "the backing resource now matches the spec". A
    driver that performs no provider work must never return it: the
    update workflow's finalize step copies the desired config onto
    ``ManagedService.applied_config`` and flips the row to ACTIVE, so a
    courtesy success makes the platform record a change it never made.
    Nothing downstream can detect that, and the operator is told their
    change landed.

    ``retryable=False`` because no amount of retrying makes an in-place
    update possible; the workflow surfaces the reason on the row's
    ``status_error`` instead of spending its retry budget. Pair this with
    ``editable_fields()`` returning the keys the driver really can apply
    (``[]`` when there are none) so ``updateManagedService`` rejects the
    change at the API boundary and this refusal is only ever the backstop.
    """
    return UpdateResult(
        ok=False,
        handle=handle,
        message=f"{reason}; apply this change with reprovisionManagedService",
        errors=[UPDATE_NOT_SUPPORTED_IN_PLACE],
        retryable=False,
    )


@dataclass(frozen=True)
class DeprovisionSpec:
    handle: str
    # The row's stored config, so a driver can recover origin/auxiliary
    # resource refs (e.g. the CloudFront OAC's origin bucket) on the
    # idempotent "already gone" path where the live resource that carried
    # them no longer exists. Other drivers ignore it.
    config: dict[str, Any] = field(default_factory=dict)
    binding_id: str = ""
    managed_service_id: str = ""
    recorded_handle_exclusive: bool = False
    """See ``UpdateSpec.recorded_handle_exclusive``."""

    organization_id: str = ""
    recorded_container_exclusive: bool = False
    """See ProvisionSpec.recorded_container_exclusive."""


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
    binding_id: str = ""
    managed_service_id: str = ""
    recorded_handle_exclusive: bool = False
    """See ``UpdateSpec.recorded_handle_exclusive``."""

    organization_id: str = ""
    recorded_container_exclusive: bool = False


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
class SliceSpec:
    """A request to carve an isolated slice out of an existing instance (#1578).

    The shape `PreviewPolicy.SHARED_WITH_MAIN` was written for and that no
    driver could satisfy: `ManagedServiceDriver` exposed provision, update,
    deprovision, binding, status, snapshot and restore, and none of them
    creates a logical database, an ACL user, or a key/bucket prefix *inside*
    an instance that already exists. So every `SharedResource` identifier
    `preview_managed_services` computed had nowhere to be created.

    ``slice_id`` is the caller's stable name for the slice -- for a preview,
    the environment slug. Drivers must derive their own identifiers from it
    deterministically rather than generating one, because the caller has to
    be able to ask for the same slice twice and tear the right one down.
    """

    slice_id: str
    """Stable, caller-owned. e.g. ``preview-pr-42``."""

    parent: ServiceHandle
    """The instance to carve out of. Must already exist."""

    labels: dict[str, str] = field(default_factory=dict)
    """Provider tags/labels to stamp, so an orphaned slice is attributable."""
    organization_id: str = ""
    app_id: str = ""
    environment_id: str = ""
    """Immutable consumer ownership for independent credential namespaces."""

    def __post_init__(self) -> None:
        if not (self.slice_id or "").strip():
            raise ValueError("slice_id is required: a slice nobody can name cannot be torn down")
        if not (self.parent.handle or "").strip():
            raise ValueError("SliceSpec.parent must reference an existing instance")


@dataclass(frozen=True)
class SliceResult:
    """What a driver created, as envelope overrides.

    ``env_overrides`` is layered *on top of* the parent instance's
    ``Binding.env_vars`` rather than replacing it: a sliced postgres reuses
    the parent's host, port and credentials and overrides only
    ``POSTGRES_DB``. Returning a whole binding instead would let a driver
    quietly hand back the parent's database and nothing would notice --
    which is the failure mode #1578 names, production data in a PR preview.

    ``slice_handle`` is what ``deprovision_slice`` is given back.
    """

    slice_handle: str
    env_overrides: dict[str, ValueRef] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        if not (self.slice_handle or "").strip():
            raise ValueError("slice_handle is required")
        if not self.env_overrides:
            # A slice that changes no envelope key is not isolation: the
            # workload would connect to exactly what the parent connects to.
            raise ValueError(
                f"slice {self.slice_handle!r} produced no env overrides, so a workload "
                f"using it would reach the parent instance unchanged",
            )


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

        This is a promise the platform enforces on the operator's behalf:
        ``updateManagedService`` rejects a change touching any key outside
        the list before it starts a workflow, and everything inside the
        list is expected to reach the provider through ``update()``. The
        permissive ``["*"]`` default therefore only fits a driver whose
        ``update()`` really can apply an arbitrary config key -- a driver
        that inherits it and implements ``update()`` as a courtesy no-op
        reports success for a change that never happened (#1376). Drivers
        with no in-place path return ``[]`` and refuse in ``update()`` with
        ``unsupported_update()``.
        """
        return ["*"]


class SliceCapableDriver(Protocol):
    """A driver that can subdivide an existing instance (#1578).

    A **separate** protocol rather than two more methods on
    ``ManagedServiceDriver``, and the parity gate is why: that protocol is a
    conformance contract -- `tests/_sdk/test_parity.py` asserts every real
    driver implements every method on it -- so an optional capability
    declared there is not optional at all. Adding them inline made forty-odd
    drivers non-conformant in one commit.

    Which is the right outcome for a gate to force. Not every backing service
    can be subdivided meaningfully: a queue or a topic has no equivalent of a
    logical database, and a driver made to pretend would hand the caller the
    parent under a different name -- production data in a PR preview, which
    is the failure #1578 exists to prevent.

    So slicing is opt-in per driver, and ``supports_slicing`` is the runtime
    check for callers that hold a driver rather than a type.
    """

    def provision_slice(self, spec: SliceSpec) -> SliceResult:
        """Carve an isolated slice out of an existing instance.

        Idempotent, like every lifecycle method in this SDK: called twice
        with the same ``slice_id`` it returns the same slice rather than a
        second one.
        """
        ...

    def deprovision_slice(self, spec: SliceSpec, slice_handle: str) -> bool:
        """Drop a slice, leaving the parent instance alone.

        The parent is passed so a driver can reach it without a second
        lookup, and to make the asymmetry explicit: this must never
        deprovision ``spec.parent``. A preview teardown that dropped the
        shared instance is the worst outcome this path can produce.

        Returns False rather than raising when a delete does not land, so a
        teardown can record the leak and carry on: one undroppable slice
        must not strand the rest of a preview's cleanup. ``-> None`` left a
        caller no way to tell a leak from a success, which is how the leak
        stays invisible until someone reads the instance's database list.

        ``supports_slicing`` requires this verb alongside ``provision_slice``
        precisely so a driver cannot offer to carve a slice it can never
        remove.
        """
        ...


def supports_slicing(driver: object) -> bool:
    """Whether ``driver`` implements the optional slice verbs (#1578).

    Both, not either. A driver that can create a slice and not remove one
    leaves a preview's database behind on every teardown, and the leak is
    invisible until someone reads the instance's database list.
    """
    return callable(getattr(driver, "provision_slice", None)) and callable(getattr(driver, "deprovision_slice", None))


def apply_slice(binding: Binding, result: SliceResult) -> Binding:
    """The parent's binding with the slice's overrides layered on.

    The composition rule, in one place, so no driver or caller invents a
    second one. Overrides replace keys and never remove them: a sliced
    postgres keeps the parent's host, port, user and password and changes
    only ``POSTGRES_DB``.
    """
    return dataclasses.replace(
        binding,
        env_vars={**binding.env_vars, **result.env_overrides},
        notes="; ".join(part for part in (binding.notes, result.notes) if part),
    )
