"""Actual CRM/Container/IAM/Vertex GAPIC codecs, including owned TLS gRPC create."""

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import grpc
import pytest
from google.api_core.exceptions import AlreadyExists, NotFound, ServiceUnavailable
from google.auth.credentials import AnonymousCredentials
from google.cloud import aiplatform_v1beta1 as vertex
from google.cloud import container_v1 as gke
from google.cloud import iam_admin_v1 as iam
from google.cloud import resourcemanager_v3 as manager
from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import EndpointServiceGrpcTransport
from google.cloud.container_v1.services.cluster_manager.transports.grpc import ClusterManagerGrpcTransport
from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

from _sdk.cloud_credentials import CloudCredential
from gcp.identity_source import (
    CreateCommitReceipt,
    CreateRequest,
    IdentitySourceError,
    NativeIdentitySource,
    SourceDeclaration,
)

ORG = "12345678-0000-4000-8000-000000000001"
APP = "12345678-0000-4000-8000-000000000002"
CLUSTER = "12345678-0000-4000-8000-000000000003"
SOURCE = "12345678-0000-4000-8000-000000000004"
OP = "12345678-0000-4000-8000-000000000005"
NUMBER = "415104041262"
PROJECT = "fixture-project"
UID = "110012345678901234567"
DECLARATION = SourceDeclaration(
    ORG,
    APP,
    CLUSTER,
    PROJECT,
    "us-central1",
    "us-central1-a",
    "owned-cluster",
    "34.1.2.3",
    hashlib.sha256(b"certificate").hexdigest(),
    CloudCredential("gcp", declared_account=PROJECT),
)


class SourceWire(grpc.Channel):
    def __init__(self, declaration=DECLARATION):
        self.declaration = declaration
        self.calls, self.writes, self.after, self.error = [], 0, None, None
        self.account = None
        self.lost = False
        self.project = manager.Project(name=f"projects/{NUMBER}", project_id=PROJECT, state=1)
        self.cluster = gke.Cluster(
            id="native-original",
            name=declaration.cluster_name,
            location=declaration.location,
            status=2,
            endpoint=declaration.endpoint,
            master_auth={"cluster_ca_certificate": "certificate"},
            workload_identity_config={"workload_pool": PROJECT + ".svc.id.goog"},
        )
        self.role = iam.Role(
            name=f"projects/{PROJECT}/roles/EndpointPredict",
            stage=2,
            included_permissions=["aiplatform.endpoints.predict"],
        )
        self.endpoint = vertex.Endpoint()
        c = AnonymousCredentials()
        self.clients = (
            manager.ProjectsClient(
                transport=ProjectsGrpcTransport(channel=self, host="cloudresourcemanager.googleapis.com", credentials=c)
            ),
            gke.ClusterManagerClient(
                transport=ClusterManagerGrpcTransport(channel=self, host="container.googleapis.com", credentials=c)
            ),
            iam.IAMClient(transport=IAMGrpcTransport(channel=self, host="iam.googleapis.com", credentials=c)),
            vertex.EndpointServiceClient(
                transport=EndpointServiceGrpcTransport(
                    channel=self, host="us-central1-aiplatform.googleapis.com", credentials=c
                )
            ),
        )

    def subscribe(self, *args, **kwargs):
        raise AssertionError("no external network")

    unsubscribe = unary_stream = stream_unary = stream_stream = subscribe

    def close(self):
        pass

    def unary_unary(self, method, request_serializer=None, response_deserializer=None, *args, **kwargs):
        name = (method.decode() if isinstance(method, bytes) else method).rsplit("/", 1)[-1]
        types = {
            "GetProject": manager.GetProjectRequest,
            "GetCluster": gke.GetClusterRequest,
            "GetServiceAccount": iam.GetServiceAccountRequest,
            "CreateServiceAccount": iam.CreateServiceAccountRequest,
            "GetRole": iam.GetRoleRequest,
            "GetEndpoint": vertex.GetEndpointRequest,
        }

        def call(request, **options):
            assert name in types, "No policy setter, broad inventory, paid deployment or invocation"
            assert 0 < options["timeout"] <= 10
            typed = types[name].deserialize(request_serializer(request))
            self.calls.append((name, typed))
            if self.error:
                raise self.error
            if name == "GetProject":
                assert typed.name in {
                    f"projects/{PROJECT}",
                    f"projects/{NUMBER}",
                    f"projects/{self.declaration.project_id}",
                }
                result = self.project
            elif name == "GetCluster":
                assert typed.name == self.declaration.cluster_resource
                result = self.cluster
            elif name == "GetRole":
                assert typed.name == self.role.name
                result = self.role
            elif name == "GetEndpoint":
                assert typed.name == self.endpoint.name
                result = self.endpoint
            elif name == "GetServiceAccount":
                if self.account is None:
                    if self.after:
                        self.after(name)
                    raise NotFound("PRIVATE_NATIVE_CANARY")
                result = self.account
            else:
                assert typed.name == f"projects/{PROJECT}"
                assert self.account is None
                self.writes += 1
                email = typed.account_id + f"@{PROJECT}.iam.gserviceaccount.com"
                self.account = iam.ServiceAccount(
                    name=f"projects/{PROJECT}/serviceAccounts/{email}",
                    project_id=PROJECT,
                    unique_id=UID,
                    email=email,
                    description=typed.service_account.description,
                    display_name=typed.service_account.display_name,
                )
                if self.lost:
                    raise ServiceUnavailable("PRIVATE_NATIVE_CANARY")
                result = self.account
            if self.after:
                self.after(name)
            return response_deserializer(type(result).serialize(result))

        class Done:
            def trailing_metadata(self):
                return ()

        call.with_call = lambda request, **options: (call(request, **options), Done())
        return call

    def port(self, declaration=None):
        return NativeIdentitySource(declaration or self.declaration, clients=self.clients)


def request(port):
    scope, _ = port.observe_cluster(current=lambda: None)
    return CreateRequest(
        SOURCE,
        OP,
        "astro-" + hashlib.sha256(SOURCE.encode()).hexdigest()[:24],
        PROJECT,
        NUMBER,
        scope.owner_description,
    )


def receipt(r, state, uid=None):
    return CreateCommitReceipt(r.source_id, 2 if state == "SENT" else 3, r.operation_id, r.sha256, state, uid)


def create(port, r, *, current=lambda: None, sent=None, evidence=None):
    return port.create(
        r,
        current=current,
        cluster_pin=port.observe_cluster(current=lambda: None)[1],
        commit_sent=sent or (lambda: receipt(r, "SENT")),
        retain_evidence=evidence or (lambda e: receipt(r, "EVIDENCE", e.unique_id)),
    )


def test_native_zonal_cluster_and_regional_vertex_are_distinct_and_create_retains_numeric_uid():
    wire = SourceWire()
    port = wire.port()
    scope, pin = port.observe_cluster(current=lambda: None)
    assert scope.region == "us-central1" and pin.location == "us-central1-a"
    r = request(port)
    evidence = create(port, r)
    port.read_account(r, evidence.unique_id, current=lambda: None)
    assert wire.writes == 1 and evidence.unique_id == UID
    assert wire.calls[-1][1].name == f"projects/{PROJECT}/serviceAccounts/{UID}"
    assert wire.account.description == scope.owner_description
    assert "12345678" in wire.account.display_name


@pytest.mark.parametrize("field,value", [("project_id", "foreign-project"), ("state", 2), ("name", "projects/unknown")])
def test_project_identity_refuses_before_any_create(field, value):
    wire = SourceWire()
    setattr(wire.project, field, value)
    with pytest.raises(IdentitySourceError, match="PROJECT_IDENTITY"):
        request(wire.port())
    assert wire.writes == 0


@pytest.mark.parametrize(
    "field,value",
    [("id", ""), ("name", "foreign"), ("location", "us-central1"), ("endpoint", "34.1.2.4"), ("status", 1)],
)
def test_cluster_original_and_registered_fields_must_match(field, value):
    wire = SourceWire()
    setattr(wire.cluster, field, value)
    with pytest.raises(IdentitySourceError, match="REGISTERED_CLUSTER"):
        request(wire.port())
    assert wire.writes == 0


@pytest.mark.parametrize("change", ["pool", "certificate"])
def test_workload_pool_and_exact_registered_certificate_are_required(change):
    wire = SourceWire()
    if change == "pool":
        wire.cluster.workload_identity_config.workload_pool = "foreign-project.svc.id.goog"
    else:
        wire.cluster.master_auth.cluster_ca_certificate = "foreign"
    with pytest.raises(IdentitySourceError, match="REGISTERED_CLUSTER"):
        request(wire.port())


@pytest.mark.parametrize("phase", ["GetProject", "GetCluster", "GetServiceAccount", "CreateServiceAccount"])
def test_authority_withdrawal_on_response_prevents_context_but_create_evidence_is_retained(phase):
    wire = SourceWire()
    port, r = wire.port(), request(wire.port())
    active, retained = [True], []

    def current():
        if not active[0]:
            raise IdentitySourceError("WITHDRAWN")

    wire.after = lambda name: active.__setitem__(0, False) if name == phase else None
    if phase in ("GetProject", "GetCluster"):
        with pytest.raises(IdentitySourceError, match="WITHDRAWN"):
            port.observe_cluster(current=current)
    elif phase == "GetServiceAccount":
        wire.account = iam.ServiceAccount()
        with pytest.raises(IdentitySourceError, match="WITHDRAWN"):
            create(port, r, current=current)
    else:

        def commit(e):
            retained.append(e)
            return receipt(r, "EVIDENCE", e.unique_id)

        with pytest.raises(IdentitySourceError, match="WITHDRAWN"):
            create(port, r, current=current, evidence=commit)
        assert len(retained) == 1 and retained[0].unique_id == UID
    assert wire.writes == (1 if phase == "CreateServiceAccount" else 0)


@pytest.mark.parametrize("ack", [None, True, False, "SENT"])
def test_untyped_durable_receipt_never_authorizes_create(ack):
    wire = SourceWire()
    r = request(wire.port())
    with pytest.raises(IdentitySourceError, match="DURABLE_CREATE"):
        create(wire.port(), r, sent=lambda: ack)
    assert wire.writes == 0


@pytest.mark.parametrize("change", ["source_id", "operation_id", "request_sha256", "state", "unique_id"])
def test_receipt_identity_substitution_refuses_before_send(change):
    wire = SourceWire()
    r = request(wire.port())
    changed = {change: ORG if change.endswith("id") else "0" * 64}
    with pytest.raises(IdentitySourceError, match="DURABLE_CREATE"):
        create(wire.port(), r, sent=lambda: replace(receipt(r, "SENT"), **changed))
    assert wire.writes == 0


def test_lost_create_reply_is_unknown_never_looked_up_by_marker_or_resent():
    wire = SourceWire()
    r = request(wire.port())
    wire.lost = True
    with pytest.raises(IdentitySourceError, match="ACCOUNT_CREATE_UNKNOWN"):
        create(wire.port(), r)
    assert wire.writes == 1
    with pytest.raises(IdentitySourceError, match="NOT_ADOPTABLE"):
        create(wire.port(), r)
    assert wire.writes == 1


def test_native_already_exists_denied_oversized_and_malformed_are_private(caplog):
    for error in (AlreadyExists("PRIVATE_NATIVE_CANARY"), ServiceUnavailable("PRIVATE_NATIVE_CANARY")):
        wire = SourceWire()
        wire.error = error
        with pytest.raises(IdentitySourceError) as caught:
            request(wire.port())
        assert "PRIVATE_NATIVE_CANARY" not in str(caught.value)
    wire = SourceWire()
    wire.project.display_name = "X" * (2 * 1024 * 1024 + 1)
    with pytest.raises(IdentitySourceError, match="OVERSIZED"):
        request(wire.port())
    assert "PRIVATE_NATIVE_CANARY" not in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("unique_id", "110000000000000000000"),
        ("project_id", "foreign-project"),
        ("email", "foreign@example.invalid"),
        ("description", "generic"),
        ("display_name", "generic"),
        ("disabled", True),
    ],
)
def test_original_numeric_uid_readback_rejects_recreated_or_foreign_account(field, value):
    wire = SourceWire()
    r = request(wire.port())
    create(wire.port(), r)
    setattr(wire.account, field, value)
    with pytest.raises(IdentitySourceError, match="OWNERSHIP_UNCONFIRMED"):
        wire.port().read_account(r, UID, current=lambda: None)
    assert wire.writes == 1


def test_no_admission_means_no_adc_or_client_discovery(monkeypatch):
    def denied():
        raise IdentitySourceError("WITHDRAWN")

    monkeypatch.setattr("google.auth.default", lambda **kw: pytest.fail("No ADC before admission"))
    with pytest.raises(IdentitySourceError, match="WITHDRAWN"):
        NativeIdentitySource(DECLARATION).observe_cluster(current=denied)


@pytest.fixture
def tls_material(tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
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
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    return key.read_bytes(), cert.read_bytes()


def test_actual_owned_tls_grpc_native_create_serializer_and_unique_id(tls_material):
    wire = SourceWire()
    key, cert = tls_material
    called = []
    r = request(wire.port())

    def send(typed, context):
        called.append(typed)
        assert typed.account_id == r.account_id and typed.service_account.description == r.owner_description
        return iam.ServiceAccount(
            name=f"projects/{PROJECT}/serviceAccounts/{r.email}",
            project_id=PROJECT,
            email=r.email,
            unique_id=UID,
            description=r.owner_description,
            display_name=r.payload["service_account"]["display_name"],
        )

    server = grpc.server(ThreadPoolExecutor(max_workers=1))
    server.add_generic_rpc_handlers(
        [
            grpc.method_handlers_generic_handler(
                "google.iam.admin.v1.IAM",
                {
                    "GetServiceAccount": grpc.unary_unary_rpc_method_handler(
                        lambda request, context: context.abort(grpc.StatusCode.NOT_FOUND, "PRIVATE_ABSENT"),
                        request_deserializer=iam.GetServiceAccountRequest.deserialize,
                        response_serializer=iam.ServiceAccount.serialize,
                    ),
                    "CreateServiceAccount": grpc.unary_unary_rpc_method_handler(
                        send,
                        request_deserializer=iam.CreateServiceAccountRequest.deserialize,
                        response_serializer=iam.ServiceAccount.serialize,
                    ),
                },
            )
        ]
    )
    port = server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials([(key, cert)]))
    server.start()
    channel = grpc.secure_channel(f"localhost:{port}", grpc.ssl_channel_credentials(cert))
    client = iam.IAMClient(
        transport=IAMGrpcTransport(channel=channel, host="iam.googleapis.com", credentials=AnonymousCredentials())
    )
    native = NativeIdentitySource(DECLARATION, clients=(wire.clients[0], wire.clients[1], client, wire.clients[3]))
    try:
        evidence = create(native, r)
        assert evidence.unique_id == UID and len(called) == 1
    finally:
        channel.close()
        server.stop(0).wait()


@pytest.mark.parametrize("numeric_reference", [False, True])
def test_numeric_credential_declaration_is_admitted_only_by_exact_active_mapping(numeric_reference):
    declaration = replace(
        DECLARATION,
        project_id=NUMBER if numeric_reference else PROJECT,
        credential=CloudCredential("gcp", declared_account=NUMBER),
    )
    wire = SourceWire(declaration)
    scope, pin = wire.port().observe_cluster(current=lambda: None)
    assert scope.project_id == PROJECT and scope.project_number == NUMBER and pin.project_id == PROJECT
    assert scope.credential.declared_account == NUMBER
    r = CreateRequest(
        SOURCE,
        OP,
        "astro-" + hashlib.sha256(SOURCE.encode()).hexdigest()[:24],
        PROJECT,
        NUMBER,
        scope.owner_description,
    )
    assert create(wire.port(), r).unique_id == UID and wire.writes == 1


def test_mismatched_numeric_declared_account_refuses_before_cluster_or_create():
    declaration = replace(DECLARATION, credential=CloudCredential("gcp", declared_account="999999999999"))
    wire = SourceWire(declaration)
    with pytest.raises(IdentitySourceError, match="PROJECT_IDENTITY"):
        wire.port().observe_cluster(current=lambda: None)
    assert [name for name, _ in wire.calls] == ["GetProject"] and wire.writes == 0


def test_production_factory_fixed_native_hosts_current_checks_and_debug_payload_privacy(monkeypatch, caplog):
    import json
    import logging

    from google.oauth2.credentials import Credentials

    wire = SourceWire()
    wire.project.display_name = "PRIVATE_BODY_CANARY"
    credential = Credentials(token="PRIVATE_TOKEN_CANARY")
    hosts, checks = [], []

    def discover(**kwargs):
        assert kwargs["request"] is not None and kwargs["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]
        return credential, "untrusted-adc-project"

    def channel(credentials, request, host, **kwargs):
        assert credentials is credential and request is not None
        hosts.append(host)
        return wire

    monkeypatch.setattr("google.auth.default", discover)
    monkeypatch.setattr("google.auth.transport.grpc.secure_authorized_channel", channel)
    port = NativeIdentitySource(DECLARATION)
    with caplog.at_level(logging.DEBUG):
        scope, _ = port.observe_cluster(current=lambda: checks.append("current"))
        logging.getLogger("unrelated.original-source-test").info("UNRELATED_VISIBLE")
    assert scope.project_id == PROJECT and scope.project_number == NUMBER
    assert hosts == [
        "cloudresourcemanager.googleapis.com:443",
        "container.googleapis.com:443",
        "iam.googleapis.com:443",
        "us-central1-aiplatform.googleapis.com:443",
    ]
    assert len(checks) >= 16
    material = caplog.text + json.dumps([record.__dict__ for record in caplog.records], default=str)
    assert "PRIVATE_BODY_CANARY" not in material and "PRIVATE_TOKEN_CANARY" not in material
    assert "UNRELATED_VISIBLE" in material
    port.close()


@pytest.mark.parametrize("failure", ["discovery", "unsupported", "withdrawal"])
def test_factory_credential_failures_are_private_and_never_fall_back(monkeypatch, failure):
    from google.oauth2.credentials import Credentials

    checks = []

    def current():
        checks.append(1)
        if failure == "withdrawal" and len(checks) > 1:
            raise IdentitySourceError("WITHDRAWN")

    def discover(**kwargs):
        if failure == "discovery":
            raise ValueError("PRIVATE_DISCOVERY_CANARY")
        return (AnonymousCredentials() if failure == "unsupported" else Credentials(token="PRIVATE_TOKEN")), None

    monkeypatch.setattr("google.auth.default", discover)
    monkeypatch.setattr(
        "google.auth.transport.grpc.secure_authorized_channel",
        lambda *a, **kw: pytest.fail("No channel on rejected discovery"),
    )
    with pytest.raises(IdentitySourceError) as error:
        NativeIdentitySource(DECLARATION).observe_cluster(current=current)
    assert "PRIVATE" not in str(error.value)
