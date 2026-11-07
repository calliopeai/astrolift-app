from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import RequestFactory
from django.utils import timezone

from astrolift_clusters.models import NvkeConnection, NvkeSite
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context, create
from astrolift_clusters.tests.test_nvke_sites import world as _site_world
from core.schema.audit import MutationAuditLog
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _site_world.__wrapped__()


CREATE = """
mutation($input: CreateNvkeSiteInput!) {
  createNvkeSite(input: $input) { ok errors { code message } data { id version slug connections { id } } }
}
"""
CONFIGURE = """
mutation($unrelated: ConfigureNvkeConnectionInput!) {
  configureNvkeConnection(input: $unrelated) {
    ok errors { code message }
    data { id version connections { id version surface endpoint hasCredentials } }
  }
}
"""


def execute(world, query, variables=None):
    from config.schema import schema

    request = RequestFactory().post("/app/gql/config/", content_type="application/json")
    request.user = world.user
    with context(world):
        return schema.execute_sync(
            query,
            variable_values=variables,
            context_value=SimpleNamespace(user=world.user, request=request),
        )


def test_site_creation_and_encrypted_connection_roundtrip_through_the_real_schema(world):
    result = execute(
        world, CREATE, {"input": {"name": "GPU site", "slug": "gpu", "organizationNamespace": "org-owned"}}
    )
    assert result.errors is None
    created = result.data["createNvkeSite"]
    assert created["ok"], created
    result = execute(
        world,
        CONFIGURE,
        {
            "unrelated": {
                "siteId": created["data"]["id"],
                "expectedSiteVersion": created["data"]["version"],
                "surface": "CONTROLLER",
                "expectedConnectionVersion": 0,
                "endpoint": "https://nvke.example.test/api",
                "credentials": {"token": "private-schema-test-token"},
            }
        },
    )
    assert result.errors is None
    payload = result.data["configureNvkeConnection"]
    assert payload["ok"], payload
    assert payload["data"]["connections"][0]["hasCredentials"] is True
    assert "private-schema-test-token" not in json.dumps(payload)
    row = NvkeConnection.objects.get(site__guid=created["data"]["id"])
    assert b"private-schema-test-token" not in bytes(row.credential_ciphertext)
    log = MutationAuditLog.objects.filter(operation="nvke.connection_configure").latest("timestamp")
    assert "private-schema-test-token" not in json.dumps(log.variables)
    assert log.variables["unrelated"]["credentials"] == "***REDACTED***"


def test_site_inventory_is_paginated_and_contains_only_the_active_organization(world):
    with context(world):
        create()
        from astrolift_clusters import nvke_sites

        nvke_sites.create_site(name="Another", slug="another", organization_namespace="another")
    result = execute(world, "{ astroliftNvkeSites(limit:1) { items { id slug } nextCursor totalCount } }")
    assert result.errors is None
    page = result.data["astroliftNvkeSites"]
    assert len(page["items"]) == 1
    assert page["nextCursor"]
    assert page["totalCount"] == 2
    other = execute(
        world,
        "query($after:String!) { astroliftNvkeSites(limit:1, after:$after) { items { id } nextCursor } }",
        {"after": page["nextCursor"]},
    )
    assert other.errors is None
    assert other.data["astroliftNvkeSites"]["items"][0]["id"] != page["items"][0]["id"]
    assert other.data["astroliftNvkeSites"]["nextCursor"] is None


def test_foreign_and_missing_sites_are_indistinguishable_through_graphql(world):
    from astrolift_identity.models import Organization

    other = Organization.objects.create(name="Foreign", slug="nvke-schema-foreign-" + uuid4().hex[:8])
    foreign = NvkeSite.objects.create(
        organization=other, name="Foreign", slug="foreign", organization_namespace="foreign"
    )
    query = "query($id:GUID!) { astroliftNvkeSite(siteId:$id) { id name } }"
    foreign_result = execute(world, query, {"id": str(foreign.guid)})
    missing_result = execute(world, query, {"id": str(uuid4())})
    assert foreign_result.errors is missing_result.errors is None
    assert foreign_result.data == missing_result.data == {"astroliftNvkeSite": None}


def test_missing_admin_permission_returns_a_complete_envelope_and_writes_nothing(world):
    from astrolift_identity.models import RoleBinding

    RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
    result = execute(
        world, CREATE, {"input": {"name": "Denied", "slug": "denied", "organizationNamespace": "org-owned"}}
    )
    assert result.errors is None
    assert result.data["createNvkeSite"]["ok"] is False
    assert result.data["createNvkeSite"]["errors"][0]["code"] == "PERMISSION_DENIED"
    assert not NvkeSite.objects.filter(organization=world.org).exists()


def test_read_permission_does_not_grant_connection_configuration(world):

    from astrolift_identity.models import RoleBinding
    from core.permissions import Permission

    with context(world):
        site = create()
    RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
    bind_role(
        world.user,
        permissions=[Permission.NVKE_READ],
        kind="ORG",
        scope_id=world.org.pk,
        slug="nvke-read-" + uuid4().hex[:8],
    )
    result = execute(
        world,
        CONFIGURE,
        {
            "unrelated": {
                "siteId": str(site.guid),
                "expectedSiteVersion": site.version,
                "surface": "CONTROLLER",
                "expectedConnectionVersion": 0,
                "endpoint": "https://nvke.example.test/api",
                "credentials": {"token": "must-never-store"},
            }
        },
    )
    assert result.errors is None
    assert result.data["configureNvkeConnection"]["errors"][0]["code"] == "PERMISSION_DENIED"
    assert not NvkeConnection.objects.filter(site=site).exists()


def test_rejected_endpoint_credentials_are_redacted_from_the_audit_record(world):
    with context(world):
        site = create()
    result = execute(
        world,
        CONFIGURE,
        {
            "unrelated": {
                "siteId": str(site.guid),
                "expectedSiteVersion": site.version,
                "surface": "CONTROLLER",
                "expectedConnectionVersion": 0,
                "endpoint": "https://user:private-endpoint-secret@nvke.example.test/api",
            }
        },
    )
    assert result.errors is None
    assert result.data["configureNvkeConnection"]["errors"][0]["code"] == "INVALID_INPUT"
    record = MutationAuditLog.objects.filter(operation="nvke.connection_configure").latest("timestamp")
    assert record.variables["unrelated"]["endpoint"] == "***REDACTED***"
    assert "private-endpoint-secret" not in json.dumps(record.variables)


def test_nested_private_key_variable_is_redacted_by_its_schema_position():
    from graphql import parse

    from config.schema import schema
    from core.schema.audit import redact_operation_variables

    document = parse("""mutation($key: String!) {
      configureNvkeConnection(input: {
        siteId: "00000000-0000-0000-0000-000000000001", expectedSiteVersion: 1,
        surface: DATASTORE, expectedConnectionVersion: 0, endpoint: "https://nvke.example.test",
        credentials: {clientKeyPem: $key}
      }) { ok }
    }""")
    variables, sensitive = redact_operation_variables(
        {"key": "private-key-secret"}, document=document, schema=schema._schema
    )
    assert variables == {"key": "***REDACTED***"}
    assert sensitive


def test_clearing_credentials_removes_the_metadata_presence_flag(world):
    with context(world):
        site = create()
    variables = {
        "unrelated": {
            "siteId": str(site.guid),
            "expectedSiteVersion": site.version,
            "surface": "CONTROLLER",
            "expectedConnectionVersion": 0,
            "endpoint": "https://nvke.example.test/api",
            "credentials": {"token": "original-token"},
        }
    }
    first = execute(world, CONFIGURE, variables)
    assert first.errors is None
    saved = first.data["configureNvkeConnection"]["data"]
    variables["unrelated"]["expectedSiteVersion"] = saved["version"]
    variables["unrelated"]["expectedConnectionVersion"] = saved["connections"][0]["version"]
    variables["unrelated"]["credentials"] = {}
    cleared = execute(world, CONFIGURE, variables)
    assert cleared.errors is None
    assert cleared.data["configureNvkeConnection"]["ok"]
    assert not cleared.data["configureNvkeConnection"]["data"]["connections"][0]["hasCredentials"]
