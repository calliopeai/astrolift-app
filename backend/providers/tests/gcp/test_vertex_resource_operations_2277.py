"""Actual Vertex protobuf/LRO shapes; resource ids deliberately differ from labels."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from google.api_core.operation import from_gapic
from google.cloud import aiplatform_v1
from google.longrunning import operations_pb2
from google.protobuf import any_pb2
from google.protobuf.empty_pb2 import Empty
from google.rpc import status_pb2

from _sdk.managed_service import ProvisionSpec
from gcp.managed.model_endpoint_vertex import VertexAIEndpointConfig, VertexAIEndpointDriver

SOURCE_ID = "d83a0353-7d44-4ec3-b0b7-57ad3cb4cd09"
PARENT = "projects/fixture-project/locations/us-central1"
ENDPOINT_NAME = f"{PARENT}/endpoints/734159286"


class VertexWire:
    """No cloud transport. Generated request/response types enforce wire semantics."""

    def __init__(self, *, create_pending: bool = False, create_failed: bool = False):
        self.calls: list[tuple[str, Any]] = []
        self.operations: dict[str, operations_pb2.Operation] = {}
        self.endpoint = None
        self.create_pending = create_pending
        self.create_failed = create_failed

    def get_operation(self, name: str, **kwargs):
        return self.operations[name]

    def cancel_operation(self, name: str, **kwargs):
        raise AssertionError("this lifecycle must not cancel an operation")

    def _operation(self, kind, result, *, pending=False, failed=False):
        name = f"{PARENT}/operations/{kind}-491"
        response = any_pb2.Any()
        response.Pack(type(result).pb(result) if hasattr(type(result), "pb") else result)
        operation = operations_pb2.Operation(name=name, done=not pending)
        if failed:
            operation.error.CopyFrom(status_pb2.Status(code=3, message="fixture operation refused"))
        elif not pending:
            operation.response.CopyFrom(response)
        self.operations[name] = operation
        return from_gapic(operation, self, type(result))

    def list_endpoints(self, *, request, **kwargs):
        typed = aiplatform_v1.ListEndpointsRequest(request)
        self.calls.append(("list", typed))
        return aiplatform_v1.ListEndpointsResponse(endpoints=[self.endpoint] if self.endpoint else [])

    def get_endpoint(self, *, request, **kwargs):
        typed = aiplatform_v1.GetEndpointRequest(request)
        self.calls.append(("get", typed))
        if self.endpoint is None or typed.name != self.endpoint.name:
            from google.api_core.exceptions import NotFound

            raise NotFound("fixture endpoint absent")
        return self.endpoint

    def create_endpoint(self, *, request, **kwargs):
        typed = aiplatform_v1.CreateEndpointRequest(request)
        self.calls.append(("create", typed))
        endpoint = aiplatform_v1.Endpoint(
            name=ENDPOINT_NAME, display_name=typed.endpoint.display_name, labels=typed.endpoint.labels, etag="etag-491"
        )
        if not self.create_pending and not self.create_failed:
            self.endpoint = endpoint
        return self._operation("create", endpoint, pending=self.create_pending, failed=self.create_failed)

    def deploy_model(self, *, request, **kwargs):
        typed = aiplatform_v1.DeployModelRequest(request)
        self.calls.append(("deploy", typed))
        assert typed.endpoint == ENDPOINT_NAME, "display_name must never be used as endpoint resource id"
        assert not typed.deployed_model.id, "this driver must record the server-assigned numeric id"
        assert dict(typed.traffic_split) == {"0": 100}, "new model uses the API's zero placeholder"
        deployed = aiplatform_v1.DeployedModel(typed.deployed_model)
        deployed.id = "851729643"
        deployed.status.available_replica_count = deployed.dedicated_resources.min_replica_count
        self.endpoint.deployed_models.append(deployed)
        self.endpoint.traffic_split[deployed.id] = 100
        return self._operation("deploy", aiplatform_v1.DeployModelResponse(deployed_model=deployed))

    def mutate_deployed_model(self, *, request, **kwargs):
        typed = aiplatform_v1.MutateDeployedModelRequest(request)
        self.calls.append(("mutate", typed))
        assert typed.endpoint == ENDPOINT_NAME
        assert typed.deployed_model.id == "851729643"
        assert all(
            path in {"dedicated_resources.min_replica_count", "dedicated_resources.max_replica_count"}
            for path in typed.update_mask.paths
        )
        model = self.endpoint.deployed_models[0]
        for path in typed.update_mask.paths:
            key = path.rsplit(".", 1)[-1]
            setattr(model.dedicated_resources, key, getattr(typed.deployed_model.dedicated_resources, key))
        model.status.available_replica_count = model.dedicated_resources.min_replica_count
        return self._operation("mutate", aiplatform_v1.MutateDeployedModelResponse(deployed_model=model))

    def update_endpoint(self, *, request, **kwargs):
        typed = aiplatform_v1.UpdateEndpointRequest(request)
        self.calls.append(("traffic", typed))
        assert typed.endpoint.name == ENDPOINT_NAME
        assert list(typed.update_mask.paths) == ["traffic_split"]
        assert typed.endpoint.etag == self.endpoint.etag
        self.endpoint.traffic_split.clear()
        self.endpoint.traffic_split.update(typed.endpoint.traffic_split)
        return self.endpoint

    def undeploy_model(self, *, request, **kwargs):
        typed = aiplatform_v1.UndeployModelRequest(request)
        self.calls.append(("undeploy", typed))
        assert typed.endpoint == ENDPOINT_NAME and typed.deployed_model_id == "851729643"
        assert not typed.traffic_split
        del self.endpoint.deployed_models[:]
        self.endpoint.traffic_split.clear()
        return self._operation("undeploy", aiplatform_v1.UndeployModelResponse())

    def delete_endpoint(self, *, request, **kwargs):
        typed = aiplatform_v1.DeleteEndpointRequest(request)
        self.calls.append(("delete", typed))
        assert typed.name == ENDPOINT_NAME and not self.endpoint.deployed_models
        self.endpoint = None
        return self._operation("delete", Empty())


def _driver(wire, state=None, **ports):
    return VertexAIEndpointDriver(
        config=VertexAIEndpointConfig(
            project_id="fixture-project", region="us-central1", operation_state=state or {}, **ports
        ),
        endpoint_client=wire,
        model_client=object(),
    )


def _spec():
    return ProvisionSpec(
        organization_id="a1a452d5-93ad-4c4d-bdee-893f320662f9",
        organization_slug="fixture-org",
        app_id="5379473f-a172-469d-86dd-d38ef5291071",
        app_slug="human-app",
        environment_id="48e30ed5-e7f9-478f-bb18-ae4b6bb53257",
        environment_name="production",
        tenant_cluster_id="d2229179-6597-4825-afd6-9b7b19a64b35",
        service_handle_hint="human-model-name",
        size="small",
        managed_service_id=SOURCE_ID,
        config={"model_artifact": f"{PARENT}/models/617492583"},
    )


def advance(wire, state, action="provision", spec=None, **flags):
    spec = spec or _spec()
    driver = _driver(wire, state)
    plan = driver.operation_plan(action, spec, **flags)
    state.clear()
    state.update(plan.state)
    if plan.phase:
        nonce = str(uuid4())
        state.setdefault("steps", {})[plan.phase] = {"state": "reserved", "nonce": nonce}

        def record(receipt):
            state.clear()
            state.update(receipt)

        driver = _driver(wire, state, submit_phase=plan.phase, submit_nonce=nonce, record_operation=record)
        driver.submit_reserved(driver.operation_plan(action, spec, **flags))
    return plan


def provisioned(wire=None):
    wire, state = wire or VertexWire(), {}
    for _ in range(5):
        plan = advance(wire, state)
        if plan.complete:
            return wire, state
    raise AssertionError("fixture did not complete bounded provisioning")


def test_returned_resource_id_and_lro_response_drive_deploy():
    wire = VertexWire()
    wire, state = provisioned(wire)
    result = _driver(wire, state).provision(_spec())
    assert result.ok, result.message
    assert ENDPOINT_NAME in result.handle
    assert [request.endpoint for name, request in wire.calls if name == "deploy"] == [ENDPOINT_NAME]


def test_pending_create_never_submits_deploy():
    wire = VertexWire(create_pending=True)
    state = {}
    advance(wire, state)
    assert advance(wire, state).pending
    assert not [request for name, request in wire.calls if name == "deploy"]


def test_failed_create_lro_never_submits_deploy():
    wire = VertexWire(create_failed=True)
    state = {}
    advance(wire, state)
    with pytest.raises(ValueError, match="operation failed"):
        advance(wire, state)
    assert not [request for name, request in wire.calls if name == "deploy"]


@pytest.mark.parametrize("fault", ["foreign_operation", "missing_response", "wrong_response", "foreign_endpoint"])
def test_unverified_create_receipt_never_advances_paid_deploy(fault):
    wire, state = VertexWire(), {}
    advance(wire, state)
    operation = wire.operations[state["steps"]["create"]["operation"]]
    if fault == "foreign_operation":
        operation.name = "projects/foreign/locations/us-central1/operations/create-491"
    elif fault == "missing_response":
        operation.ClearField("response")
    elif fault == "wrong_response":
        operation.response.Pack(Empty())
    else:
        operation.response.Pack(
            aiplatform_v1.Endpoint.pb(
                aiplatform_v1.Endpoint(name="projects/foreign/locations/us-central1/endpoints/734159286")
            )
        )
    with pytest.raises(ValueError):
        advance(wire, state)
    assert [name for name, _ in wire.calls if name in {"create", "deploy"}] == ["create"]


@pytest.mark.parametrize("fault", ["foreign_model", "invalid_id"])
def test_unverified_deploy_lro_never_reports_ready(fault):
    wire, state = VertexWire(), {}
    advance(wire, state)
    advance(wire, state)
    operation = wire.operations[state["steps"]["deploy"]["operation"]]
    deployed = aiplatform_v1.DeployedModel(wire.endpoint.deployed_models[0])
    if fault == "foreign_model":
        deployed.model = f"{PARENT}/models/foreign"
    else:
        deployed.id = "human-model-name"
    operation.response.Pack(
        aiplatform_v1.DeployModelResponse.pb(aiplatform_v1.DeployModelResponse(deployed_model=deployed))
    )
    result = _driver(wire, state).provision(_spec())
    assert not result.ok and not result.ready
    assert [name for name, _ in wire.calls if name in {"create", "deploy"}] == ["create", "deploy"]


def test_incomplete_recovery_inventory_cannot_be_treated_as_empty_or_create():
    class IncompleteWire(VertexWire):
        def list_endpoints(self, *, request, **kwargs):
            self.calls.append(("list", aiplatform_v1.ListEndpointsRequest(request)))
            return aiplatform_v1.ListEndpointsResponse(next_page_token=f"page-{len(self.calls)}")

    wire = IncompleteWire()
    result = _driver(wire).provision(_spec())
    assert not result.ok and "inventory is incomplete" in result.message
    assert len(wire.calls) == 4 and all(name == "list" for name, _ in wire.calls)
