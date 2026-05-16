"""Tests for GCP Vertex AI Matching Engine driver (#372).

Same fake-client pattern as the CloudSQL / Memorystore tests --
no GCP emulator exists for Vertex Matching Engine, so we drive
the SDK via call-recording fakes that return canned responses.
Test surface mirrors AWS OpenSearchVectorDriver.
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
from gcp.managed.vector_vertex import (
    KIND,
    VertexMatchingEngineConfig,
    VertexMatchingEngineDriver,
    _deployed_index_id_for,
    _ManagedServiceError,
)

# ---- fakes -----------------------------------------------------------


@dataclass
class FakeIndex:
    name: str
    display_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeDeployedIndex:
    id: str
    index: str
    display_name: str = ""
    dedicated_resources: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeIndexEndpoint:
    name: str
    display_name: str
    deployed_indexes: list[FakeDeployedIndex] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    public_endpoint_enabled: bool = False
    network: str = ""
    state: str = "READY"


class FakeIndexClient:
    def __init__(self):
        self.indexes: dict[str, FakeIndex] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, op: str, **kwargs):
        self.calls.append((op, kwargs))

    def create_index(self, *, request):
        self._record("create_index", request=request)
        idx = request["index"]
        name = request["parent"] + "/indexes/" + idx["display_name"]
        self.indexes[idx["display_name"]] = FakeIndex(
            name=name,
            display_name=idx["display_name"],
            metadata=idx.get("metadata", {}),
            labels=idx.get("labels", {}),
        )

    def delete_index(self, *, request):
        self._record("delete_index", request=request)
        # name shape: projects/p/locations/r/indexes/<display>
        display = request["name"].rsplit("/", 1)[-1]
        self.indexes.pop(display, None)


class FakeEndpointClient:
    def __init__(self):
        self.endpoints: dict[str, FakeIndexEndpoint] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, op: str, **kwargs):
        self.calls.append((op, kwargs))

    def create_index_endpoint(self, *, request):
        self._record("create_index_endpoint", request=request)
        ep_body = request["index_endpoint"]
        name = request["parent"] + "/indexEndpoints/" + ep_body["display_name"]
        self.endpoints[ep_body["display_name"]] = FakeIndexEndpoint(
            name=name,
            display_name=ep_body["display_name"],
            labels=ep_body.get("labels", {}),
            public_endpoint_enabled=bool(
                ep_body.get("public_endpoint_enabled", False),
            ),
            network=str(ep_body.get("network", "")),
        )

    def get_index_endpoint(self, *, request):
        self._record("get_index_endpoint", request=request)
        display = request["name"].rsplit("/", 1)[-1]
        if display not in self.endpoints:
            raise RuntimeError("404 NotFound")
        return self.endpoints[display]

    def delete_index_endpoint(self, *, request):
        self._record("delete_index_endpoint", request=request)
        display = request["name"].rsplit("/", 1)[-1]
        if display not in self.endpoints:
            raise RuntimeError("404 NotFound")
        del self.endpoints[display]

    def deploy_index(self, *, request):
        self._record("deploy_index", request=request)
        ep_display = request["index_endpoint"].rsplit("/", 1)[-1]
        if ep_display not in self.endpoints:
            raise RuntimeError("404 NotFound endpoint")
        di = request["deployed_index"]
        self.endpoints[ep_display].deployed_indexes.append(
            FakeDeployedIndex(
                id=di["id"],
                index=di["index"],
                display_name=di.get("display_name", ""),
                dedicated_resources=di.get("dedicated_resources", {}),
            ),
        )

    def undeploy_index(self, *, request):
        self._record("undeploy_index", request=request)
        ep_display = request["index_endpoint"].rsplit("/", 1)[-1]
        di_id = request["deployed_index_id"]
        ep = self.endpoints.get(ep_display)
        if ep is None:
            raise RuntimeError("404 NotFound")
        ep.deployed_indexes = [d for d in ep.deployed_indexes if d.id != di_id]

    def mutate_deployed_index(self, *, request):
        self._record("mutate_deployed_index", request=request)
        ep_display = request["index_endpoint"].rsplit("/", 1)[-1]
        ep = self.endpoints.get(ep_display)
        if ep is None:
            raise RuntimeError("404 NotFound")
        di = request["deployed_index"]
        for d in ep.deployed_indexes:
            if d.id == di["id"]:
                d.dedicated_resources = di.get("dedicated_resources", {})
                return
        raise RuntimeError("deployed index not found")


@dataclass
class FakeBucket:
    name: str
    deleted: bool = False
    force_used_on_delete: bool = False

    def delete(self, *, force: bool = False) -> None:
        self.deleted = True
        self.force_used_on_delete = force


class FakeStorageClient:
    def __init__(self):
        self.buckets: dict[str, FakeBucket] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_bucket(self, name: str, *, location: str = "") -> FakeBucket:
        self.calls.append(
            ("create_bucket", {"name": name, "location": location}),
        )
        if name in self.buckets:
            raise RuntimeError("Conflict: bucket already exists")
        bkt = FakeBucket(name=name)
        self.buckets[name] = bkt
        return bkt

    def get_bucket(self, name: str) -> FakeBucket:
        self.calls.append(("get_bucket", {"name": name}))
        if name not in self.buckets:
            raise RuntimeError("404 NotFound")
        return self.buckets[name]


@pytest.fixture
def index_client() -> FakeIndexClient:
    return FakeIndexClient()


@pytest.fixture
def endpoint_client() -> FakeEndpointClient:
    return FakeEndpointClient()


@pytest.fixture
def storage_client() -> FakeStorageClient:
    return FakeStorageClient()


@pytest.fixture
def driver(
    index_client: FakeIndexClient,
    endpoint_client: FakeEndpointClient,
    storage_client: FakeStorageClient,
) -> VertexMatchingEngineDriver:
    return VertexMatchingEngineDriver(
        config=VertexMatchingEngineConfig(
            project_id="acme-prod",
            region="us-west1",
        ),
        index_client=index_client,
        endpoint_client=endpoint_client,
        storage_client=storage_client,
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
        service_handle_hint="v",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_index_endpoint_and_deploys(
    driver: VertexMatchingEngineDriver,
    index_client: FakeIndexClient,
    endpoint_client: FakeEndpointClient,
    storage_client: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, base_name = result.handle.partition("/")[0::2]
    assert kind == KIND
    assert base_name in index_client.indexes
    assert base_name in endpoint_client.endpoints
    ep = endpoint_client.endpoints[base_name]
    assert len(ep.deployed_indexes) == 1
    # GCS bucket exists for the shard storage
    assert any(b.startswith("astrolift-vec-shards-") for b in storage_client.buckets)


def test_provision_idempotent(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    # No double-create on endpoint
    create_calls = [c for op, c in endpoint_client.calls if op == "create_index_endpoint"]
    assert len(create_calls) == 1


def test_provision_defaults_to_brute_force(
    driver: VertexMatchingEngineDriver,
    index_client: FakeIndexClient,
) -> None:
    driver.provision(_spec())
    create = next(c for op, c in index_client.calls if op == "create_index")
    algo = create["request"]["index"]["metadata"]["config"]["algorithm_config"]
    assert "bruteForceConfig" in algo


def test_provision_supports_tree_ah_algorithm(
    driver: VertexMatchingEngineDriver,
    index_client: FakeIndexClient,
) -> None:
    driver.provision(_spec(config={"algorithm": "TREE_AH"}))
    create = next(c for op, c in index_client.calls if op == "create_index")
    algo = create["request"]["index"]["metadata"]["config"]["algorithm_config"]
    assert "treeAhConfig" in algo
    assert algo["treeAhConfig"]["leafNodeEmbeddingCount"] == 1000


def test_provision_honours_dimension_override(
    driver: VertexMatchingEngineDriver,
    index_client: FakeIndexClient,
) -> None:
    driver.provision(_spec(config={"dimension": 1536}))
    create = next(c for op, c in index_client.calls if op == "create_index")
    assert create["request"]["index"]["metadata"]["config"]["dimensions"] == 1536


def test_provision_size_to_machine_type(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec(size="large"))
    deploy = next(c for op, c in endpoint_client.calls if op == "deploy_index")
    dedicated = deploy["request"]["deployed_index"]["dedicated_resources"]
    assert dedicated["machine_spec"]["machine_type"] == "n1-standard-8"
    assert dedicated["min_replica_count"] == 2


def test_provision_size_to_replicas(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    driver.provision(_spec(size="xlarge"))
    deploy = next(c for op, c in endpoint_client.calls if op == "deploy_index")
    dedicated = deploy["request"]["deployed_index"]["dedicated_resources"]
    assert dedicated["min_replica_count"] == 3


def test_provision_creates_gcs_shard_bucket(
    driver: VertexMatchingEngineDriver,
    storage_client: FakeStorageClient,
) -> None:
    result = driver.provision(_spec())
    base_name = result.handle.split("/", 1)[1]
    # Bucket name embeds the sanitized project_id + base name; we
    # assert on the prefix + base-name presence rather than the
    # exact string because the sanitizer rules are tested separately.
    bucket_names = list(storage_client.buckets.keys())
    assert any(name.startswith("astrolift-vec-shards-") and base_name in name for name in bucket_names)


def test_provision_labels_with_astrolift_namespace(
    driver: VertexMatchingEngineDriver,
    index_client: FakeIndexClient,
) -> None:
    driver.provision(_spec())
    create = next(c for op, c in index_client.calls if op == "create_index")
    labels = create["request"]["index"]["labels"]
    assert labels["astrolift-managed-by"] == "platform"
    assert labels["astrolift-app"] == "api"


def test_provision_public_endpoint_off_by_default(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    result = driver.provision(_spec())
    base_name = result.handle.split("/", 1)[1]
    assert endpoint_client.endpoints[base_name].public_endpoint_enabled is False


def test_provision_public_endpoint_opt_in(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    result = driver.provision(
        _spec(config={"public_endpoint_enabled": True}),
    )
    base_name = result.handle.split("/", 1)[1]
    assert endpoint_client.endpoints[base_name].public_endpoint_enabled is True


# ---- update -----------------------------------------------------


def test_update_resize_mutates_deployed_index(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="medium"),
    )
    assert result.ok
    mutate = next(c for op, c in endpoint_client.calls if op == "mutate_deployed_index")
    dedicated = mutate["request"]["deployed_index"]["dedicated_resources"]
    assert dedicated["machine_spec"]["machine_type"] == "n1-standard-4"


def test_update_explicit_replica_bounds(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={
                "min_replica_count": 4,
                "max_replica_count": 10,
            },
        ),
    )
    assert result.ok
    mutate = next(c for op, c in endpoint_client.calls if op == "mutate_deployed_index")
    dedicated = mutate["request"]["deployed_index"]["dedicated_resources"]
    assert dedicated["min_replica_count"] == 4
    assert dedicated["max_replica_count"] == 10


def test_update_noop_when_nothing_to_change(
    driver: VertexMatchingEngineDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_when_deployed_indexes_present(
    driver: VertexMatchingEngineDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "deployed index" in result.message


def test_deprovision_delete_data_true_atomically_unwinds(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
    index_client: FakeIndexClient,
    storage_client: FakeStorageClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "undeployed=1" in result.message
    assert "bucket=deleted" in result.message
    assert base_name not in endpoint_client.endpoints
    assert base_name not in index_client.indexes


def test_deprovision_force_destroy_undeploys_and_keeps_bucket(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
    storage_client: FakeStorageClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    bucket_names_before = set(storage_client.buckets.keys())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=False,
        force_destroy=True,
    )
    assert result.ok
    assert "bucket=preserved" in result.message
    assert base_name not in endpoint_client.endpoints
    # Bucket untouched on data-retained path
    bucket_names_after = set(storage_client.buckets.keys())
    assert bucket_names_after == bucket_names_before


def test_deprovision_atomic_both_flags_forces_bucket_delete(
    driver: VertexMatchingEngineDriver,
    storage_client: FakeStorageClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "bucket=deleted" in result.message
    # GCS bucket.delete(force=True) was used to empty + delete
    forced = [b for b in storage_client.buckets.values() if b.deleted]
    assert forced
    assert all(b.force_used_on_delete for b in forced)


def test_deprovision_idempotent_when_already_gone(
    driver: VertexMatchingEngineDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="vector_index/missing"),
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status / binding -------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: VertexMatchingEngineDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="vector_index/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_deployed_to_available(
    driver: VertexMatchingEngineDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_provisioning_when_no_deployed_indexes(
    driver: VertexMatchingEngineDriver,
    endpoint_client: FakeEndpointClient,
) -> None:
    provisioned = driver.provision(_spec())
    base_name = provisioned.handle.split("/", 1)[1]
    endpoint_client.endpoints[base_name].deployed_indexes = []
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "provisioning"


def test_binding_returns_envelope(
    driver: VertexMatchingEngineDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "VERTEX_PROJECT_ID",
        "VERTEX_REGION",
        "VERTEX_INDEX_ENDPOINT_ID",
        "VERTEX_DEPLOYED_INDEX_ID",
    ):
        assert key in env
    assert env["VERTEX_PROJECT_ID"].literal == "acme-prod"
    assert env["VERTEX_REGION"].literal == "us-west1"
    base_name = provisioned.handle.split("/", 1)[1]
    assert env["VERTEX_DEPLOYED_INDEX_ID"].literal == _deployed_index_id_for(base_name=base_name)


def test_binding_iam_grants_cover_endpoint_and_index(
    driver: VertexMatchingEngineDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert "aiplatform.indexEndpoints.queryVectors" in actions
    assert "aiplatform.indexes.update" in actions


def test_binding_for_missing_raises(
    driver: VertexMatchingEngineDriver,
) -> None:
    with pytest.raises(_ManagedServiceError):
        driver.binding(ServiceHandle(handle="vector_index/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_gcs_prefix(
    driver: VertexMatchingEngineDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert snap.snapshot_id.startswith("gs://astrolift-vec-shards-")
    assert "/snapshots/" in snap.snapshot_id


def test_snapshot_for_missing_raises(
    driver: VertexMatchingEngineDriver,
) -> None:
    with pytest.raises(_ManagedServiceError):
        driver.snapshot(ServiceHandle(handle="vector_index/missing"))


def test_restore_provisions_target_with_snapshot_uri(
    driver: VertexMatchingEngineDriver,
    index_client: FakeIndexClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="rt")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    # The target name should differ from the source (different
    # service_handle_hint)
    assert result.handle != provisioned.handle


# ---- module helpers ---------------------------------------------


def test_deployed_index_id_id_format() -> None:
    di = _deployed_index_id_for(base_name="astrolift-vec-acme-api-prod-v")
    assert di == "astrolift_vec_acme_api_prod_v_d"
    assert di[0].isalpha()
    assert all(c.isalnum() or c == "_" for c in di)
    assert len(di) <= 128


def test_deployed_index_id_prefixes_digit_start() -> None:
    di = _deployed_index_id_for(base_name="42-foo")
    assert di[0].isalpha()


def test_base_name_canonicalization(
    driver: VertexMatchingEngineDriver,
) -> None:
    name = driver._base_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My_API",
            environment_name="Prod",
            service_handle_hint="v",
        ),
    )
    assert name == name.lower()
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    assert name[0].isalpha()
    assert len(name) <= 40


# ---- schemas ----------------------------------------------------


def test_config_schema_shape(driver: VertexMatchingEngineDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "dimension",
        "distance_measure",
        "algorithm",
        "approximate_neighbors_count",
        "machine_type",
        "min_replica_count",
        "max_replica_count",
        "public_endpoint_enabled",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: VertexMatchingEngineDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "VERTEX_PROJECT_ID",
        "VERTEX_REGION",
        "VERTEX_INDEX_ENDPOINT_ID",
        "VERTEX_DEPLOYED_INDEX_ID",
    ):
        assert key in schema.env_vars
