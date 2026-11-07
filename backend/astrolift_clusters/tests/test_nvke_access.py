from __future__ import annotations

import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from nvke.client import NvkeResponse
from nvke.contract import controller_spec
from nvke.operations import action
from tests.nvke.test_datastore_integration import seed_access

from astrolift_clusters import nvke_operations as operations
from astrolift_clusters import nvke_runtime as runtime
from astrolift_clusters.nvke_connections import open_connection
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_operations import configured as _configured
from astrolift_clusters.tests.test_nvke_operations import datastore_site as _datastore_site
from astrolift_clusters.tests.test_nvke_operations import reserve
from astrolift_clusters.tests.test_nvke_operations import world as _world
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
def controller(request, identity=None):
    identity = identity or str(uuid4())
    calls = []
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
            elif self.path.endswith("/control-plane"):
                self.reply({})
            else:
                self.reply(response)

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
            response.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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


def test_real_postgres_controller_receipt_and_kubernetes_download_without_a_native_crd(datastore_site):
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
                creation = reserve(state, operation_key="createCluster", parameters=request)
                assert (
                    runtime.attempt(
                        creation.pk,
                        0,
                        execution=(creation.workflow_id, "receipt-create", "NvkeOperationWorkflow"),
                    )
                    == "awaiting_ready"
                )
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
