"""Tests for GCP VertexAIEndpointDriver (#376).

Same fake-client pattern as the Matching Engine driver -- no GCP
emulator exists for Vertex AI endpoints, so we drive the SDK via
call-recording fakes that return canned responses.

Test surface mirrors the AWS Bedrock driver tests: provision
(idempotent, deployed-model wired, public-endpoint flag), update,
four-corner deprovision (delete_data x force_destroy with the
live-traffic guard), status state-mapping, binding env-var shape,
snapshot + restore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from gcp.managed.model_endpoint_vertex import (
    KIND,
    VertexAIEndpointConfig,
    VertexAIEndpointDriver,
    VertexAIEndpointError,
    _deployed_model_id_for,
)

# ---- fakes -----------------------------------------------------------


@dataclass
class FakeDeployedModel:
    id: str
    model: str
    display_name: str = ""
    dedicated_resources: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeEndpoint:
    name: str
    display_name: str
    deployed_models: list[FakeDeployedModel] = field(default_factory=list)
    traffic_split: dict[str, int] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    network: str = ""
    state: str = "READY"


class FakeEndpointClient:
    def __init__(self):
        self.endpoints: dict[str, FakeEndpoint] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, op: str, **kwargs: Any) -> None:
        self.calls.append((op, kwargs))

    def create_endpoint(self, *, request: dict[str, Any]) -> None:
        self._record("create_endpoint", request=request)
        ep_body = request["endpoint"]
        display = ep_body["display_name"]
        name = request["parent"] + "/endpoints/" + display
        self.endpoints[display] = FakeEndpoint(
            name=name,
            display_name=display,
            labels=ep_body.get("labels", {}),
            network=str(ep_body.get("network", "")),
        )

    def get_endpoint(self, *, request: dict[str, Any]) -> FakeEndpoint:
        self._record("get_endpoint", request=request)
        display = request["name"].rsplit("/", 1)[-1]
        if display not in self.endpoints:
            raise RuntimeError("404 NotFound")
        return self.endpoints[display]

    def delete_endpoint(self, *, request: dict[str, Any]) -> None:
        self._record("delete_endpoint", request=request)
        display = request["name"].rsplit("/", 1)[-1]
        if display not in self.endpoints:
            raise RuntimeError("404 NotFound")
        del self.endpoints[display]

    def deploy_model(self, *, request: dict[str, Any]) -> None:
        self._record("deploy_model", request=request)
        ep_display = request["endpoint"].rsplit("/", 1)[-1]
        if ep_display not in self.endpoints:
            raise RuntimeError("404 NotFound endpoint")
        dm = request["deployed_model"]
        self.endpoints[ep_display].deployed_models.append(
            FakeDeployedModel(
                id=dm["id"],
                model=dm["model"],
                display_name=dm.get("display_name", ""),
                dedicated_resources=dm.get("dedicated_resources", {}),
            ),
        )
        traffic = request.get("traffic_split", {})
        self.endpoints[ep_display].traffic_split.update(traffic)

    def undeploy_model(self, *, request: dict[str, Any]) -> None:
        self._record("undeploy_model", request=request)
        ep_display = request["endpoint"].rsplit("/", 1)[-1]
        di_id = request["deployed_model_id"]
        ep = self.endpoints.get(ep_display)
        if ep is None:
            raise RuntimeError("404 NotFound")
        ep.deployed_models = [d for d in ep.deployed_models if d.id != di_id]
        ep.traffic_split.pop(di_id, None)

    def mutate_deployed_model(self, *, request: dict[str, Any]) -> None:
        self._record("mutate_deployed_model", request=request)
        ep_display = request["endpoint"].rsplit("/", 1)[-1]
        ep = self.endpoints.get(ep_display)
        if ep is None:
            raise RuntimeError("404 NotFound")
        dm = request["deployed_model"]
        for d in ep.deployed_models:
            if d.id == dm["id"]:
                d.dedicated_resources = dm.get("dedicated_resources", {})
                return
        raise RuntimeError("deployed model not found")

    def update_endpoint(self, *, request: dict[str, Any]) -> None:
        self._record("update_endpoint", request=request)
        ep_body = request["endpoint"]
        ep_display = ep_body["name"].rsplit("/", 1)[-1]
        ep = self.endpoints.get(ep_display)
        if ep is None:
            raise RuntimeError("404 NotFound")
        if "traffic_split" in ep_body:
            ep.traffic_split = dict(ep_body["traffic_split"])


class FakeModelClient:
    def __init__(self):
        self.deleted: list[str] = []

    def delete_model(self, *, request: dict[str, Any]) -> None:
        self.deleted.append(request["name"])


# ---- fixtures ---------------------------------------------------


@pytest.fixture
def endpoint_client() -> FakeEndpointClient:
    return FakeEndpointClient()


@pytest.fixture
def model_client() -> FakeModelClient:
    return FakeModelClient()


@pytest.fixture
def driver(
    endpoint_client: FakeEndpointClient,
    model_client: FakeModelClient,
) -> VertexAIEndpointDriver:
    return VertexAIEndpointDriver(
        config=VertexAIEndpointConfig(
            project_id="acme-prod",
            region="us-west1",
        ),
        endpoint_client=endpoint_client,
        model_client=model_client,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="gcp-prod",
        service_handle_hint="m",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_endpoint_and_deploys_model(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind = result.handle.split("/", 1)[0]
    base_name = result.handle.split("/", 1)[1]
    assert kind == KIND
    assert base_name in endpoint_client.endpoints
    ep = endpoint_client.endpoints[base_name]
    assert len(ep.deployed_models) == 1
    # Default traffic is 100 to the single deployed model
    di_id = ep.deployed_models[0].id
    assert ep.traffic_split.get(di_id) == 100


def test_provision_idempotent(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    # No double-create on the endpoint
    create_calls = [c for op, c in endpoint_client.calls if op == "create_endpoint"]
    assert len(create_calls) == 1


def test_provision_defaults_to_text_bison(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec())
    deploy = next(c for op, c in endpoint_client.calls if op == "deploy_model")
    assert deploy["request"]["deployed_model"]["model"] == "publishers/google/models/text-bison"


def test_provision_larger_size_picks_gemini_pro(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec(size="large"))
    deploy = next(c for op, c in endpoint_client.calls if op == "deploy_model")
    assert deploy["request"]["deployed_model"]["model"] == "publishers/google/models/gemini-pro"


def test_provision_honours_model_artifact_override(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(
        _spec(
            config={
                "model_artifact": ("projects/acme-prod/locations/us-west1/models/custom"),
            },
        ),
    )
    deploy = next(c for op, c in endpoint_client.calls if op == "deploy_model")
    assert deploy["request"]["deployed_model"]["model"].endswith(
        "/models/custom",
    )


def test_provision_size_to_machine_type(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec(size="large"))
    deploy = next(c for op, c in endpoint_client.calls if op == "deploy_model")
    dedicated = deploy["request"]["deployed_model"]["dedicated_resources"]
    assert dedicated["machine_spec"]["machine_type"] == "n1-standard-8"
    assert dedicated["min_replica_count"] == 2


def test_provision_labels_with_astrolift_namespace(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec())
    create = next(c for op, c in endpoint_client.calls if op == "create_endpoint")
    labels = create["request"]["endpoint"]["labels"]
    assert labels["astrolift-managed-by"] == "platform"
    assert labels["astrolift-app"] == "api"


def test_provision_public_endpoint_off_by_default(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec())
    create = next(c for op, c in endpoint_client.calls if op == "create_endpoint")
    # public_endpoint_enabled isn't passed when off by default
    assert (
        "public_endpoint_enabled" not in create["request"]["endpoint"]
        or create["request"]["endpoint"]["public_endpoint_enabled"] is False
    )


def test_provision_surfaces_create_endpoint_error(
    endpoint_client: FakeEndpointClient,
    model_client: FakeModelClient,
) -> None:
    def boom(**_kwargs):
        raise RuntimeError("VPC peering required")

    endpoint_client.create_endpoint = boom  # type: ignore[assignment]
    d = VertexAIEndpointDriver(
        config=VertexAIEndpointConfig(
            project_id="p",
            region="us-west1",
        ),
        endpoint_client=endpoint_client,
        model_client=model_client,
    )
    result = d.provision(_spec())
    assert not result.ok
    assert "VPC peering required" in result.message


# ---- update -----------------------------------------------------


def test_update_resize_mutates_deployed_model(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="large"),
    )
    assert result.ok
    mutate = next(c for op, c in endpoint_client.calls if op == "mutate_deployed_model")
    dedicated = mutate["request"]["deployed_model"]["dedicated_resources"]
    assert dedicated["machine_spec"]["machine_type"] == "n1-standard-8"


def test_update_traffic_percentage_calls_update_endpoint(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"traffic_percentage": 50},
        ),
    )
    assert result.ok
    update = next(c for op, c in endpoint_client.calls if op == "update_endpoint")
    ep_body = update["request"]["endpoint"]
    di_id = next(iter(ep_body["traffic_split"]))
    assert ep_body["traffic_split"][di_id] == 50


def test_update_noop_when_nothing_to_change(
    driver: VertexAIEndpointDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_live_traffic(
    driver: VertexAIEndpointDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert not result.ok
    assert "live traffic" in result.message


def test_deprovision_default_succeeds_when_no_traffic(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    # Drain traffic before deprovision
    endpoint_client.endpoints[base_name].traffic_split = {}
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
    )
    assert result.ok
    assert "artifact=preserved" in result.message
    assert base_name not in endpoint_client.endpoints


def test_deprovision_force_destroy_bypasses_traffic_check(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert base_name not in endpoint_client.endpoints


def test_deprovision_delete_data_atomic_drains_and_deletes(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "artifact=deleted" in result.message
    assert base_name not in endpoint_client.endpoints


def test_deprovision_delete_data_preserves_publisher_artifact(
    driver: VertexAIEndpointDriver,
    model_client: FakeModelClient,
) -> None:
    """publishers/google/* artifacts are first-party foundation
    models -- delete_data=True must NOT attempt to drop them."""
    provisioned = driver.provision(_spec())
    driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    # Nothing deleted on the model client; the publishers/ artifact
    # was correctly recognised as not-owned.
    assert model_client.deleted == []


def test_deprovision_delete_data_drops_owned_artifact(
    driver: VertexAIEndpointDriver,
    model_client: FakeModelClient,
) -> None:
    custom_artifact = "projects/acme-prod/locations/us-west1/models/custom-llama"
    provisioned = driver.provision(
        _spec(config={"model_artifact": custom_artifact}),
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert custom_artifact in model_client.deleted


def test_deprovision_idempotent_when_already_gone(
    driver: VertexAIEndpointDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle=f"{KIND}/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: VertexAIEndpointDriver,
) -> None:
    state = driver.status(ServiceHandle(handle=f"{KIND}/missing"))
    assert state.state == "deprovisioned"


def test_status_for_endpoint_with_models_is_available(
    driver: VertexAIEndpointDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_for_empty_endpoint_is_provisioning(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    endpoint_client.endpoints[base_name].deployed_models = []
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "provisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_connection_envelope(
    driver: VertexAIEndpointDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    env = binding.env_vars
    for key in (
        "MODEL_ENDPOINT_URL",
        "MODEL_ENDPOINT_MODEL_ID",
        "MODEL_ENDPOINT_PROVIDER",
        "VERTEX_PROJECT_ID",
        "VERTEX_REGION",
        "VERTEX_ENDPOINT_ID",
        "VERTEX_DEPLOYED_MODEL_ID",
    ):
        assert key in env
    assert env["MODEL_ENDPOINT_PROVIDER"].literal == "vertex_ai"
    assert env["VERTEX_PROJECT_ID"].literal == "acme-prod"
    assert env["VERTEX_REGION"].literal == "us-west1"


def test_binding_iam_grants_cover_predict(
    driver: VertexAIEndpointDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(
        ServiceHandle(handle=provisioned.handle),
    )
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "aiplatform.endpoints.predict" in actions


def test_binding_for_missing_raises(
    driver: VertexAIEndpointDriver,
) -> None:
    with pytest.raises(VertexAIEndpointError):
        driver.binding(ServiceHandle(handle=f"{KIND}/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_deterministic_id(
    driver: VertexAIEndpointDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    base_name = provisioned.handle.split("/", 1)[1]
    assert snap.snapshot_id.startswith(base_name)


def test_snapshot_for_missing_raises(
    driver: VertexAIEndpointDriver,
) -> None:
    with pytest.raises(VertexAIEndpointError):
        driver.snapshot(ServiceHandle(handle=f"{KIND}/missing"))


def test_restore_provisions_target_endpoint(
    driver: VertexAIEndpointDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))

    restore_spec = _spec(service_handle_hint="restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_name = result.handle.split("/", 1)[1]
    assert target_name in endpoint_client.endpoints


# ---- naming + helpers -------------------------------------------


def test_base_name_canonicalization(
    driver: VertexAIEndpointDriver,
) -> None:
    name = driver._base_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
            service_handle_hint="m",
        ),
    )
    assert 1 <= len(name) <= 40
    assert name[0].isalpha()
    for c in name:
        assert c.islower() or c.isdigit() or c == "-"
    assert "--" not in name


def test_deployed_model_id_for_starts_with_letter() -> None:
    assert _deployed_model_id_for(base_name="123-foo").startswith("d")


def test_deployed_model_id_for_swaps_hyphens() -> None:
    assert "-" not in _deployed_model_id_for(
        base_name="acme-prod-m",
    )


def test_handle_round_trip(driver: VertexAIEndpointDriver) -> None:
    handle = driver._handle_for(  # type: ignore[attr-defined]
        base_name="my-endpoint",
    )
    assert handle.startswith(f"{KIND}/")
    assert (
        driver._base_name_from_handle(handle)  # type: ignore[attr-defined]
        == "my-endpoint"
    )


def test_handle_rejects_malformed(
    driver: VertexAIEndpointDriver,
) -> None:
    with pytest.raises(VertexAIEndpointError):
        driver._base_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(VertexAIEndpointError):
        driver._base_name_from_handle("model_endpoint/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(
    driver: VertexAIEndpointDriver,
) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "model_artifact",
        "machine_type",
        "min_replica_count",
        "max_replica_count",
        "traffic_percentage",
        "public_endpoint_enabled",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: VertexAIEndpointDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "MODEL_ENDPOINT_URL",
        "MODEL_ENDPOINT_MODEL_ID",
        "MODEL_ENDPOINT_PROVIDER",
        "VERTEX_PROJECT_ID",
        "VERTEX_REGION",
        "VERTEX_ENDPOINT_ID",
        "VERTEX_DEPLOYED_MODEL_ID",
    ):
        assert key in schema.env_vars
