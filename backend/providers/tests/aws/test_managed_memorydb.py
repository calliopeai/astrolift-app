from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.memorydb import MemoryDBConfig, MemoryDBDriver


class NotFound(Exception):
    pass


class FakeMemoryDB:
    def __init__(self) -> None:
        self.clusters: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.acls: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_clusters(self, **kwargs):
        name = kwargs["ClusterName"]
        if name not in self.clusters:
            raise NotFound("ClusterNotFoundFault")
        return {"Clusters": [self.clusters[name]]}

    def list_tags(self, **kwargs):
        for cluster in self.clusters.values():
            if cluster["ARN"] == kwargs["ResourceArn"]:
                return {"TagList": cluster.get("Tags", [])}
        return {"TagList": []}

    def create_cluster(self, **kwargs):
        self.calls.append(("CreateCluster", kwargs))
        name = kwargs["ClusterName"]
        self.clusters[name] = {
            **kwargs,
            "Name": name,
            "Status": "creating",
            "ClusterEndpoint": {"Address": f"{name}.memorydb", "Port": 6379},
            "ARN": f"arn:aws:memorydb:us-west-2:123456789012:cluster/{name}",
        }

    def update_cluster(self, **kwargs):
        self.calls.append(("UpdateCluster", kwargs))
        self.clusters[kwargs["ClusterName"]].update(kwargs)

    def delete_cluster(self, **kwargs):
        self.calls.append(("DeleteCluster", kwargs))
        self.clusters[kwargs["ClusterName"]]["Status"] = "deleting"

    def describe_users(self, **kwargs):
        name = kwargs["UserName"]
        if name not in self.users:
            raise NotFound("UserNotFoundFault")
        return {"Users": [self.users[name]]}

    def create_user(self, **kwargs):
        self.calls.append(("CreateUser", kwargs))
        name = kwargs["UserName"]
        self.users[name] = {
            **kwargs,
            "Name": name,
            "Status": "active",
            "ARN": f"arn:aws:memorydb:us-west-2:123456789012:user/{name}",
        }

    def update_user(self, **kwargs):
        self.calls.append(("UpdateUser", kwargs))
        self.users[kwargs["UserName"]].update(kwargs)

    def delete_user(self, **kwargs):
        self.calls.append(("DeleteUser", kwargs))
        self.users[kwargs["UserName"]]["Status"] = "deleting"

    def describe_acls(self, **kwargs):
        name = kwargs["ACLName"]
        if name not in self.acls:
            raise NotFound("ACLNotFoundFault")
        return {"ACLs": [self.acls[name]]}

    def create_acl(self, **kwargs):
        self.calls.append(("CreateACL", kwargs))
        name = kwargs["ACLName"]
        self.acls[name] = {**kwargs, "Name": name, "Status": "active"}

    def update_acl(self, **kwargs):
        self.calls.append(("UpdateACL", kwargs))
        acl = self.acls[kwargs["ACLName"]]
        acl["UserNames"] = sorted(set(acl.get("UserNames", [])) | set(kwargs.get("UserNamesToAdd", [])))

    def delete_acl(self, **kwargs):
        self.calls.append(("DeleteACL", kwargs))
        self.acls[kwargs["ACLName"]]["Status"] = "deleting"

    def create_snapshot(self, **kwargs):
        self.calls.append(("CreateSnapshot", kwargs))
        return {"Snapshot": {"ARN": (f"arn:aws:memorydb:us-west-2:123456789012:snapshot/{kwargs['SnapshotName']}")}}


class ResourceNotFound(Exception):
    pass


class ResourceExists(Exception):
    pass


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_secret_value(self, **kwargs):
        name = kwargs["SecretId"]
        if name not in self.values:
            raise ResourceNotFound("ResourceNotFoundException")
        return {"SecretString": self.values[name]}

    def create_secret(self, **kwargs):
        name = kwargs["Name"]
        if name in self.values:
            raise ResourceExists("ResourceExistsException")
        self.values[name] = kwargs["SecretString"]

    def put_secret_value(self, **kwargs):
        self.values[kwargs["SecretId"]] = kwargs["SecretString"]

    def delete_secret(self, **kwargs):
        name = kwargs["SecretId"]
        if name not in self.values:
            raise ResourceNotFound("ResourceNotFoundException")
        del self.values[name]


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="durable",
        size="small",
        config=config,
        isolation="dedicated",
    )


def driver(engine: str = "valkey"):
    memorydb = FakeMemoryDB()
    secrets = FakeSecrets()
    subject = MemoryDBDriver(
        config=MemoryDBConfig(
            region="us-west-2",
            subnet_group="private-memorydb",
            security_group_ids=["sg-memorydb"],
            engine=engine,
        ),
        memorydb_client=memorydb,
        secrets_client=secrets,
    )
    return subject, memorydb, secrets


def test_password_provision_binding_update_and_idempotence():
    subject, memorydb, secrets = driver()

    result = subject.provision(spec(auth_mode="password"))
    again = subject.provision(spec(auth_mode="password"))
    name = result.handle.split("/", 1)[1]
    memorydb.clusters[name]["Status"] = "available"
    binding = subject.binding(ServiceHandle(result.handle), {"auth_mode": "password"})
    updated = subject.update(
        UpdateSpec(
            result.handle,
            size="large",
            config={"num_shards": 2, "num_replicas_per_shard": 2},
        )
    )

    assert result.ok and again.ok and updated.ok
    assert len([call for call in memorydb.calls if call[0] == "CreateCluster"]) == 1
    create = next(payload for operation, payload in memorydb.calls if operation == "CreateCluster")
    assert create["Engine"] == "valkey"
    assert create["NodeType"] == "db.t4g.small"
    assert create["TLSEnabled"] is True
    assert create["ACLName"].startswith("a-")
    assert set(memorydb.users).isdisjoint(memorydb.acls)
    assert binding.env_vars["REDIS_PASSWORD"].secret_ref in secrets.values
    assert binding.env_vars["REDIS_URL"].secret_ref in secrets.values
    update = [payload for operation, payload in memorydb.calls if operation == "UpdateCluster"][-1]
    assert update["ShardConfiguration"] == {"ShardCount": 2}
    assert update["ReplicaConfiguration"] == {"ReplicaCount": 2}


def test_iam_auth_emits_cluster_and_user_connect_grants():
    subject, memorydb, secrets = driver("redis")
    result = subject.provision(spec(auth_mode="iam", engine_version="7.0"))
    name = result.handle.split("/", 1)[1]
    binding = subject.binding(
        ServiceHandle(result.handle),
        {"auth_mode": "iam", "engine_version": "7.0"},
    )

    create_user = next(payload for operation, payload in memorydb.calls if operation == "CreateUser")
    assert create_user["AuthenticationMode"] == {"Type": "iam"}
    assert not secrets.values
    assert {grant.actions[0] for grant in binding.iam_grants} == {"memorydb:Connect"}
    assert {grant.resource for grant in binding.iam_grants} == {
        memorydb.clusters[name]["ARN"],
        next(iter(memorydb.users.values()))["ARN"],
    }


def test_open_access_is_explicit_and_does_not_create_users():
    subject, memorydb, _ = driver()
    refused = subject.provision(spec(auth_mode="open"))
    allowed = subject.provision(spec(auth_mode="open", allow_open_access=True))

    assert refused.ok is False
    assert "allow_open_access=true" in refused.message
    assert allowed.ok is True
    create = next(payload for operation, payload in memorydb.calls if operation == "CreateCluster")
    assert create["ACLName"] == "open-access"
    assert not memorydb.users and not memorydb.acls


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"num_shards": "many"}, "must be an integer"),
        ({"num_replicas_per_shard": 6}, "0 through 5"),
        ({"auth_mode": "external"}, "requires acl_name"),
        ({"auth_mode": "external", "acl_name": "shared"}, "requires user_name"),
        ({"auth_mode": "iam", "engine_version": "six"}, "numeric version"),
        ({"data_tiering": True, "node_type": "db.r7g.large"}, "r6gd"),
    ],
)
def test_invalid_config_returns_controlled_failure(config, message):
    subject, _, _ = driver()
    result = subject.provision(spec(**config))
    assert result.ok is False
    assert message in result.message


def test_snapshot_restore_and_async_delete_cleanup():
    subject, memorydb, secrets = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    snapshot = subject.snapshot(ServiceHandle(result.handle))
    restored = subject.restore(
        snapshot,
        replace(
            spec(),
            environment_name="restore",
            service_handle_hint="restored",
        ),
    )

    assert restored.ok
    restore_call = [payload for operation, payload in memorydb.calls if operation == "CreateCluster"][-1]
    assert restore_call["SnapshotArns"] == [snapshot.snapshot_id]

    first = subject.deprovision(DeprovisionSpec(result.handle))
    assert first.ok is False and first.retryable is True
    memorydb.clusters.pop(name)
    second = subject.deprovision(DeprovisionSpec(result.handle))
    assert second.ok is False
    memorydb.acls.clear()
    third = subject.deprovision(DeprovisionSpec(result.handle))
    assert third.ok is False
    memorydb.users.clear()
    final = subject.deprovision(DeprovisionSpec(result.handle))
    assert final.ok is True
    assert all(name not in secret_name for secret_name in secrets.values)


def test_current_botocore_accepts_memorydb_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, memorydb, _ = driver()
    result = subject.provision(spec(auth_mode="iam", engine_version="7.2"))
    subject.update(UpdateSpec(result.handle, size="medium", config={"num_shards": 2}))
    subject.snapshot(ServiceHandle(result.handle))
    subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)

    service = Session().get_service_model("memorydb")
    for operation, request in memorydb.calls:
        validate_parameters(request, service.operation_model(operation).input_shape)


def test_provision_does_not_adopt_another_services_resource():
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    subject, client = driver()[:2]
    first = subject.provision(dataclasses.replace(spec(), managed_service_id="svc-a"))
    calls = len(client.calls)

    second = subject.provision(dataclasses.replace(spec(), managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and second.handle == "" and "refusing to adopt" in second.message
    assert not any(name.startswith(("Create", "Update", "Modify")) for name, _ in client.calls[calls:])
