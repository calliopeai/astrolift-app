"""Actual PostgreSQL/HTTP common native projection without adoption or cloud effects."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest
from aws.bedrock_catalogue import BedrockSourceKind

from astrolift_services.native_model_connections import fingerprint
from astrolift_services.native_model_projection import native_connection_to_type
from astrolift_services.schema.model_types import cluster_model_to_type
from astrolift_services.tests.test_bedrock_connections_2269 import (
    ARN,
    DETAIL,
    MODEL,
    PROFILE,
    REGISTER,
    SOURCE,
    register,
    register_input,
)
from astrolift_services.tests.test_bedrock_connections_2269 import (
    world as bedrock_world,
)
from astrolift_services.tests.test_model_connection_2270 import graphql_http

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return bedrock_world.__wrapped__(monkeypatch)


_QUERY = """
query CommonNativeSource($organization: GUID!, $id: GUID!) {
  clusterModelDeployment(organizationId: $organization, id: $id) {
    sourceKind ready runtimeSupported modelRepo revisionSha computeMode
    localArtifactId readinessObservedAt readinessGeneration
    desiredResources {cpuRequest memoryRequest gpuCount replicas}
    appliedResources {cpuRequest}
    nativeSource {protocol sourceKind sourceFingerprint}
    nativeConnection {
      family sourceKind configurationState invokeAccess reason
      resourceIdentityFingerprint reviewedSourceFingerprint metadataObservedAt
      source {
        __typename
        ... on NativeModelConnectionSource {protocol sourceKind sourceArn sourceFingerprint}
        ... on VertexEndpointConnectionSource {projectId projectNumber region endpointResourceName}
        ... on FoundryDeploymentConnectionSource {subscriptionId accountResourceId deploymentResourceId}
      }
    }
  }
}
"""


def read(world, client, service):
    reply = graphql_http(
        client,
        world.headers,
        _QUERY,
        {"organization": str(world.org.guid), "id": str(service.guid)},
    )
    assert not reply.get("errors"), reply
    return reply["data"]["clusterModelDeployment"]


def assert_unavailable(value, *, family="BEDROCK", kind=None):
    native = value["nativeConnection"]
    assert native["family"] == family
    if kind is not None:
        assert native["sourceKind"] == kind
    assert native["configurationState"] == "UNAVAILABLE"
    assert native["invokeAccess"] == "UNKNOWN"
    assert native["reason"]
    for key in ("source", "resourceIdentityFingerprint", "reviewedSourceFingerprint", "metadataObservedAt"):
        assert native[key] is None
    assert value["nativeSource"] is None
    assert value["ready"] is value["runtimeSupported"] is None


@pytest.mark.parametrize("kind", ["foundation", "profile"])
def test_actual_common_bedrock_producer_preserves_legacy_projection(world, client, kind):  # noqa: F811
    if kind == "profile":
        source = replace(
            SOURCE,
            kind=BedrockSourceKind.INFERENCE_PROFILE,
            identifier="us." + MODEL,
            arn=PROFILE,
            profile_type="SYSTEM_DEFINED",
            destination_model_arns=(ARN,),
        )
        world.detail = replace(DETAIL, source=source)
        response = graphql_http(
            client,
            world.headers,
            REGISTER,
            {
                "input": register_input(world)
                | {"sourceKind": "INFERENCE_PROFILE", "sourceIdentifier": PROFILE}
            },
        )
        result = response["data"]["registerBedrockModelConnection"]
        assert result["ok"], result
        from astrolift_services.models import ManagedService

        service = ManagedService.objects.get(guid=result["data"]["id"])
    else:
        service, _ = register(world, client)
    value = read(world, client, service)
    native = value["nativeConnection"]
    literal_kind = "foundation_model" if kind == "foundation" else "inference_profile"
    assert value["sourceKind"] == "bedrock_" + literal_kind
    assert value["nativeSource"]["sourceKind"] == literal_kind.upper()
    assert native["family"] == "BEDROCK"
    assert native["sourceKind"] == "BEDROCK_" + literal_kind.upper()
    assert native["configurationState"] == "CONFIGURED" and native["invokeAccess"] == "UNKNOWN"
    assert native["reason"] is None
    assert native["source"]["__typename"] == "NativeModelConnectionSource"
    assert native["source"]["sourceKind"] == literal_kind.upper()
    assert native["reviewedSourceFingerprint"] == value["nativeSource"]["sourceFingerprint"]
    stored = service.config["native_connection"]
    resource = {
        key: stored[key]
        for key in ("protocol", "source_kind", "account_id", "region", "partition", "source_id", "source_arn")
    }
    assert native["resourceIdentityFingerprint"] == fingerprint(resource)
    assert native["resourceIdentityFingerprint"] != native["reviewedSourceFingerprint"]
    assert native["metadataObservedAt"]
    assert value["ready"] is value["runtimeSupported"] is None


@pytest.mark.parametrize(
    "change",
    [
        "flag",
        "provider",
        "account",
        "region",
        "credential",
        "unknown_kind",
        "protocol",
        "placement",
        "timestamp",
        "malformed",
    ],
)
def test_withdrawn_or_inconsistent_common_source_retains_family_without_hashes(
    world, client, monkeypatch, change
):  # noqa: F811
    service, _ = register(world, client)
    if change == "flag":
        monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
    elif change == "provider":
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    elif change in ("account", "region", "credential"):
        if change == "account":
            world.cluster.provider_config["account_id"] = "999999999999"
        elif change == "region":
            world.cluster.provider_config["region"] = "us-west-2"
        else:
            world.cluster.provider_config["credential"] = {
                "mode": "aws_assume_role",
                "role_arn": "arn:aws:iam::123456789012:role/changed",
            }
        world.cluster.save()
    else:
        if change == "unknown_kind":
            service.config["native_connection"]["source_kind"] = "invalid"
        elif change == "protocol":
            service.config["native_connection"]["protocol"] = "vertex"
        elif change == "placement":
            service.config["native_connection"]["cluster_id"] = str(uuid4())
        elif change == "timestamp":
            service.config["native_metadata_observed_at"] = {"private": "not-a-timestamp"}
        else:
            service.config["native_connection"] = []
        service.save()
    value = read(world, client, service)
    assert_unavailable(value)
    assert value["sourceKind"] == (
        "bedrock_unknown" if change in ("unknown_kind", "malformed") else "bedrock_foundation_model"
    )


@pytest.mark.parametrize("source", ["huggingface", "local_artifact"])
def test_existing_hosted_models_have_no_common_native_envelope(world, client, source):  # noqa: F811
    service = world.model
    if source == "local_artifact":
        artifact = str(uuid4())
        service.config = {
            "model_source": source,
            "model": "local-" + artifact,
            "model_artifact_id": artifact,
            "model_artifact_version": 2,
            "model_artifact_manifest_sha256": "a" * 64,
            "compute_mode": "cpu",
            "cpu": "1",
            "memory": "4Gi",
        }
    service.save()
    value = read(world, client, service)
    assert value["nativeConnection"] is value["nativeSource"] is None
    assert value["sourceKind"] == source
    assert value["runtimeSupported"] is not None and value["ready"] is not None
    assert value["modelRepo"] == service.config["model"]
    if source == "local_artifact":
        assert value["localArtifactId"] == service.config["model_artifact_id"]


@pytest.mark.parametrize(
    "change",
    ["bedrock_source", "vertex_source", "foundry_source", "unknown_source", "native_record", "existing_only"],
)
def test_native_config_on_vllm_never_falls_back_to_hosted_readiness(world, client, change):  # noqa: F811
    service = world.model
    if change == "native_record":
        service.config["native_connection"] = {"protocol": "bedrock"}
    elif change == "existing_only":
        service.config["existing_connection_only"] = True
    else:
        service.config["model_source"] = {
            "bedrock_source": "bedrock",
            "vertex_source": "vertex",
            "foundry_source": "foundry",
        }.get(change, "unrecognized_native")
    service.save()
    value = read(world, client, service)
    assert_unavailable(value, family="UNKNOWN", kind="UNKNOWN")
    assert value["modelRepo"] == "" and value["revisionSha"] is None
    assert value["computeMode"] is value["localArtifactId"] is None
    assert value["readinessObservedAt"] is value["readinessGeneration"] is None
    assert all(observed is None for observed in value["desiredResources"].values())
    assert value["appliedResources"] is None


@pytest.mark.parametrize(
    "variant,family", [("vertex_ai", "VERTEX"), ("azure_foundry", "FOUNDRY"), ("unrecognized", "UNKNOWN")]
)
def test_unadopted_native_variants_are_unavailable_not_fabricated_sources(world, variant, family):  # noqa: F811
    service = world.model
    service.variant = variant
    service.config = {"model_source": "huggingface", "model": "not-native-authority", "compute_mode": "cpu"}
    projected = cluster_model_to_type(service, dedicated=None)
    native = projected.native_connection
    assert native.family.name == family
    assert native.configuration_state.name == "UNAVAILABLE"
    assert native.source is native.resource_identity_fingerprint is native.reviewed_source_fingerprint is None
    assert projected.ready is projected.runtime_supported is projected.compute_mode is None
    assert projected.native_source is None
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        service.save()
    service.refresh_from_db()
    assert service.variant == "vllm"


def test_public_common_projection_excludes_config_handles_and_private_values(world, client):  # noqa: F811
    service, _ = register(world, client)
    marker = "PRIVATE_COMMON_PROJECTION_MARKER"
    service.config.update(
        {
            "model": marker,
            "credential": {"value": marker},
            "headers": {"Authorization": marker},
            "secret_ref": marker,
        }
    )
    service.save()
    value = read(world, client, service)
    assert marker not in json.dumps(value)
    assert value["modelRepo"] == ""
    assert value["nativeConnection"]["configurationState"] == "CONFIGURED"
    source = value["nativeConnection"]["source"]
    assert "backendRef" not in source and "credential" not in source and "config" not in source


def test_resource_identity_hash_does_not_relabel_mutable_profile_destinations_as_incarnation(world, client):  # noqa: F811
    source = replace(
        SOURCE,
        kind=BedrockSourceKind.INFERENCE_PROFILE,
        identifier="us." + MODEL,
        arn=PROFILE,
        profile_type="SYSTEM_DEFINED",
        destination_model_arns=(ARN,),
    )
    world.detail = replace(DETAIL, source=source)
    from astrolift_services.models import ManagedService

    reply = graphql_http(
        client,
        world.headers,
        REGISTER,
        {"input": register_input(world) | {"sourceKind": "INFERENCE_PROFILE", "sourceIdentifier": PROFILE}},
    )
    service = ManagedService.objects.get(guid=reply["data"]["registerBedrockModelConnection"]["data"]["id"])
    before = read(world, client, service)["nativeConnection"]
    native = service.config["native_connection"]
    native["destination_model_arns"] = [ARN, ARN.replace("us-east-1", "us-west-2")]
    native["source_fingerprint"] = fingerprint(
        {
            key: native[key]
            for key in (
                "protocol",
                "source_kind",
                "source_id",
                "source_arn",
                "destination_model_arns",
                "account_id",
                "region",
                "partition",
            )
        }
    )
    service.save()
    after = read(world, client, service)["nativeConnection"]
    assert after["resourceIdentityFingerprint"] == before["resourceIdentityFingerprint"]
    assert after["reviewedSourceFingerprint"] != before["reviewedSourceFingerprint"]
    # This is recorded metadata consistency, not fresh cloud observation or adoption.
    assert native_connection_to_type(service).invoke_access.name == "UNKNOWN"
