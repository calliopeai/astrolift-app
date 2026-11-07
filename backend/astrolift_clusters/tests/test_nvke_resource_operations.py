from __future__ import annotations

import copy
import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from nvke.client import NvkeError
from nvke.datastore import NvkeDatastore

from astrolift_clusters import nvke_inventory as inventory
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_resource_operations as resource_operations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_access import controller
from astrolift_clusters.tests.test_nvke_inventory import completed
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import datastore_site as _datastore_site
from astrolift_clusters.tests.test_nvke_operations import image_request, reserve
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context, put

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


def source_for(state, row):
    with context(state.world):
        item = inventory.page(operation_id=str(row.guid), expected_actor_id=state.world.user.pk)[2][0]
    return {"operation_id": str(row.guid), "version": row.version, "resource_key": item.key}


def run(row, name):
    return runtime.attempt(row.pk, 0, execution=(row.workflow_id, name, "NvkeOperationWorkflow"))


def test_resource_admission_fixes_target_and_idempotency_includes_the_reviewed_source(configured):
    source = completed(configured)
    reference = source_for(configured, source)
    row = reserve(
        configured, operation_key="updateCluster", parameters={"body": {}}, resource_source=reference
    )
    target = operations.decode_input(row)
    with context(configured.world):
        native = inventory.document(
            operation_id=str(source.guid),
            expected_actor_id=configured.world.user.pk,
            resource_key=reference["resource_key"],
        )
    assert target["path"] == {"clusterID": json.loads(native)["id"]}
    assert row.authority["resource"]["source"] == reference
    source.soft_delete()
    assert (
        reserve(
            configured,
            request_id=row.request_id,
            operation_key="updateCluster",
            parameters={"body": {}},
            resource_source=reference,
        ).pk
        == row.pk
    )
    with pytest.raises(NvkeError) as changed:
        reserve(configured, request_id=row.request_id, operation_key="updateCluster", parameters={"body": {}})
    assert changed.value.code == "CONFLICT"


def test_controller_editor_projects_writable_fields_without_changing_native_values(configured):
    source = completed(
        configured,
        items=[
            {
                "id": str(uuid4()),
                "name": "cluster-a",
                "etcdEncrypted": True,
                "controlPlaneConfig": {
                    "desiredCount": 3,
                    "systemReservedMemory": "16Gi",
                    "readOnlyCounter": 99,
                },
                "privateReadOnlyField": "PRIVATE_NATIVE_DATA",
            }
        ],
    )
    reference = source_for(configured, source)
    with context(configured.world):
        value = resource_operations.editor(
            site_id=str(configured.site.guid),
            expected_actor_id=configured.world.user.pk,
            operation_key="updateCluster",
            reference=reference,
        )
    parameters = value[3]
    assert value[4] is None and value[5] == "observed_only"
    assert parameters["body"]["controlPlaneConfig"]["desiredCount"] == 3
    assert "readOnlyCounter" not in str(parameters) and "PRIVATE_NATIVE_DATA" not in str(parameters)


def test_public_edits_require_a_read_etag_and_fix_it_into_the_native_request(configured):
    with context(configured.world):
        put(configured.site, surface="operator", endpoint="https://operator.example.test")
        configured.site.refresh_from_db()
    from tests.nvke.support import PUBLIC_SPECS

    native = {
        "metadata": {"name": "image-a", "uid": str(uuid4())},
        "spec": copy.deepcopy(PUBLIC_SPECS["MachineImage"]),
    }
    source = completed(configured, key="operator.listMachineImage", items=[native])
    reference = source_for(configured, source)
    with context(configured.world):
        editor = resource_operations.editor(
            site_id=str(configured.site.guid),
            expected_actor_id=configured.world.user.pk,
            operation_key="operator.updateMachineImage",
            reference=reference,
        )
    assert editor[3] is None and editor[4] == "operator.getMachineImage"
    read = reserve(configured, operation_key=editor[4], parameters={}, resource_source=reference)
    read.status = "succeeded"
    from nvke.client import NvkeResponse

    operations.save_result(read, {"response": asdict(NvkeResponse(200, native, "original-etag"))})
    read.save(update_fields=["status", "result_backend_kind", "result_ciphertext", "updated_at", "version"])
    fresh = {"operation_id": str(read.guid), "version": read.version, "resource_key": None}
    with context(configured.world):
        value = resource_operations.editor(
            site_id=str(configured.site.guid),
            expected_actor_id=configured.world.user.pk,
            operation_key="operator.updateMachineImage",
            reference=fresh,
        )
    assert value[3]["path"] == {"name": "image-a"}
    assert value[3]["resource_version"] == "original-etag"
    assert value[3]["body"]["metadata"] == {"name": "image-a"}
    row = reserve(
        configured, operation_key="operator.updateMachineImage", parameters=value[3], resource_source=fresh
    )
    assert operations.decode_input(row)["resource_version"] == "original-etag"


@pytest.mark.parametrize("change", ["path", "version", "action", "site"])
def test_resource_admission_rejects_changed_target_source_or_action(configured, change):
    source = completed(configured)
    reference = source_for(configured, source)
    values = {"operation_key": "updateCluster", "parameters": {"body": {}}, "resource_source": reference}
    if change == "path":
        values["parameters"]["path"] = {"clusterID": str(uuid4())}
    elif change == "version":
        reference["version"] += 1
    elif change == "action":
        values["operation_key"] = "updateNodePool"
    else:
        foreign = _configured.__wrapped__(_world.__wrapped__())
        values["resource_source"] = source_for(foreign, completed(foreign))
    with pytest.raises(NvkeError) as error:
        reserve(configured, **values)
    assert error.value.code in {"INVALID_INPUT", "SOURCE_CHANGED", "NOT_FOUND"}


@pytest.mark.parametrize("status", ["queued", "awaiting_ready", "uncertain"])
def test_resource_mutation_does_not_issue_a_second_nonce_over_an_unresolved_effect(configured, status):
    source = completed(configured)
    reference = source_for(configured, source)
    row = reserve(
        configured, operation_key="updateCluster", parameters={"body": {}}, resource_source=reference
    )
    row.status = status
    row.save(update_fields=["status", "updated_at", "version"])
    with pytest.raises(NvkeError) as busy:
        reserve(configured, operation_key="updateCluster", parameters={"body": {}}, resource_source=reference)
    assert busy.value.code == "RESOURCE_BUSY"
    assert (
        reserve(
            configured,
            request_id=row.request_id,
            operation_key="updateCluster",
            parameters={"body": {}},
            resource_source=reference,
        ).pk
        == row.pk
    )


def test_resource_admission_graphql_preserves_source_metadata_and_masks_native_parameters(configured):
    source = completed(configured)
    reference = source_for(configured, source)
    query = """mutation($input: RequestNvkeResourceOperationInput!) {
      requestNvkeResourceOperation(input: $input) { ok errors { code }
        data { id resourceSource { operationId version resourceKey } }
      }
    }"""
    values = {
        "siteId": str(configured.site.guid),
        "expectedSiteVersion": configured.site.version,
        "expectedActorId": configured.world.user.pk,
        "requestId": str(uuid4()),
        "operationKey": "updateCluster",
        "parametersText": '{"body":{}}',
        "sourceOperationId": reference["operation_id"],
        "expectedSourceVersion": reference["version"],
        "sourceResourceKey": reference["resource_key"],
    }
    result = execute(configured.world, query, {"input": values})
    assert not result.errors and result.data["requestNvkeResourceOperation"]["ok"]
    assert (
        result.data["requestNvkeResourceOperation"]["data"]["resourceSource"]["resourceKey"]
        == reference["resource_key"]
    )
    from graphql import parse

    from config.schema import schema
    from core.schema.audit import redact_operation_variables

    redacted, sensitive = redact_operation_variables(
        {"input": values}, document=parse(query), schema=schema._schema
    )
    assert sensitive and redacted["input"]["parametersText"] == "***REDACTED***"


def test_resource_editor_query_requires_the_exact_current_actor(configured):
    source = completed(configured)
    reference = source_for(configured, source)
    query = """query($site: GUID!, $actor: Int!, $source: GUID!, $version: Int!, $key: String!) {
      astroliftNvkeResourceEditor(siteId: $site, expectedActorId: $actor, operationKey: "updateCluster",
        sourceOperationId: $source, expectedSourceVersion: $version, sourceResourceKey: $key) {
        ok errors { code } data { operationKey parametersText concurrency source { operationId version resourceKey } }
      }
    }"""
    values = {
        "site": str(configured.site.guid),
        "actor": configured.world.user.pk,
        "source": reference["operation_id"],
        "version": reference["version"],
        "key": reference["resource_key"],
    }
    result = execute(configured.world, query, values)
    assert not result.errors and result.data["astroliftNvkeResourceEditor"]["ok"]
    data = result.data["astroliftNvkeResourceEditor"]["data"]
    assert data["concurrency"] == "observed_only"
    assert json.loads(data["parametersText"])["path"]["clusterID"]
    denied = execute(configured.world, query, {**values, "actor": configured.world.user.pk + 10000})
    assert denied.data["astroliftNvkeResourceEditor"]["errors"][0]["code"] == "SOURCE_CHANGED"


def test_controller_changed_observation_refuses_to_dispatch_a_patch(configured):
    identity = str(uuid4())
    with controller({"body": {"name": "cluster-a"}}, identity) as (endpoint, _id, _calls):
        with context(configured.world):
            put(
                configured.site,
                expected_connection_version=configured.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            configured.site.refresh_from_db()
        source = completed(
            configured,
            items=[{"id": identity, "name": "cluster-a", "controlPlaneKubernetesVersion": "1.35.0"}],
        )
        row = reserve(
            configured,
            operation_key="updateCluster",
            parameters={"body": {}},
            resource_source=source_for(configured, source),
        )
        assert run(row, "controller-observation-changed") == "failed"
        row.refresh_from_db()
        assert row.error_code == "CONFLICT" and "sending" not in operations.decode_result(row)


def prepare_image(state):
    document = image_request(state)["document"]
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        created = client.create("MachineImage", document)
    source = reserve(
        state,
        operation_key="datastore.listMachineImage",
        inventory=True,
        parameters={"label_selector": f"astrolift.test/run={state.site.guid}"},
    )
    assert run(source, "resource-inventory") == "succeeded"
    source.refresh_from_db()
    return document, created, source, source_for(state, source)


def test_controller_volatile_status_changes_do_not_prevent_a_reviewed_patch(configured):
    identity = str(uuid4())
    with controller({"body": {"name": "cluster-a"}}, identity) as (endpoint, _id, calls):
        with context(configured.world):
            put(
                configured.site,
                expected_connection_version=configured.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            configured.site.refresh_from_db()
        source = completed(
            configured,
            items=[{"id": identity, "name": "cluster-a", "conditions": {"Ready": {"status": "True"}}}],
        )
        row = reserve(
            configured,
            operation_key="updateCluster",
            parameters={"body": {}},
            resource_source=source_for(configured, source),
        )
        assert run(row, "controller-status-updated") == "awaiting_ready"
        assert sum(method == "PATCH" for method, _path in calls) == 1


def test_real_kubernetes_bound_replace_carries_native_uid_and_version(datastore_site):
    state = datastore_site
    document, created, _source, reference = prepare_image(state)
    document["metadata"]["annotations"] = {"reviewed": "2026-11-08"}
    row = reserve(
        state,
        operation_key="datastore.replaceMachineImage",
        parameters={"document": document},
        resource_source=reference,
    )
    request = operations.decode_input(row)
    assert request["uid"] == created.data["metadata"]["uid"]
    assert request["resource_version"] == created.resource_version
    assert run(row, "bound-replace") == "succeeded"
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        actual = client.get("MachineImage", document["metadata"]["name"])
    assert actual.data["metadata"]["annotations"]["reviewed"] == "2026-11-08"
    assert actual.data["metadata"]["uid"] == request["uid"]


def test_real_kubernetes_stale_snapshot_fails_without_overwriting_current_resource(datastore_site):
    state = datastore_site
    document, created, _source, reference = prepare_image(state)
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        external = copy.deepcopy(document)
        external["metadata"]["annotations"] = {"external": "2026-11-09"}
        client.replace(
            "MachineImage", document["metadata"]["name"], external, resource_version=created.resource_version
        )
    row = reserve(
        state,
        operation_key="datastore.replaceMachineImage",
        parameters={"document": document},
        resource_source=reference,
    )
    assert run(row, "stale-replace") == "failed"
    row.refresh_from_db()
    assert row.error_code == "CONFLICT" and "sending" not in operations.decode_result(row)
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        assert (
            client.get("MachineImage", document["metadata"]["name"]).data["metadata"]["annotations"][
                "external"
            ]
            == "2026-11-09"
        )


def test_real_kubernetes_replacement_between_preflight_and_put_cannot_be_overwritten(
    datastore_site, monkeypatch
):
    state = datastore_site
    document, created, _source, reference = prepare_image(state)
    row = reserve(
        state,
        operation_key="datastore.replaceMachineImage",
        parameters={"document": document},
        resource_source=reference,
    )
    original = NvkeDatastore.replace
    replacement = []

    def race(client, kind, name, *args, **kwargs):
        client.delete(
            kind,
            name,
            uid=created.data["metadata"]["uid"],
            resource_version=created.resource_version,
            propagation_policy="Background",
        )
        changed = copy.deepcopy(document)
        changed["spec"]["buildDate"] = "2026-11-10"
        replacement.append(client.create(kind, changed))
        return original(client, kind, name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(NvkeDatastore, "replace", race)
        assert run(row, "replace-race") == "failed"
    row.refresh_from_db()
    assert row.error_code == "CONFLICT"
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:
        current = client.get("MachineImage", document["metadata"]["name"])
    assert current.data["metadata"]["uid"] == replacement[0].data["metadata"]["uid"]
    assert current.data["spec"]["buildDate"] == "2026-11-10"


@pytest.fixture
def cluster_subresources(configured):
    from contextlib import ExitStack

    identity = str(uuid4())
    details = {
        "spire": {
            "requested": {"address": "spire.old.test", "port": 30440},
            "nodes": [
                {
                    "nodeID": str(uuid4()),
                    "name": "node-a",
                    "reachable": True,
                    "hasValidSVID": True,
                    "serverAddress": "spire.old.test",
                    "serverPort": 30440,
                }
            ],
        },
        "control_plane": {"nativeStatus": "observed"},
        "history": {"items": []},
    }
    with ExitStack() as stack:
        endpoint, _, calls = stack.enter_context(
            controller({"body": {"name": "cluster-a"}}, identity, details)
        )
        with context(configured.world):
            put(
                configured.site,
                expected_connection_version=configured.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            configured.site.refresh_from_db()
        source = completed(configured, items=[{"id": identity, "name": "cluster-a"}])
        yield configured, source, source_for(configured, source), identity, details, calls


def prepared_spire(state):
    configured, source, reference, identity, details, calls = state
    read = reserve(
        configured, operation_key="getClusterSpireServer", parameters={}, resource_source=reference
    )
    assert run(read, "spire-read") == "succeeded"
    read.refresh_from_db()
    return read, {"operation_id": str(read.guid), "version": read.version, "resource_key": None}


@pytest.mark.parametrize(
    "key", ["getClusterControlPlane", "getClusterControlPlaneConfigHistory", "getClusterSpireServer"]
)
def test_bound_subresource_reads_confirm_parent_identity_around_the_native_read(cluster_subresources, key):
    configured, source, reference, identity, details, calls = cluster_subresources
    row = reserve(configured, operation_key=key, parameters={}, resource_source=reference)
    assert operations.decode_input(row) == {"path": {"clusterID": identity}}
    assert run(row, "bound-subresource") == "succeeded"
    row.refresh_from_db()
    assert calls[0] == calls[-1] == ("GET", f"/clusters/{identity}")
    assert len(calls) == 3
    assert row.authority["resource"]["subresource"] == key
    assert row.authority["resource"]["identity_action"] == "getCluster"
    assert (
        operations.decode_result(row)["response"]["data"]
        == details[
            {
                "getClusterControlPlane": "control_plane",
                "getClusterControlPlaneConfigHistory": "history",
                "getClusterSpireServer": "spire",
            }[key]
        ]
    )


def test_spire_editor_requires_its_bound_read_and_survives_parent_snapshot_removal(cluster_subresources):
    configured, source, reference, identity, details, calls = cluster_subresources
    with context(configured.world):
        editor = resource_operations.editor(
            site_id=str(configured.site.guid),
            expected_actor_id=configured.world.user.pk,
            operation_key="updateClusterSpireServer",
            reference=reference,
        )
    assert editor[3] is None and editor[4:] == ("getClusterSpireServer", "observed_only")
    assert calls == []
    read, prepared = prepared_spire(cluster_subresources)
    source.soft_delete()
    with context(configured.world):
        editor = resource_operations.editor(
            site_id=str(configured.site.guid),
            expected_actor_id=configured.world.user.pk,
            operation_key="updateClusterSpireServer",
            reference=prepared,
        )
    assert editor[3] == {"path": {"clusterID": identity}, "body": details["spire"]["requested"]}
    assert editor[4:] == (None, "observed_only")
    assert "nodes" not in str(editor[3])


def test_unbound_spire_read_cannot_authorize_a_resource_edit(cluster_subresources):
    configured, _, _, identity, _, _ = cluster_subresources
    read = reserve(
        configured, operation_key="getClusterSpireServer", parameters={"path": {"clusterID": identity}}
    )
    assert run(read, "unbound-spire-read") == "succeeded"
    read.refresh_from_db()
    with pytest.raises(NvkeError) as error:
        reserve(
            configured,
            operation_key="updateClusterSpireServer",
            parameters={"body": {"address": "spire.new.test", "port": 30440}},
            resource_source={"operation_id": str(read.guid), "version": read.version, "resource_key": None},
        )
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("key", ["updateCluster", "getClusterControlPlane"])
def test_spire_read_cannot_be_reused_as_a_different_resource_observation(cluster_subresources, key):
    configured, _, _, _, _, _ = cluster_subresources
    _, prepared = prepared_spire(cluster_subresources)
    with pytest.raises(NvkeError) as error:
        reserve(
            configured,
            operation_key=key,
            parameters={"body": {}} if key == "updateCluster" else {},
            resource_source=prepared,
        )
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("configuration_changed", [True, False])
def test_spire_patch_checks_requested_configuration_but_ignores_live_node_status(
    cluster_subresources, configuration_changed
):
    configured, _, _, _, details, calls = cluster_subresources
    _, prepared = prepared_spire(cluster_subresources)
    row = reserve(
        configured,
        operation_key="updateClusterSpireServer",
        parameters={"body": {"address": "spire.new.test", "port": 30440}},
        resource_source=prepared,
    )
    if configuration_changed:
        details["spire"]["requested"]["address"] = "another-writer.test"
    else:
        details["spire"]["nodes"][0]["message"] = "Volatile diagnostic state changed"
    result = run(row, "spire-patch")
    row.refresh_from_db()
    if configuration_changed:
        assert result == "failed" and row.error_code == "CONFLICT"
        assert not any(method == "PATCH" for method, _ in calls)
    else:
        assert result == "succeeded"
        assert sum(method == "PATCH" for method, _ in calls) == 1


def test_initial_spire_configuration_has_an_explicit_empty_draft(cluster_subresources):
    configured, _, _, _, details, _ = cluster_subresources
    details["spire"].pop("requested")
    _, prepared = prepared_spire(cluster_subresources)
    with context(configured.world):
        editor = resource_operations.editor(
            site_id=str(configured.site.guid),
            expected_actor_id=configured.world.user.pk,
            operation_key="updateClusterSpireServer",
            reference=prepared,
        )
    assert editor[3]["body"] == {} and editor[4] is None
    with pytest.raises(NvkeError) as error:
        reserve(
            configured,
            operation_key="updateClusterSpireServer",
            parameters={"body": {}},
            resource_source=prepared,
        )
    assert error.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("change", ["missing", "identity", "during-read"])
def test_bound_spire_read_does_not_promote_an_unconfirmed_parent(cluster_subresources, change):
    configured, _, reference, _, details, calls = cluster_subresources
    row = reserve(configured, operation_key="getClusterSpireServer", parameters={}, resource_source=reference)
    if change == "missing":
        details["parent_missing"] = True
    elif change == "identity":
        details["parent_override"] = {"id": str(uuid4())}
    else:
        details["remove_parent_during_read"] = True
    assert run(row, "parent-unconfirmed") == "failed"
    row.refresh_from_db()
    assert row.error_code in {"NOT_FOUND", "CONFLICT"}
    assert "response" not in operations.decode_result(row)
    assert len(calls) == (3 if change == "during-read" else 1)


def test_prepared_spire_read_does_not_survive_connection_authority_rotation(cluster_subresources):
    configured, _, _, _, _, _ = cluster_subresources
    _, prepared = prepared_spire(cluster_subresources)
    with context(configured.world):
        configured.connection.refresh_from_db()
        put(
            configured.site,
            expected_connection_version=configured.connection.version,
            credentials={"token": "ROTATED_PRIVATE"},
        )
    configured.site.refresh_from_db()
    with pytest.raises(NvkeError) as error:
        reserve(
            configured,
            operation_key="updateClusterSpireServer",
            parameters={"body": {"address": "spire.new.test", "port": 30440}},
            resource_source=prepared,
        )
    assert error.value.code == "SOURCE_CHANGED"
