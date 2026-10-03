from __future__ import annotations

import json
from typing import Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed._base import ManagedServiceError
from aws.managed.opensearch_serverless import (
    OpenSearchServerlessConfig,
    OpenSearchServerlessSearchDriver,
    OpenSearchServerlessVectorDriver,
)


class NotFound(Exception):
    pass


class FakeAOSS:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, Any]] = {}
        self.security_policies: dict[tuple[str, str], dict[str, Any]] = {}
        self.access_policies: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def batch_get_collection(self, **kwargs):
        self.calls.append(("BatchGetCollection", kwargs))
        rows = [self.collections[name] for name in kwargs.get("names", []) if name in self.collections]
        return {"collectionDetails": rows, "collectionErrorDetails": []}

    def list_tags_for_resource(self, **kwargs):
        for collection in self.collections.values():
            if collection["arn"] == kwargs["resourceArn"]:
                return {"tags": collection.get("tags", [])}
        return {"tags": []}

    def create_collection(self, **kwargs):
        self.calls.append(("CreateCollection", kwargs))
        name = kwargs["name"]
        self.collections[name] = {
            **kwargs,
            "id": f"col-{name}",
            "arn": f"arn:aws:aoss:us-west-2:123456789012:collection/col-{name}",
            "status": "CREATING",
            "collectionEndpoint": f"https://col-{name}.us-west-2.aoss.amazonaws.com",
            "dashboardEndpoint": f"https://dash-{name}.us-west-2.aoss.amazonaws.com",
        }

    def update_collection(self, **kwargs):
        self.calls.append(("UpdateCollection", kwargs))
        collection = next(row for row in self.collections.values() if row["id"] == kwargs["id"])
        collection.update(kwargs)

    def delete_collection(self, **kwargs):
        self.calls.append(("DeleteCollection", kwargs))
        collection = next(row for row in self.collections.values() if row["id"] == kwargs["id"])
        collection["status"] = "DELETING"

    def get_security_policy(self, **kwargs):
        self.calls.append(("GetSecurityPolicy", kwargs))
        key = (kwargs["type"], kwargs["name"])
        if key not in self.security_policies:
            raise NotFound("ResourceNotFoundException: not found")
        return {"securityPolicyDetail": self.security_policies[key]}

    def create_security_policy(self, **kwargs):
        self.calls.append(("CreateSecurityPolicy", kwargs))
        self.security_policies[(kwargs["type"], kwargs["name"])] = {
            **kwargs,
            "policyVersion": "v1",
        }

    def update_security_policy(self, **kwargs):
        self.calls.append(("UpdateSecurityPolicy", kwargs))
        self.security_policies[(kwargs["type"], kwargs["name"])] = {
            **kwargs,
            "policyVersion": "v2",
        }

    def delete_security_policy(self, **kwargs):
        self.calls.append(("DeleteSecurityPolicy", kwargs))
        self.security_policies.pop((kwargs["type"], kwargs["name"]), None)

    def get_access_policy(self, **kwargs):
        self.calls.append(("GetAccessPolicy", kwargs))
        key = (kwargs["type"], kwargs["name"])
        if key not in self.access_policies:
            raise NotFound("ResourceNotFoundException: not found")
        return {"accessPolicyDetail": self.access_policies[key]}

    def create_access_policy(self, **kwargs):
        self.calls.append(("CreateAccessPolicy", kwargs))
        self.access_policies[(kwargs["type"], kwargs["name"])] = {
            **kwargs,
            "policyVersion": "v1",
        }

    def update_access_policy(self, **kwargs):
        self.calls.append(("UpdateAccessPolicy", kwargs))
        self.access_policies[(kwargs["type"], kwargs["name"])] = {
            **kwargs,
            "policyVersion": "v2",
        }

    def delete_access_policy(self, **kwargs):
        self.calls.append(("DeleteAccessPolicy", kwargs))
        self.access_policies.pop((kwargs["type"], kwargs["name"]), None)


def spec(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="catalog",
        size="small",
        config=config,
        isolation="dedicated",
        managed_service_id="11111111-1111-4111-8111-111111111111",
    )


def driver(collection_type: str = "SEARCH", *, public: bool = False):
    client = FakeAOSS()
    cls = OpenSearchServerlessVectorDriver if collection_type == "VECTORSEARCH" else OpenSearchServerlessSearchDriver
    subject = cls(
        config=OpenSearchServerlessConfig(
            region="us-west-2",
            account_id="123456789012",
            collection_type=collection_type,
            vpc_endpoint_ids=[] if public else ["vpce-aoss"],
            public_access_default=public,
        ),
        opensearch_serverless_client=client,
    )
    return subject, client


def test_search_provision_policies_binding_and_idempotence():
    subject, client = driver()
    result = subject.provision(spec(index_prefix="products"))
    again = subject.provision(spec(index_prefix="products"))
    name = result.handle.split("/", 1)[1]
    client.collections[name]["status"] = "ACTIVE"
    binding = subject.binding(ServiceHandle(result.handle), {"index_prefix": "products"})

    assert result.ok and again.ok
    assert result.handle.startswith("search/")
    assert len([call for call in client.calls if call[0] == "CreateCollection"]) == 1
    create = next(payload for operation, payload in client.calls if operation == "CreateCollection")
    assert create["type"] == "SEARCH"
    assert create["deletionProtection"] == "ENABLED"
    assert len(client.security_policies) == 2
    assert len(client.access_policies) == 1
    network = next(
        row["policy"] for (policy_type, _), row in client.security_policies.items() if policy_type == "network"
    )
    assert "vpce-aoss" in network
    access = next(iter(client.access_policies.values()))["policy"]
    assert "arn:aws:iam::123456789012:root" in access
    assert binding.env_vars["SEARCH_ENDPOINT"].literal.startswith("https://")
    assert binding.env_vars["SEARCH_INDEX_PREFIX"].literal == "products"
    assert {grant.actions[0] for grant in binding.iam_grants} == {
        "aoss:APIAccessAll",
        "aoss:DashboardsAccessAll",
    }


def test_vector_collection_has_vector_envelope_and_no_dashboard_grant():
    subject, client = driver("VECTORSEARCH")
    result = subject.provision(spec(index_name="embeddings", namespace="tenant-a"))
    binding = subject.binding(
        ServiceHandle(result.handle),
        {"index_name": "embeddings", "namespace": "tenant-a"},
    )
    create = next(payload for operation, payload in client.calls if operation == "CreateCollection")

    assert result.handle.startswith("vector_index/")
    assert create["type"] == "VECTORSEARCH"
    assert binding.env_vars["VECTOR_INDEX_NAME"].literal == "embeddings"
    assert binding.env_vars["VECTOR_NAMESPACE"].literal == "tenant-a"
    assert [grant.actions for grant in binding.iam_grants] == [["aoss:APIAccessAll"]]


def test_public_network_policy_is_explicit():
    subject, client = driver(public=True)
    result = subject.provision(spec(public_access=True))
    assert result.ok
    network = next(
        row["policy"] for (policy_type, _), row in client.security_policies.items() if policy_type == "network"
    )
    assert '"AllowFromPublic":true' in network
    assert "SourceVPCEs" not in network


def test_source_service_only_network_policy_excludes_dashboards():
    client = FakeAOSS()
    subject = OpenSearchServerlessVectorDriver(
        config=OpenSearchServerlessConfig(
            region="us-west-2",
            account_id="123456789012",
            collection_type="VECTORSEARCH",
            source_services=["bedrock.amazonaws.com"],
        ),
        opensearch_serverless_client=client,
    )

    result = subject.provision(spec())
    network_json = next(
        row["policy"] for (policy_type, _), row in client.security_policies.items() if policy_type == "network"
    )
    network = json.loads(network_json)

    assert result.ok
    assert network[0]["SourceServices"] == ["bedrock.amazonaws.com"]
    assert network[0]["Rules"] == [
        {
            "ResourceType": "collection",
            "Resource": [f"collection/{result.handle.split('/', 1)[1]}"],
        },
    ]


def test_deprovision_requires_data_ack_and_force_for_protection_then_cleans_policies():
    subject, client = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    refused_data = subject.deprovision(DeprovisionSpec(result.handle))
    refused_guard = subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)
    disabling = subject.deprovision(
        DeprovisionSpec(result.handle),
        delete_data=True,
        force_destroy=True,
    )

    assert refused_data.ok is False and refused_data.retryable is False
    assert refused_guard.ok is False and "deletion protection" in refused_guard.message
    assert disabling.ok is False and disabling.retryable is True
    assert client.collections[name]["deletionProtection"] == "DISABLED"

    deleting = subject.deprovision(
        DeprovisionSpec(result.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert deleting.ok is False
    client.collections.pop(name)
    final = subject.deprovision(
        DeprovisionSpec(result.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert final.ok is True
    assert not client.security_policies and not client.access_policies


def test_snapshot_contract_is_honest():
    subject, _ = driver()
    with pytest.raises(ManagedServiceError, match="automatic snapshots"):
        subject.snapshot(ServiceHandle("search/example"))


def test_current_botocore_accepts_all_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, client = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    subject.update(UpdateSpec(result.handle, config={"description": "updated"}))
    client.collections[name]["deletionProtection"] = "DISABLED"
    subject.deprovision(DeprovisionSpec(result.handle), delete_data=True)

    service = Session().get_service_model("opensearchserverless")
    for operation, request in client.calls:
        validate_parameters(request, service.operation_model(operation).input_shape)


def test_provision_does_not_adopt_or_repolicy_another_services_collection():
    """Ownership is checked before the name-keyed policies are rewritten (#1961)."""
    import dataclasses

    subject, client = driver()
    first = subject.provision(dataclasses.replace(spec(), managed_service_id="11111111-1111-4111-8111-111111111111"))
    policies = (dict(client.security_policies), dict(client.access_policies))
    calls = len(client.calls)

    second = subject.provision(
        dataclasses.replace(
            spec(), managed_service_id="22222222-2222-4222-8222-222222222222", recorded_handle=first.handle
        )
    )

    assert first.ok, first.message
    assert not second.ok and second.handle == "" and "refusing to adopt" in second.message
    assert (client.security_policies, client.access_policies) == policies
    assert not any(name != "BatchGetCollection" for name, _ in client.calls[calls:])


@pytest.mark.parametrize("policy_type", ["encryption", "network", "data"])
@pytest.mark.parametrize("policy", ["foreign", "wildcard", "invalid"])
def test_existing_policy_for_another_target_is_never_rewritten(policy_type, policy):
    import copy

    subject, client = driver()
    first = subject.provision(spec())
    assert first.ok
    collection_name = first.handle.split("/", 1)[1]
    prefix = {"encryption": "e", "network": "n", "data": "d"}[policy_type]
    key = (policy_type, subject._policy_name(prefix, collection_name))
    store = client.access_policies if policy_type == "data" else client.security_policies
    if policy == "invalid":
        store[key]["policy"] = "not-json"
    else:
        value = json.loads(store[key]["policy"])
        block = value if isinstance(value, dict) else value[0]
        block["Rules"][0]["Resource"] = ["collection/foreign" if policy == "foreign" else "collection/*"]
        store[key]["policy"] = json.dumps(value)
    before = copy.deepcopy((client.collections, client.security_policies, client.access_policies))
    offset = len(client.calls)
    refused = subject.provision(spec(public_access=True))
    assert not refused.ok and ("different collection" in refused.message or "unverifiable" in refused.message)
    assert before == (client.collections, client.security_policies, client.access_policies)
    assert not any(operation.startswith(("Create", "Update", "Delete")) for operation, _ in client.calls[offset:])


def test_compact_collection_and_policy_names_retain_complete_identity_digest():
    import dataclasses
    import hashlib

    subject, client = driver()
    for identity in ("11111111-1111-4111-8111-111111111111", "11111111-1111-4111-8111-111111111112"):
        result = subject.provision(dataclasses.replace(spec(), managed_service_id=identity))
        assert result.ok
        name = result.handle.split("/", 1)[1]
        from uuid import UUID

        suffix = hashlib.sha256(UUID(identity).bytes).hexdigest()[:20]
        assert name.endswith(suffix) and len(name) <= 30
        for prefix in ("e", "n", "d"):
            policy_name = subject._policy_name(prefix, name)
            assert len(policy_name) <= 32 and policy_name.endswith(suffix)
    assert len(client.collections) == 2 and len(client.security_policies) == 4 and len(client.access_policies) == 2
