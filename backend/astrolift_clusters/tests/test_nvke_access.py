from __future__ import annotations

import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from nvke.client import NvkeError, NvkeResponse
from nvke.contract import controller_spec
from nvke.operations import action
from tests.nvke.test_datastore_integration import seed_access

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters import nvke_workload_access as access
from astrolift_clusters.models import NvkeWorkloadAccessBinding
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import datastore_site as _datastore_site
from astrolift_clusters.tests.test_nvke_operations import reserve
from astrolift_clusters.tests.test_nvke_operations import world as _world
from astrolift_clusters.tests.test_nvke_sites import context, put
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


def create_request():
    body = copy.deepcopy(
        controller_spec()["paths"]["/clusters"]["post"]["requestBody"]["content"]["application/json"][
            "example"
        ]
    )
    body["name"] = "private-cluster-name-" + uuid4().hex[:12]
    return {"body": body}


def accepted(state):
    request = create_request()
    row = reserve(state, operation_key="createCluster", parameters=request)
    identity = str(uuid4())
    row.remote_reference = operations.created_cluster_reference(
        row,
        action("createCluster"),
        request,
        NvkeResponse(201, {"id": identity, "name": request["body"]["name"]}),
    )
    row.save(update_fields=["remote_reference", "updated_at", "version"])
    return row, request["body"]["name"], identity


def test_receipt_binding_is_org_site_interface_and_native_identity_scoped(configured):
    row, name, identity = accepted(configured)
    assert "private-cluster-name" not in str(row.remote_reference)
    assert operations.trusted_cluster_ids(row, name) == frozenset([identity])
    assert operations.trusted_cluster_ids(row, "foreign") == frozenset()
    foreign = _configured.__wrapped__(_world.__wrapped__())
    foreign_row = reserve(foreign)
    assert operations.trusted_cluster_ids(foreign_row, name) == frozenset()
    # Credential renewal retains the logical interface and resource identity.
    with context(configured.world):
        configured.site.refresh_from_db()
        put(
            configured.site,
            expected_connection_version=configured.connection.version,
            credentials={"token": "ROTATED_PRIVATE"},
        )
    assert operations.trusted_cluster_ids(row, name) == frozenset([identity])
    configured.connection.soft_delete()
    with context(configured.world):
        configured.site.refresh_from_db()
        put(configured.site, credentials={"token": "REPLACEMENT_INTERFACE"})
    assert operations.trusted_cluster_ids(row, name) == frozenset()


@pytest.mark.parametrize(
    "changed",
    [{"id": "not-a-uuid"}, {"id": "00000000-0000-0000-0000-000000000000"}, {"name": "another-cluster"}],
)
def test_unconfirmed_or_mismatched_native_identity_never_becomes_a_binding(configured, changed):
    request = create_request()
    row = reserve(configured, operation_key="createCluster", parameters=request)
    data = {"id": str(uuid4()), "name": request["body"]["name"], **changed}
    assert (
        operations.created_cluster_reference(row, action("createCluster"), request, NvkeResponse(201, data))
        == {}
    )
    assert (
        operations.created_cluster_reference(row, action("createCluster"), request, NvkeResponse(409, data))
        == {}
    )


@contextmanager
def controller(request, identity=None, subresources=None):
    identity = identity or str(uuid4())
    calls = []
    subresources = subresources if subresources is not None else {}
    response = {"id": identity, "name": request["body"]["name"], "conditions": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path))
            if self.path.startswith("/clusters?"):
                self.reply({"clusters": []})
            elif self.path.startswith("/nodePools?"):
                self.reply({"nodePools": []})
            elif self.path.startswith("/nodes?"):
                self.reply({"nodes": []})
            elif self.path.endswith("/control-plane/configHistory"):
                self.reply(subresources.get("history", {}))
            elif self.path.endswith("/control-plane"):
                self.reply(subresources.get("control_plane", {}))
            elif self.path.endswith("/spire-server"):
                self.reply(subresources.get("spire", {"nodes": []}))
                if subresources.get("remove_parent_during_read"):
                    subresources["parent_missing"] = True
            elif subresources.get("parent_missing"):
                self.reply({}, 404)
            else:
                self.reply({**response, **subresources.get("parent_override", {})})

        def do_POST(self):
            calls.append((self.command, self.path))
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply(response, 201)

        def reply(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PATCH(self):
            calls.append((self.command, self.path))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/spire-server"):
                subresources.setdefault("spire", {"nodes": []})["requested"] = body
                for node in subresources["spire"].get("nodes", []):
                    node.update(serverAddress=body["address"], serverPort=body["port"])
                self.reply({}, 202)
            else:
                response.update(body)
                self.reply(response)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", identity, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_native_controller_response_persists_an_owned_receipt_before_readiness(configured):
    request = create_request()
    with controller(request) as (endpoint, identity, calls):
        with context(configured.world):
            connection = put(
                configured.site,
                expected_connection_version=configured.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            configured.site.refresh_from_db()
        state = SimpleNamespace(world=configured.world, site=configured.site, connection=connection)
        row = reserve(state, operation_key="createCluster", parameters=request)
        assert (
            runtime.attempt(row.pk, 0, execution=(row.workflow_id, "native-create", "NvkeOperationWorkflow"))
            == "awaiting_ready"
        )
        row.refresh_from_db()
    assert operations.trusted_cluster_ids(row, request["body"]["name"]) == frozenset([identity])
    assert "private-cluster-name" not in str(row.remote_reference)
    assert sum(method == "POST" for method, _path in calls) == 1


@pytest.mark.parametrize("ownership", ["create_receipt", "enrolled_read"])
def test_real_postgres_controller_receipt_and_kubernetes_download_without_a_native_crd(
    datastore_site, ownership
):
    state = datastore_site
    with open_connection(
        state.connection, namespace=state.site.organization_namespace, checkpoint=lambda: None
    ) as client:

        class Raw:
            def get(self, path, **kwargs):
                return client._http.get(state.connection.endpoint.rstrip("/") + path, **kwargs)

            def post(self, path, **kwargs):
                return client._http.post(state.connection.endpoint.rstrip("/") + path, **kwargs)

            def put(self, path, **kwargs):
                return client._http.put(state.connection.endpoint.rstrip("/") + path, **kwargs)

            def delete(self, path, **kwargs):
                return client._http.request("DELETE", state.connection.endpoint.rstrip("/") + path, **kwargs)

        raw = Raw()
        namespace_path = f"/api/v1/namespaces/{state.site.organization_namespace}"
        if raw.get(namespace_path).status_code == 404:
            raw.post(
                "/api/v1/namespaces", json={"metadata": {"name": state.site.organization_namespace}}
            ).raise_for_status()
        resource_name = "receipt-access-" + uuid4().hex[:12]
        name, identity, content, secret = seed_access(
            (client, raw, state.site.organization_namespace), resource_name
        )
        native = client.get("NVKECluster", resource_name).data["metadata"]
        client.delete(
            "NVKECluster",
            resource_name,
            uid=native["uid"],
            resource_version=native["resourceVersion"],
            propagation_policy="Background",
        )
        try:
            request = create_request()
            request["body"]["name"] = name
            with controller(request, identity) as (endpoint, _identity, _calls):
                with context(state.world):
                    put(state.site, surface="controller", endpoint=endpoint, allow_http=True)
                    state.site.refresh_from_db()
                creation = reserve(
                    state,
                    operation_key="getCluster" if ownership == "enrolled_read" else "createCluster",
                    parameters={"path": {"clusterID": identity}} if ownership == "enrolled_read" else request,
                )
                assert runtime.attempt(
                    creation.pk,
                    0,
                    execution=(creation.workflow_id, "receipt-create", "NvkeOperationWorkflow"),
                ) == ("succeeded" if ownership == "enrolled_read" else "awaiting_ready")
                creation.refresh_from_db()
                if ownership == "enrolled_read":
                    with context(state.world):
                        binding = access.enroll(**enrollment(state, creation))
                    creation.soft_delete()
            row = reserve(
                state,
                operation_key="datastore.getWorkloadKubeconfig",
                parameters={"cluster_name": name, "management_namespace": state.site.organization_namespace},
            )
            assert (
                runtime.attempt(
                    row.pk, 0, execution=(row.workflow_id, "receipt-download", "NvkeOperationWorkflow")
                )
                == "succeeded"
            )
            row.refresh_from_db()
            result = operations.decode_result(row)
            assert result["response"]["data"]["kubeconfig"] == content.decode()
            assert content not in bytes(row.result_ciphertext)
            if ownership == "enrolled_read":
                with context(state.world):
                    access.revoke(
                        site_id=state.site.guid,
                        expected_site_version=state.site.version,
                        expected_actor_id=state.world.user.pk,
                        binding_id=binding.guid,
                        expected_version=binding.version,
                    )
                retry = reserve(
                    state, operation_key=row.operation_key, parameters=operations.decode_input(row)
                )
                assert (
                    runtime.attempt(
                        retry.pk,
                        0,
                        execution=(retry.workflow_id, "revoked-download", "NvkeOperationWorkflow"),
                    )
                    == "failed"
                )
                retry.refresh_from_db()
                assert retry.error_code == "OWNERSHIP_UNVERIFIED"
                assert "kubeconfig" not in operations.decode_result(retry).get("response", {}).get("data", {})
        finally:
            paths = [
                f"/api/v1/namespaces/{state.site.organization_namespace}/secrets/{secret['metadata']['name']}",
                f"/apis/cluster.x-k8s.io/v1beta2/namespaces/{state.site.organization_namespace}/clusters/{name}",
            ]
            for path in paths:
                existing = raw.get(path)
                if existing.status_code == 404:
                    continue
                existing.raise_for_status()
                meta = existing.json()["metadata"]
                raw.delete(
                    path,
                    json={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "propagationPolicy": "Background",
                        "preconditions": {"uid": meta["uid"], "resourceVersion": meta["resourceVersion"]},
                    },
                ).raise_for_status()


def enrollment(state, source):
    return {
        "site_id": state.site.guid,
        "expected_site_version": state.site.version,
        "expected_actor_id": state.world.user.pk,
        "source_operation_id": source.guid,
        "expected_source_version": source.version,
    }


def observed(state, *, overrides=None):
    request = create_request()
    with controller(request, subresources={"parent_override": overrides or {}}) as (
        endpoint,
        identity,
        calls,
    ):
        with context(state.world):
            state.connection = put(
                state.site,
                expected_connection_version=state.connection.version,
                endpoint=endpoint,
                allow_http=True,
            )
            state.site.refresh_from_db()
        row = reserve(state, operation_key="getCluster", parameters={"path": {"clusterID": identity}})
        assert (
            runtime.attempt(
                row.pk, 0, execution=(row.workflow_id, "enrollment-read", "NvkeOperationWorkflow")
            )
            == "succeeded"
        )
        row.refresh_from_db()
    assert calls == [("GET", f"/clusters/{identity}")]
    return row, request["body"]["name"], identity


def test_enrollment_is_explicit_durable_private_and_revocable(configured):
    source, name, identity = observed(configured)
    assert operations.trusted_cluster_ids(source, name) == frozenset()
    with context(configured.world):
        binding = access.enroll(**enrollment(configured, source))
        assert access.enroll(**enrollment(configured, source)).pk == binding.pk
        assert access.list_bindings(configured.site.guid).count() == 1
    assert binding.cluster_id == UUID(identity)
    assert name not in str(binding.__dict__)
    assert operations.trusted_cluster_ids(source, name) == frozenset([identity])
    assert operations.trusted_cluster_ids(source, "foreign") == frozenset()
    source.soft_delete()
    assert operations.trusted_cluster_ids(source, name) == frozenset([identity])
    with context(configured.world):
        with pytest.raises(NvkeSiteError) as stale:
            access.revoke(
                site_id=configured.site.guid,
                expected_site_version=configured.site.version,
                expected_actor_id=configured.world.user.pk,
                binding_id=binding.guid,
                expected_version=binding.version + 1,
            )
        assert stale.value.code == "VERSION_MISMATCH"
        access.revoke(
            site_id=configured.site.guid,
            expected_site_version=configured.site.version,
            expected_actor_id=configured.world.user.pk,
            binding_id=binding.guid,
            expected_version=binding.version,
        )
    assert operations.trusted_cluster_ids(source, name) == frozenset()
    assert NvkeWorkloadAccessBinding.all_objects.get(pk=binding.pk).deleted_at is not None


def test_imported_binding_survives_credential_renewal_but_not_controller_replacement(configured):
    source, name, identity = observed(configured)
    with context(configured.world):
        binding = access.enroll(**enrollment(configured, source))
        put(
            configured.site,
            expected_connection_version=configured.connection.version,
            credentials={"token": "RENEWED_PRIVATE_TOKEN"},
        )
        configured.site.refresh_from_db()
    assert operations.trusted_cluster_ids(source, name) == frozenset([identity])
    configured.connection.soft_delete()
    with context(configured.world):
        put(configured.site, credentials={"token": "NEW_INTERFACE"})
    assert operations.trusted_cluster_ids(source, name) == frozenset()
    binding.refresh_from_db()
    assert binding.deleted_at is None
    binding.cluster_id = uuid4()
    with pytest.raises(ValueError, match="cannot change"):
        binding.save()


def test_retrying_an_old_enrollment_cannot_resurrect_a_revoked_grant(configured):
    source, name, _identity = observed(configured)
    with context(configured.world):
        args = enrollment(configured, source)
        binding = access.enroll(**args)
        access.revoke(
            site_id=configured.site.guid,
            expected_site_version=configured.site.version,
            expected_actor_id=configured.world.user.pk,
            binding_id=binding.guid,
            expected_version=binding.version,
        )
        with pytest.raises(NvkeError) as replay:
            access.enroll(**args)
        assert replay.value.code == "SOURCE_CHANGED"
    assert operations.trusted_cluster_ids(source, name) == frozenset()


def test_imported_enrollment_and_revocation_use_audited_graphql_envelopes(configured):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    source, name, identity = observed(configured)
    args = enrollment(configured, source)
    mutation = """
    mutation($input: EnrollNvkeWorkloadAccessInput!) {
      enrollNvkeWorkloadAccess(input:$input) {
        ok errors { code } data { id version siteId clusterId sourceOperationId }
      }
    }
    """
    result = execute(
        configured.world,
        mutation,
        {
            "input": {
                "siteId": str(args["site_id"]),
                "expectedSiteVersion": args["expected_site_version"],
                "expectedActorId": args["expected_actor_id"],
                "sourceOperationId": str(args["source_operation_id"]),
                "expectedSourceVersion": args["expected_source_version"],
            }
        },
    )
    assert result.errors is None
    payload = result.data["enrollNvkeWorkloadAccess"]
    assert payload["ok"], payload
    assert payload["data"]["clusterId"] == identity
    query = "query($site:GUID!) { astroliftNvkeWorkloadAccessBindings(siteId:$site,limit:1) { items { id clusterId } totalCount } }"
    page = execute(configured.world, query, {"site": str(configured.site.guid)})
    assert page.errors is None
    assert page.data["astroliftNvkeWorkloadAccessBindings"]["totalCount"] == 1
    foreign = _world.__wrapped__()
    hidden = execute(foreign, query, {"site": str(configured.site.guid)})
    assert hidden.errors
    assert hidden.data is None
    result = execute(
        configured.world,
        """
    mutation($input:RevokeNvkeWorkloadAccessInput!) {
      revokeNvkeWorkloadAccess(input:$input) { ok errors { code } data { deletedAt } }
    }
    """,
        {
            "input": {
                "siteId": str(configured.site.guid),
                "expectedSiteVersion": configured.site.version,
                "expectedActorId": configured.world.user.pk,
                "bindingId": payload["data"]["id"],
                "expectedVersion": payload["data"]["version"],
            }
        },
    )
    assert result.errors is None
    assert result.data["revokeNvkeWorkloadAccess"]["ok"]
    assert result.data["revokeNvkeWorkloadAccess"]["data"]["deletedAt"]
    logs = MutationAuditLog.objects.filter(
        operation__in=["nvke.workload_access_enroll", "nvke.workload_access_revoke"]
    )
    assert logs.count() == 2
    assert name not in str(list(logs.values("variables", "errors")))


@pytest.mark.parametrize(
    "change",
    [
        "actor",
        "source_version",
        "site_version",
        "connection",
        "foreign",
        "permission",
        "pending",
        "identity",
        "invalid_name",
    ],
)
def test_enrollment_refuses_stale_foreign_unreviewed_and_unproven_sources(configured, change):
    from django.utils import timezone

    from astrolift_identity.models import RoleBinding

    source, _name, _identity = observed(
        configured,
        overrides={"id": str(uuid4())}
        if change == "identity"
        else {"name": "invalid/name"}
        if change == "invalid_name"
        else None,
    )
    args = enrollment(configured, source)
    if change == "actor":
        args["expected_actor_id"] += 1
    elif change == "source_version":
        args["expected_source_version"] -= 1
    elif change == "site_version":
        args["expected_site_version"] -= 1
    elif change == "foreign":
        other = _configured.__wrapped__(_world.__wrapped__())
        args["site_id"] = other.site.guid
    elif change == "permission":
        RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())
    elif change == "pending":
        source.status = "queued"
        source.save(update_fields=["status", "updated_at", "version"])
        args["expected_source_version"] = source.version
    elif change == "connection":
        with context(configured.world):
            put(
                configured.site,
                expected_connection_version=configured.connection.version,
                credentials={"token": "ROTATED"},
            )
            configured.site.refresh_from_db()
            args["expected_site_version"] = configured.site.version
    with context(configured.world), pytest.raises((NvkeError, NvkeSiteError, PermissionDenied)):
        access.enroll(**args)
    assert not NvkeWorkloadAccessBinding.objects.exists()
