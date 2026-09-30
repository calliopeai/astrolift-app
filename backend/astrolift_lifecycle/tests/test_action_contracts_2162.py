# ruff: noqa: F811
"""Current mobile documents validate, and HTTP version mismatches carry context."""

import json
from pathlib import Path

import pytest
from django.test import Client
from graphql import parse, print_ast, validate

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member
from astrolift_lifecycle.tests.test_action_preconditions_2162 import effects, targets  # noqa: F401
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, world  # noqa: F401
from config.schema import schema
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db
DOCUMENT = Path(__file__).with_name("contracts") / "mobile_quick_actions_2162.graphql"


def test_all_four_unchanged_mobile_documents_validate_with_the_version_safeguard():
    document = parse(DOCUMENT.read_text())
    assert len(document.definitions) == 4
    assert validate(schema._schema, document) == []
    for operation in document.definitions:
        field = operation.selection_set.selections[0]
        assert "ifMatchVersion" in {argument.name.value for argument in field.arguments}
        assert (
            schema._schema.get_type("Mutation").fields[field.name.value].args["ifMatchVersion"].default_value
            is None
        )


@pytest.mark.parametrize(
    "action", ["rollbackDeployment", "redeployApp", "restartAstroliftWorkload", "scaleAstroliftWorkload"]
)
def test_real_http_rejects_stale_quick_action_with_structured_versions_before_effects(
    world, targets, effects, action
):
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        world.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=world.org.pk, slug="http-2162"
    )
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user, organization=world.org, name="Action", token_hash=minted.token_hash, scopes=["admin"]
    )
    client = Client()
    headers = {"HTTP_AUTHORIZATION": "Bearer " + minted.plaintext}
    read = client.post(
        "/app/gql/config/",
        data=json.dumps(
            {
                "query": "query($id:String!, $slug:String!){ astroliftDeployment(id:$id){ id version } astroliftWorkloads(appSlug:$slug){ id version } }",
                "variables": {"id": str(targets.deployment.guid), "slug": world.medops_app.slug},
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert read.status_code == 200
    payload = read.json()
    assert not payload.get("errors"), payload
    assert payload["data"]["astroliftDeployment"]["version"] == 7
    assert (
        next(row for row in payload["data"]["astroliftWorkloads"] if row["id"] == str(targets.workload.guid))[
            "version"
        ]
        == 11
    )
    document = next(
        operation
        for operation in parse(DOCUMENT.read_text()).definitions
        if operation.selection_set.selections[0].name.value == action
    )
    query = print_ast(document).replace("field", "field currentVersion requestedVersion")
    deployment = action in {"rollbackDeployment", "redeployApp"}
    input = {"id": str(targets.deployment.guid)} if deployment else {"workloadId": str(targets.workload.guid)}
    if action == "scaleAstroliftWorkload":
        input["replicas"] = 2
    response = client.post(
        "/app/gql/config/",
        data=json.dumps({"query": query, "variables": {"input": input, "ifMatchVersion": 0}}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert not payload.get("errors"), payload
    result = payload["data"][action]
    assert not result["ok"] and result["data"] is None
    assert result["errors"][0]["code"] == "VERSION_MISMATCH"
    assert result["errors"][0]["currentVersion"] == (7 if deployment else 11)
    assert result["errors"][0]["requestedVersion"] == 0
    assert effects == []
