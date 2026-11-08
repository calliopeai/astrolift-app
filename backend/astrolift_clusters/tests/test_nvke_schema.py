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


REQUEST_OPERATION = """
mutation($unrelated: RequestNvkeOperationInput!) {
  requestNvkeOperation(input:$unrelated) {
    ok errors { code message }
    data { id version requestId operationKey status stage siteId }
  }
}
"""


def operation_request(world, *, operation_key="listClusters", parameters=None):
    from astrolift_clusters.tests.test_nvke_sites import put

    with context(world):
        site = create()
        put(site)
        site.refresh_from_db()
    return site, {
        "siteId": str(site.guid),
        "expectedSiteVersion": site.version,
        "requestId": str(uuid4()),
        "operationKey": operation_key,
        "parameters": parameters or {},
    }


def test_complete_execution_catalog_is_available_through_real_schema(world):
    from nvke.operations import actions

    result = execute(
        world,
        "{ astroliftNvkeContract { revision actions { key surface verb mutates permission } resources { kind namespaced schema } } }",
    )
    assert result.errors is None
    catalog = result.data["astroliftNvkeContract"]
    assert {item["key"] for item in catalog["actions"]} == {item.key for item in actions()}
    assert len(catalog["resources"]) == 6
    assert len(catalog["revision"]) == 40


def test_request_metadata_and_paginated_history_do_not_expose_private_parameters(world):
    site, request = operation_request(world, parameters={"query": {"name": "PRIVATE_FILTER_IN_SCRIPT"}})
    result = execute(world, REQUEST_OPERATION, {"unrelated": request})
    assert result.errors is None
    payload = result.data["requestNvkeOperation"]
    assert payload["ok"], payload
    assert payload["data"]["operationKey"] == "listClusters"
    log = MutationAuditLog.objects.filter(operation="nvke.operation_request").latest("timestamp")
    assert log.variables["unrelated"]["parameters"] == "***REDACTED***"
    assert "PRIVATE_FILTER_IN_SCRIPT" not in json.dumps(log.variables)
    repeated = execute(world, REQUEST_OPERATION, {"unrelated": request})
    assert repeated.data["requestNvkeOperation"]["data"]["id"] == payload["data"]["id"]
    history = execute(
        world,
        "query($id:GUID!) { astroliftNvkeOperations(siteId:$id, limit:1) { items { id status } totalCount } }",
        {"id": str(site.guid)},
    )
    assert history.errors is None
    assert history.data["astroliftNvkeOperations"]["totalCount"] == 1
    assert history.data["astroliftNvkeOperations"]["items"][0]["id"] == payload["data"]["id"]


def test_rejected_operation_payload_is_redacted_from_audit_even_when_variable_is_renamed(world):
    _site, request = operation_request(world, parameters={"unknown": "PRIVATE_REJECTED_VALUE"})
    result = execute(world, REQUEST_OPERATION, {"unrelated": request})
    assert result.errors is None
    payload = result.data["requestNvkeOperation"]
    assert not payload["ok"]
    assert payload["errors"][0]["code"] == "INVALID_INPUT"
    assert "PRIVATE_REJECTED_VALUE" not in json.dumps(payload)
    log = MutationAuditLog.objects.filter(operation="nvke.operation_request").latest("timestamp")
    assert "PRIVATE_REJECTED_VALUE" not in json.dumps(log.variables)


def test_manage_permission_cannot_dispatch_an_operator_operation(world):
    _site, request = operation_request(world, operation_key="operator.listMachineImage")
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member
    from core.permissions import Permission

    user = get_user_model().objects.create(username="nvke-manager-" + uuid4().hex[:10], is_active=True)
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        user,
        permissions=[Permission.NVKE_READ, Permission.NVKE_MANAGE],
        kind="ORG",
        scope_id=world.org.pk,
        slug="nvke-manager-" + uuid4().hex[:10],
    )
    limited = SimpleNamespace(user=user, org=world.org)
    result = execute(limited, REQUEST_OPERATION, {"unrelated": request})
    assert result.errors is None
    payload = result.data["requestNvkeOperation"]
    assert not payload["ok"]
    assert payload["errors"][0]["code"] == "PERMISSION_DENIED"


REQUEST_TEXT = """
mutation($unrelated: RequestNvkeOperationTextInput!) {
  requestNvkeOperationText(input:$unrelated) {
    ok errors { code message } data { id operationKey requestId status }
  }
}
"""


def test_browser_text_path_retains_int64_native_values_and_masks_all_input(world):
    from astrolift_clusters import nvke_operations
    from astrolift_clusters.models import NvkeOperation
    from astrolift_clusters.tests.test_nvke_sites import put

    with context(world):
        site = create()
        put(site, surface="controller")
        site.refresh_from_db()
    request_id = str(uuid4())
    raw = '{"path":{"nodePoolID":"123e4567-e89b-12d3-a456-426614174000"},"body":{"desiredNodeCount":9007199254740993,"description":"PRIVATE_BROWSER_INPUT"}}'
    request = {
        "siteId": str(site.guid),
        "expectedSiteVersion": site.version,
        "requestId": request_id,
        "operationKey": "updateNodePool",
        "parametersText": raw,
        "expectedActorId": world.user.pk,
    }
    result = execute(world, REQUEST_TEXT, {"unrelated": request})
    assert result.errors is None
    payload = result.data["requestNvkeOperationText"]
    assert payload["ok"], payload
    row = NvkeOperation.objects.get(guid=payload["data"]["id"])
    assert nvke_operations.decode_input(row)["body"]["desiredNodeCount"] == 9007199254740993
    log = MutationAuditLog.objects.filter(operation="nvke.operation_request_text").latest("timestamp")
    assert log.variables["unrelated"]["parametersText"] == "***REDACTED***"
    assert "PRIVATE_BROWSER_INPUT" not in json.dumps(log.variables)
    nvke_operations.save_result(row, {"counter": 9007199254740993, "futureField": {"kept": True}})
    row.save(update_fields=["result_backend_kind", "result_ciphertext", "updated_at", "version"])
    observed = execute(
        world,
        "query($id:GUID!) { astroliftNvkeOperationResultText(operationId:$id) { ok data errors { code } } }",
        {"id": str(row.guid)},
    )
    assert observed.errors is None
    assert "9007199254740993" in observed.data["astroliftNvkeOperationResultText"]["data"]
    assert (
        json.loads(observed.data["astroliftNvkeOperationResultText"]["data"])["counter"] == 9007199254740993
    )
    # Existing JSON clients and the text editor share one durable identity.
    del request["expectedActorId"]
    del request["parametersText"]
    request["parameters"] = json.loads(raw)
    duplicate = execute(world, REQUEST_OPERATION, {"unrelated": request})
    assert duplicate.errors is None
    assert duplicate.data["requestNvkeOperation"]["data"]["id"] == str(row.guid)


@pytest.mark.parametrize("raw", ['{"private":"PRIVATE_REJECTED_TEXT"', '{"x":NaN}', "[]", "null"])
def test_browser_text_rejects_invalid_native_objects_without_leaking_them(world, raw):
    _site, request = operation_request(world)
    request.pop("parameters")
    request["parametersText"] = raw
    request["expectedActorId"] = world.user.pk
    result = execute(world, REQUEST_TEXT, {"unrelated": request})
    assert result.errors is None
    payload = result.data["requestNvkeOperationText"]
    assert not payload["ok"]
    assert "PRIVATE_REJECTED_TEXT" not in json.dumps(payload)
    log = MutationAuditLog.objects.filter(operation="nvke.operation_request_text").latest("timestamp")
    assert "PRIVATE_REJECTED_TEXT" not in json.dumps(log.variables)


def test_browser_reviewed_actor_change_cannot_admit_under_another_identity(world):
    from astrolift_clusters.models import NvkeOperation

    _site, request = operation_request(world)
    request.pop("parameters")
    request.update(parametersText="{}", expectedActorId=world.user.pk + 1)
    response = execute(world, REQUEST_TEXT, {"unrelated": request})
    assert response.errors is None
    assert response.data["requestNvkeOperationText"]["errors"][0]["code"] == "SOURCE_CHANGED"
    assert not NvkeOperation.objects.exists()


def test_exact_browser_receipt_recovery_is_bound_to_original_actor_and_org(world):
    site, request = operation_request(world)
    response = execute(world, REQUEST_OPERATION, {"unrelated": request})
    assert response.errors is None
    query = "query($site:GUID!, $request:String!) { astroliftNvkeRequest(siteId:$site, requestId:$request) { id requestId } }"
    variables = {"site": str(site.guid), "request": request["requestId"]}
    recovered = execute(world, query, variables)
    assert recovered.errors is None
    assert recovered.data["astroliftNvkeRequest"]["id"] == response.data["requestNvkeOperation"]["data"]["id"]
    from astrolift_clusters.tests.test_nvke_sites import world as factory

    foreign = factory.__wrapped__()
    missing = execute(foreign, query, variables)
    assert missing.errors is None
    assert missing.data["astroliftNvkeRequest"] is None


def test_browser_receipt_is_not_shared_with_another_authorized_actor_in_the_same_org(world):
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member
    from core.permissions import Permission

    site, request = operation_request(world)
    response = execute(world, REQUEST_OPERATION, {"unrelated": request})
    assert response.data["requestNvkeOperation"]["ok"]
    user = get_user_model().objects.create(username="other-nvke-caller-" + uuid4().hex[:10], is_active=True)
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        user,
        permissions=[Permission.NVKE_READ],
        kind="ORG",
        scope_id=world.org.pk,
        slug="other-nvke-reader-" + uuid4().hex[:10],
    )
    other = SimpleNamespace(user=user, org=world.org)
    result = execute(
        other,
        "query($site:GUID!, $request:String!) { astroliftNvkeRequest(siteId:$site, requestId:$request) { id } }",
        {"site": str(site.guid), "request": request["requestId"]},
    )
    assert result.errors is None
    assert result.data["astroliftNvkeRequest"] is None


def test_native_schema_text_preserves_exact_int64_limits(world):
    from nvke.native_workflows import workflows

    result = execute(world, "{astroliftNvkeContract{nativeWorkflows{name inputSchemaText}}}")
    assert not result.errors
    rows = result.data["astroliftNvkeContract"]["nativeWorkflows"]
    assert {row["name"] for row in rows} == {row["name"] for row in workflows()}
    for row in rows:
        expected = next(item["input_schema"] for item in workflows() if item["name"] == row["name"])
        assert json.loads(row["inputSchemaText"]) == expected
    renewal = next(row for row in rows if row["name"] == "RenewK8sCertsV2")
    assert '"maximum": 9223372036854775807' in renewal["inputSchemaText"]
