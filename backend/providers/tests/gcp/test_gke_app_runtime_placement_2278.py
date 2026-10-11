"""Original placement acceptance through generated GKE RPC and owned native TLS."""

import copy
from dataclasses import replace
from uuid import uuid4

import pytest

from gcp.gke_app_apply import ApplyError, compile_app_plan
from gcp.gke_app_runtime_handoff import RuntimeHandoff, current_runtime_targets, observe_handoff_runtime
from gcp.gke_identity_observation import GKEObservationError

from .test_gke_app_apply_2278 import (
    SUBJECT,
    MemoryHooks,
    compiled,
    resources,
)
from .test_gke_app_apply_2278 import (
    app_native as app_native_fixture,
)
from .test_gke_app_apply_2278 import (
    native as native_fixture,
)
from .test_gke_identity_runtime_2278 import RuntimeKube, owner


@pytest.fixture
def native(tmp_path, monkeypatch):
    yield from native_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def app_native(native):
    yield from app_native_fixture.__wrapped__(native)


def completed(driver, raw=None):
    plan, bodies = compiled(driver, raw)
    hooks = MemoryHooks(plan)
    result = hooks.call(driver, bodies)
    return RuntimeHandoff(
        hooks.journal_id,
        hooks.version,
        plan.execution.operation_id,
        1,
        plan,
        SUBJECT.name,
        tuple(r for r in result.ledger.resources if r.resource.kind in ("Deployment", "StatefulSet", "DaemonSet")),
    )


def extra_pool(wire):
    row = copy.deepcopy(wire.pools.node_pools[0])
    row.name = "unrelated-pool"
    wire.pools.node_pools.append(row)


def test_acceptance_captures_final_controller_digest_before_any_effect(app_native):
    driver, state, wire = app_native
    plan, _ = compiled(driver)
    assert plan.placement.cluster_resource == driver.context.cluster_resource
    assert plan.placement.native_cluster_id == driver.context.native_cluster_id
    assert plan.placement.allowed_node_pools == ("owned-pool",) and not plan.placement.autopilot
    assert len(plan.placement.controller_binding_sha256) == 64
    assert not state["effects"] and wire.calls
    with pytest.raises(ApplyError, match="CONTROLLER_PLACEMENT_CHANGED"):
        replace(plan, resources=(*plan.resources[:-1], replace(plan.resources[-1], placement_sha256="a" * 64)))


def test_legacy_null_placement_refuses_effects_without_native_discovery(app_native):
    driver, state, wire = app_native
    plan, bodies = compile_app_plan(
        driver.context, compiled(driver)[0].execution, resources(), identity_sha256="1" * 64
    )
    before = len(state["requests"]), len(wire.calls)
    with pytest.raises(ApplyError, match="PLACEMENT_ACCEPTANCE_REQUIRED"):
        MemoryHooks(plan).call(driver, bodies)
    assert (len(state["requests"]), len(wire.calls)) == before and not state["effects"]


@pytest.mark.parametrize("value", [False, True, "yes"])
def test_capture_requires_current_none_checkpoint_before_adc(app_native, value):
    driver, state, wire = app_native
    execution = compiled(driver)[0].execution
    plan, _ = compile_app_plan(driver.context, execution, resources(), identity_sha256="1" * 64)
    before = len(state["requests"]), len(wire.calls)
    with pytest.raises(GKEObservationError, match="CURRENT_ADMISSION_UNCONFIRMED"):
        driver.capture_placement(plan, checkpoint=lambda: value)
    assert (len(state["requests"]), len(wire.calls)) == before and not state["effects"]


def test_withdrawal_during_actual_rpc_discards_capture_before_effect(app_native):
    driver, state, wire = app_native
    plan, _ = compile_app_plan(driver.context, compiled(driver)[0].execution, resources(), identity_sha256="1" * 64)
    admitted = [True]
    wire.after = lambda name: admitted.__setitem__(0, False) if name == "GetCluster" else None
    with pytest.raises(GKEObservationError, match="CURRENT_ADMISSION_UNCONFIRMED"):
        driver.capture_placement(plan, checkpoint=lambda: None if admitted[0] else False)
    assert not state["effects"]


@pytest.mark.parametrize("change", ["incarnation", "mode", "admitted-pool"])
def test_changed_original_native_admission_prevents_controller_effect(app_native, change):
    driver, state, wire = app_native
    plan, bodies = compiled(driver)
    if change == "incarnation":
        wire.cluster.id = "replacement-cluster"
    elif change == "mode":
        wire.cluster.autopilot.enabled = True
    else:
        wire.pools.node_pools[0].name = "replacement-pool"
    with pytest.raises(ApplyError):
        MemoryHooks(plan).call(driver, bodies)
    assert not state["effects"]


def test_unrelated_pool_and_node_autoscaling_never_expand_original_ceiling(app_native):
    driver, state, wire = app_native
    plan, bodies = compiled(driver)
    wire.pools.node_pools[0].initial_node_count = 9
    extra_pool(wire)
    hooks = MemoryHooks(plan)
    result = hooks.call(driver, bodies)
    handoff = RuntimeHandoff(
        hooks.journal_id,
        hooks.version,
        plan.execution.operation_id,
        1,
        plan,
        SUBJECT.name,
        (result.ledger.resources[-1],),
    )
    targets = current_runtime_targets(driver, handoff, checkpoint=lambda: None)
    assert targets.targets[0].allowed_node_pools == ("owned-pool",)
    assert targets.targets[0].uid == result.ledger.resources[-1].uid
    assert targets.targets[0].generation == result.ledger.resources[-1].generation
    assert len(state["effects"]) == 3


def test_autopilot_acceptance_has_no_invented_standard_pool(app_native):
    driver, _, wire = app_native
    wire.cluster.autopilot.enabled = True
    handoff = completed(driver)
    assert handoff.plan.placement.autopilot and not handoff.plan.placement.allowed_node_pools
    assert current_runtime_targets(driver, handoff, checkpoint=lambda: None).targets[0].allowed_node_pools == ()


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_exact_controller_uid_generation_and_positive_count_handoff(app_native, kind):
    driver, state, _ = app_native
    handoff = completed(driver, resources(kind))
    row = handoff.controllers[0]
    state["objects"][row.resource.path]["status"] = {"observedGeneration": row.generation, "desiredNumberScheduled": 1}
    target = current_runtime_targets(driver, handoff, checkpoint=lambda: None).targets[0]
    assert (target.uid, target.generation, target.desired_count) == (row.uid, row.generation, 1)
    assert target.template_sha256 == row.native_template_sha256
    assert target.placement_sha256 == row.native_placement_sha256


@pytest.mark.parametrize("count", [None, 0])
def test_daemon_zero_or_unknown_count_remains_pending_without_target(app_native, count):
    driver, state, _ = app_native
    handoff = completed(driver, resources("DaemonSet"))
    row = handoff.controllers[0]
    state["objects"][row.resource.path]["status"] = {
        "observedGeneration": row.generation,
        "desiredNumberScheduled": count,
    }
    targets = current_runtime_targets(driver, handoff, checkpoint=lambda: None)
    assert targets.configuration_observed and targets.reason == "DAEMON_COUNT_PENDING" and not targets.targets
    result = observe_handoff_runtime(driver, handoff, checkpoint=lambda: None)
    assert not result.workload_ready and result.reason == "DAEMON_COUNT_PENDING"


@pytest.mark.parametrize("count", [False, True, -1, 257, 1.5, "1"])
def test_daemon_malformed_or_unbounded_count_refuses(app_native, count):
    driver, state, _ = app_native
    handoff = completed(driver, resources("DaemonSet"))
    row = handoff.controllers[0]
    state["objects"][row.resource.path]["status"] = {
        "observedGeneration": row.generation,
        "desiredNumberScheduled": count,
    }
    with pytest.raises(GKEObservationError, match="DAEMON_COUNT_UNVERIFIED"):
        current_runtime_targets(driver, handoff, checkpoint=lambda: None)


@pytest.mark.parametrize("change", ["uid", "generation", "template"])
def test_replaced_or_changed_controller_cannot_borrow_runtime_target(app_native, change):
    driver, state, _ = app_native
    handoff = completed(driver)
    value = state["objects"][handoff.controllers[0].resource.path]
    if change == "uid":
        value["metadata"]["uid"] = str(uuid4())
    elif change == "generation":
        value["metadata"]["generation"] += 1
    else:
        value["spec"]["template"]["spec"]["containers"][0]["image"] = "foreign"
    with pytest.raises((ApplyError, GKEObservationError)):
        current_runtime_targets(driver, handoff, checkpoint=lambda: None)


@pytest.mark.parametrize("unadmitted_node", [False, True])
@pytest.mark.parametrize("extra_state", ["eligible", "provisioning", "gce"])
def test_real_tls_runtime_uses_original_ceiling_even_when_new_pool_exists(app_native, unadmitted_node, extra_state):
    driver, state, wire = app_native
    kube = RuntimeKube("StatefulSet")
    raw = resources("StatefulSet")
    raw[-1] = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": "owned-controller", "namespace": SUBJECT.namespace},
        "spec": kube.controller["spec"],
    }
    handoff = completed(driver, raw)
    row = handoff.controllers[0]
    current = state["objects"][row.resource.path]
    current["status"] = copy.deepcopy(kube.controller["status"])
    current["status"]["observedGeneration"] = row.generation
    kube.pod["metadata"]["ownerReferences"] = owner("StatefulSet", row.resource.name, row.uid)
    state["objects"][f"/api/v1/namespaces/{SUBJECT.namespace}/pods?limit=257"] = {
        "apiVersion": "v1",
        "kind": "PodList",
        "metadata": {"resourceVersion": "pods-original", "continue": ""},
        "items": [kube.pod],
    }
    state["objects"]["/api/v1/nodes/owned-node"] = kube.node
    wire.cluster.locations = [driver.context.location]
    wire.pools.node_pools[0].locations = [driver.context.location]
    extra_pool(wire)
    if extra_state == "provisioning":
        wire.pools.node_pools[-1].status = 1
    elif extra_state == "gce":
        wire.pools.node_pools[-1].config.workload_metadata_config.mode = 1
    if unadmitted_node:
        kube.node["metadata"]["labels"]["cloud.google.com/gke-nodepool"] = "unrelated-pool"
    result = observe_handoff_runtime(driver, handoff, checkpoint=lambda: None)
    assert result.workload_ready is not unadmitted_node, result.reason
    assert not result.impersonation_verified and not result.inference_verified


@pytest.mark.parametrize("extra_state", ["provisioning", "gce"])
def test_capture_eligible_subset_and_preserve_generic_strict_observation(app_native, extra_state):
    driver, state, wire = app_native
    extra_pool(wire)
    if extra_state == "provisioning":
        wire.pools.node_pools[-1].status = 1
    else:
        wire.pools.node_pools[-1].config.workload_metadata_config.mode = 1
    with pytest.raises(GKEObservationError, match="NODE_CONFIGURATION_UNVERIFIED"):
        driver.observer._cluster(lambda: None)
    handoff = completed(driver)
    assert handoff.plan.placement.allowed_node_pools == ("owned-pool",)
    assert current_runtime_targets(driver, handoff, checkpoint=lambda: None).targets[0].allowed_node_pools == (
        "owned-pool",
    )
    assert len(state["effects"]) == 3


@pytest.mark.parametrize("change", ["status", "mode", "missing", "duplicate", "name", "oversized"])
def test_complete_inventory_and_admitted_pool_security_remain_mandatory(app_native, change):
    driver, state, wire = app_native
    plan, bodies = compiled(driver)
    if change == "status":
        wire.pools.node_pools[0].status = 1
    elif change == "mode":
        wire.pools.node_pools[0].config.workload_metadata_config.mode = 1
    elif change == "missing":
        wire.pools.node_pools.clear()
    elif change == "duplicate":
        wire.pools.node_pools.append(copy.deepcopy(wire.pools.node_pools[0]))
    elif change == "name":
        extra_pool(wire)
        wire.pools.node_pools[-1].name = "INVALID-NAME"
    else:
        for index in range(64):
            row = copy.deepcopy(wire.pools.node_pools[0])
            row.name = f"extra-{index}"
            wire.pools.node_pools.append(row)
    with pytest.raises(ApplyError, match="NODE_CONFIGURATION_UNVERIFIED"):
        MemoryHooks(plan).call(driver, bodies)
    assert not state["effects"]
