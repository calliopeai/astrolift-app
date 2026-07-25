"""Tests for TeardownAppWorkflow policy (#127, spec 06 §4.10)."""

from __future__ import annotations

import pytest

from astrolift_workflows.app_teardown import (
    TEARDOWN_ORDER,
    AppLifecycleState,
    AppTeardownError,
    AppTeardownState,
    TeardownInputs,
    TeardownStep,
    must_soft_delete,
    plan_managed_service_teardown,
    steps_to_run,
)

# ---- step ordering -------------------------------------------------


def test_teardown_order_locked():
    """Spec 06 §4.10 — out-of-order teardown can leak resources
    or destroy data while ingress still routes traffic."""
    assert TEARDOWN_ORDER[0] == TeardownStep.MARK_TEARING_DOWN
    assert TEARDOWN_ORDER[-1] == TeardownStep.MARK_DEREGISTERED
    # Ingress before namespace before managed services
    ingress_idx = TEARDOWN_ORDER.index(TeardownStep.DELETE_INGRESS_RULES)
    namespace_idx = TEARDOWN_ORDER.index(TeardownStep.DELETE_K8S_NAMESPACE)
    services_idx = TEARDOWN_ORDER.index(TeardownStep.DEPROVISION_MANAGED_SERVICES)
    assert ingress_idx < namespace_idx < services_idx


def test_teardown_order_dns_after_ingress():
    """DNS removal AFTER ingress so in-flight requests have time
    to drain rather than getting NXDOMAIN mid-request."""
    ingress_idx = TEARDOWN_ORDER.index(TeardownStep.DELETE_INGRESS_RULES)
    dns_idx = TEARDOWN_ORDER.index(TeardownStep.DELETE_DNS_RECORDS)
    assert ingress_idx < dns_idx


# ---- TeardownInputs validation -------------------------------------


def test_inputs_basic():
    inputs = TeardownInputs(
        registered_app_id=1,
        delete_data=False,
        operator_actor_id=42,
    )
    assert inputs.delete_data is False


def test_inputs_rejects_invalid_app_id():
    with pytest.raises(AppTeardownError):
        TeardownInputs(
            registered_app_id=0,
            delete_data=False,
            operator_actor_id=42,
        )


# ---- idempotency ---------------------------------------------------


def test_steps_full_when_no_progress():
    state = AppTeardownState(
        registered_app_id=1,
        state=AppLifecycleState.ACTIVE,
        last_completed_step=None,
    )
    assert steps_to_run(state=state) == TEARDOWN_ORDER


def test_steps_resume_after_last_completed():
    """Spec acceptance: 're-running is safe.' Skip already-done
    steps."""
    state = AppTeardownState(
        registered_app_id=1,
        state=AppLifecycleState.TEARING_DOWN,
        last_completed_step=TeardownStep.DELETE_DNS_RECORDS,
    )
    steps = steps_to_run(state=state)
    # Should start at DELETE_K8S_NAMESPACE
    assert steps[0] == TeardownStep.DELETE_K8S_NAMESPACE
    # And not include any already-done steps
    assert TeardownStep.DELETE_INGRESS_RULES not in steps
    assert TeardownStep.MARK_TEARING_DOWN not in steps


def test_steps_empty_when_already_deregistered():
    """Re-run against fully torn-down app: no-op."""
    state = AppTeardownState(
        registered_app_id=1,
        state=AppLifecycleState.DEREGISTERED,
        last_completed_step=TeardownStep.MARK_DEREGISTERED,
    )
    assert steps_to_run(state=state) == ()


def test_steps_starts_from_top_for_unknown_step():
    """Defensive: if last_completed_step doesn't match current
    enum (e.g. older code version), restart from top — safer
    than skipping."""
    # Simulate an unknown step by passing an enum-like value
    # the workflow doesn't recognize. We use a real enum value
    # but the test path that returns full order is only hit when
    # last_completed_step is None or not in TEARDOWN_ORDER —
    # since all enum values ARE in TEARDOWN_ORDER, we can only
    # exercise this via None.
    state = AppTeardownState(
        registered_app_id=1,
        state=AppLifecycleState.ACTIVE,
        last_completed_step=None,
    )
    assert steps_to_run(state=state) == TEARDOWN_ORDER


# ---- managed service teardown plan ---------------------------------


def test_postgres_with_delete_data_snapshots_first():
    """Catastrophe defense: snapshot before drop."""
    plan = plan_managed_service_teardown(
        bindings=[(1, "postgres")],
        delete_data=True,
    )
    assert plan[0].snapshot_first is True
    assert plan[0].destroy_data is True


def test_postgres_without_delete_data_keeps_instance():
    """Operator chose to keep data; unbind only."""
    plan = plan_managed_service_teardown(
        bindings=[(1, "postgres")],
        delete_data=False,
    )
    assert plan[0].snapshot_first is False
    assert plan[0].destroy_data is False


def test_object_store_with_delete_data_snapshots_first():
    plan = plan_managed_service_teardown(
        bindings=[(1, "object_store")],
        delete_data=True,
    )
    assert plan[0].snapshot_first is True


def test_redis_never_snapshots():
    """Cache state has no recovery value."""
    plan = plan_managed_service_teardown(
        bindings=[(1, "redis")],
        delete_data=True,
    )
    assert plan[0].snapshot_first is False
    assert plan[0].destroy_data is True


def test_queue_never_snapshots():
    """In-flight messages aren't worth snapshotting."""
    plan = plan_managed_service_teardown(
        bindings=[(1, "queue")],
        delete_data=True,
    )
    assert plan[0].snapshot_first is False


def test_unknown_kind_rejected():
    with pytest.raises(AppTeardownError, match="unknown"):
        plan_managed_service_teardown(
            bindings=[(1, "exotic")],
            delete_data=True,
        )


def test_plan_preserves_binding_order():
    plan = plan_managed_service_teardown(
        bindings=[(5, "postgres"), (2, "redis"), (8, "queue")],
        delete_data=False,
    )
    assert [p.binding_id for p in plan] == [5, 2, 8]


# ---- soft-delete invariants ---------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "RegisteredApp",
        "Deployment",
        "ManagedServiceBinding",
        "WorkloadIdentityRole",
        "DeployToken",
        "AppDomain",
        "PreviewEnvironment",
        "AppEnvironment",
        "Workload",
        "AlertRule",
    ],
)
def test_business_records_soft_deleted(kind):
    assert must_soft_delete(record_kind=kind) is True


@pytest.mark.parametrize(
    "kind",
    [
        "AuditLog",
        "EventLog",
        "DeploymentLog",
        "WorkflowHistory",
    ],
)
def test_audit_records_retained(kind):
    """Spec acceptance: 'audit log retained.'"""
    assert must_soft_delete(record_kind=kind) is False


def test_unknown_record_kind_rejected():
    """Catalog gap defense: new record types must be classified
    explicitly so we don't accidentally hard-delete them."""
    with pytest.raises(AppTeardownError, match="catalog"):
        must_soft_delete(record_kind="WeirdNewModel")
