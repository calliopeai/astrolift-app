"""Tests for managed service state machine + step ordering (#126, spec 06 §4.7-4.9)."""

from __future__ import annotations

import pytest

from astrolift_workflows.managed_service_states import (
    BIND_ORDER,
    DEPROVISION_ORDER,
    PROVISION_ORDER,
    UPDATE_ORDER,
    DeprovisionStep,
    ManagedServiceState,
    ManagedServiceStateError,
    ProvisionStep,
    UpdateStep,
    WorkloadBinding,
    assert_transition,
    bind_step_order,
    can_transition,
    destructive_fields_in,
    is_destructive_update,
    workloads_to_redeploy,
)

# ---- state transitions ---------------------------------------------


def test_provisioning_to_active():
    assert (
        can_transition(
            current=ManagedServiceState.PROVISIONING,
            target=ManagedServiceState.ACTIVE,
        )
        is True
    )


def test_provisioning_to_failed():
    assert (
        can_transition(
            current=ManagedServiceState.PROVISIONING,
            target=ManagedServiceState.FAILED,
        )
        is True
    )


def test_active_to_updating():
    assert (
        can_transition(
            current=ManagedServiceState.ACTIVE,
            target=ManagedServiceState.UPDATING,
        )
        is True
    )


def test_active_to_deprovisioning():
    assert (
        can_transition(
            current=ManagedServiceState.ACTIVE,
            target=ManagedServiceState.DEPROVISIONING,
        )
        is True
    )


def test_failed_can_retry_to_provisioning():
    """Retry path: FAILED → PROVISIONING (the workflow restarts)."""
    assert (
        can_transition(
            current=ManagedServiceState.FAILED,
            target=ManagedServiceState.PROVISIONING,
        )
        is True
    )


def test_soft_deleted_is_terminal():
    """No transitions out of SOFT_DELETED — provision a new
    instance instead of resurrecting."""
    for target in ManagedServiceState:
        assert (
            can_transition(
                current=ManagedServiceState.SOFT_DELETED,
                target=target,
            )
            is False
        )


def test_active_cannot_skip_to_soft_deleted():
    """Must go through DEPROVISIONING first."""
    assert (
        can_transition(
            current=ManagedServiceState.ACTIVE,
            target=ManagedServiceState.SOFT_DELETED,
        )
        is False
    )


def test_provisioning_cannot_skip_to_active_via_updating():
    """Defensive: arbitrary transitions refused."""
    assert (
        can_transition(
            current=ManagedServiceState.PROVISIONING,
            target=ManagedServiceState.UPDATING,
        )
        is False
    )


def test_assert_transition_raises_on_invalid():
    with pytest.raises(ManagedServiceStateError, match="invalid transition"):
        assert_transition(
            current=ManagedServiceState.SOFT_DELETED,
            target=ManagedServiceState.ACTIVE,
        )


def test_assert_transition_passes_on_valid():
    """No raise on valid transition."""
    assert_transition(
        current=ManagedServiceState.PROVISIONING,
        target=ManagedServiceState.ACTIVE,
    )


# ---- step ordering -------------------------------------------------


def test_provision_order_locked():
    """Spec §4.7."""
    assert PROVISION_ORDER == (
        ProvisionStep.MARK_PROVISIONING,
        ProvisionStep.DRIVER_PROVISION,
        ProvisionStep.WAIT_UNTIL_ACTIVE,
        ProvisionStep.BIND_ENVS,
        ProvisionStep.NOTIFY_DEPENDENT_WORKLOADS,
        ProvisionStep.MARK_ACTIVE,
    )


def test_update_order_locked():
    """Spec §4.8."""
    assert UPDATE_ORDER[0] == UpdateStep.MARK_UPDATING
    assert UPDATE_ORDER[-1] == UpdateStep.MARK_ACTIVE
    # Compute delta before warning (warning depends on delta)
    assert UPDATE_ORDER.index(UpdateStep.COMPUTE_DELTA) < UPDATE_ORDER.index(UpdateStep.WARN_IF_DESTRUCTIVE)
    # Warning before driver update (operator approval gates
    # destructive ops)
    assert UPDATE_ORDER.index(UpdateStep.WARN_IF_DESTRUCTIVE) < UPDATE_ORDER.index(UpdateStep.DRIVER_UPDATE)


def test_deprovision_order_locked():
    """Spec §4.9: snapshot before driver delete."""
    snapshot_idx = DEPROVISION_ORDER.index(
        DeprovisionStep.SNAPSHOT_IF_APPLICABLE,
    )
    delete_idx = DEPROVISION_ORDER.index(DeprovisionStep.DRIVER_DELETE)
    assert snapshot_idx < delete_idx


def test_deprovision_scrub_before_soft_delete():
    """Scrub secrets BEFORE soft-deleting the row so the row
    being soft-deleted is the last evidence the binding
    existed (audit trail intact)."""
    scrub_idx = DEPROVISION_ORDER.index(
        DeprovisionStep.SCRUB_SECRETS_BACKEND,
    )
    soft_idx = DEPROVISION_ORDER.index(
        DeprovisionStep.SOFT_DELETE_ROW,
    )
    assert scrub_idx < soft_idx


# ---- bind ordering -------------------------------------------------


def test_bind_order_secrets_first():
    """Spec §4.7 step 4: secrets-backend write must precede
    binding-row creation. Refused order would leave a row
    pointing at an absent secret on partial failure."""
    out = bind_step_order()
    names = [s.name for s in out]
    assert names.index("write_secrets_backend") < names.index("create_binding_row")


def test_bind_order_emit_event_last():
    """Event consumers may rely on the binding row + secret
    both being present; emit AFTER both."""
    out = bind_step_order()
    names = [s.name for s in out]
    assert names[-1] == "emit_bound_event"


def test_bind_order_three_steps():
    """Spec acceptance: lock the count."""
    assert len(BIND_ORDER) == 3


# ---- destructive update detection ---------------------------------


def test_variant_change_is_destructive():
    """Spec §4.8: variant change rebuilds the instance."""
    assert is_destructive_update(changed_fields=["variant"]) is True


def test_region_change_is_destructive():
    """Cross-region = migration."""
    assert is_destructive_update(changed_fields=["region"]) is True


def test_size_change_is_not_destructive():
    """Size up/down is in-place on most drivers; not destructive."""
    assert is_destructive_update(changed_fields=["size"]) is False


def test_destructive_fields_in_returns_subset():
    """Mixed change: only the destructive fields surface for
    the operator-approval prompt."""
    out = destructive_fields_in(
        changed_fields=["size", "variant", "tags"],
    )
    assert out == ("variant",)


def test_destructive_fields_in_sorted():
    """Stable order for prompt rendering."""
    out = destructive_fields_in(
        changed_fields=["region", "variant"],
    )
    assert out == ("region", "variant")


# ---- dependent workload notification ------------------------------


def test_workloads_to_redeploy_filters_by_binding():
    """Only workloads bound to the rebound bindings need to
    redeploy."""
    bindings = (
        WorkloadBinding(workload_id=1, binding_id=10),
        WorkloadBinding(workload_id=2, binding_id=10),
        WorkloadBinding(workload_id=3, binding_id=20),
    )
    out = workloads_to_redeploy(
        rebound_binding_ids=[10],
        all_bindings=bindings,
    )
    assert out == (1, 2)


def test_workloads_to_redeploy_dedupes():
    """Workload bound to multiple rebound bindings: one redeploy."""
    bindings = (
        WorkloadBinding(workload_id=1, binding_id=10),
        WorkloadBinding(workload_id=1, binding_id=20),
    )
    out = workloads_to_redeploy(
        rebound_binding_ids=[10, 20],
        all_bindings=bindings,
    )
    assert out == (1,)


def test_workloads_to_redeploy_empty_when_no_rebound():
    """No bindings rebound → no workloads to redeploy."""
    bindings = (WorkloadBinding(workload_id=1, binding_id=10),)
    out = workloads_to_redeploy(
        rebound_binding_ids=[],
        all_bindings=bindings,
    )
    assert out == ()


def test_workloads_to_redeploy_stable_order():
    """Sorted ascending workload_id for stability."""
    bindings = (
        WorkloadBinding(workload_id=5, binding_id=10),
        WorkloadBinding(workload_id=2, binding_id=10),
        WorkloadBinding(workload_id=8, binding_id=10),
    )
    out = workloads_to_redeploy(
        rebound_binding_ids=[10],
        all_bindings=bindings,
    )
    assert out == (2, 5, 8)
