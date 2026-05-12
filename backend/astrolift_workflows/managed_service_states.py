"""
Managed service workflow states + step ordering (#126, spec 06 §4.7-4.9).

Pure-Python policy. Complements
``managed_service_lifecycle.py`` (#20) with:

* **Lifecycle state machine** — provisioning / active / updating /
  deprovisioning / failed / soft-deleted, with allowed transitions.
* **Step ordering** for ProvisionManagedService /
  UpdateManagedService / DeprovisionManagedService workflows.
* **Bind ordering** — secrets-backend write must precede
  ManagedServiceBinding row creation so a partial failure
  doesn't leave a binding row pointing at an absent secret.
* **Dependent-workload notification** — given a binding, the
  workloads that need a redeploy when the binding's connection
  metadata changes.

The actual driver calls live in
``astrolift_drivers/managed_services/*``. This module is the
phase contract those drivers consume.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class ManagedServiceStateError(ValueError):
    pass


# ---- lifecycle state machine ---------------------------------------


class ManagedServiceState(str, Enum):
    """Spec 06 §4.7-§4.9: lifecycle states a managed service
    instance moves through."""

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    UPDATING = "updating"
    DEPROVISIONING = "deprovisioning"
    FAILED = "failed"
    SOFT_DELETED = "soft_deleted"


# Allowed transitions. Refusing arbitrary transitions catches
# bugs where a workflow tries to go from e.g. SOFT_DELETED back
# to ACTIVE (it should provision a new one instead).
_ALLOWED_TRANSITIONS: dict[ManagedServiceState, frozenset[ManagedServiceState]] = {
    ManagedServiceState.PROVISIONING: frozenset(
        {
            ManagedServiceState.ACTIVE,
            ManagedServiceState.FAILED,
        }
    ),
    ManagedServiceState.ACTIVE: frozenset(
        {
            ManagedServiceState.UPDATING,
            ManagedServiceState.DEPROVISIONING,
            ManagedServiceState.FAILED,
        }
    ),
    ManagedServiceState.UPDATING: frozenset(
        {
            ManagedServiceState.ACTIVE,
            ManagedServiceState.FAILED,
        }
    ),
    ManagedServiceState.DEPROVISIONING: frozenset(
        {
            ManagedServiceState.SOFT_DELETED,
            ManagedServiceState.FAILED,
        }
    ),
    ManagedServiceState.FAILED: frozenset(
        {
            # FAILED can be retried back into PROVISIONING/UPDATING/
            # DEPROVISIONING depending on which workflow originated.
            ManagedServiceState.PROVISIONING,
            ManagedServiceState.UPDATING,
            ManagedServiceState.DEPROVISIONING,
        }
    ),
    # SOFT_DELETED is terminal — provision a new instance to
    # recover, don't transition this row back.
    ManagedServiceState.SOFT_DELETED: frozenset(),
}


def can_transition(
    *,
    current: ManagedServiceState,
    target: ManagedServiceState,
) -> bool:
    """Pure validity check. Workflow consults before persisting
    a transition; mutation refuses if False."""
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(
    *,
    current: ManagedServiceState,
    target: ManagedServiceState,
) -> None:
    """Raise on invalid transition. Convenience for mutation
    layer that wants exception-on-deny."""
    if not can_transition(current=current, target=target):
        raise ManagedServiceStateError(
            f"invalid transition {current.value} → {target.value}; "
            f"allowed: "
            f"{[t.value for t in _ALLOWED_TRANSITIONS.get(current, [])]}"
        )


# ---- step ordering -------------------------------------------------


class ProvisionStep(str, Enum):
    """Spec §4.7 ordered steps."""

    MARK_PROVISIONING = "mark_provisioning"
    DRIVER_PROVISION = "driver_provision"
    WAIT_UNTIL_ACTIVE = "wait_until_active"
    BIND_ENVS = "bind_envs"
    NOTIFY_DEPENDENT_WORKLOADS = "notify_dependent_workloads"
    MARK_ACTIVE = "mark_active"


PROVISION_ORDER = (
    ProvisionStep.MARK_PROVISIONING,
    ProvisionStep.DRIVER_PROVISION,
    ProvisionStep.WAIT_UNTIL_ACTIVE,
    ProvisionStep.BIND_ENVS,
    ProvisionStep.NOTIFY_DEPENDENT_WORKLOADS,
    ProvisionStep.MARK_ACTIVE,
)


class UpdateStep(str, Enum):
    """Spec §4.8."""

    MARK_UPDATING = "mark_updating"
    COMPUTE_DELTA = "compute_delta"
    """Diff old spec vs new — see #20's diff_spec."""

    WARN_IF_DESTRUCTIVE = "warn_if_destructive"
    """Variant change, region change, etc. require operator
    approval signal."""

    DRIVER_UPDATE = "driver_update"
    WAIT_UNTIL_ACTIVE = "wait_until_active"
    REBIND_ENVS_IF_CHANGED = "rebind_envs_if_changed"
    """Connection metadata may change (new endpoint, new password
    on rotation); rebind only if changed."""

    NOTIFY_DEPENDENT_WORKLOADS_IF_REBOUND = "notify_dependent_workloads_if_rebound"
    MARK_ACTIVE = "mark_active"


UPDATE_ORDER = (
    UpdateStep.MARK_UPDATING,
    UpdateStep.COMPUTE_DELTA,
    UpdateStep.WARN_IF_DESTRUCTIVE,
    UpdateStep.DRIVER_UPDATE,
    UpdateStep.WAIT_UNTIL_ACTIVE,
    UpdateStep.REBIND_ENVS_IF_CHANGED,
    UpdateStep.NOTIFY_DEPENDENT_WORKLOADS_IF_REBOUND,
    UpdateStep.MARK_ACTIVE,
)


class DeprovisionStep(str, Enum):
    """Spec §4.9."""

    MARK_DEPROVISIONING = "mark_deprovisioning"
    SNAPSHOT_IF_APPLICABLE = "snapshot_if_applicable"
    """Snapshot before deletion when delete_data=True and the
    service kind supports it (#127's plan rules)."""

    DRIVER_DELETE = "driver_delete"
    WAIT_FOR_DELETION = "wait_for_deletion"
    SCRUB_SECRETS_BACKEND = "scrub_secrets_backend"
    """Remove the connection metadata from the SecretsBackend
    so dependent workloads fail fast rather than connecting to
    a deleted service."""

    SOFT_DELETE_ROW = "soft_delete_row"


DEPROVISION_ORDER = (
    DeprovisionStep.MARK_DEPROVISIONING,
    DeprovisionStep.SNAPSHOT_IF_APPLICABLE,
    DeprovisionStep.DRIVER_DELETE,
    DeprovisionStep.WAIT_FOR_DELETION,
    DeprovisionStep.SCRUB_SECRETS_BACKEND,
    DeprovisionStep.SOFT_DELETE_ROW,
)


# ---- bind ordering -------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BindStep:
    """One step in the bind sequence. Order matters."""

    order: int
    name: str
    description: str


# Spec §4.7 step 4: secrets-backend write must precede binding row
# creation. Otherwise, a row that points at an absent secret would
# leave a workload trying to mount a missing secret. Order is:
BIND_ORDER = (
    BindStep(
        order=1,
        name="write_secrets_backend",
        description=(
            "Write connection metadata (host, port, username, " "password, ssl mode) to the SecretsBackend"
        ),
    ),
    BindStep(
        order=2,
        name="create_binding_row",
        description=("Create ManagedServiceBinding row referencing the " "secrets-backend secret name"),
    ),
    BindStep(
        order=3,
        name="emit_bound_event",
        description=("Emit MANAGED_SERVICE_BOUND for downstream workflows"),
    ),
)


def bind_step_order() -> tuple[BindStep, ...]:
    """Deterministic order. Caller walks and runs each as an
    activity."""
    return BIND_ORDER


# ---- destructive-update detection ----------------------------------


# Fields that destroy data or rebuild the instance:
# - variant: new instance entirely; old data lost unless
#   migration ran first
# - region: cross-region move = data migration, not in-place
# - kind: service-kind change is impossible — refuse upstream
_DESTRUCTIVE_FIELDS = frozenset({"variant", "region", "kind"})


def is_destructive_update(*, changed_fields: Sequence[str]) -> bool:
    """Spec §4.8: warn when fields that destroy data or rebuild
    the instance are part of the diff. Workflow waits for explicit
    operator approval signal before proceeding."""
    return any(f in _DESTRUCTIVE_FIELDS for f in changed_fields)


def destructive_fields_in(
    *,
    changed_fields: Sequence[str],
) -> tuple[str, ...]:
    """List the destructive fields that triggered the warning,
    for the operator-approval prompt."""
    return tuple(sorted(set(changed_fields) & _DESTRUCTIVE_FIELDS))


# ---- dependent-workload notification ------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class WorkloadBinding:
    """Minimum projection: which workloads bind which services."""

    workload_id: int
    binding_id: int


def workloads_to_redeploy(
    *,
    rebound_binding_ids: Sequence[int],
    all_bindings: Sequence[WorkloadBinding],
) -> tuple[int, ...]:
    """Spec §4.7 step 5: identify workloads that depend on this
    binding and need a redeploy.

    Used by both Provision (dependent workloads need to mount
    the new secret) and Update (when connection metadata
    rebinds).

    Returns deduplicated workload_ids in stable order.
    """
    rebound = set(rebound_binding_ids)
    affected: list[int] = []
    seen: set[int] = set()
    for b in all_bindings:
        if b.binding_id in rebound and b.workload_id not in seen:
            affected.append(b.workload_id)
            seen.add(b.workload_id)
    affected.sort()
    return tuple(affected)
