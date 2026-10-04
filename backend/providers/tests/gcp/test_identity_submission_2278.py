"""Typed journal contract through actual GAPIC serialization; no live cloud/DB."""

import copy
import json
import threading
from dataclasses import replace

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable

from gcp.identity_owned import (
    DurableSubmissionReceipt,
    NativeGCPIdentity,
    NativeIdentityError,
    NativeIdentityReconciliationError,
    OwnedGrant,
    OwnedGrantLedger,
    PolicyOwnership,
    PolicyStepState,
    PolicySubmissionPhase,
    desired_owned_union_sha256,
    owned_ledger_from_payload,
    owned_ledger_payload,
    validate_owned_ledger,
)

from .test_identity_owned_2278 import CONTEXT, PERMISSIONS, SECOND, UID, Wire

JOURNAL_ID = "12345678-0000-4000-8000-000000000009"


class SubmissionWire(Wire):
    def __init__(self):
        self.setter_entries = 0
        self.before_set = None
        super().__init__()

    def unary_unary(self, method, request_serializer=None, response_deserializer=None, *args, **kwargs):
        call = super().unary_unary(method, request_serializer, response_deserializer, *args, **kwargs)
        name = (method.decode() if isinstance(method, bytes) else method).rsplit("/", 1)[-1]
        if name != "SetIamPolicy":
            return call

        def setter(request, **kwargs):
            self.setter_entries += 1
            if self.before_set:
                self.before_set(request)
            return call(request, **kwargs)

        class CompletedCall:
            def trailing_metadata(self):
                return ()

        setter.with_call = lambda request, **kwargs: (setter(request, **kwargs), CompletedCall())
        return setter


class Journal:
    def __init__(self, wire):
        self.wire = wire
        self.ledger = OwnedGrantLedger(CONTEXT.fingerprint)
        self.version = 0
        self.hooks = []
        self.mode = None
        self.current = True
        self.proofs = []

    def checkpoint(self):
        if not self.current:
            raise NativeIdentityError("CURRENT_AUTHORITY_WITHDRAWN")

    def persist(self, ledger):
        old = {row.submission_id for row in self.ledger.pending}
        assert all(row.submission_id in old for row in ledger.pending), "phases belong only to submission hook"
        self.ledger = ledger
        self.version += 1

    def hook(self, submission):
        assert submission.context_sha256 == CONTEXT.fingerprint
        self.hooks.append(submission)
        self.ledger = submission.ledger
        self.version += 1
        receipt = DurableSubmissionReceipt(
            JOURNAL_ID,
            self.version,
            submission.intent.submission_id,
            submission.submission_sha256,
            submission.intent.submission_phase,
            submission.ledger_sha256,
        )
        if self.mode:
            return self.mode(submission, receipt)
        return receipt

    def call(self, permissions=PERMISSIONS, uids=(UID,), driver=None):
        driver = driver or NativeGCPIdentity(CONTEXT, clients=self.wire.clients)
        return driver.reconcile(
            permissions,
            service_account_uids=uids,
            ledger=self.ledger,
            checkpoint=self.checkpoint,
            persist=self.persist,
            submission_hook=self.hook,
        )


@pytest.fixture
def journal():
    wire = SubmissionWire()
    state = Journal(wire)

    def before_set(request):
        intent = next(row for row in state.ledger.pending if row.resource == request.resource)
        assert intent.submission_phase == PolicySubmissionPhase.SENT
        assert [row.intent.submission_phase for row in state.hooks[-2:]] == [
            PolicySubmissionPhase.UNSENT,
            PolicySubmissionPhase.SENT,
        ]
        assert state.hooks[-1].ledger == state.ledger
        state.proofs.append((intent.submission_id, state.version, intent.resource))

    wire.before_set = before_set
    return state


def test_typed_commits_are_visible_before_actual_sdk_setter(journal):
    result = journal.call()
    assert journal.wire.writes == 2 and len(journal.proofs) == 2
    assert all(row.state == PolicyStepState.OBSERVED and row.transport_invoked for row in result.receipt.steps)
    assert result.receipt.ledger == result.ledger == journal.ledger
    assert not result.ledger.pending and not result.workload_ready
    assert journal.call().receipt.steps == ()


@pytest.mark.parametrize("bad", [True, False, None, "committed"])
def test_boolean_none_and_string_are_not_durable_receipts(journal, bad):
    journal.mode = lambda request, receipt: bad
    with pytest.raises(NativeIdentityReconciliationError, match="DURABLE_SUBMISSION_RECEIPT_REQUIRED") as failure:
        journal.call()
    assert journal.wire.setter_entries == 0
    assert not any(row.transport_invoked for row in failure.value.receipt.steps)


@pytest.mark.parametrize(
    "field,value",
    [
        ("submission_id", JOURNAL_ID),
        ("submission_sha256", "f" * 64),
        ("ledger_sha256", "f" * 64),
        ("phase", PolicySubmissionPhase.SENT),
        ("journal_id", "invalid"),
        ("journal_version", True),
        ("journal_version", 0),
    ],
)
def test_mismatched_commit_proof_refuses_before_setter(journal, field, value):
    journal.mode = lambda request, receipt: replace(receipt, **{field: value})
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    assert journal.wire.setter_entries == 0


def test_same_or_foreign_journal_revision_refuses_sent(journal):
    def mode(request, receipt):
        return (
            replace(receipt, journal_version=1)
            if request.intent.submission_phase == PolicySubmissionPhase.SENT
            else receipt
        )

    journal.mode = mode
    with pytest.raises(NativeIdentityReconciliationError, match="DURABLE_SUBMISSION_RECEIPT_REQUIRED") as failure:
        journal.call()
    assert journal.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNKNOWN
    assert not failure.value.receipt.steps[0].transport_invoked
    assert not journal.wire.setter_entries


def test_lost_sdk_setter_returns_unknown_and_observed_after_hash_does_not_duplicate(journal):
    journal.wire.lost_reply = True
    with pytest.raises(NativeIdentityReconciliationError, match="NATIVE_SET_UNCONFIRMED") as failure:
        journal.call()
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNKNOWN
    assert failure.value.receipt.steps[0].transport_invoked and journal.wire.writes == 1
    assert journal.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
    result = journal.call()
    assert not result.ledger.pending and journal.wire.writes == 2
    assert result.receipt.steps[0].state == PolicyStepState.OBSERVED


def test_read_failure_before_submission_is_known_no_effect_this_invocation(journal):
    journal.wire.error = PermissionDenied
    with pytest.raises(NativeIdentityReconciliationError, match="NATIVE_READ_OR_WRITE_UNCONFIRMED") as failure:
        journal.call()
    assert failure.value.receipt.steps == () and not journal.ledger.pending
    assert not journal.wire.setter_entries and not journal.hooks


def test_unknown_before_hash_blocks_any_new_send(journal):
    def timeout(request):
        raise ServiceUnavailable("private-native-canary")

    journal.wire.before_set = timeout
    with pytest.raises(NativeIdentityReconciliationError) as first:
        journal.call()
    assert first.value.receipt.steps[0].state == PolicyStepState.UNKNOWN
    assert journal.wire.setter_entries == 1 and journal.wire.writes == 0
    with pytest.raises(NativeIdentityReconciliationError, match="SENT_SUBMISSION_UNRESOLVED") as second:
        journal.call()
    assert not second.value.receipt.steps[0].transport_invoked
    assert journal.wire.setter_entries == 1


def test_held_setter_cannot_be_reissued_before_original_finishes(journal):
    entered, release = threading.Event(), threading.Event()
    failures = []

    def held(request):
        entered.set()
        assert release.wait(timeout=3)
        raise ServiceUnavailable("private-native-canary")

    journal.wire.before_set = held

    def run():
        try:
            journal.call()
        except NativeIdentityReconciliationError as error:
            failures.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=2)
        assert journal.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
        with pytest.raises(NativeIdentityReconciliationError, match="SENT_SUBMISSION_UNRESOLVED"):
            journal.call()
        assert journal.wire.setter_entries == 1
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and len(failures) == 1
    assert failures[0].receipt.steps[0].state == PolicyStepState.UNKNOWN


def test_partial_multi_resource_failure_retains_observed_and_unknown(journal):
    original = journal.wire.before_set

    def fail_second(request):
        original(request)
        if journal.wire.setter_entries == 2:
            journal.wire.lost_reply = True

    journal.wire.before_set = fail_second
    with pytest.raises(NativeIdentityReconciliationError) as failure:
        journal.call([*PERMISSIONS, {"role": PERMISSIONS[0]["role"], "resource": SECOND}])
    assert [step.state for step in failure.value.receipt.steps] == [PolicyStepState.OBSERVED, PolicyStepState.UNKNOWN]
    assert len(failure.value.receipt.ledger.policies) == 1
    assert len(failure.value.receipt.ledger.pending) == 1
    assert journal.wire.writes == 2


def test_withdrawn_after_sent_commit_is_conservative_sent_without_setter(journal):
    def withdraw(request, receipt):
        if request.intent.submission_phase == PolicySubmissionPhase.SENT:
            journal.current = False
        return receipt

    journal.mode = withdraw
    with pytest.raises(NativeIdentityReconciliationError, match="CURRENT_AUTHORITY_WITHDRAWN") as failure:
        journal.call()
    assert journal.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
    assert failure.value.receipt.steps[0].state == PolicyStepState.SENT
    assert not failure.value.receipt.steps[0].transport_invoked and not journal.wire.setter_entries
    journal.current = True
    journal.mode = None
    with pytest.raises(NativeIdentityReconciliationError, match="SENT_SUBMISSION_UNRESOLVED"):
        journal.call()
    assert not journal.wire.setter_entries


def test_uncertain_sent_hook_commit_is_never_reported_as_unsent(journal):
    def lost(request, receipt):
        if request.intent.submission_phase == PolicySubmissionPhase.SENT:
            raise RuntimeError("private-journal-canary")
        return receipt

    journal.mode = lost
    with pytest.raises(NativeIdentityReconciliationError, match="SUBMISSION_COMMIT_UNCONFIRMED") as failure:
        journal.call()
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNKNOWN
    assert not failure.value.receipt.steps[0].transport_invoked
    assert journal.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
    assert not journal.wire.setter_entries


def test_typed_pending_cannot_fall_back_to_legacy(journal):
    journal.wire.before_set = lambda request: (_ for _ in ()).throw(ServiceUnavailable("private"))
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    with pytest.raises(NativeIdentityError, match="TYPED_SUBMISSION_HOOK_REQUIRED"):
        NativeGCPIdentity(CONTEXT, clients=journal.wire.clients).reconcile(
            PERMISSIONS,
            service_account_uids=(UID,),
            ledger=journal.ledger,
            checkpoint=journal.checkpoint,
            persist=journal.persist,
        )
    assert journal.wire.setter_entries == 1


def test_pending_union_and_context_retarget_refuse_without_more_effects(journal):
    journal.wire.before_set = lambda request: (_ for _ in ()).throw(ServiceUnavailable("private"))
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    with pytest.raises(NativeIdentityReconciliationError, match="PENDING_SUBMISSION_UNION_CHANGED"):
        journal.call([], ())
    assert journal.wire.setter_entries == 1


def test_versioned_codec_roundtrip_legacy_and_typed_metadata(journal):
    original = OwnedGrantLedger(CONTEXT.fingerprint)
    assert owned_ledger_from_payload(json.loads(json.dumps(owned_ledger_payload(original)))) == original
    journal.wire.lost_reply = True
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    payload = json.loads(json.dumps(owned_ledger_payload(journal.ledger)))
    assert payload["schema_version"] == 2
    assert payload["pending"][0]["submission_phase"] == "SENT"
    assert owned_ledger_from_payload(payload) == journal.ledger
    for bad in (
        dict(payload, credential="private"),
        dict(payload, schema_version=True),
        dict(payload, schema_version=1),
    ):
        with pytest.raises(NativeIdentityError, match="INVALID_LEDGER_PAYLOAD"):
            owned_ledger_from_payload(bad)
    bad = copy.deepcopy(payload)
    bad["pending"][0]["submission_phase"] = "APPROVED"
    with pytest.raises(NativeIdentityError, match="INVALID_LEDGER_PAYLOAD"):
        owned_ledger_from_payload(bad)


def test_pure_plan_and_ledger_helpers_never_discover_credentials_or_construct_clients(monkeypatch, journal):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure helper attempted native discovery")

    monkeypatch.setattr(NativeGCPIdentity, "_native", forbidden)
    extra = {"role": PERMISSIONS[0]["role"], "resource": SECOND}
    digest = desired_owned_union_sha256(CONTEXT, [*PERMISSIONS, extra], service_account_uids=(UID,))
    assert digest == desired_owned_union_sha256(CONTEXT, [extra, *PERMISSIONS, extra], service_account_uids=(UID,))
    validate_owned_ledger(CONTEXT, journal.ledger)
    assert not journal.wire.calls


def test_pure_union_digest_is_the_exact_actual_submission_digest(journal):
    digest = desired_owned_union_sha256(CONTEXT, PERMISSIONS, service_account_uids=(UID,))
    journal.call()
    assert all(row.intent.desired_union_sha256 == digest for row in journal.hooks)
    assert digest != desired_owned_union_sha256(CONTEXT, [], service_account_uids=())


@pytest.mark.parametrize(
    "permissions,uids",
    [
        ([{"role": "roles/aiplatform.user", "resource": SECOND}], (UID,)),
        ([{"role": PERMISSIONS[0]["role"], "resource": "projects/foreign/locations/us-central1/endpoints/1"}], (UID,)),
        (PERMISSIONS, ()),
        (PERMISSIONS, (UID, UID)),
        (PERMISSIONS, ("invalid",)),
    ],
)
def test_pure_union_helper_refuses_invalid_native_grant_plan(permissions, uids):
    with pytest.raises(NativeIdentityError):
        desired_owned_union_sha256(CONTEXT, permissions, service_account_uids=uids)


def test_pure_ledger_validator_confines_context_resource_and_grants():
    grant = OwnedGrant(PERMISSIONS[0]["role"], f"serviceAccount:{CONTEXT.email}")
    valid = OwnedGrantLedger(CONTEXT.fingerprint, (PolicyOwnership(SECOND, (grant,)),))
    validate_owned_ledger(CONTEXT, valid)
    for invalid in (
        replace(valid, context_sha256="f" * 64),
        replace(valid, policies=(PolicyOwnership(SECOND, (replace(grant, member="user:foreign@example.invalid"),)),)),
        replace(valid, policies=(PolicyOwnership("projects/foreign/locations/us-central1/endpoints/1", (grant,)),)),
    ):
        with pytest.raises(NativeIdentityError):
            validate_owned_ledger(CONTEXT, invalid)


def test_unsent_withdrawal_is_known_no_setter_and_same_etag_resumes_same_identity(journal):
    def withdraw(request, receipt):
        journal.current = False
        return receipt

    journal.mode = withdraw
    with pytest.raises(NativeIdentityReconciliationError, match="CURRENT_AUTHORITY_WITHDRAWN") as failure:
        journal.call()
    original_id = journal.ledger.pending[0].submission_id
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNSENT
    assert not failure.value.receipt.steps[0].transport_invoked and not journal.wire.setter_entries
    journal.mode, journal.current = None, True
    journal.call()
    assert journal.hooks[1].intent.submission_id == original_id


def test_unsent_unchanged_body_with_changed_etag_refuses_before_setter(journal):
    journal.mode = lambda request, receipt: (setattr(journal, "current", False), receipt)[1]
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    resource = journal.ledger.pending[0].resource
    journal.wire.policies[resource].etag = b"changed-etag-same-policy"
    journal.mode, journal.current = None, True
    with pytest.raises(NativeIdentityReconciliationError, match="UNSENT_POLICY_ETAG_CHANGED"):
        journal.call()
    assert not journal.wire.setter_entries


def test_journal_identity_change_at_sent_commit_is_uncertain_without_setter(journal):
    journal.mode = lambda request, receipt: (
        replace(receipt, journal_id="12345678-0000-4000-8000-000000000008")
        if request.intent.submission_phase == PolicySubmissionPhase.SENT
        else receipt
    )
    with pytest.raises(NativeIdentityReconciliationError, match="SUBMISSION_JOURNAL_CHANGED") as failure:
        journal.call()
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNKNOWN
    assert not journal.wire.setter_entries


@pytest.mark.parametrize("withdraw", ["role", "gsa"])
def test_native_authority_withdrawal_after_unsent_never_commits_sent(journal, withdraw):
    def changed(request, receipt):
        if withdraw == "role":
            journal.wire.role.included_permissions.append("aiplatform.endpoints.update")
        else:
            journal.wire.account.description = "foreign-owner"
        return receipt

    journal.mode = changed
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    assert [row.intent.submission_phase for row in journal.hooks] == [PolicySubmissionPhase.UNSENT]
    assert not journal.wire.setter_entries


def test_readback_failure_retains_sent_unknown_after_actual_setter(journal):
    def after_call(name):
        if name == "SetIamPolicy":
            journal.wire.error = PermissionDenied

    journal.wire.after_call = after_call
    with pytest.raises(NativeIdentityReconciliationError) as failure:
        journal.call()
    assert journal.wire.writes == 1
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNKNOWN
    assert journal.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
    journal.wire.error, journal.wire.after_call = None, None
    journal.call()
    assert journal.wire.writes == 2  # the observed first resource was not sent again


def test_lost_observed_persistence_reply_preserves_acknowledged_sent_snapshot(journal):
    original = journal.persist

    def persist(ledger):
        original(ledger)
        if journal.wire.writes and not ledger.pending:
            raise RuntimeError("private-persistence-canary")

    journal.persist = persist
    with pytest.raises(NativeIdentityReconciliationError, match="JOURNAL_PERSISTENCE_UNCONFIRMED") as failure:
        journal.call()
    assert failure.value.receipt.steps[0].state == PolicyStepState.OBSERVED
    assert failure.value.receipt.ledger.pending[0].submission_phase == PolicySubmissionPhase.SENT
    assert not journal.ledger.pending  # commit may have succeeded; receipt cannot assert journal bytes
    assert "private-persistence-canary" not in str(failure.value)


@pytest.mark.parametrize("value", [False, True, "accepted"])
def test_invalid_checkpoint_refuses_before_adc_or_sdk(monkeypatch, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("ADC or client construction before admission")

    monkeypatch.setattr(NativeGCPIdentity, "_native", forbidden)
    with pytest.raises(NativeIdentityReconciliationError, match="CURRENT_ADMISSION_UNCONFIRMED") as failure:
        NativeGCPIdentity(CONTEXT).reconcile(
            PERMISSIONS,
            service_account_uids=(UID,),
            ledger=OwnedGrantLedger(CONTEXT.fingerprint),
            checkpoint=lambda: value,
            persist=lambda ledger: None,
            submission_hook=lambda submission: True,
        )
    assert failure.value.receipt.steps == ()


@pytest.mark.parametrize("failure_mode", ["none", "native", "journal"])
def test_strict_actual_factory_does_not_log_native_or_journal_payloads(monkeypatch, caplog, failure_mode):
    import google.auth
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import (
        EndpointServiceGrpcTransport,
    )
    from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
    from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

    wire = SubmissionWire()
    state = Journal(wire)
    hosts = []

    def channel(host, **kwargs):
        hosts.append(host)
        return wire

    for transport in (ProjectsGrpcTransport, IAMGrpcTransport, EndpointServiceGrpcTransport):
        monkeypatch.setattr(transport, "create_channel", channel)
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (AnonymousCredentials(), CONTEXT.project_id))
    if failure_mode == "native":
        wire.before_set = lambda request: (_ for _ in ()).throw(ServiceUnavailable("synthetic-private-canary"))
    elif failure_mode == "journal":
        state.mode = lambda request, receipt: (_ for _ in ()).throw(RuntimeError("synthetic-private-canary"))
    caplog.set_level("DEBUG")
    with NativeGCPIdentity(CONTEXT) as driver:
        if failure_mode == "none":
            assert not state.call(driver=driver).workload_ready
        else:
            with pytest.raises(NativeIdentityReconciliationError) as failure:
                state.call(driver=driver)
            assert "synthetic-private-canary" not in str(failure.value)
    assert hosts == [
        "cloudresourcemanager.googleapis.com",
        "iam.googleapis.com",
        f"{CONTEXT.region}-aiplatform.googleapis.com",
    ]
    assert wire.closed == 3
    for marker in ("synthetic-private-canary", CONTEXT.email, "foreign@example.invalid", JOURNAL_ID):
        assert marker not in caplog.text


@pytest.mark.parametrize("value", [False, True])
def test_invalid_checkpoint_after_held_sdk_response_refuses_without_submission(journal, value):
    entered, release = threading.Event(), threading.Event()
    results = []
    current = [None]

    def after_call(name):
        if name == "GetProject":
            entered.set()
            assert release.wait(timeout=3)

    journal.wire.after_call = after_call
    journal.checkpoint = lambda: current[0]

    def run():
        try:
            journal.call()
        except NativeIdentityReconciliationError as failure:
            results.append(failure)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=2)
        current[0] = value
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive() and len(results) == 1
    assert str(results[0]) == "CURRENT_ADMISSION_UNCONFIRMED"
    assert not journal.hooks and not journal.wire.setter_entries


def test_unsent_foreign_after_policy_never_becomes_owned(journal):
    journal.mode = lambda request, receipt: (setattr(journal, "current", False), receipt)[1]
    with pytest.raises(NativeIdentityReconciliationError):
        journal.call()
    pending = journal.ledger.pending[0]
    assert pending.submission_phase == PolicySubmissionPhase.UNSENT
    assert not journal.wire.setter_entries and not journal.ledger.policies
    external = journal.wire.policies[pending.resource]
    for grant in pending.owned_after:
        external.bindings.add(role=grant.role, members=[grant.member])
    external.etag = b"foreign-writer-etag"
    assert NativeGCPIdentity._policy_hash(external) == pending.after_sha256
    original_ledger = journal.ledger
    journal.mode, journal.current = None, True
    with pytest.raises(NativeIdentityReconciliationError, match="UNSENT_POLICY_CONFLICT") as failure:
        journal.call()
    assert journal.ledger == original_ledger == failure.value.receipt.ledger
    assert not failure.value.receipt.ledger.policies
    assert failure.value.receipt.steps[0].state == PolicyStepState.UNSENT
    assert not journal.wire.setter_entries and not journal.wire.writes
    assert NativeGCPIdentity._policy_hash(journal.wire.policies[pending.resource]) == pending.after_sha256
