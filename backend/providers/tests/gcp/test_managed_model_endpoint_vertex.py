"""Vertex resource/LRO, ownership and legacy boundaries; no cloud effects (#2277)."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, SnapshotHandle, UpdateSpec
from gcp.managed.model_endpoint_vertex import VertexAIEndpointError

from .test_vertex_resource_operations_2277 import (
    ENDPOINT_NAME,
    SOURCE_ID,
    VertexWire,
    _driver,
    _spec,
    advance,
    provisioned,
)


def handle():
    return ServiceHandle(handle=f"model_endpoint/{ENDPOINT_NAME}", managed_service_id=SOURCE_ID)


def new_steps(state):
    return {k: copy.deepcopy(v) for k, v in state.items() if k != "steps"}


def test_actual_binding_and_fresh_replay_are_read_only():
    wire, state = provisioned()
    wire.calls.clear()
    for _ in range(3):
        driver = _driver(wire, state)
        assert driver.provision(_spec()).ready
        assert driver.status(handle()).state == "available"
        binding = driver.binding(handle())
        assert binding.env_vars["VERTEX_ENDPOINT_ID"].literal == "734159286"
        assert binding.env_vars["VERTEX_DEPLOYED_MODEL_ID"].literal == "851729643"
        assert binding.env_vars["MODEL_API_STYLE"].literal == "vertex_ai"
        assert binding.env_vars["MODEL_AUTH_MODE"].literal == "cloud_identity"
        assert binding.iam_grants[0].resource == ENDPOINT_NAME
        assert binding.iam_grants[0].actions == ["roles/aiplatform.user"]
    assert all(name == "get" for name, _ in wire.calls)


@pytest.mark.parametrize("replacement", ["foreign", "missing", "conflicting"])
def test_reads_and_effects_refuse_unproved_endpoint_owner(replacement):
    wire, state = provisioned()
    if replacement == "foreign":
        wire.endpoint.labels.clear()
        wire.endpoint.labels["astrolift-managed-service-id"] = "317a6c83-c7b6-4794-807a-49d86d9a2fe4"
    elif replacement == "missing":
        wire.endpoint.labels.clear()
    else:
        wire.endpoint.labels["astrolift_io_managed_service_id"] = "317a6c83-c7b6-4794-807a-49d86d9a2fe4"
    wire.calls.clear()
    driver = _driver(wire, state)
    assert driver.status(handle()).state == "error"
    with pytest.raises(VertexAIEndpointError):
        driver.binding(handle())
    assert not driver.provision(_spec()).ok
    assert not driver.update(UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID)).ok
    assert not driver.deprovision(DeprovisionSpec(handle=handle().handle, managed_service_id=SOURCE_ID)).ok
    assert all(name in {"get", "list"} for name, _ in wire.calls)


@pytest.mark.parametrize(
    "target",
    [
        "model_endpoint/projects/foreign/locations/us-central1/endpoints/734159286",
        "model_endpoint/projects/fixture-project/locations/europe-west1/endpoints/734159286",
        "redis/projects/fixture-project/locations/us-central1/endpoints/734159286",
        "model_endpoint/projects/fixture-project/locations/us-central1/endpoints/",
        "model_endpoint/../../foreign",
    ],
)
def test_foreign_invalid_target_never_reaches_transport(target):
    wire = VertexWire()
    assert _driver(wire).status(ServiceHandle(handle=target, managed_service_id=SOURCE_ID)).state == "error"
    assert not wire.calls


def test_default_driver_requires_committed_write_port():
    wire = VertexWire()
    result = _driver(wire).provision(_spec())
    assert not result.ok and "committed lifecycle reservation" in result.message
    assert all(name in {"get", "list"} for name, _ in wire.calls)


def test_legacy_display_recovers_resource_but_never_fabricates_ready_identity():
    wire, _ = provisioned()
    wire.calls.clear()
    legacy = ServiceHandle(handle=f"model_endpoint/{wire.endpoint.display_name}", managed_service_id=SOURCE_ID)
    result = _driver(wire).status(legacy)
    assert result.state == "error" and "operation identity" in result.message
    assert all(request.name == ENDPOINT_NAME for name, request in wire.calls if name == "get")


@pytest.mark.parametrize("phase", ["create", "deploy"])
def test_unknown_intent_is_never_resent(phase):
    wire, state = provisioned() if phase == "deploy" else (VertexWire(), {})
    if phase == "deploy":
        del wire.endpoint.deployed_models[:]
        state.pop("deployed_model_id")
    state.setdefault("steps", {})[phase] = {"state": "reserved", "nonce": "lost"}
    wire.calls.clear()
    result = _driver(wire, state).provision(_spec())
    assert not result.ok and "unknown" in result.message
    assert all(name in {"get", "list"} for name, _ in wire.calls)


def test_exact_observed_deployment_does_not_prove_lost_operation_complete():
    wire, state = provisioned()
    state.pop("deployed_model_id")
    state["steps"]["deploy"] = {"state": "reserved", "nonce": "lost"}
    wire.calls.clear()
    result = _driver(wire, state).provision(_spec())
    assert not result.ok and not result.ready and "outcome is unknown" in result.message
    assert all(name == "get" for name, _ in wire.calls)


def test_partial_endpoint_is_not_bindable():
    wire, state = provisioned()
    del wire.endpoint.deployed_models[:]
    assert _driver(wire, state).status(handle()).state == "error"
    with pytest.raises(VertexAIEndpointError):
        _driver(wire, state).binding(handle())


@pytest.mark.parametrize("available", [0, 1])
def test_completed_lro_without_available_minimum_is_not_ready_or_bindable(available):
    wire, state = provisioned()
    model = wire.endpoint.deployed_models[0]
    model.dedicated_resources.min_replica_count = 2
    model.status.available_replica_count = available
    wire.calls.clear()
    driver = _driver(wire, state)
    assert driver.status(handle()).state == "provisioning"
    result = driver.provision(_spec())
    assert not result.ok and not result.ready and "replicas" in result.message
    with pytest.raises(VertexAIEndpointError, match="completed, owned, observed"):
        driver.binding(handle())
    assert all(name == "get" for name, _ in wire.calls)


def test_missing_replica_status_is_unknown_not_healthy():
    wire, state = provisioned()
    wire.endpoint.deployed_models[0].status = {}
    assert _driver(wire, state).status(handle()).state == "provisioning"


def test_traffic_write_without_observed_etag_is_refused():
    wire, state = provisioned()
    wire.endpoint.etag = ""
    wire.calls.clear()
    result = _driver(wire, new_steps(state)).update(
        UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID, config={"traffic_percentage": 0})
    )
    assert not result.ok and "etag" in result.message
    assert all(name == "get" for name, _ in wire.calls)


def test_replica_update_binds_output_id_and_supported_mask():
    wire, state = provisioned()
    state = new_steps(state)
    spec = UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID, config={"min_replica_count": 2})
    advance(wire, state, "update", spec)
    assert advance(wire, state, "update", spec).complete
    request = [req for name, req in wire.calls if name == "mutate"][-1]
    assert request.deployed_model.id == "851729643"
    assert list(request.update_mask.paths) == ["dedicated_resources.min_replica_count"]
    assert not request.deployed_model.dedicated_resources.machine_spec.machine_type


@pytest.mark.parametrize("outcome", ["completed", "pending", "failed"])
def test_update_result_matches_actual_native_operation_outcome_without_resubmission(outcome):
    from google.rpc import status_pb2

    wire, state = provisioned()
    state = new_steps(state)
    spec = UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID, config={"min_replica_count": 2})
    advance(wire, state, "update", spec)
    operation = wire.operations[state["steps"]["mutate"]["operation"]]
    if outcome == "pending":
        operation.done = False
        operation.ClearField("response")
    elif outcome == "failed":
        operation.ClearField("response")
        operation.error.CopyFrom(status_pb2.Status(code=3, message="controlled mutation refused"))
    result = _driver(wire, state).update(spec)
    assert result.ok is (outcome == "completed")
    assert result.handle == handle().handle
    if outcome == "pending":
        assert "pending" in result.message and not result.errors
    elif outcome == "failed":
        assert "operation failed" in result.message
        assert result.errors == ["vertex_refused"] and result.retryable is False
    else:
        assert wire.endpoint.deployed_models[0].dedicated_resources.min_replica_count == 2
        assert not result.errors
    assert len([request for name, request in wire.calls if name == "mutate"]) == 1


def test_mutation_receipt_for_other_deployment_cannot_confirm_update():
    from google.cloud import aiplatform_v1

    wire, state = provisioned()
    state = new_steps(state)
    spec = UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID, config={"min_replica_count": 2})
    advance(wire, state, "update", spec)
    operation = wire.operations[state["steps"]["mutate"]["operation"]]
    foreign = aiplatform_v1.DeployedModel(wire.endpoint.deployed_models[0])
    foreign.id = "451398712"
    operation.response.Pack(
        aiplatform_v1.MutateDeployedModelResponse.pb(aiplatform_v1.MutateDeployedModelResponse(deployed_model=foreign))
    )
    result = _driver(wire, state).update(spec)
    assert not result.ok and "deployment changed" in result.message
    assert len([request for name, request in wire.calls if name == "mutate"]) == 1


@pytest.mark.parametrize("config", [{"machine_type": "n1-standard-8"}, {"traffic_percentage": 50}])
def test_unsupported_update_has_no_false_applied_result(config):
    wire, state = provisioned()
    wire.calls.clear()
    result = _driver(wire, state).update(
        UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID, config=config)
    )
    assert not result.ok and not result.retryable
    assert all(name == "get" for name, _ in wire.calls)


@pytest.mark.parametrize("traffic", [0, 100])
def test_traffic_update_has_actual_endpoint_and_required_mask(traffic):
    wire, state = provisioned()
    state = new_steps(state)
    wire.endpoint.traffic_split.clear()
    wire.endpoint.traffic_split["851729643"] = 100 if traffic == 0 else 0
    spec = UpdateSpec(handle=handle().handle, managed_service_id=SOURCE_ID, config={"traffic_percentage": traffic})
    advance(wire, state, "update", spec)
    assert advance(wire, state, "update", spec).complete
    request = [req for name, req in wire.calls if name == "traffic"][-1]
    assert request.endpoint.name == ENDPOINT_NAME
    assert list(request.update_mask.paths) == ["traffic_split"]
    assert dict(request.endpoint.traffic_split) == ({"851729643": 100} if traffic else {})


def test_live_traffic_and_unproved_artifact_delete_are_refused():
    wire, state = provisioned()
    spec = DeprovisionSpec(handle=handle().handle, managed_service_id=SOURCE_ID)
    wire.calls.clear()
    assert not _driver(wire, state).deprovision(spec).ok
    result = _driver(wire, state).deprovision(spec, delete_data=True, force_destroy=True)
    assert not result.ok and "ownership is not proven" in result.message
    assert all(name == "get" for name, _ in wire.calls)


def test_undeploy_and_delete_confirm_distinct_operations_before_success():
    wire, state = provisioned()
    state = new_steps(state)
    spec = DeprovisionSpec(handle=handle().handle, managed_service_id=SOURCE_ID)
    for _ in range(5):
        plan = advance(wire, state, "deprovision", spec, force_destroy=True)
        if plan.complete:
            break
    assert plan.complete and "artifact preserved" in plan.message and wire.endpoint is None
    assert [name for name, _ in wire.calls if name in {"undeploy", "delete"}] == ["undeploy", "delete"]
    assert state["steps"]["undeploy"]["operation"] != state["steps"]["delete"]["operation"]


def test_recorded_absent_resource_and_unresolved_legacy_are_distinct():
    wire = VertexWire()
    assert _driver(wire).deprovision(DeprovisionSpec(handle=handle().handle, managed_service_id=SOURCE_ID)).ok
    unknown = _driver(wire).deprovision(
        DeprovisionSpec(handle="model_endpoint/old-display", managed_service_id=SOURCE_ID)
    )
    assert not unknown.ok and "cannot be proved" in unknown.message


def test_no_fabricated_snapshot_or_restore_and_no_publisher_deploy():
    wire, state = provisioned()
    with pytest.raises(VertexAIEndpointError, match="no endpoint snapshot"):
        _driver(wire, state).snapshot(handle())
    snapshot = SnapshotHandle(handle=handle().handle, snapshot_id="old-fabricated-snapshot", created_at="")
    assert not _driver(wire, state).restore(snapshot, _spec()).ok
    wire.calls.clear()
    result = _driver(wire).provision(replace(_spec(), config={"model_artifact": "publishers/google/models/text-bison"}))
    assert not result.ok and not wire.calls


def test_schema_and_display_contract():
    driver = _driver(VertexWire())
    assert set(driver.config_schema()["properties"]) == {
        "model_artifact",
        "machine_type",
        "min_replica_count",
        "max_replica_count",
        "traffic_percentage",
        "public_endpoint_enabled",
        "network",
    }
    assert "VERTEX_ENDPOINT_ID" in driver.binding_schema().env_vars
    display = driver._base_name_for(spec=replace(_spec(), organization_slug="ACME!", app_slug="My API"))
    assert len(display) <= 40 and display[0].isalpha() and "--" not in display
