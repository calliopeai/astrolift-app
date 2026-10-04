"""Actual Container/CRM GAPIC requests and private TLS Kubernetes GET transport."""

import base64
import copy
import json
import ssl
import subprocess
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import grpc
import pytest
from google.api_core.exceptions import PermissionDenied
from google.auth.credentials import AnonymousCredentials
from google.cloud import container_v1 as gke
from google.cloud import resourcemanager_v3 as manager
from google.cloud.container_v1.services.cluster_manager.transports.grpc import ClusterManagerGrpcTransport
from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport
from google.oauth2.credentials import Credentials

from _sdk.cloud_credentials import CloudCredential
from gcp import gke_identity_observation as module
from gcp.gke_identity_observation import (
    GKEIdentityObserver,
    GKEObservationContext,
    GKEObservationError,
    KSASubject,
    KubernetesReadAdapter,
)
from gcp.identity_owned import NativeIdentityContext

IDENTITY = NativeIdentityContext(
    "12345678-0000-4000-8000-000000000001",
    "12345678-0000-4000-8000-000000000002",
    "12345678-0000-4000-8000-000000000003",
    "fixture-project",
    "415104041262",
    "us-central1",
    "owned-fixture",
    "110012345678901234567",
    CloudCredential("gcp", declared_account="fixture-project"),
)
SUBJECT = KSASubject(
    "owned-namespace", "owned-app", "12345678-0000-4000-8000-000000000004", "12345678-0000-4000-8000-000000000005"
)
CONTEXT = GKEObservationContext(IDENTITY, "us-central1-a", "owned-cluster", "native-original-cluster-id", (SUBJECT,))


def obj(namespace=False):
    metadata = {
        "name": SUBJECT.namespace if namespace else SUBJECT.name,
        "uid": SUBJECT.namespace_uid if namespace else SUBJECT.service_account_uid,
        "resourceVersion": "17",
        "labels": CONTEXT.owner_labels,
    }
    if not namespace:
        metadata.update(namespace=SUBJECT.namespace, annotations={"iam.gke.io/gcp-service-account": IDENTITY.email})
    return {"apiVersion": "v1", "kind": "Namespace" if namespace else "ServiceAccount", "metadata": metadata}


class Wire(grpc.Channel):
    def __init__(self):
        self.project = manager.Project(
            name=f"projects/{IDENTITY.project_number}", project_id=IDENTITY.project_id, state=1
        )
        self.cluster = gke.Cluster(
            id=CONTEXT.native_cluster_id,
            name=CONTEXT.cluster_name,
            location=CONTEXT.location,
            status=2,
            endpoint="34.1.2.3",
            master_auth={"cluster_ca_certificate": "fixture-native-ca"},
            workload_identity_config={"workload_pool": IDENTITY.project_id + ".svc.id.goog"},
        )
        self.pools = gke.ListNodePoolsResponse(
            node_pools=[gke.NodePool(name="owned-pool", status=2, config={"workload_metadata_config": {"mode": 2}})]
        )
        self.calls = []
        self.error = None
        self.after = None
        self.closed = 0
        self.clients = (
            manager.ProjectsClient(
                transport=ProjectsGrpcTransport(
                    channel=self,
                    host="cloudresourcemanager.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
            gke.ClusterManagerClient(
                transport=ClusterManagerGrpcTransport(
                    channel=self,
                    host="container.googleapis.com",
                    credentials=AnonymousCredentials(),
                )
            ),
        )

    def subscribe(self, *a, **k):
        raise AssertionError("no live network")

    unsubscribe = subscribe
    unary_stream = subscribe
    stream_unary = subscribe
    stream_stream = subscribe

    def close(self):
        self.closed += 1

    def unary_unary(self, method, request_serializer=None, response_deserializer=None, *args, **kwargs):
        name = (method.decode() if isinstance(method, bytes) else method).rsplit("/", 1)[-1]
        types = {
            "GetProject": manager.GetProjectRequest,
            "GetCluster": gke.GetClusterRequest,
            "ListNodePools": gke.ListNodePoolsRequest,
        }

        def call(request, **kwargs):
            assert name in types, "GET-only observation, no create/set/update/list clusters"
            typed = types[name].deserialize(request_serializer(request))
            assert kwargs["timeout"] == module.TIMEOUT
            if name == "GetProject":
                assert typed.name == f"projects/{IDENTITY.project_number}"
            elif name == "GetCluster":
                assert typed.name == CONTEXT.cluster_resource
            else:
                assert typed.parent == CONTEXT.cluster_resource
            self.calls.append(name)
            if self.error:
                raise self.error("synthetic-secret-native-error")
            value = {"GetProject": self.project, "GetCluster": self.cluster, "ListNodePools": self.pools}[name]
            result = response_deserializer(type(value).serialize(value))
            if self.after:
                self.after(name)
            return result

        class CompletedCall:
            def trailing_metadata(self):
                return ()

        call.with_call = lambda request, **kwargs: (call(request, **kwargs), CompletedCall())
        return call


class Kube:
    def __init__(self):
        self.namespace = obj(True)
        self.account = obj()
        self.calls = []
        self.after = None
        self.error = None

    def read(self, namespace, name=None):
        assert namespace == SUBJECT.namespace
        assert name in (None, SUBJECT.name)
        self.calls.append((namespace, name))
        if self.error:
            raise GKEObservationError(self.error)
        value = copy.deepcopy(self.namespace if name is None else self.account)
        if self.after:
            self.after(namespace, name)
        return value


@pytest.fixture
def fixture():
    wire, kube = Wire(), Kube()
    observer = GKEIdentityObserver(CONTEXT, clients=wire.clients, kubernetes_factory=lambda *args: kube)
    return wire, kube, observer


def test_actual_sdk_standard_configuration_and_exact_uid(fixture):
    wire, kube, observer = fixture
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed and not result.workload_ready and not result.impersonation_verified
    assert result.native_cluster_id == CONTEXT.native_cluster_id
    assert result.node_pools == ("owned-pool",)
    assert result.subjects[0].annotation_matches
    assert wire.calls == ["GetProject", "GetCluster", "ListNodePools"] * 2
    assert len(kube.calls) == 4


def test_autopilot_native_proof_does_not_invent_standard_inventory(fixture):
    wire, _, observer = fixture
    wire.cluster.autopilot.enabled = True
    wire.pools.node_pools.clear()
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed and result.autopilot and result.node_pools == ()
    assert "ListNodePools" not in wire.calls


@pytest.mark.parametrize("change", ["foreign-number", "foreign-id", "deleted", "unknown"])
def test_project_refusals(fixture, change):
    wire, kube, observer = fixture
    if change == "foreign-number":
        wire.project.name = "projects/98765"
    elif change == "foreign-id":
        wire.project.project_id = "foreign-project"
    else:
        wire.project.state = 2 if change == "deleted" else 0
    result = observer.observe(checkpoint=lambda: None)
    assert result.reason == "PROJECT_IDENTITY_CHANGED" and not result.configuration_observed
    assert not kube.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "recreated"),
        ("name", "other"),
        ("location", "us-east1"),
        ("status", 0),
        ("status", 3),
        ("pool", "foreign.svc.id.goog"),
    ],
)
def test_cluster_refusals(fixture, field, value):
    wire, kube, observer = fixture
    if field == "pool":
        wire.cluster.workload_identity_config.workload_pool = value
    else:
        setattr(wire.cluster, field, value)
    result = observer.observe(checkpoint=lambda: None)
    assert result.reason == "CLUSTER_CONFIGURATION_UNVERIFIED" and not kube.calls


@pytest.mark.parametrize("change", ["empty", "duplicate", "too-many", "gce", "unknown", "not-running", "invalid-name"])
def test_complete_standard_inventory_fail_closed(fixture, change):
    wire, kube, observer = fixture
    pool = wire.pools.node_pools[0]
    if change == "empty":
        wire.pools.node_pools.clear()
    elif change in ("duplicate", "too-many"):
        wire.pools.node_pools.extend([pool] * (1 if change == "duplicate" else 64))
    elif change in ("gce", "unknown"):
        pool.config.workload_metadata_config.mode = 1 if change == "gce" else 0
    elif change == "not-running":
        pool.status = 3
    else:
        pool.name = "../foreign"
    assert observer.observe(checkpoint=lambda: None).reason == "NODE_CONFIGURATION_UNVERIFIED"
    assert not kube.calls


@pytest.mark.parametrize("namespace", [True, False])
@pytest.mark.parametrize("change", ["uid", "foreign-owner", "deleted", "version", "kind", "name"])
def test_original_kubernetes_identity_refusals(fixture, namespace, change):
    _, kube, observer = fixture
    value = kube.namespace if namespace else kube.account
    if change == "kind":
        value["kind"] = "Pod"
    elif change == "foreign-owner":
        value["metadata"]["labels"]["astrolift.io/app-id"] = "foreign"
    else:
        key = {"uid": "uid", "deleted": "deletionTimestamp", "version": "resourceVersion", "name": "name"}[change]
        value["metadata"][key] = "" if change == "version" else "changed"
    assert observer.observe(checkpoint=lambda: None).reason == "SUBJECT_OWNERSHIP_UNVERIFIED"


def test_missing_original_uid_refuses_before_adc_or_native(fixture):
    wire, _, observer = fixture
    observer.context = replace(CONTEXT, subjects=(replace(SUBJECT, service_account_uid=""),))
    assert observer.observe(checkpoint=lambda: None).reason == "SUBJECT_UID_UNRECORDED"
    assert wire.calls == []


def test_foreign_annotation_refuses_no_adoption(fixture):
    _, kube, observer = fixture
    kube.account["metadata"]["annotations"]["iam.gke.io/gcp-service-account"] = "foreign@example.invalid"
    assert observer.observe(checkpoint=lambda: None).reason == "SERVICE_ACCOUNT_LINK_FOREIGN"


def test_unannotated_is_configuration_only(fixture):
    _, kube, observer = fixture
    kube.account["metadata"]["annotations"] = {}
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed and not result.subjects[0].annotation_matches and not result.workload_ready


@pytest.mark.parametrize("late", ["cluster-id", "pools", "ksa-uid", "namespace-uid", "version"])
def test_held_late_observation_rechecks_original_identity(fixture, late):
    wire, kube, observer = fixture

    def after(namespace, name):
        if len(kube.calls) == 2:
            if late == "cluster-id":
                wire.cluster.id = "changed"
            elif late == "pools":
                wire.pools.node_pools[0].config.workload_metadata_config.mode = 1
            elif late == "ksa-uid":
                kube.account["metadata"]["uid"] = SUBJECT.namespace_uid
            elif late == "namespace-uid":
                kube.namespace["metadata"]["uid"] = SUBJECT.service_account_uid
            else:
                kube.account["metadata"]["resourceVersion"] = "18"

    kube.after = after
    result = observer.observe(checkpoint=lambda: None)
    assert not result.configuration_observed


def test_current_withdrawal_before_adc(monkeypatch):
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda **kwargs: pytest.fail("no ADC after withdrawal"))

    def deny():
        raise PermissionError("current authority withdrawn")

    with pytest.raises(PermissionError):
        GKEIdentityObserver(CONTEXT).observe(checkpoint=deny)


@pytest.mark.parametrize("native", [True, False])
def test_current_withdrawal_after_actual_response(fixture, native):
    wire, kube, observer = fixture
    current = [True]

    def deny():
        if not current[0]:
            raise PermissionError("withdrawn")

    if native:
        wire.after = lambda name: current.__setitem__(0, False)
    else:
        kube.after = lambda *args: current.__setitem__(0, False)
    with pytest.raises(PermissionError):
        observer.observe(checkpoint=deny)


def test_native_denial_and_oversized_exception_privacy(fixture, caplog):
    wire, _, observer = fixture
    wire.error = PermissionDenied
    assert observer.observe(checkpoint=lambda: None).reason == "NATIVE_READ_UNCONFIRMED"
    assert "synthetic-secret-native-error" not in caplog.text
    wire.error = None
    wire.project.display_name = "x" * (module.MAX_BYTES + 1)
    assert observer.observe(checkpoint=lambda: None).reason == "OVERSIZED_RESPONSE"


@pytest.fixture
def tls_server(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1,IP:10.23.1.7",
        ],
        check=True,
        capture_output=True,
    )
    state = {
        "requests": [],
        "status": 200,
        "namespace": obj(True),
        "account": obj(),
        "raw": None,
        "encoding": None,
        "redirect": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append((self.command, self.path, self.headers.get("Authorization")))
            raw = state["raw"]
            if raw is None:
                raw = json.dumps(state["account"] if "/serviceaccounts/" in self.path else state["namespace"]).encode()
            self.send_response(state["status"])
            if state["redirect"]:
                self.send_header("Location", state["redirect"])
            if state["encoding"]:
                self.send_header("Content-Encoding", state["encoding"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state, base64.b64encode(cert.read_bytes()).decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def adapter_for(server, certificate, monkeypatch, checkpoint=lambda: None):
    monkeypatch.setattr(module, "_endpoint", lambda value: f"https://127.0.0.1:{server.server_port}")
    return KubernetesReadAdapter("34.1.2.3", certificate, Credentials(token="synthetic-private-credential"), checkpoint)


def test_actual_tls_production_adapter_reads_no_body_or_credential_logs(tls_server, monkeypatch, caplog, fixture):
    caplog.set_level("DEBUG")
    server, state, cert = tls_server
    state["account"]["metadata"]["annotations"]["fixture-private-marker"] = "synthetic-private-body-marker"
    wire, _, _ = fixture
    wire.cluster.master_auth.cluster_ca_certificate = cert
    observer = GKEIdentityObserver(
        CONTEXT, clients=wire.clients, kubernetes_factory=lambda *args: adapter_for(server, cert, monkeypatch, args[3])
    )
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed
    assert [r[1] for r in state["requests"]] == [
        f"/api/v1/namespaces/{SUBJECT.namespace}",
        f"/api/v1/namespaces/{SUBJECT.namespace}/serviceaccounts/{SUBJECT.name}",
    ] * 2
    assert all(row[0] == "GET" and row[2] == "Bearer synthetic-private-credential" for row in state["requests"])
    assert "synthetic-private-credential" not in caplog.text
    assert SUBJECT.service_account_uid not in caplog.text
    assert "synthetic-private-body-marker" not in caplog.text


@pytest.mark.parametrize(
    "status,reason", [(404, "SUBJECT_UNOBSERVED"), (403, "KUBERNETES_READ_REFUSED"), (500, "KUBERNETES_READ_REFUSED")]
)
def test_actual_tls_denial(tls_server, monkeypatch, status, reason):
    server, state, cert = tls_server
    state["status"] = status
    with pytest.raises(GKEObservationError, match=reason):
        adapter_for(server, cert, monkeypatch).read(SUBJECT.namespace, SUBJECT.name)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("change", ["redirect", "oversized", "gzip", "invalid-json", "array"])
def test_actual_tls_unsafe_protocol_refusal(tls_server, monkeypatch, change):
    server, state, cert = tls_server
    if change == "redirect":
        state.update(status=302, redirect="https://foreign.example.invalid/token")
    elif change == "oversized":
        state["raw"] = b"x" * (module.MAX_BYTES + 1)
    elif change == "gzip":
        state["encoding"] = "gzip"
    else:
        state["raw"] = b"not json" if change == "invalid-json" else b"[]"
    with pytest.raises(GKEObservationError):
        adapter_for(server, cert, monkeypatch).read(SUBJECT.namespace)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize(
    "endpoint",
    ["127.0.0.1", "169.254.169.254", "http://34.1.2.3", "34.1.2.3:443", "34.1.2.3/evil", "foreign.example", "::1"],
)
def test_production_endpoint_not_arbitrary(endpoint):
    with pytest.raises(GKEObservationError, match="ENDPOINT_UNVERIFIED"):
        module._endpoint(endpoint)


def test_fixed_native_endpoint_and_current_before_construction(monkeypatch, caplog):
    import google.auth
    import google.auth.transport.grpc

    caplog.set_level("DEBUG")
    wire = Wire()
    made = []

    def default(**kwargs):
        assert isinstance(kwargs["request"], module._CredentialRequest)
        return Credentials(token="synthetic-private-credential"), "irrelevant-ambient-project"

    def channel(credentials, request, target, **kwargs):
        assert kwargs["options"] == [("grpc.max_receive_message_length", module.MAX_BYTES)]
        made.append(target)
        return wire

    monkeypatch.setattr(google.auth, "default", default)
    monkeypatch.setattr(google.auth.transport.grpc, "secure_authorized_channel", channel)
    monkeypatch.setenv("GOOGLE_API_USE_MTLS_ENDPOINT", "always")
    monkeypatch.setenv("HTTPS_PROXY", "http://foreign.example.invalid:8181")
    observer = GKEIdentityObserver(CONTEXT, kubernetes_factory=lambda *args: Kube())
    with observer:
        assert observer.observe(checkpoint=lambda: None).configuration_observed
    assert made == ["cloudresourcemanager.googleapis.com:443", "container.googleapis.com:443"]
    assert wire.closed == 2
    assert "synthetic-private-credential" not in caplog.text
    assert CONTEXT.native_cluster_id not in caplog.text
    assert IDENTITY.project_number not in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "https://foreign.example/token",
        "http://oauth2.googleapis.com/token",
        "https://oauth2.googleapis.com/evil",
        "https://oauth2.googleapis.com:443/token",
        "http://169.254.169.254/evil",
    ],
)
def test_credential_route_refusal_before_transport(monkeypatch, url):
    monkeypatch.setattr(module, "_http", lambda *a, **k: pytest.fail("no secret transport"))
    with pytest.raises(GKEObservationError, match="CREDENTIAL_ROUTE_UNSUPPORTED"):
        module._CredentialRequest(lambda: None)(url)


def test_actual_compute_engine_refresh_uses_private_request(monkeypatch):
    from google.auth.compute_engine.credentials import Credentials as ComputeCredentials

    urls = []

    def transport(url, **kwargs):
        urls.append(url)
        assert kwargs["method"] == "GET"
        assert url.startswith("http://metadata.google.internal/computeMetadata/v1/")
        if "/token" in url:
            data = {"access_token": "synthetic-private-credential", "expires_in": 3600, "token_type": "Bearer"}
        elif "universe-domain" in url:
            return module._Response(200, b"googleapis.com", {"content-type": "text/plain"})
        else:
            data = {
                "email": "fixture@fixture-project.iam.gserviceaccount.com",
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                "aliases": ["default"],
            }
        return module._Response(200, json.dumps(data).encode(), {"content-type": "application/json"})

    monkeypatch.setattr(module, "_http", transport)
    credentials = ComputeCredentials()
    calls = []
    credentials.refresh(module._CredentialRequest(lambda: calls.append(True)))
    assert credentials.valid and credentials.token == "synthetic-private-credential"
    assert len(calls) == 2 * len(urls)


def test_executable_adc_refuses_without_discovery(monkeypatch):
    import google.auth

    monkeypatch.setenv("GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES", "1")
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: pytest.fail("no executable ADC"))
    assert GKEIdentityObserver(CONTEXT).observe(checkpoint=lambda: None).reason == "CREDENTIAL_TYPE_UNSUPPORTED"


def test_unknown_adc_type_does_not_construct_native_clients(monkeypatch):
    import google.auth
    import google.auth.transport.grpc

    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (AnonymousCredentials(), None))
    monkeypatch.setattr(
        google.auth.transport.grpc, "secure_authorized_channel", lambda *a, **k: pytest.fail("unsupported ADC")
    )
    assert GKEIdentityObserver(CONTEXT).observe(checkpoint=lambda: None).reason == "CREDENTIAL_TYPE_UNSUPPORTED"


def test_failed_client_construction_sanitizes_and_closes_prior(monkeypatch, caplog):
    import google.auth
    import google.auth.transport.grpc

    wire = Wire()
    made = []
    monkeypatch.setattr(
        google.auth, "default", lambda **kwargs: (Credentials(token="synthetic-private-credential"), None)
    )

    def channel(*args, **kwargs):
        made.append(True)
        if len(made) == 2:
            raise RuntimeError("synthetic-private-credential")
        return wire

    monkeypatch.setattr(google.auth.transport.grpc, "secure_authorized_channel", channel)
    assert GKEIdentityObserver(CONTEXT).observe(checkpoint=lambda: None).reason == "NATIVE_CLIENT_UNAVAILABLE"
    assert wire.closed == 1 and "synthetic-private-credential" not in caplog.text


def test_current_withdrawal_after_actual_tls_response(tls_server, monkeypatch):
    server, state, cert = tls_server
    current = [True]

    def checkpoint():
        if state["requests"]:
            current[0] = False
        if not current[0]:
            raise PermissionError("current authority withdrawn")

    with pytest.raises(PermissionError):
        adapter_for(server, cert, monkeypatch, checkpoint).read(SUBJECT.namespace)
    assert len(state["requests"]) == 1


def test_tls_ca_mismatch_never_sends_credential(tls_server, monkeypatch):
    server, state, cert = tls_server
    adapter = adapter_for(server, cert, monkeypatch)
    adapter.tls = ssl.create_default_context()
    with pytest.raises(GKEObservationError, match="KUBERNETES_READ_UNCONFIRMED"):
        adapter.read(SUBJECT.namespace)
    assert state["requests"] == []


def test_credential_response_does_not_admit_after_withdrawal(monkeypatch):
    current = [True]

    def transport(*args, **kwargs):
        current[0] = False
        return module._Response(200, b"private-response", {})

    def checkpoint():
        if not current[0]:
            raise PermissionError("withdrawn")

    monkeypatch.setattr(module, "_http", transport)
    with pytest.raises(PermissionError):
        module._CredentialRequest(checkpoint)("https://oauth2.googleapis.com/token", method="POST")


def test_closed_observer_refuses_before_native(fixture):
    wire, _, observer = fixture
    observer.close()
    assert observer.observe(checkpoint=lambda: None).reason == "CLOSED"
    assert wire.calls == [] and wire.closed == 0


@pytest.mark.parametrize("change", ["endpoint", "ca", "autopilot", "pool-name"])
def test_last_native_configuration_change_refuses(fixture, change):
    wire, kube, observer = fixture

    def after(namespace, name):
        if len(kube.calls) == 2:
            if change == "endpoint":
                wire.cluster.endpoint = "35.1.2.3"
            elif change == "ca":
                wire.cluster.master_auth.cluster_ca_certificate = "changed"
            elif change == "autopilot":
                wire.cluster.autopilot.enabled = True
            else:
                wire.pools.node_pools[0].name = "other-pool"

    kube.after = after
    assert observer.observe(checkpoint=lambda: None).reason == "CLUSTER_OBSERVATION_CHANGED"


def test_read_deadline_enforced_between_bounded_chunks(monkeypatch):
    class Response:
        code = 200
        closed = False

        def __init__(self):
            self.headers = {}

        def read1(self, count):
            assert count <= 65536
            return b"chunk"

        def close(self):
            self.closed = True

    response = Response()

    class Opener:
        def open(self, request, timeout):
            assert timeout == module.TIMEOUT
            return response

    times = iter([0.0, 0.0, module.TIMEOUT + 1])
    monkeypatch.setattr(module, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    with pytest.raises(GKEObservationError, match="READ_DEADLINE_EXCEEDED"):
        module._http(
            "https://owned.example.invalid", method="GET", headers={}, data=None, context=ssl.create_default_context()
        )
    assert response.closed


@pytest.mark.parametrize("value", [False, True, 0, "admitted", {}])
def test_invalid_checkpoint_before_discovery_is_not_admission(monkeypatch, value):
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda **kwargs: pytest.fail("no ADC on non-None checkpoint"))
    with pytest.raises(GKEObservationError, match="CURRENT_ADMISSION_UNCONFIRMED"):
        GKEIdentityObserver(CONTEXT).observe(checkpoint=lambda: value)


@pytest.mark.parametrize("native", [True, False])
def test_invalid_checkpoint_after_held_response_refuses(fixture, native):
    wire, kube, observer = fixture
    current = [None]
    if native:
        wire.after = lambda name: current.__setitem__(0, False)
    else:
        kube.after = lambda *args: current.__setitem__(0, False)
    with pytest.raises(GKEObservationError, match="CURRENT_ADMISSION_UNCONFIRMED"):
        observer.observe(checkpoint=lambda: current[0])
    if native:
        assert wire.calls == ["GetProject"] and kube.calls == []
    else:
        assert len(kube.calls) == 1


def test_private_native_endpoint_actual_tls_configuration(tls_server, fixture, monkeypatch, caplog):
    import socket

    server, state, cert = tls_server
    wire, _, _ = fixture
    wire.cluster.endpoint = "10.23.1.7"
    wire.cluster.master_auth.cluster_ca_certificate = cert
    original = socket.create_connection
    connections = []

    def route(address, *args, **kwargs):
        connections.append(address)
        assert address == (wire.cluster.endpoint, 443)
        return original(("127.0.0.1", server.server_port), *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", route)
    caplog.set_level("DEBUG")
    observer = GKEIdentityObserver(CONTEXT, clients=wire.clients)
    observer._credentials = Credentials(token="synthetic-private-credential")
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed and len(connections) == 4
    assert all(row[0] == "GET" for row in state["requests"])
    assert "synthetic-private-credential" not in caplog.text


def test_private_native_wrong_ca_sends_no_credential(tls_server, fixture, monkeypatch, tmp_path):
    import socket

    server, state, cert = tls_server
    wire, _, _ = fixture
    wire.cluster.endpoint = "10.23.1.7"
    wire.cluster.master_auth.cluster_ca_certificate = cert
    original = socket.create_connection

    def route(address, *args, **kwargs):
        assert address == (wire.cluster.endpoint, 443)
        return original(("127.0.0.1", server.server_port), *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", route)

    other_cert = tmp_path / "other-ca.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(tmp_path / "other-key.pem"),
            "-out",
            str(other_cert),
            "-subj",
            "/CN=10.23.1.7",
            "-addext",
            "subjectAltName=IP:10.23.1.7",
        ],
        check=True,
        capture_output=True,
    )
    wire.cluster.master_auth.cluster_ca_certificate = base64.b64encode(other_cert.read_bytes()).decode()
    observer = GKEIdentityObserver(CONTEXT, clients=wire.clients)
    observer._credentials = Credentials(token="synthetic-private-credential")
    assert observer.observe(checkpoint=lambda: None).reason == "KUBERNETES_READ_UNCONFIRMED"
    assert state["requests"] == []


@pytest.mark.parametrize("value", ["10.23.1.7", "172.31.0.7", "192.168.1.7", "34.1.2.3"])
def test_only_verified_native_global_or_rfc1918_endpoint_format(value):
    assert module._endpoint(value) == "https://" + value


@pytest.mark.parametrize("value", ["224.0.0.1", "255.255.255.255", "100.64.0.1", "0.0.0.0", "192.0.2.1", "240.0.0.1"])
def test_non_cluster_endpoint_ranges_refuse(value):
    with pytest.raises(GKEObservationError, match="ENDPOINT_UNVERIFIED"):
        module._endpoint(value)


@pytest.mark.parametrize("value", ["v1:revision-17/fixture", "9" * 128])
def test_resource_versions_are_bounded_opaque_equalities(fixture, value):
    _, kube, observer = fixture
    kube.namespace["metadata"]["resourceVersion"] = value
    kube.account["metadata"]["resourceVersion"] = value
    result = observer.observe(checkpoint=lambda: None)
    assert result.configuration_observed
    assert result.subjects[0].namespace_resource_version == value
    assert result.subjects[0].service_account_resource_version == value


@pytest.mark.parametrize("value", ["", "x" * 257, "newline\n", "null\x00"])
def test_resource_version_bound_and_control_refusal(fixture, value):
    _, kube, observer = fixture
    kube.account["metadata"]["resourceVersion"] = value
    assert observer.observe(checkpoint=lambda: None).reason == "SUBJECT_OWNERSHIP_UNVERIFIED"


def test_actual_tls_opaque_version_drift_is_refused(tls_server, fixture, monkeypatch):
    server, state, cert = tls_server
    wire, _, _ = fixture
    wire.cluster.master_auth.cluster_ca_certificate = cert
    state["account"]["metadata"]["resourceVersion"] = "opaque:revision-1"
    adapter = adapter_for(server, cert, monkeypatch)
    original = adapter.read

    def read(*args):
        value = original(*args)
        if len(state["requests"]) == 2:
            state["account"]["metadata"]["resourceVersion"] = "opaque:revision-2"
        return value

    adapter.read = read
    observer = GKEIdentityObserver(CONTEXT, clients=wire.clients, kubernetes_factory=lambda *args: adapter)
    assert observer.observe(checkpoint=lambda: None).reason == "SUBJECT_OBSERVATION_CHANGED"
