from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError
from nvke.datastore import NvkeDatastore

from astrolift_clusters import nvke_inventory as inventory
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters.models import NvkeOperation
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import datastore_site as _datastore_site
from astrolift_clusters.tests.test_nvke_operations import image_request, reserve
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return _world.__wrapped__()


@pytest.fixture
def configured(world):
    return _configured.__wrapped__(world)


@pytest.fixture
def datastore_site(world):
    yield from _datastore_site.__wrapped__(world)


def test_real_kubernetes_inventory_collects_more_than_one_native_page(datastore_site):
    state = datastore_site
    names = []
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        for _index in range(501):
            request = image_request(state)
            names.append(request["document"]["metadata"]["name"])
            client.create("MachineImage", request["document"])
    row = reserve(
        state,
        operation_key="datastore.listMachineImage",
        parameters={"label_selector": f"astrolift.test/run={state.site.guid}"},
        inventory=True,
    )
    assert (
        runtime.attempt(row.pk, 0, execution=(row.workflow_id, "inventory-run", "NvkeOperationWorkflow"))
        == "succeeded"
    )
    row.refresh_from_db()
    data = operations.decode_result(row)["response"]["data"]
    assert len(data["items"]) == 501 and data["native_page_count"] == 2
    assert data["resource_version"] and data["complete"] is True
    last_name = data["items"][-1]["metadata"]["name"]
    assert last_name in names
    with context(state.world):
        found = inventory.page(
            operation_id=str(row.guid), expected_actor_id=state.world.user.pk, search=last_name
        )
    assert found[4] == 1 and found[2][0].name == last_name


def test_condition_summary_bounds_output_and_ignores_invalid_status_values(configured):
    conditions = {f"Condition{index}": {"status": "True"} for index in range(70)}
    conditions["Invalid"] = {"status": {"PRIVATE": "value"}}
    row = completed(configured, items=[{"id": str(uuid4()), "conditions": conditions}])
    with context(configured.world):
        result = inventory.page(operation_id=str(row.guid), expected_actor_id=configured.world.user.pk)
    assert len(result[2][0].conditions) == 64
    assert result[2][0].conditions_omitted == 6


def test_resource_links_are_stable_and_metadata_query_never_reveals_native_document(configured, monkeypatch):
    row = completed(configured)
    with context(configured.world):
        first = inventory.page(operation_id=str(row.guid), expected_actor_id=configured.world.user.pk)[2][0]
        with monkeypatch.context() as patch:
            patch.setattr("django.core.signing.time.time", lambda: 2000000000)
            result = inventory.resource(
                operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, resource_key=first.key
            )
        assert result[2].key == first.key
    response = execute(
        configured.world,
        """query($id: GUID!, $actor: Int!, $key: String!) {
      astroliftNvkeInventoryResource(operationId: $id, expectedActorId: $actor, resourceKey: $key) {
        operationId siteId observedAt collection { key }
        item { key identity desiredCount conditions { name status } }
      }
      astroliftNvkeInventoryPage(operationId: $id, expectedActorId: $actor, search: null) { totalCount }
    }""",
        {"id": str(row.guid), "actor": configured.world.user.pk, "key": first.key},
    )
    assert not response.errors
    data = response.data["astroliftNvkeInventoryResource"]
    assert data["siteId"] == str(row.site.guid) and data["item"]["key"] == first.key
    assert data["item"]["desiredCount"] == "9007199254740993"
    assert "PRIVATE" not in str(data)
    assert response.data["astroliftNvkeInventoryPage"]["totalCount"] == 205


@pytest.mark.parametrize("change", ["cancellation", "deadline", "membership"])
def test_inventory_checks_live_authority_before_each_native_page(datastore_site, monkeypatch, change):
    state = datastore_site
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        for _index in range(2):
            client.create("MachineImage", image_request(state)["document"])
    row = reserve(
        state,
        operation_key="datastore.listMachineImage",
        parameters={"page_size": 1, "label_selector": f"astrolift.test/run={state.site.guid}"},
        inventory=True,
    )
    original = NvkeDatastore.list
    responses = []

    def changed_after_first_page(client, *args, **kwargs):
        response = original(client, *args, **kwargs)
        responses.append(response)
        if change == "cancellation":
            NvkeOperation.objects.filter(pk=row.pk).update(cancellation_requested_at=timezone.now())
        elif change == "deadline":
            NvkeOperation.objects.filter(pk=row.pk).update(deadline=timezone.now() - timedelta(seconds=1))
        else:
            state.world.member.soft_delete()
        return response

    with monkeypatch.context() as patch:
        patch.setattr(NvkeDatastore, "list", changed_after_first_page)
        status = runtime.attempt(
            row.pk, 0, execution=(row.workflow_id, "inventory-fence-run", "NvkeOperationWorkflow")
        )
    assert status == ("cancelled" if change == "cancellation" else "failed")
    assert len(responses) == 1
    row.refresh_from_db()
    assert "response" not in operations.decode_result(row)


def test_inventory_snapshot_and_document_recheck_current_org_membership(configured):
    row = completed(configured)
    with context(configured.world):
        key = inventory.page(operation_id=str(row.guid), expected_actor_id=configured.world.user.pk)[2][0].key
    other = _world.__wrapped__()
    with context(other):
        for identifier in (row.guid, uuid4()):
            with pytest.raises(NvkeError) as missing:
                inventory.page(operation_id=str(identifier), expected_actor_id=other.user.pk)
            assert missing.value.code == "NOT_FOUND"
        with pytest.raises(NvkeError) as missing:
            inventory.document(operation_id=str(row.guid), expected_actor_id=other.user.pk, resource_key=key)
        assert missing.value.code == "NOT_FOUND"
    configured.world.member.soft_delete()
    with context(configured.world):
        with pytest.raises(PermissionDenied):
            inventory.page(operation_id=str(row.guid), expected_actor_id=configured.world.user.pk)
        with pytest.raises(PermissionDenied):
            inventory.document(
                operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, resource_key=key
            )


def completed(state, *, key="listClusters", items=None):
    row = reserve(state, operation_key=key, inventory=True)
    row.status = "succeeded"
    row.finished_at = timezone.now()
    operations.save_result(
        row,
        {
            "response": {
                "status_code": 200,
                "data": {
                    "inventory_format": 1,
                    "collection": key,
                    "complete": True,
                    "items": items
                    if items is not None
                    else [
                        {
                            "id": str(uuid4()),
                            "name": f"cluster-{index:04}",
                            "desiredNodeCount": 9007199254740993,
                            "credentials": "PRIVATE_NATIVE_RESULT",
                            "conditions": {"provisioned": {"status": "True", "message": "PRIVATE_CONDITION"}},
                        }
                        for index in range(205)
                    ],
                    "resource_version": "",
                    "native_page_count": 1,
                },
            }
        },
    )
    row.save(
        update_fields=[
            "status",
            "finished_at",
            "result_backend_kind",
            "result_ciphertext",
            "version",
            "updated_at",
        ]
    )
    return row


def test_complete_inventory_paginates_and_searches_all_rows_without_revealing_native_payloads(configured):
    row = completed(configured)
    with context(configured.world):
        values = inventory.page(
            operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, limit=100
        )
        first, cursor = values[2], values[3]
        assert len(first) == 100 and values[4] == 205
        second = inventory.page(
            operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, limit=100, after=cursor
        )
        third = inventory.page(
            operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, limit=100, after=second[3]
        )
        assert len(second[2]) == 100 and len(third[2]) == 5 and third[3] is None
        found = inventory.page(
            operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, search="cluster-0204"
        )
        assert len(found[2]) == found[4] == 1
        assert found[2][0].desired_count == "9007199254740993"
        assert "PRIVATE" not in str(found[2])
        assert found[2][0].conditions == [("provisioned", "True")]
        text = inventory.document(
            operation_id=str(row.guid), expected_actor_id=configured.world.user.pk, resource_key=first[0].key
        )
        assert "PRIVATE_NATIVE_RESULT" in text
        assert "9007199254740993" in text


def test_inventory_cursors_cannot_cross_snapshot_search_actor_or_version(configured):
    first, other = completed(configured), completed(configured)
    with context(configured.world):
        args = {"operation_id": str(first.guid), "expected_actor_id": configured.world.user.pk, "limit": 1}
        page = inventory.page(**args)
        for changed in ({"operation_id": str(other.guid)}, {"search": "cluster"}, {"limit": 2}):
            with pytest.raises(NvkeError) as error:
                inventory.page(**{**args, **changed, "after": page[3]})
            assert error.value.code == "INVALID_CURSOR"
        with pytest.raises(NvkeError) as actor:
            inventory.page(**{**args, "expected_actor_id": configured.world.user.pk + 1})
        assert actor.value.code == "SOURCE_CHANGED"
        with pytest.raises(NvkeError):
            inventory.document(
                operation_id=str(other.guid),
                expected_actor_id=configured.world.user.pk,
                resource_key=page[2][0].key,
            )
        with pytest.raises(NvkeError):
            inventory.page(**args, after=page[3][:-1] + "X")


def test_inventory_and_native_requests_have_distinct_idempotency_but_native_digest_is_unchanged(configured):
    request_id = uuid4()
    native = reserve(configured, request_id=request_id)
    assert reserve(configured, request_id=request_id).pk == native.pk
    with pytest.raises(NvkeError) as error:
        reserve(configured, request_id=request_id, inventory=True)
    assert error.value.code == "CONFLICT"
    snapshot = reserve(configured, inventory=True)
    assert snapshot.authority["inventory"] is True
    assert reserve(configured, request_id=snapshot.request_id, inventory=True).pk == snapshot.pk


def test_graphql_inventory_contract_keeps_native_catalog_and_redacts_inventory_admission(configured):
    world = configured.world
    catalog = execute(
        world,
        "{ astroliftNvkeContract { actions { key } collections { key permission resourceKind defaultParametersText actions { key } } } }",
    )
    assert catalog.errors is None
    assert len(catalog.data["astroliftNvkeContract"]["actions"]) == 88
    assert len(catalog.data["astroliftNvkeContract"]["collections"]) == 18
    response = execute(
        world,
        "mutation($input:RequestNvkeOperationTextInput!) { requestNvkeInventory(input:$input) { ok errors {code message} data {id isInventory} } }",
        {
            "input": {
                "siteId": str(configured.site.guid),
                "expectedSiteVersion": configured.site.version,
                "expectedActorId": world.user.pk,
                "requestId": str(uuid4()),
                "operationKey": "listClusters",
                "parametersText": '{"query":{"name":"PRIVATE_NATIVE_FILTER"}}',
                "timeoutSeconds": 60,
            }
        },
    )
    assert response.errors is None
    assert response.data["requestNvkeInventory"]["ok"]
    assert response.data["requestNvkeInventory"]["data"]["isInventory"] is True
    from core.schema.audit import MutationAuditLog

    log = MutationAuditLog.objects.filter(operation="nvke.inventory_request").latest("timestamp")
    assert "PRIVATE_NATIVE_FILTER" not in json.dumps(log.variables)


def test_graphql_inventory_page_returns_only_summary_fields_and_exact_document_on_explicit_query(configured):
    row = completed(configured)
    variables = {"id": str(row.guid), "actor": configured.world.user.pk}
    result = execute(
        configured.world,
        "query($id:GUID!, $actor:Int!) { astroliftNvkeInventoryPage(operationId:$id, expectedActorId:$actor, limit:1) { items {key name desiredCount conditions{name status}} totalCount nextCursor nativePageCount } }",
        variables,
    )
    assert result.errors is None
    page = result.data["astroliftNvkeInventoryPage"]
    assert page["totalCount"] == 205
    assert page["items"][0]["desiredCount"] == "9007199254740993"
    assert "PRIVATE" not in json.dumps(result.data)
    reveal = execute(
        configured.world,
        "query($id:GUID!, $actor:Int!, $key:String!) { astroliftNvkeInventoryDocumentText(operationId:$id, expectedActorId:$actor, resourceKey:$key) { ok data errors{code} } }",
        {**variables, "key": page["items"][0]["key"]},
    )
    assert reveal.errors is None
    assert "PRIVATE_NATIVE_RESULT" in reveal.data["astroliftNvkeInventoryDocumentText"]["data"]
