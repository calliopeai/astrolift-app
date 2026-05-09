"""
TeardownAppWorkflow policy (#127, spec 06 §4.10).

Pure-Python policy. Whole-app teardown — full removal of an app's
runtime resources across cluster, DNS, registry, identity, and
managed services.

* **Step ordering** — ingress before DNS before namespace before
  managed services before identity. Reverse of provisioning so
  half-torn-down apps end up at recoverable intermediate states.
* **delete_data flag** — controls whether managed-service data
  is preserved (snapshot) or destroyed.
* **Idempotency** — re-runs short-circuit on already-deregistered
  apps; partial-progress states converge.
* **Soft-delete invariant** — platform records (App, Deployment,
  ManagedServiceBinding) get soft-deleted; audit log retained
  per spec acceptance.
* **Report emission** — what was actually removed, for the
  operator audit trail.

Pairs with #87 (preview teardown — same shape, narrower scope)
and #126 (managed service deprovision child workflows).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class AppTeardownError(ValueError):
    pass


# ---- step ordering -------------------------------------------------


class TeardownStep(str, Enum):
    """Spec 06 §4.10."""

    MARK_TEARING_DOWN = "mark_tearing_down"
    """Set RegisteredApp.state=tearing_down so concurrent
    operations refuse to start."""

    DELETE_INGRESS_RULES = "delete_ingress_rules"
    """Remove the app's Ingress / Gateway / VirtualService.
    Stops new traffic FIRST so subsequent steps don't race
    in-flight requests."""

    DELETE_DNS_RECORDS = "delete_dns_records"
    """Remove A/CNAME/TXT records owned by the app."""

    DELETE_K8S_NAMESPACE = "delete_k8s_namespace"
    """Workloads, secrets, configmaps, services. Wait on
    finalizers (PVC reclaim, image-pull cleanup)."""

    DEPROVISION_MANAGED_SERVICES = "deprovision_managed_services"
    """Fan-out child workflows per ManagedServiceBinding;
    honor delete_data."""

    REVOKE_WORKLOAD_IDENTITY = "revoke_workload_identity"
    """Drop IAM role / GCP SA / Azure AAD app per cloud
    (#8 reverse)."""

    DELETE_REGISTRY_REPO = "delete_registry_repo"
    """Or archive — per platform-level retention policy.
    Default archive (audit + replay value) over delete."""

    REVOKE_DEPLOY_TOKENS = "revoke_deploy_tokens"
    """Active deploy tokens become unusable so any cron jobs
    or CI configs fail loudly rather than mysteriously."""

    MARK_DEREGISTERED = "mark_deregistered"
    """Final state. Emit APP_DEREGISTERED event."""


TEARDOWN_ORDER = (
    TeardownStep.MARK_TEARING_DOWN,
    TeardownStep.DELETE_INGRESS_RULES,
    TeardownStep.DELETE_DNS_RECORDS,
    TeardownStep.DELETE_K8S_NAMESPACE,
    TeardownStep.DEPROVISION_MANAGED_SERVICES,
    TeardownStep.REVOKE_WORKLOAD_IDENTITY,
    TeardownStep.DELETE_REGISTRY_REPO,
    TeardownStep.REVOKE_DEPLOY_TOKENS,
    TeardownStep.MARK_DEREGISTERED,
)


# ---- delete_data flag ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TeardownInputs:
    """Spec inputs to the workflow."""

    registered_app_id: int
    delete_data: bool
    """If True: managed services are destroyed including data
    (DROP DATABASE etc.). If False: snapshot first, then
    decommission compute. Operator decides — destructive
    operations require explicit opt-in."""

    operator_actor_id: int
    """Audit trail: who triggered the teardown."""

    def __post_init__(self) -> None:
        if self.registered_app_id <= 0:
            raise AppTeardownError(
                "registered_app_id must be positive"
            )


# ---- idempotency ---------------------------------------------------


class AppLifecycleState(str, Enum):
    """Subset of RegisteredApp.state the teardown cares about."""

    ACTIVE = "active"
    TEARING_DOWN = "tearing_down"
    DEREGISTERED = "deregistered"


@dataclasses.dataclass(frozen=True, slots=True)
class AppTeardownState:
    """Minimum projection. Mirrors what the workflow queries
    on retry to know what's been done."""

    registered_app_id: int
    state: AppLifecycleState
    last_completed_step: TeardownStep | None
    """The most-recent successfully-completed teardown step on
    the prior run. Workflow resumes from the next step."""


def steps_to_run(*, state: AppTeardownState) -> tuple[TeardownStep, ...]:
    """Decide which steps the workflow should execute given
    prior progress. Re-runs are safe — already-completed steps
    are skipped; the resume point is exactly after
    last_completed_step.

    DEREGISTERED state short-circuits to no-op (deploy already
    fully torn down).
    """
    if state.state == AppLifecycleState.DEREGISTERED:
        return ()

    if state.last_completed_step is None:
        return TEARDOWN_ORDER

    try:
        idx = TEARDOWN_ORDER.index(state.last_completed_step)
    except ValueError:
        # Unknown step value (e.g. from an older code version);
        # safest is to start from the top.
        return TEARDOWN_ORDER

    return TEARDOWN_ORDER[idx + 1:]


# ---- managed service deprovision plan ------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceTeardown:
    """One binding to deprovision. Workflow fans out a child
    workflow per binding."""

    binding_id: int
    kind: str
    """postgres / redis / object_store / queue."""

    snapshot_first: bool
    """Always snapshot when delete_data=True for postgres/object
    store; queue/cache snapshotting is policy-dependent."""

    destroy_data: bool


def plan_managed_service_teardown(
    *,
    bindings: Sequence[tuple[int, str]],
    delete_data: bool,
) -> tuple[ManagedServiceTeardown, ...]:
    """For each binding, decide snapshot-first vs straight
    decommission. ``bindings`` is an iterable of
    (binding_id, kind) tuples.

    Snapshot rules:
      - postgres + delete_data: snapshot → drop. Catastrophe
        defense — operator can recover from snapshot if they
        teardown'd the wrong app.
      - object_store + delete_data: snapshot bucket inventory
        + lifecycle archive. Same reasoning.
      - postgres/object_store + !delete_data: keep instance,
        unbind only.
      - redis/queue + delete_data: drop straight (data is
        ephemeral by definition; snapshotting cache state is
        meaningless).
      - redis/queue + !delete_data: unbind only.
    """
    out: list[ManagedServiceTeardown] = []
    for binding_id, kind in bindings:
        if kind in ("postgres", "object_store"):
            snapshot_first = delete_data
            destroy_data = delete_data
        elif kind in ("redis", "queue"):
            snapshot_first = False
            destroy_data = delete_data
        else:
            raise AppTeardownError(
                f"unknown managed service kind {kind!r} for "
                f"teardown of binding {binding_id}"
            )
        out.append(ManagedServiceTeardown(
            binding_id=binding_id, kind=kind,
            snapshot_first=snapshot_first,
            destroy_data=destroy_data,
        ))
    return tuple(out)


# ---- teardown report ----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class TeardownReport:
    """Emitted at workflow completion. Operator audit trail.
    Soft-deleted platform records are not 'removed' from this
    perspective — they're retained for audit. The report shows
    what was destroyed in the runtime."""

    registered_app_id: int
    ingress_rules_removed: int
    dns_records_removed: int
    namespace_deleted: bool
    managed_services_deprovisioned: int
    workload_identity_revoked: bool
    registry_repo_action: str
    """'deleted', 'archived', or 'absent'."""

    deploy_tokens_revoked: int
    data_destroyed: bool
    """Reflects the input flag plus what actually got destroyed
    (some bindings may have failed deprovision; the activity
    layer reports per-binding outcomes)."""

    completed_at_unix: int


# ---- soft-delete invariants ---------------------------------------


# Platform record kinds that get soft-deleted on teardown vs the
# audit-log kinds that are retained per spec acceptance.
_SOFT_DELETED_RECORD_KINDS = frozenset({
    "RegisteredApp", "Deployment", "AppEnvironment",
    "ManagedServiceBinding", "WorkloadIdentityRole",
    "DeployToken", "AppDomain", "PreviewEnvironment",
})

_RETAINED_RECORD_KINDS = frozenset({
    "AuditLog", "EventLog", "DeploymentLog", "WorkflowHistory",
})


def must_soft_delete(*, record_kind: str) -> bool:
    """Catalog of which platform records get soft-deleted on app
    teardown. Audit + event logs are RETAINED per spec — they're
    the record of what happened."""
    if record_kind in _SOFT_DELETED_RECORD_KINDS:
        return True
    if record_kind in _RETAINED_RECORD_KINDS:
        return False
    raise AppTeardownError(
        f"record kind {record_kind!r} not in either catalog; "
        "add to soft-delete or retain set explicitly"
    )
