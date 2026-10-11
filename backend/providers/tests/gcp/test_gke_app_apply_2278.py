"""Generated Container RPC + actual HTTPS Kubernetes guarded app writes."""

import base64
import copy
import json
import socket
import ssl
import subprocess
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from google.oauth2.credentials import Credentials

from gcp.gke_app_apply import (
    ApplyError,
    ApplyLedger,
    ApplyPhase,
    EvidenceReceipt,
    ExecutionBinding,
    GKEAppApply,
    SubmissionReceipt,
    compile_app_plan,
    ledger_from_payload,
    ledger_payload,
)
from gcp.gke_identity_observation import GKEObservationContext
from gcp.gke_identity_preparation import GKEIdentityPreparation, PreparationPhase

from .test_gke_identity_observation_2278 import CONTEXT, SUBJECT, Wire, obj
from .test_gke_identity_preparation_2278 import BLANK, TOKEN, Store


class MemoryHooks:
    """Only transport ordering fixture; backend tests prove committed DB receipts."""

    def __init__(self, plan):
        self.plan, self.ledger, self.records = plan, ApplyLedger(), []
        self.journal_id, self.version = str(uuid4()), 1

    def submission(self, intent):
        self.records.append(intent)
        self.ledger = ApplyLedger(self.ledger.resources, intent)
        self.version += 1
        return SubmissionReceipt(
            self.journal_id, self.version, self.plan.execution.operation_id, intent.sha256, intent.phase
        )

    def evidence(self, evidence):
        self.records.append(evidence)
        rows = {r.resource.path: r for r in self.ledger.resources}
        rows[evidence.resource.resource.path] = evidence.resource
        self.ledger = ApplyLedger(tuple(rows.values()), replace(evidence.intent, phase=ApplyPhase.EVIDENCE))
        self.version += 1
        return EvidenceReceipt(self.journal_id, self.version, self.plan.execution.operation_id, evidence.sha256)

    def observed(self, evidence):
        result = self.evidence(evidence)
        self.ledger = ApplyLedger(self.ledger.resources)
        return result

    def rejected(self, rejection):
        self.ledger = ApplyLedger(
            self.ledger.resources, replace(rejection.intent, phase=ApplyPhase.REJECTED), rejection
        )
        self.version += 1
        return EvidenceReceipt(self.journal_id, self.version, self.plan.execution.operation_id, rejection.sha256)

    def call(self, driver, bodies, checkpoint=lambda: None):
        return driver.apply(
            self.plan,
            bodies,
            ledger=self.ledger,
            checkpoint=checkpoint,
            commit_submission=self.submission,
            commit_evidence=self.evidence,
            commit_observation=self.observed,
            commit_rejection=self.rejected,
        )


@pytest.fixture
def native(tmp_path, monkeypatch):
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
            "/CN=10.23.1.7",
            "-addext",
            "subjectAltName=IP:10.23.1.7",
        ],
        check=True,
        capture_output=True,
    )
    state = {
        "objects": {},
        "requests": [],
        "effects": [],
        "before_write": None,
        "after_write": None,
        "lost": False,
        "status": None,
        "raw": None,
        "encoding": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, value):
            raw = state["raw"] if state["raw"] is not None else json.dumps(value).encode()
            self.send_response(state["status"] or status)
            self.send_header("Content-Type", "application/json")
            if state["encoding"]:
                self.send_header("Content-Encoding", state["encoding"])
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state["requests"].append(("GET", self.path, self.headers.get("Authorization")))
            value = state["objects"].get(self.path)
            self.respond(200 if value else 404, value or {"kind": "Status"})

        def write(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.command, self.path, self.headers.get("Authorization")))
            if state["before_write"]:
                rejection = state["before_write"](self.command, self.path, body)
                if rejection:
                    return self.respond(rejection["code"], rejection)
            if self.command == "POST":
                path = self.path + "/" + body["metadata"]["name"]
                if path in state["objects"]:
                    return self.respond(409, {"kind": "Status"})
                value = copy.deepcopy(body)
                value["metadata"].update(uid=str(uuid4()), resourceVersion="rv-created")
            else:
                path = self.path
                value = copy.deepcopy(state["objects"][path])
                assert self.headers["Content-Type"] == "application/json-patch+json"
                for step in body:
                    fields = [part.replace("~1", "/").replace("~0", "~") for part in step["path"].split("/")[1:]]
                    parent = value
                    for field in fields[:-1]:
                        parent = parent[field]
                    if step["op"] == "test":
                        if parent.get(fields[-1]) != step["value"]:
                            return self.respond(409, {"kind": "Status"})
                    else:
                        assert step["op"] == "add"
                        parent[fields[-1]] = copy.deepcopy(step["value"])
                value["metadata"]["resourceVersion"] = "rv-patched"
            state["objects"][path] = value
            state["effects"].append((self.command, path, body))
            if state["after_write"]:
                state["after_write"](self.command, path)
            if state["lost"]:
                state["lost"] = False
                self.connection.shutdown(socket.SHUT_RDWR)
                return self.connection.close()
            self.respond(201 if self.command == "POST" else 200, value)

        def do_POST(self):
            self.write()

        def do_PATCH(self):
            self.write()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_connect = socket.create_connection

    def route(address, *args, **kwargs):
        assert address == ("10.23.1.7", 443), "preserve fresh native endpoint and TLS hostname"
        return real_connect(("127.0.0.1", server.server_port), *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", route)
    wire = Wire()
    wire.cluster.endpoint = "10.23.1.7"
    wire.cluster.master_auth.cluster_ca_certificate = base64.b64encode(cert.read_bytes()).decode()
    driver = GKEIdentityPreparation(BLANK, clients=wire.clients)
    driver.observer._credentials = Credentials(token=TOKEN)
    store = Store()

    def committed(method, path, body):
        pending = store.ledger.pending[0]
        assert pending.phase == PreparationPhase.SENT
        assert path == (pending.path.rsplit("/", 1)[0] if method == "POST" else pending.path)

    state["before_write"] = committed
    try:
        yield driver, store, state, wire
    finally:
        driver.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def app_native(native):
    preparation, _, state, wire = native
    state["objects"]["/api/v1/namespaces/" + SUBJECT.namespace] = obj(True)
    state["objects"]["/api/v1/namespaces/" + SUBJECT.namespace + "/serviceaccounts/" + SUBJECT.name] = obj()
    state["before_write"] = None
    context = GKEObservationContext(
        CONTEXT.identity, CONTEXT.location, CONTEXT.cluster_name, CONTEXT.native_cluster_id, (SUBJECT,)
    )
    driver = GKEAppApply(context, clients=wire.clients)
    driver.observer._credentials = preparation.observer._credentials

    def response_generation(method, path):
        row = state["objects"][path]
        if row["kind"] in ("Deployment", "StatefulSet", "DaemonSet"):
            row["metadata"]["generation"] = row["metadata"].get("generation", 0) + 1

    state["after_write"] = response_generation
    try:
        yield driver, state, wire
    finally:
        driver.close()


def resources(kind="Deployment"):
    controller = {
        "apiVersion": "apps/v1",
        "kind": kind,
        "metadata": {"name": "web", "namespace": SUBJECT.namespace},
        "spec": {
            "selector": {"matchLabels": {"app": "web"}},
            "template": {
                "metadata": {"labels": {"app": "web", **CONTEXT.owner_labels}},
                "spec": {
                    "serviceAccountName": SUBJECT.name,
                    "containers": [
                        {
                            "name": "web",
                            "image": "fixture@sha256:" + "1" * 64,
                            "resources": {"requests": {"cpu": "500m", "memory": "1Gi"}},
                        }
                    ],
                },
            },
        },
    }
    if kind != "DaemonSet":
        controller["spec"]["replicas"] = 1
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "app-secret", "namespace": SUBJECT.namespace},
        "stringData": {"password": "private-body-canary"},
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "web", "namespace": SUBJECT.namespace},
        "spec": {"selector": {"app": "web"}, "ports": [{"port": 80, "targetPort": 8080}]},
    }
    return [secret, service, controller]


def compiled(driver, raw=None, execution=None):
    plan, bodies = compile_app_plan(
        driver.context,
        execution or ExecutionBinding(str(uuid4()), str(uuid4()), "workflow", "run"),
        raw or resources(),
        identity_sha256="1" * 64,
    )
    return driver.capture_placement(plan, checkpoint=lambda: None), bodies


def test_actual_https_sdk_all_ancillary_configuration_not_rollout(app_native, caplog):
    driver, state, wire = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)

    def committed(method, path, body):
        assert hooks.ledger.pending.phase == ApplyPhase.SENT

    state["before_write"] = committed
    result = hooks.call(driver, bodies)
    assert result.configuration_observed and not result.workload_ready
    assert len(result.ledger.resources) == 3 and len(state["effects"]) == 3
    assert wire.calls and set(wire.calls) <= {"GetProject", "GetCluster", "ListNodePools"}
    assert "private-body-canary" not in str(ledger_payload(result.ledger))
    assert "private-body-canary" not in caplog.text
    assert ledger_from_payload(ledger_payload(result.ledger)) == result.ledger
    hooks.ledger = result.ledger
    hooks.call(driver, bodies)
    assert len(state["effects"]) == 3


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_supported_controller_actual_uid_generation_and_static_counts(app_native, kind):
    driver, _state, _ = app_native
    plan, bodies = compiled(driver, resources(kind))
    result = MemoryHooks(plan).call(driver, bodies)
    row = result.ledger.resources[-1]
    assert row.generation == 1
    assert row.resource.replicas == (None if kind == "DaemonSet" else 1)


def test_lost_create_receipt_never_name_adopts_or_resends(app_native):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    state["lost"] = True
    with pytest.raises(ApplyError, match="UNCONFIRMED") as caught:
        hooks.call(driver, bodies)
    assert caught.value.ledger.pending.phase == ApplyPhase.SENT
    assert not caught.value.ledger.resources and len(state["effects"]) == 1
    hooks.ledger = caught.value.ledger
    with pytest.raises(ApplyError, match="UNKNOWN_NO_RESEND"):
        hooks.call(driver, bodies)
    assert len(state["effects"]) == 1


def test_withdrawal_after_ack_retains_uid_without_followon(app_native):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)

    def checkpoint():
        if state["effects"]:
            raise ValueError("private-actor-canary")

    with pytest.raises(ApplyError, match="UNCONFIRMED") as caught:
        hooks.call(driver, bodies, checkpoint)
    assert len(state["effects"]) == 1 and len(hooks.ledger.resources) == 1
    assert hooks.ledger.pending.phase == ApplyPhase.EVIDENCE
    assert "private-actor-canary" not in str(caught.value)
    result = hooks.call(driver, bodies)
    assert result.configuration_observed and len(state["effects"]) == 3


def test_unknown_admission_mutation_commits_uid_but_no_observation(app_native):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)

    def mutation(method, path):
        state["objects"][path]["immutable"] = True

    state["after_write"] = mutation
    with pytest.raises(ApplyError, match="ADMISSION_MUTATION"):
        hooks.call(driver, bodies)
    assert len(hooks.ledger.resources) == 1 and hooks.ledger.pending.phase == ApplyPhase.EVIDENCE
    assert len(state["effects"]) == 1


def test_original_uid_replacement_refused_before_any_update(app_native):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    hooks.ledger = hooks.call(driver, bodies).ledger
    original = hooks.ledger.resources[0]
    state["objects"][original.resource.path]["metadata"]["uid"] = str(uuid4())
    with pytest.raises(ApplyError, match="RESPONSE_UNVERIFIED"):
        hooks.call(driver, bodies)
    assert len(state["effects"]) == 3


def test_uid_rv_patch_preserves_external_labels_annotations(app_native):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    hooks.ledger = hooks.call(driver, bodies).ledger
    for row in state["objects"].values():
        if row["kind"] not in ("Namespace", "ServiceAccount"):
            row["metadata"]["labels"]["external.example/label"] = "retained"
            row["metadata"]["annotations"]["external.example/annotation"] = "retained"
    raw = resources()
    raw[-1]["spec"]["template"]["spec"]["containers"][0]["image"] = "fixture@sha256:" + "2" * 64
    updated, new_bodies = compiled(driver, raw, replace(plan.execution, operation_id=str(uuid4())))
    hooks.plan = updated
    result = hooks.call(driver, new_bodies)
    assert result.configuration_observed
    patches = [body for method, _, body in state["effects"] if method == "PATCH"]
    assert len(patches) == 3 and all(
        body[0]["path"] == "/metadata/uid" and body[1]["path"] == "/metadata/resourceVersion" for body in patches
    )
    assert all(
        state["objects"][r.resource.path]["metadata"]["labels"]["external.example/label"] == "retained"
        for r in result.ledger.resources
    )


@pytest.mark.parametrize("value", [False, True, "yes"])
def test_invalid_checkpoint_refuses_before_any_native_read(app_native, value):
    driver, state, wire = app_native
    plan, bodies = compiled(driver)
    before = len(state["requests"]), len(wire.calls), len(state["effects"])
    with pytest.raises(ApplyError):
        MemoryHooks(plan).call(driver, bodies, lambda: value)
    assert (len(state["requests"]), len(wire.calls), len(state["effects"])) == before


@pytest.mark.parametrize("mutation", ["HPA", "zero", "uid", "foreign_sa", "unprepared"])
def test_unsupported_shapes_before_adc(app_native, mutation):
    driver, state, wire = app_native
    raw = resources()
    if mutation == "HPA":
        raw[-1]["kind"] = "HorizontalPodAutoscaler"
    if mutation == "zero":
        raw[-1]["spec"]["replicas"] = 0
    if mutation == "uid":
        raw[-1]["metadata"]["uid"] = str(uuid4())
    if mutation == "foreign_sa":
        raw[-1]["spec"]["template"]["spec"]["serviceAccountName"] = "foreign"
    if mutation == "unprepared":
        raw[-1]["metadata"]["namespace"] = "foreign"
    with pytest.raises(ApplyError):
        compiled(driver, raw)
    assert not state["requests"] and not wire.calls


@pytest.mark.parametrize("code,reason", [(400, "BadRequest"), (403, "Forbidden"), (409, "Conflict"), (422, "Invalid")])
def test_validated_definitive_rejection_records_exact_intent_safe_retry(app_native, code, reason):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    state["before_write"] = lambda *_: {
        "apiVersion": "v1",
        "kind": "Status",
        "status": "Failure",
        "code": code,
        "reason": reason,
        "message": "private-status-canary",
    }
    with pytest.raises(ApplyError, match="REQUEST_REJECTED"):
        hooks.call(driver, bodies)
    assert not state["effects"]
    assert hooks.ledger.pending.phase == ApplyPhase.REJECTED
    assert hooks.ledger.rejection.http_code == code
    assert "private-status-canary" not in str(ledger_payload(hooks.ledger))
    assert ledger_from_payload(ledger_payload(hooks.ledger)) == hooks.ledger
    state["before_write"] = None
    assert hooks.call(driver, bodies).configuration_observed


@pytest.mark.parametrize(
    "response",
    [
        {"apiVersion": "v1", "kind": "Status", "status": "Failure", "code": 503, "reason": "ServiceUnavailable"},
        {"apiVersion": "v1", "kind": "Status", "status": "Success", "code": 403, "reason": "Forbidden"},
        {"apiVersion": "v1", "kind": "Status", "status": "Failure", "code": 409, "reason": "Forbidden"},
        {"kind": "Status", "status": "Failure", "code": 403, "reason": "Forbidden"},
    ],
)
def test_ambiguous_or_forged_status_never_clears_sent(app_native, response):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    state["before_write"] = lambda *_: response
    with pytest.raises(ApplyError, match="UNCONFIRMED"):
        hooks.call(driver, bodies)
    assert not state["effects"] and hooks.ledger.pending.phase == ApplyPhase.SENT
    with pytest.raises(ApplyError, match="UNKNOWN_NO_RESEND"):
        hooks.call(driver, bodies)


def test_native_allocated_service_address_preserved_and_drift_refused(app_native):
    driver, state, _ = app_native
    original = state["after_write"]

    def allocated(method, path):
        original(method, path)
        if state["objects"][path]["kind"] == "Service":
            state["objects"][path]["spec"].update(
                clusterIP="10.48.0.2", clusterIPs=["10.48.0.2"], ipFamilies=["IPv4"], ipFamilyPolicy="SingleStack"
            )

    state["after_write"] = allocated
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    hooks.ledger = hooks.call(driver, bodies).ledger
    next_plan, next_bodies = compiled(driver, execution=replace(plan.execution, operation_id=str(uuid4())))
    hooks.plan = next_plan
    result = hooks.call(driver, next_bodies)
    service = next(r for r in result.ledger.resources if r.resource.kind == "Service")
    assert state["objects"][service.resource.path]["spec"]["clusterIP"] == "10.48.0.2"
    effects = len(state["effects"])
    state["objects"][service.resource.path]["spec"]["clusterIP"] = "10.48.0.3"
    with pytest.raises(ApplyError, match="ALLOCATION_CHANGED"):
        hooks.call(driver, next_bodies)
    assert len(state["effects"]) == effects


def test_reviewed_defaults_allow_equivalence_unknown_defaults_refuse(app_native):
    driver, state, _ = app_native
    original = state["after_write"]

    def defaults(method, path):
        original(method, path)
        value = state["objects"][path]
        if value["kind"] == "Deployment":
            value["spec"].update(
                revisionHistoryLimit=10,
                progressDeadlineSeconds=600,
                strategy={"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": "25%"}},
            )
            value["spec"]["template"]["metadata"]["creationTimestamp"] = None
            value["spec"]["template"]["spec"].update(
                restartPolicy="Always",
                dnsPolicy="ClusterFirst",
                schedulerName="default-scheduler",
                terminationGracePeriodSeconds=30,
                securityContext={},
            )
            value["spec"]["template"]["spec"]["containers"][0].update(
                imagePullPolicy="IfNotPresent",
                terminationMessagePath="/dev/termination-log",
                terminationMessagePolicy="File",
            )

    state["after_write"] = defaults
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    hooks.ledger = hooks.call(driver, bodies).ledger
    row = hooks.ledger.resources[-1]
    state["objects"][row.resource.path]["spec"]["template"]["spec"]["injected"] = "unknown"
    with pytest.raises(ApplyError, match="RESPONSE_UNVERIFIED"):
        hooks.call(driver, bodies)


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("Deployment", "podManagementPolicy", "OrderedReady"),
        ("StatefulSet", "progressDeadlineSeconds", 600),
        ("DaemonSet", "progressDeadlineSeconds", 600),
        ("DaemonSet", "podManagementPolicy", "OrderedReady"),
    ],
)
def test_other_controller_defaults_are_unreviewed_native_admission(app_native, kind, field, value):
    driver, state, _ = app_native
    original = state["after_write"]

    def mutate(method, path):
        original(method, path)
        if state["objects"][path]["kind"] == kind:
            state["objects"][path]["spec"][field] = value

    state["after_write"] = mutate
    plan, bodies = compiled(driver, resources(kind))
    hooks = MemoryHooks(plan)
    with pytest.raises(ApplyError, match="ADMISSION_MUTATION_UNREVIEWED"):
        hooks.call(driver, bodies)
    assert hooks.ledger.pending.phase == ApplyPhase.EVIDENCE
    assert hooks.ledger.resources[-1].uid == state["objects"][plan.resources[-1].path]["metadata"]["uid"]


@pytest.mark.parametrize("category", ["labels", "annotations"])
def test_owned_metadata_removal_refuses_before_submission(app_native, category):
    driver, state, _ = app_native
    raw = resources()
    raw[0]["metadata"][category] = {"owned.example/key": "owned-value"}
    plan, bodies = compiled(driver, raw)
    hooks = MemoryHooks(plan)
    hooks.ledger = hooks.call(driver, bodies).ledger
    obj = state["objects"][plan.resources[0].path]
    obj["metadata"][category]["foreign.example/key"] = "foreign-value"
    before = len(state["effects"]), len(hooks.records)
    updated, bodies = compiled(driver, resources(), replace(plan.execution, operation_id=str(uuid4())))
    hooks.plan = updated
    with pytest.raises(ApplyError, match="METADATA_REMOVAL_REQUIRES_REVIEW"):
        hooks.call(driver, bodies)
    assert (len(state["effects"]), len(hooks.records)) == before
    assert obj["metadata"][category]["owned.example/key"] == "owned-value"
    assert obj["metadata"][category]["foreign.example/key"] == "foreign-value"


@pytest.mark.parametrize("change", ["journal", "version"])
def test_sent_receipt_must_match_advancing_journal_fence_before_transport(app_native, change):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    original = hooks.submission

    def changed(intent):
        receipt = original(intent)
        if intent.phase == ApplyPhase.SENT:
            return replace(receipt, **({"journal_id": str(uuid4())} if change == "journal" else {"journal_version": 2}))
        return receipt

    hooks.submission = changed
    with pytest.raises(ApplyError, match="RECEIPT_FENCE_CHANGED"):
        hooks.call(driver, bodies)
    assert not state["effects"] and hooks.ledger.pending.phase == ApplyPhase.SENT


def test_evidence_receipt_changed_journal_prevents_followon(app_native):
    driver, state, _ = app_native
    plan, bodies = compiled(driver)
    hooks = MemoryHooks(plan)
    original = hooks.evidence
    hooks.evidence = lambda evidence: replace(original(evidence), journal_id=str(uuid4()))
    with pytest.raises(ApplyError, match="RECEIPT_FENCE_CHANGED"):
        hooks.call(driver, bodies)
    assert len(state["effects"]) == 1 and hooks.ledger.pending.phase == ApplyPhase.EVIDENCE


@pytest.mark.parametrize(
    ("api_version", "kind", "content"),
    [
        ("v1", "ConfigMap", {"data": {"mode": "reviewed"}}),
        (
            "v1",
            "PersistentVolumeClaim",
            {"spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}},
        ),
        (
            "networking.k8s.io/v1",
            "Ingress",
            {
                "spec": {
                    "rules": [
                        {
                            "host": "fixture.invalid",
                            "http": {
                                "paths": [
                                    {
                                        "path": "/",
                                        "pathType": "Prefix",
                                        "backend": {"service": {"name": "web", "port": {"number": 80}}},
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
        ),
        (
            "networking.k8s.io/v1",
            "NetworkPolicy",
            {"spec": {"podSelector": {}, "policyTypes": ["Ingress"], "ingress": []}},
        ),
        (
            "monitoring.coreos.com/v1",
            "PodMonitor",
            {"spec": {"selector": {"matchLabels": {"app": "web"}}, "podMetricsEndpoints": [{"port": "metrics"}]}},
        ),
        (
            "gateway.networking.k8s.io/v1",
            "Gateway",
            {"spec": {"gatewayClassName": "fixture", "listeners": [{"name": "http", "port": 80, "protocol": "HTTP"}]}},
        ),
        (
            "gateway.networking.k8s.io/v1",
            "HTTPRoute",
            {"spec": {"parentRefs": [{"name": "gateway"}], "rules": [{"backendRefs": [{"name": "web", "port": 80}]}]}},
        ),
    ],
)
def test_each_compiled_ancillary_has_owned_route_uid_and_completion(api_version, kind, content, app_native):
    driver, state, _ = app_native
    raw = resources()
    raw.insert(
        0,
        {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {"name": "ancillary", "namespace": SUBJECT.namespace},
            **content,
        },
    )
    plan, bodies = compiled(driver, raw)
    hooks = MemoryHooks(plan)
    result = hooks.call(driver, bodies)
    first = result.ledger.resources[0]
    assert first.resource.kind == kind and first.uid
    assert state["effects"][0][1] == first.resource.path
    assert next(path for method, path, _ in state["requests"] if method == "POST") == first.resource.collection
    assert state["objects"][first.resource.path]["metadata"]["labels"] == dict(driver.context.owner_labels)
    assert len(result.ledger.resources) == 4 and result.configuration_observed and not result.workload_ready
