# ruff: noqa: F811
"""The GraphQL HTTP stack retains role and bearer owner checks."""

import json

import pytest
from django.test import Client

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member
from astrolift_lifecycle.models import DeployToken
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, world  # noqa: F401
from core.permissions import Permission
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("authority", ["own-app", "org-team-bearer", "org-bearer"])
def test_graphql_http_token_reads_and_writes_use_persisted_owner_despite_selected_headers(world, authority):
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    kind = "APP" if authority == "own-app" else "ORG"
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_UPDATE],
        kind=kind,
        scope_id=world.medops_app.pk if kind == "APP" else world.org.pk,
        slug="http",
    )
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="HTTP",
        token_hash=minted.token_hash,
        scopes=["admin"],
        team=world.medops if authority == "org-team-bearer" else None,
    )
    headers = {
        "HTTP_AUTHORIZATION": "Bearer " + minted.plaintext,
        "HTTP_X_ASTROLIFT_TEAM": str(world.medops.pk),
        "HTTP_X_ASTROLIFT_PROJECT": str(world.medops_project.pk),
    }
    client = Client()

    def send(query, slug):
        response = client.post(
            "/app/gql/config/",
            data=json.dumps({"query": query, "variables": {"slug": slug}}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200, response.content
        return response.json()

    read = "query($slug:String!){ astroliftAppDeployTokens(appSlug:$slug){ id name } }"
    own = send(read, world.medops_app.slug)
    assert not own.get("errors"), own
    assert len(own["data"]["astroliftAppDeployTokens"]) == 1
    sibling = send(read, world.platform_app.slug)
    if authority == "org-bearer":
        assert not sibling.get("errors"), sibling
        assert len(sibling["data"]["astroliftAppDeployTokens"]) == 1
    else:
        assert sibling.get("errors"), sibling
        assert sibling.get("data") is None or sibling["data"].get("astroliftAppDeployTokens") is None
    before = list(DeployToken.objects.values())
    write = 'mutation($slug:String!){ createDeployToken(input:{appSlug:$slug,name:"HTTP created"}){ ok errors{ code } } }'
    result = send(write, world.platform_app.slug)
    assert not result.get("errors"), result
    envelope = result["data"]["createDeployToken"]
    if authority == "org-bearer":
        assert envelope["ok"], envelope
        assert DeployToken.objects.filter(registered_app=world.platform_app, name="HTTP created").count() == 1
    else:
        assert not envelope["ok"]
        assert envelope["errors"][0]["code"] == "PERMISSION_DENIED"
        assert list(DeployToken.objects.values()) == before
