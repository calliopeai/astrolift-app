"""Actual protected source/prep/IAM/execution and PG apply-to-runtime admission."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.db import DatabaseError, transaction
from gcp.gke_app_apply import ApplyError
from gcp.gke_app_runtime_handoff import current_runtime_targets
from gcp.gke_identity_observation import GKEObservationError

from astrolift_services.gcp_gke_app_apply_journal import AppApplyJournalError
from astrolift_services.models import GCPGKEAppApplyOperation
from astrolift_services.tests.test_gcp_gke_app_apply_journal_2278 import (
    bridge as apply_bridge,
)
from astrolift_services.tests.test_gcp_gke_app_apply_journal_2278 import (
    call,
    current,
)
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def bridge(monkeypatch, client, tmp_path, request):
    yield from apply_bridge.__wrapped__(monkeypatch, client, tmp_path, request)


def handoff(world):
    return world.store.runtime_handoff(world.reservation, checkpoint=lambda c: current(world, c))


def targets(world, receipt):
    return current_runtime_targets(
        world.app_driver,
        receipt,
        checkpoint=lambda: world.store.validate_runtime_handoff(
            world.reservation, receipt, checkpoint=lambda c: current(world, c)
        ),
    )


def test_actual_completed_pg_receipt_preserves_original_ceiling_before_runtime_native_reads(bridge):
    w = bridge
    call(w)
    receipt = handoff(w)
    row = GCPGKEAppApplyOperation.objects.get()
    assert row.accepted_plan["placement"]["allowed_node_pools"] == ["owned-pool"]
    assert row.accepted_plan["placement"]["native_cluster_id"] == w.original.cluster.native_cluster_id
    assert row.execution_receipt_id and row.preparation_operation_id and row.identity_source_id
    pool = deepcopy(w.wire.pools.node_pools[0])
    pool.name = "new-unadmitted-pool"
    w.wire.pools.node_pools.append(pool)
    result = targets(w, receipt)
    assert result.configuration_observed and len(result.targets) == 1
    assert result.targets[0].allowed_node_pools == ("owned-pool",)
    assert result.targets[0].uid == receipt.controllers[0].uid
    assert result.targets[0].generation == receipt.controllers[0].generation
    before = len(w.http["effects"])
    assert targets(w, receipt) == result and len(w.http["effects"]) == before
    changed = deepcopy(row.accepted_plan)
    changed["placement"]["allowed_node_pools"].append("new-unadmitted-pool")
    with pytest.raises(DatabaseError), transaction.atomic():
        GCPGKEAppApplyOperation._unscoped.filter(pk=row.pk).update(accepted_plan=changed)
    row.refresh_from_db()
    assert row.accepted_plan["placement"]["allowed_node_pools"] == ["owned-pool"]


def test_no_runtime_receipt_from_reserved_incomplete_configuration(bridge):
    w = bridge
    before = len(w.wire.calls), len(w.http["requests"]), len(w.http["effects"])
    with pytest.raises(AppApplyJournalError, match="RUNTIME_APPLY_NOT_COMPLETED"):
        handoff(w)
    assert (len(w.wire.calls), len(w.http["requests"]), len(w.http["effects"])) == before


def test_typed_receipt_mutation_is_not_current_db_authority(bridge):
    w = bridge
    call(w)
    receipt = handoff(w)
    before = len(w.wire.calls), len(w.http["requests"])
    forged = replace(receipt, journal_version=receipt.journal_version - 1)
    with pytest.raises(AppApplyJournalError, match="CURRENT_RUNTIME_HANDOFF_CHANGED"):
        targets(w, forged)
    assert (len(w.wire.calls), len(w.http["requests"])) == before


def test_old_null_plan_cannot_acquire_acceptance_in_place_or_emit_effect(bridge):
    w = bridge
    accepted = w.store.accepted
    w.store.accepted = replace(accepted, plan=replace(accepted.plan, placement=None))
    before = len(w.wire.calls), len(w.http["requests"]), len(w.http["effects"])
    try:
        with pytest.raises(AppApplyJournalError, match="PLACEMENT_ACCEPTANCE_REQUIRED"):
            w.store.reserve(checkpoint=lambda c: current(w, c))
    finally:
        w.store.accepted = accepted
    assert (len(w.wire.calls), len(w.http["requests"]), len(w.http["effects"])) == before
    assert GCPGKEAppApplyOperation.objects.get().accepted_plan["placement"] is not None


@pytest.mark.parametrize("withdrawal", ["token", "source", "apply", "execution"])
def test_current_original_fence_withdrawal_refuses_runtime_before_native_lookup(bridge, withdrawal):
    w = bridge
    call(w)
    receipt = handoff(w)
    if withdrawal == "token":
        type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
    elif withdrawal == "source":
        type(w.service).objects.filter(pk=w.service.pk).update(version=w.service.version + 1)
    elif withdrawal == "apply":
        type(w.store._context.journal)._unscoped.filter(guid=receipt.journal_id).update(state="UNKNOWN")
        GCPGKEAppApplyOperation._unscoped.filter(operation_id=receipt.operation_id).update(state="UNKNOWN")
    else:
        type(w.deployment).objects.filter(pk=w.deployment.pk).update(status="failed")
    before = len(w.wire.calls), len(w.http["requests"]), len(w.http["effects"])
    with pytest.raises((ValueError, PermissionDenied)):
        targets(w, receipt)
    assert (len(w.wire.calls), len(w.http["requests"]), len(w.http["effects"])) == before


@pytest.mark.parametrize("change", ["uid", "generation", "placement", "pool", "mode"])
def test_current_native_controller_or_placement_cannot_replace_protected_runtime(bridge, change):
    w = bridge
    call(w)
    receipt = handoff(w)
    row = w.http["objects"][receipt.controllers[0].resource.path]
    if change == "uid":
        from uuid import uuid4

        row["metadata"]["uid"] = str(uuid4())
    elif change == "generation":
        row["metadata"]["generation"] += 1
    elif change == "placement":
        row["spec"]["template"]["spec"]["nodeSelector"] = {"pool": "foreign"}
    elif change == "pool":
        w.wire.pools.node_pools[0].name = "replacement-pool"
    else:
        w.wire.cluster.autopilot.enabled = True
    before = len(w.http["effects"])
    with pytest.raises((ApplyError, GKEObservationError)):
        targets(w, receipt)
    assert len(w.http["effects"]) == before


@pytest.mark.parametrize("bridge", ["DaemonSet"], indirect=True)
def test_daemon_count_is_actual_uid_generation_bound_and_zero_unknown_pending(bridge):
    w = bridge
    call(w)
    receipt = handoff(w)
    row = w.http["objects"][receipt.controllers[0].resource.path]
    assert receipt.controllers[0].resource.replicas is None
    assert not targets(w, receipt).targets  # No current-generation status yet.
    row["status"] = {"observedGeneration": receipt.controllers[0].generation, "desiredNumberScheduled": 0}
    assert targets(w, receipt).reason == "DAEMON_COUNT_PENDING"
    row["status"]["desiredNumberScheduled"] = 1
    target = targets(w, receipt).targets[0]
    assert (target.uid, target.generation, target.desired_count) == (
        receipt.controllers[0].uid,
        receipt.controllers[0].generation,
        1,
    )
    row["metadata"]["generation"] += 1
    with pytest.raises(GKEObservationError, match="ORIGINAL_CONTROLLER_CHANGED"):
        targets(w, receipt)
