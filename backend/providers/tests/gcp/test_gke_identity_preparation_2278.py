"""Actual Container/CRM serializers + owned private-address TLS Kubernetes effects."""

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

from gcp import gke_identity_preparation as module
from gcp.gke_identity_preparation import (
    GKEIdentityPreparation,
    GKEPreparationError,
    GKEPreparationLedger,
    PreparationCommitReceipt,
    PreparationPhase,
    VerifiedIAMConfigurationReceipt,
    preparation_target_sha256,
)
from tests.gcp.test_gke_identity_observation_2278 import CONTEXT, IDENTITY, SUBJECT, Wire, obj

OPERATION = "12345678-0000-4000-8000-000000000006"
JOURNAL = "12345678-0000-4000-8000-000000000007"
BLANK = replace(CONTEXT, subjects=(replace(SUBJECT, namespace_uid="", service_account_uid=""),))
NS = "/api/v1/namespaces/" + SUBJECT.namespace
SA = NS + "/serviceaccounts/" + SUBJECT.name
TOKEN = "synthetic-private-credential"


class Store:
    """In-memory hook visibility; deliberately no PostgreSQL durability claim."""

    def __init__(self, context=BLANK):
        self.context = context
        self.ledger = GKEPreparationLedger(preparation_target_sha256(context))
        self.version = 0
        self.records = []
        self.current = True
        self.bad = None

    def checkpoint(self):
        if not self.current:
            raise module.GKEObservationError("CURRENT_AUTHORITY_WITHDRAWN")

    def commit(self, record):
        self.ledger = record.ledger
        self.version += 1
        self.records.append(record)
        phase = record.intent.phase if isinstance(record, module.PreparationSubmission) else PreparationPhase.OBSERVED
        receipt = PreparationCommitReceipt(
            JOURNAL, self.version, OPERATION, self.ledger.target_sha256, self.ledger.sha256, record.record_sha256, phase
        )
        return self.bad(record, receipt) if self.bad else receipt

    def call(self, driver, iam=None):
        kwargs = dict(
            operation_id=OPERATION,
            ledger=self.ledger,
            commit_submission=self.commit,
            commit_observation=self.commit,
            checkpoint=self.checkpoint,
        )
        return (
            driver.prepare(**kwargs)
            if iam is None
            else driver.annotate(iam_receipt=iam, expected_desired_union_sha256="f" * 64, **kwargs)
        )

    def iam(self):
        return VerifiedIAMConfigurationReceipt(
            JOURNAL,
            self.version,
            OPERATION,
            IDENTITY.fingerprint,
            "f" * 64,
            tuple(row.uid for row in self.ledger.objects if row.kind == "ServiceAccount"),
            True,
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
                state["before_write"](self.command, self.path, body)
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


def test_actual_sdk_tls_create_commits_original_uids_before_iam_use(native, caplog):
    driver, store, state, wire = native
    caplog.set_level("DEBUG")
    result = store.call(driver)
    assert result.configuration_observed and not result.workload_ready and not result.impersonation_verified
    assert len(result.ledger.objects) == 2 and not result.ledger.pending
    assert [
        record.intent.phase if isinstance(record, module.PreparationSubmission) else PreparationPhase.OBSERVED
        for record in store.records
    ] == [PreparationPhase.UNSENT, PreparationPhase.SENT, PreparationPhase.OBSERVED] * 2
    assert all(step.phase == "OBSERVED" and step.transport_invoked for step in result.steps)
    assert len(state["effects"]) == 2 and all(row[0] == "POST" for row in state["effects"])
    assert all(record.object.uid for record in store.records if isinstance(record, module.ObjectObservation))
    assert wire.calls and all(name in ("GetProject", "GetCluster", "ListNodePools") for name in wire.calls)
    assert TOKEN not in caplog.text and IDENTITY.email not in caplog.text
    store.call(driver)
    assert len(state["effects"]) == 2


def test_annotation_requires_verified_iam_and_preserves_foreign_metadata(native):
    driver, store, state, _ = native
    store.call(driver)
    state["objects"][SA]["metadata"]["labels"]["foreign-label"] = "keep"
    state["objects"][SA]["metadata"]["annotations"]["foreign-annotation"] = "keep"
    result = store.call(driver, store.iam())
    patch = state["effects"][-1][2]
    assert [row["path"] for row in patch[:3]] == ["/metadata/uid", "/metadata/resourceVersion", "/metadata/annotations"]
    assert all(row["op"] == "test" for row in patch[:3])
    assert state["objects"][SA]["metadata"]["annotations"]["foreign-annotation"] == "keep"
    assert state["objects"][SA]["metadata"]["labels"]["foreign-label"] == "keep"
    assert (
        result.configuration_observed
        and state["objects"][SA]["metadata"]["annotations"][module._LINK] == IDENTITY.email
    )
    store.call(driver, store.iam())
    assert len(state["effects"]) == 3


@pytest.mark.parametrize("bad", [True, False, None, "committed"])
def test_invalid_durable_submission_proof_never_reaches_post(native, bad):
    driver, store, state, _ = native
    store.bad = lambda record, receipt: bad
    with pytest.raises(GKEPreparationError, match="DURABLE_RECEIPT_REQUIRED"):
        store.call(driver)
    assert not state["effects"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("ledger_sha256", "f" * 64),
        ("operation_id", JOURNAL),
        ("target_sha256", "f" * 64),
        ("journal_version", True),
        ("record_sha256", "f" * 64),
    ],
)
def test_mismatched_durable_proof_refuses_effect(native, field, value):
    driver, store, state, _ = native
    store.bad = lambda record, receipt: replace(receipt, **{field: value})
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    assert not state["effects"]


def test_unknown_create_recovers_only_own_sent_marker_and_commits_uid(native):
    driver, store, state, _ = native
    state["lost"] = True
    with pytest.raises(GKEPreparationError) as failure:
        store.call(driver)
    assert failure.value.receipt.steps[0].phase == "UNKNOWN"
    assert store.ledger.pending[0].phase == PreparationPhase.SENT and len(state["effects"]) == 1
    result = store.call(driver)
    assert not result.ledger.pending and len(state["effects"]) == 2
    assert result.ledger.objects[0].uid == state["objects"][NS]["metadata"]["uid"]


def test_unknown_sent_without_observed_object_never_resends(native):
    driver, store, state, _ = native

    # Withdraw after SENT acknowledgement before transport; persisted SENT remains conservative.
    store.bad = lambda record, receipt: (
        (setattr(store, "current", False), receipt)[1] if receipt.phase == PreparationPhase.SENT else receipt
    )
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    store.bad, store.current = None, True
    with pytest.raises(GKEPreparationError, match="SENT_SUBMISSION_UNRESOLVED"):
        store.call(driver)
    assert not state["effects"]


@pytest.mark.parametrize("change", ["marker", "owner", "uid"])
def test_lost_create_foreign_or_replaced_subject_refuses(native, change):
    driver, store, state, _ = native
    state["lost"] = True
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    if change == "marker":
        state["objects"][NS]["metadata"]["annotations"][module._OPERATION] = JOURNAL
    elif change == "owner":
        state["objects"][NS]["metadata"]["labels"]["astrolift.io/app-id"] = JOURNAL
    else:
        # Once committed, a same-label replacement must not be adopted.
        store.call(driver)
        state["objects"][NS]["metadata"]["uid"] = JOURNAL
    prior = len(state["effects"])
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    assert len(state["effects"]) == prior


def test_initial_blank_uid_never_adopts_generic_same_label_object(native):
    driver, store, state, wire = native
    state["objects"][NS] = obj(True)
    with pytest.raises(GKEPreparationError, match="EXISTING_SUBJECT_UID_UNRECORDED"):
        store.call(driver)
    assert not state["effects"] and not store.ledger.objects
    assert wire.calls


def test_unsent_matching_object_never_adopts(native):
    driver, store, state, _ = native
    store.bad = lambda record, receipt: (setattr(store, "current", False), receipt)[1]
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    pending = store.ledger.pending[0]
    state["objects"][NS] = obj(True)
    state["objects"][NS]["metadata"]["annotations"] = {
        module._OPERATION: OPERATION,
        module._REQUEST: pending.request_sha256,
    }
    store.current, store.bad = True, None
    with pytest.raises(GKEPreparationError, match="EXISTING_SUBJECT_UID_UNRECORDED"):
        store.call(driver)
    assert not store.ledger.objects and not state["effects"]


@pytest.mark.parametrize("change", ["uid", "rv", "link"])
def test_atomic_json_patch_refuses_intervening_replacement_or_competing_link(native, change):
    driver, store, state, _ = native
    store.call(driver)

    def changed(method, path, body):
        if method == "PATCH":
            if change == "uid":
                state["objects"][SA]["metadata"]["uid"] = JOURNAL
            elif change == "rv":
                state["objects"][SA]["metadata"]["resourceVersion"] = "changed:opaque-rv"
            else:
                state["objects"][SA]["metadata"]["annotations"][module._LINK] = "competing@foreign.invalid"

    state["before_write"] = changed
    with pytest.raises(GKEPreparationError):
        store.call(driver, store.iam())
    assert len(state["effects"]) == 2
    assert store.ledger.pending[0].phase == PreparationPhase.SENT


@pytest.mark.parametrize(
    "field,value",
    [
        ("completed", False),
        ("desired_union_sha256", "a" * 64),
        ("identity_sha256", "f" * 64),
        ("operation_id", JOURNAL),
        ("service_account_uids", (JOURNAL,)),
    ],
)
def test_missing_or_foreign_verified_iam_receipt_refuses_annotation(native, field, value):
    driver, store, state, _ = native
    store.call(driver)
    with pytest.raises(GKEPreparationError):
        store.call(driver, replace(store.iam(), **{field: value}))
    assert len(state["effects"]) == 2


def test_post_success_without_committed_uid_cannot_be_returned_to_iam(native):
    driver, store, state, _ = native

    def lost(record, receipt):
        if isinstance(record, module.ObjectObservation):
            raise RuntimeError("journal-private-canary")
        return receipt

    store.bad = lost
    with pytest.raises(GKEPreparationError, match="JOURNAL_COMMIT_UNCONFIRMED") as failure:
        store.call(driver)
    assert not failure.value.receipt.ledger.objects
    assert failure.value.receipt.ledger.pending[0].phase == PreparationPhase.SENT
    assert len(state["effects"]) == 1


@pytest.mark.parametrize("value", [False, True, "accepted"])
def test_invalid_checkpoint_refuses_before_adc(monkeypatch, value):
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda **kwargs: pytest.fail("no ADC"))
    store = Store()
    store.checkpoint = lambda: value
    with pytest.raises(GKEPreparationError, match="CURRENT_ADMISSION_UNCONFIRMED"):
        store.call(GKEIdentityPreparation(BLANK))


def test_actor_withdrawal_after_native_response_prevents_post(native):
    driver, store, state, wire = native
    wire.after = lambda *args: setattr(store, "current", False)
    with pytest.raises(GKEPreparationError, match="CURRENT_AUTHORITY_WITHDRAWN"):
        store.call(driver)
    assert not state["effects"] and not store.records


def test_lost_sent_commit_acknowledgement_never_sends_or_retries(native):
    driver, store, state, _ = native

    def lost(record, receipt):
        if receipt.phase == PreparationPhase.SENT:
            raise RuntimeError("journal-private-canary")
        return receipt

    store.bad = lost
    with pytest.raises(GKEPreparationError) as failure:
        store.call(driver)
    assert failure.value.receipt.steps[0].phase == "UNKNOWN"
    assert not failure.value.receipt.steps[0].transport_invoked
    store.bad = None
    with pytest.raises(GKEPreparationError, match="SENT_SUBMISSION_UNRESOLVED"):
        store.call(driver)
    assert not state["effects"]


def test_held_create_has_committed_sent_and_second_invocation_cannot_resend(native):
    driver, store, state, wire = native
    entered, release = threading.Event(), threading.Event()
    results = []
    original = state["before_write"]

    def held(method, path, body):
        original(method, path, body)
        entered.set()
        assert release.wait(timeout=3)

    state["before_write"] = held

    def run():
        try:
            results.append(store.call(driver))
        except GKEPreparationError as error:
            results.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=2)
        assert store.ledger.pending[0].phase == PreparationPhase.SENT
        second = GKEIdentityPreparation(BLANK, clients=wire.clients)
        second.observer._credentials = driver.observer._credentials
        with pytest.raises(GKEPreparationError, match="SENT_SUBMISSION_UNRESOLVED"):
            store.call(second)
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and len(results) == 1 and not isinstance(results[0], Exception)
    assert len(state["effects"]) == 2


def test_lost_annotation_reply_recovers_exact_original_uid_without_second_patch(native):
    driver, store, state, _ = native
    store.call(driver)
    state["lost"] = True
    with pytest.raises(GKEPreparationError) as error:
        store.call(driver, store.iam())
    assert error.value.receipt.steps[-1].phase == "UNKNOWN" and len(state["effects"]) == 3
    result = store.call(driver, store.iam())
    assert result.steps[-1].phase == "OBSERVED" and not result.steps[-1].transport_invoked
    assert not result.ledger.pending and len(state["effects"]) == 3


def test_source_incarnation_withdrawn_between_phase_commits_prevents_create(native):
    driver, store, state, wire = native
    store.bad = lambda record, receipt: (setattr(wire.cluster, "id", "foreign-replacement"), receipt)[1]
    with pytest.raises(GKEPreparationError, match="CLUSTER_CONFIGURATION_UNVERIFIED"):
        store.call(driver)
    assert store.ledger.pending[0].phase == PreparationPhase.UNSENT
    assert not state["effects"]


def test_actual_factory_has_fixed_rpc_hosts_private_credentials_and_cleanup(native, monkeypatch, caplog):
    import google.auth
    import google.auth.transport.grpc

    _driver, store, _, wire = native
    made = []
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (Credentials(token=TOKEN), None))

    def channel(credentials, request, host, **kwargs):
        made.append(host)
        assert kwargs["options"] == [("grpc.max_receive_message_length", module.MAX_BYTES)]
        return wire

    monkeypatch.setattr(google.auth.transport.grpc, "secure_authorized_channel", channel)
    monkeypatch.setenv("HTTPS_PROXY", "http://foreign.invalid:8181")
    monkeypatch.setenv("GOOGLE_API_USE_MTLS_ENDPOINT", "always")
    caplog.set_level("DEBUG")
    with GKEIdentityPreparation(BLANK) as production:
        assert store.call(production).configuration_observed
    assert made == ["cloudresourcemanager.googleapis.com:443", "container.googleapis.com:443"]
    assert wire.closed == 2
    for marker in (TOKEN, IDENTITY.email, IDENTITY.project_number, OPERATION):
        assert marker not in caplog.text


def test_actual_wrong_native_ca_sends_no_credentials_or_writes(native, tmp_path):
    driver, store, state, wire = native
    other_cert, other_key = tmp_path / "other.pem", tmp_path / "other.key"
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
            str(other_key),
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
    with pytest.raises(GKEPreparationError, match="KUBERNETES_TRANSPORT_UNCONFIRMED"):
        store.call(driver)
    assert not state["requests"] and not state["effects"]


@pytest.mark.parametrize("change", ["denied", "array", "oversized", "gzip"])
def test_tls_transport_bounded_protocol_privacy(native, change, caplog):
    driver, store, state, _ = native
    caplog.set_level("DEBUG")
    if change == "denied":
        state["status"] = 403
    elif change == "array":
        state["status"], state["raw"] = 200, b"[]"
    elif change == "oversized":
        state["status"], state["raw"] = 200, b"x" * (module.MAX_BYTES + 1)
    else:
        state["encoding"] = "gzip"
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    assert not state["effects"] and TOKEN not in caplog.text


@pytest.mark.parametrize("field", ["request_sha256", "wire_request_sha256"])
def test_recovery_refuses_source_substituted_create_digest_before_native(native, field):
    driver, store, state, wire = native
    state["lost"] = True
    with pytest.raises(GKEPreparationError):
        store.call(driver)
    store.ledger = replace(store.ledger, pending=(replace(store.ledger.pending[0], **{field: "a" * 64}),))
    calls = len(wire.calls)
    with pytest.raises(GKEPreparationError, match="PENDING_REQUEST_CHANGED"):
        store.call(driver)
    assert len(wire.calls) == calls and len(state["effects"]) == 1


def test_existing_subjects_require_original_pinned_uid_and_need_no_create(native):
    driver, _store, state, _ = native
    driver.context = CONTEXT
    driver.observer.context = CONTEXT
    store = Store(CONTEXT)
    state["objects"].update({NS: obj(True), SA: obj()})
    state["before_write"] = lambda *args: pytest.fail("pinned existing objects need no effects")
    result = store.call(driver)
    assert result.configuration_observed and not state["effects"]
    assert {row.uid for row in result.ledger.objects} == {SUBJECT.namespace_uid, SUBJECT.service_account_uid}


def test_create_intent_wire_digest_matches_actual_transmitted_json(native):
    driver, store, state, _ = native
    original = state["before_write"]

    def verify(method, path, body):
        original(method, path, body)
        intent = store.ledger.pending[0]
        assert module._body_sha(body) == intent.wire_request_sha256
        unmarked = copy.deepcopy(body)
        assert unmarked["metadata"]["annotations"].pop(module._REQUEST) == intent.request_sha256
        assert module._body_sha(unmarked) == intent.request_sha256

    state["before_write"] = verify
    store.call(driver)
    assert len(state["effects"]) == 2


def test_any_later_unresolved_sent_blocks_earlier_new_subject_effect(native):
    driver, store, state, _ = native
    second = replace(BLANK.subjects[0], namespace="earlier-new-namespace", name="earlier-new-app")
    context = replace(BLANK, subjects=(second, BLANK.subjects[0]))
    driver.context = context
    driver.observer.context = context
    store.context = context
    body, digest = driver._create_body(BLANK.subjects[0], True, OPERATION)
    # Restored trusted-journal SENT fixture: absence is not proof that an old send stopped.
    intent = module.PreparationIntent(
        str(uuid4()), OPERATION, "Namespace", NS, "POST", digest, module._body_sha(body), phase=PreparationPhase.SENT
    )
    store.ledger = GKEPreparationLedger(preparation_target_sha256(context), pending=(intent,))
    with pytest.raises(GKEPreparationError, match="SENT_SUBMISSION_UNRESOLVED"):
        store.call(driver)
    assert not state["effects"]
    assert not any(row[0] == "POST" for row in state["requests"])


def test_partial_original_ksa_uid_without_namespace_uid_refuses_before_adc(monkeypatch):
    import google.auth

    context = replace(CONTEXT, subjects=(replace(SUBJECT, namespace_uid=""),))
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: pytest.fail("no ADC"))
    with pytest.raises(GKEPreparationError, match="NAMESPACE_UID_UNRECORDED"):
        Store(context).call(GKEIdentityPreparation(context))
