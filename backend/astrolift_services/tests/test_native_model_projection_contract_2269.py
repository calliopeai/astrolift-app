"""Keep the browser's native source corpus bound to actual HTTP serialization."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from aws.bedrock_catalogue import BedrockSourceKind

from astrolift_services.models import ManagedService
from astrolift_services.tests.test_bedrock_connections_2269 import (
    ARN,
    DETAIL,
    MODEL,
    PROFILE,
    REGISTER,
    SOURCE,
    register,
    register_input,
    world,  # noqa: F401
)
from astrolift_services.tests.test_model_connection_2270 import graphql_http

pytestmark = pytest.mark.django_db

_CORPUS = (
    Path(__file__).resolve().parents[3]
    / "frontend/components/screens/models/native-model-projection.fixture.json"
)
_CASES = ("foundation", "profile", "withdrawn", "unknown")
_QUERY = """
query NativeSourceContract($organization: GUID!, $id: GUID!) {
  clusterModelDeployment(organizationId: $organization, id: $id) {
    sourceKind
    nativeSource { protocol sourceKind }
  }
}
"""


@pytest.mark.parametrize("variant", _CASES)
def test_native_http_projection_matches_browser_corpus(world, client, monkeypatch, variant):  # noqa: F811
    corpus = json.loads(_CORPUS.read_text())
    expected = {row["case"]: row["serializedQueryData"] for row in corpus["rows"]}
    assert len(corpus["rows"]) == len(_CASES)
    assert set(expected) == set(_CASES)

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
        wire = register_input(world) | {"sourceKind": "INFERENCE_PROFILE", "sourceIdentifier": PROFILE}
        response = graphql_http(client, world.headers, REGISTER, {"input": wire})
        assert not response.get("errors"), response
        result = response["data"]["registerBedrockModelConnection"]
        assert result["ok"], result
        service = ManagedService.objects.get(guid=result["data"]["id"])
    else:
        service, _ = register(world, client)

    if variant in ("withdrawn", "unknown"):
        monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
    if variant == "unknown":
        service.config["native_connection"]["source_kind"] = "invalid"
        service.save()

    response = graphql_http(
        client,
        world.headers,
        _QUERY,
        {"organization": str(world.org.guid), "id": str(service.guid)},
    )
    assert not response.get("errors"), response
    assert response["data"]["clusterModelDeployment"] == expected[variant]
