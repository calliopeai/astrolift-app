"""Bind one browser corpus to HTTP/PG and explicitly unadopted transient serialization."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import strawberry
from aws.bedrock_catalogue import BedrockSourceKind

from astrolift_services.models import ManagedService
from astrolift_services.schema.model_types import ClusterModelDeploymentType, cluster_model_to_type
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
_CORPUS = (
    Path(__file__).resolve().parents[3]
    / "frontend/components/screens/models/native-model-projection.fixture.json"
)
_HTTP_CASES = ("foundation", "profile", "withdrawn", "unknown")
_TRANSIENT_CASES = ("vertex_unadopted", "foundry_unadopted", "unknown_family")
_COMMON_FIELDS = """
family sourceKind configurationState invokeAccess reason
resourceIdentityFingerprint reviewedSourceFingerprint metadataObservedAt
source {
  __typename
  ... on NativeModelConnectionSource {
    protocol sourceKind accountId region partition sourceId sourceArn destinationModelArns
    sourceFingerprint metadataObservedAt configurationState invokeAccess
  }
  ... on VertexEndpointConnectionSource {
    projectId projectNumber region endpointResourceName
    deployedModels {deployedModelId modelResourceName modelVersionId trafficPercent machineType minReplicas maxReplicas availableReplicas}
  }
  ... on FoundryDeploymentConnectionSource {
    subscriptionId resourceGroup accountResourceId region accountKind localAuthDisabled
    deploymentResourceId deploymentName modelFormat modelName modelVersion declaredSku declaredCapacity provisioningState
  }
}
"""
_FIELDS = "sourceKind nativeSource {protocol sourceKind} nativeConnection {" + _COMMON_FIELDS + "}"
_QUERY = (
    "query NativeSourceContract($organization: GUID!, $id: GUID!) {clusterModelDeployment(organizationId:$organization, id:$id){"
    + _FIELDS
    + "}}"
)


@pytest.fixture
def world(monkeypatch):
    return bedrock_world.__wrapped__(monkeypatch)


def serialized_case(world, client, monkeypatch, variant):
    """Return actual wire data; transient cases do not advertise an admitted connection."""
    if variant in _TRANSIENT_CASES:
        service = world.model
        before = ManagedService.objects.count()
        original = service.variant
        service.variant = {
            "vertex_unadopted": "vertex_ai",
            "foundry_unadopted": "azure_foundry",
            "unknown_family": "unsupported_native",
        }[variant]
        service.config = {"model_source": "unadopted_native_fixture"}

        @strawberry.type
        class TransientProjectionProbe:
            @strawberry.field
            def fixture_native(self) -> ClusterModelDeploymentType:
                return cluster_model_to_type(service, dedicated=None)

        schema = strawberry.Schema(query=TransientProjectionProbe)
        result = schema.execute_sync("{fixtureNative{" + _FIELDS + "}}")
        assert not result.errors
        assert ManagedService.objects.count() == before
        assert ManagedService.objects.get(pk=service.pk).variant == original
        return result.data["fixtureNative"]
    if variant == "profile":
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
        assert not response.get("errors"), response
        result = response["data"]["registerBedrockModelConnection"]
        assert result["ok"], result
        service = ManagedService.objects.get(guid=result["data"]["id"])
    else:
        service, _ = register(world, client)
    service.config["native_metadata_observed_at"] = "2026-10-04T00:00:00+00:00"
    service.applied_config = dict(service.config)
    service.save()
    if variant in ("withdrawn", "unknown"):
        monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
    if variant == "unknown":
        service.config["native_connection"]["source_kind"] = "invalid"
        service.save()
    response = graphql_http(
        client, world.headers, _QUERY, {"organization": str(world.org.guid), "id": str(service.guid)}
    )
    assert not response.get("errors"), response
    return response["data"]["clusterModelDeployment"]


@pytest.mark.parametrize("variant", _HTTP_CASES + _TRANSIENT_CASES)
def test_native_wire_projection_matches_single_browser_corpus(world, client, monkeypatch, variant):
    corpus = json.loads(_CORPUS.read_text())
    expected = {row["case"]: row for row in corpus["rows"]}
    assert set(expected) == set(_HTTP_CASES + _TRANSIENT_CASES)
    assert len(corpus["rows"]) == len(expected)
    row = expected[variant]
    assert row["provenance"] == (
        "HTTP_POSTGRES" if variant in _HTTP_CASES else "TRANSIENT_STRAWBERRY_UNADOPTED"
    )
    actual = serialized_case(world, client, monkeypatch, variant)
    assert actual == row["serializedQueryData"]
    if variant in _HTTP_CASES:
        assert {key: actual[key] for key in ("sourceKind", "nativeSource")} == row["legacyProjection"]
    else:
        assert actual["nativeConnection"]["configurationState"] == "UNAVAILABLE"
        assert actual["nativeConnection"]["source"] is None
