"""Review regressions against actual Vertex protobuf observations (#2277)."""

from dataclasses import replace

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
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


def test_absent_endpoint_cannot_claim_unproved_model_artifact_deletion():
    wire, state = provisioned()
    wire.endpoint = None
    wire.calls.clear()
    result = _driver(wire, state).deprovision(
        DeprovisionSpec(handle=f"model_endpoint/{ENDPOINT_NAME}", managed_service_id=SOURCE_ID),
        delete_data=True,
        force_destroy=True,
    )
    assert not result.ok and "delete_data refused" in result.message
    assert not [name for name, _ in wire.calls if name not in {"get", "list"}]


@pytest.mark.parametrize("divergence", ["minimum", "maximum", "machine", "traffic"])
def test_completed_deploy_requires_requested_serving_configuration(divergence):
    spec = replace(
        _spec(),
        config={**_spec().config, "min_replica_count": 2, "max_replica_count": 4, "machine_type": "n1-standard-4"},
    )
    wire, state = VertexWire(), {}
    for _ in range(5):
        if advance(wire, state, spec=spec).complete:
            break
    else:
        raise AssertionError("controlled deployment did not complete")
    model = wire.endpoint.deployed_models[0]
    if divergence == "minimum":
        model.dedicated_resources.min_replica_count = 1
        model.status.available_replica_count = 1
    elif divergence == "maximum":
        model.dedicated_resources.max_replica_count = 3
    elif divergence == "machine":
        model.dedicated_resources.machine_spec.machine_type = "n1-standard-2"
    else:
        wire.endpoint.traffic_split.clear()
    wire.calls.clear()
    driver = _driver(wire, state)
    result = driver.provision(spec)
    assert not result.ok and not result.ready
    assert (
        driver.status(ServiceHandle(handle=f"model_endpoint/{ENDPOINT_NAME}", managed_service_id=SOURCE_ID)).state
        != "available"
    )
    with pytest.raises(VertexAIEndpointError):
        driver.binding(ServiceHandle(handle=f"model_endpoint/{ENDPOINT_NAME}", managed_service_id=SOURCE_ID))
    assert all(name == "get" for name, _ in wire.calls)


def test_unknown_serving_intent_is_not_inferred_from_native_resources():
    wire, state = provisioned()
    state.pop("serving_request", None)
    wire.calls.clear()
    driver = _driver(wire, state)
    assert (
        driver.status(ServiceHandle(handle=f"model_endpoint/{ENDPOINT_NAME}", managed_service_id=SOURCE_ID)).state
        != "available"
    )
    result = driver.update(
        UpdateSpec(
            handle=f"model_endpoint/{ENDPOINT_NAME}", managed_service_id=SOURCE_ID, config={"min_replica_count": 2}
        )
    )
    assert not result.ok and "not recorded" in result.message
    assert all(name == "get" for name, _ in wire.calls)
