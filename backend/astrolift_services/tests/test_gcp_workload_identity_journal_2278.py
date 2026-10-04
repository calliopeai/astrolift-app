"""Real PostgreSQL durability/mutex controls; no cloud operation is performed."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from queue import Queue
from threading import Event
from uuid import uuid4

import pytest
from _sdk.cloud_credentials import CloudCredential
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.models.deletion import ProtectedError
from gcp.identity_owned import (
    NativeIdentityContext,
    OwnedGrant,
    OwnedGrantLedger,
    PolicyIntent,
    PolicyOwnership,
    PolicyReconcileReceipt,
    PolicyStepReceipt,
    PolicyStepState,
    PolicySubmission,
    PolicySubmissionPhase,
    owned_ledger_payload,
)

from astrolift_clusters.models import ProviderPlugin
from astrolift_services.gcp_workload_identity_journal import (
    JournalError,
    JournalTarget,
    KSAIdentity,
    OperationIdentity,
    journal_mutex,
)
from astrolift_services.models import GCPWorkloadIdentityJournal
from core.tests.utils.scope_world import ScopeWorld, make_cluster

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world():
    world = ScopeWorld("gcp-journal")
    cluster = make_cluster(world, "gcp-journal")
    provider = cluster.provider_plugin
    provider.slug = "gcp"
    provider.save()

    def target_guid():
        return str(uuid4())

    native = NativeIdentityContext(
        str(world.org.guid),
        str(world.medops_app.guid),
        str(cluster.guid),
        "native-project",
        "12345678",
        "us-central1",
        "app-identity",
        "123456789",
        CloudCredential("gcp", declared_account="native-project"),
    )
    world.cluster = cluster
    world.provider = provider
    world.target = JournalTarget(
        str(world.org.guid),
        str(world.medops_app.guid),
        str(cluster.guid),
        str(provider.guid),
        "native-project",
        "12345678",
        "us-central1",
        "app-identity",
        "123456789",
        "us-central1-a",
        "owned-gke",
        "native-cluster-001",
        "c" * 64,
        native.fingerprint,
        (KSAIdentity(target_guid(), "app-namespace", "app-identity", target_guid(), target_guid()),),
    )
    world.operation = OperationIdentity(
        str(uuid4()), "workflow-identity", "execution-identity", 1, "d" * 64, "e" * 64
    )
    return world


def admitted(context):
    assert context.app.organization_id == context.organization.pk
    assert context.provider.slug == "gcp"
    assert context.target.app_id == str(context.app.guid)


def intent(world, *, phase=PolicySubmissionPhase.UNSENT, submission_id=None):
    return PolicyIntent(
        "projects/12345678/locations/us-central1/endpoints/123",
        "1" * 64,
        "2" * 64,
        (
            OwnedGrant(
                "projects/native-project/roles/EndpointPredict",
                "serviceAccount:app-identity@native-project.iam.gserviceaccount.com",
            ),
        ),
        submission_id or str(uuid4()),
        phase,
        "3" * 64,
        world.operation.desired_union_sha256,
    )


def submit(store, reservation, world):
    planned = intent(world)
    ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
    first = store.commit_submission(
        reservation, PolicySubmission(world.target.context_sha256, planned, ledger), checkpoint=admitted
    )
    sent = replace(planned, submission_phase=PolicySubmissionPhase.SENT)
    ledger = replace(ledger, pending=(sent,))
    second = store.commit_submission(
        reservation, PolicySubmission(world.target.context_sha256, sent, ledger), checkpoint=admitted
    )
    assert second.journal_version > first.journal_version
    return ledger, sent, second


def independent_row(guid):
    db = connection.Database.connect(**connection.get_connection_params())
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT state, ledger, version, operation_id FROM astrolift_services_gcpworkloadidentityjournal WHERE guid=%s",
                [guid],
            )
            row = cursor.fetchone()
            return (row[0], json.loads(row[1]) if isinstance(row[1], str) else row[1], *row[2:])
    finally:
        db.close()


def test_reserved_commit_visible_separate_connection_and_provider_rollback(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        assert independent_row(reservation.journal_id)[0] == "RESERVED"
        ledger, sent, receipt = submit(store, reservation, world)
        assert independent_row(reservation.journal_id)[:3] == (
            "SENT",
            json.loads(json.dumps(owned_ledger_payload(ledger))),
            receipt.journal_version,
        )
        with pytest.raises(RuntimeError, match="provider result lost"):
            with transaction.atomic():
                raise RuntimeError("provider result lost")
        assert independent_row(reservation.journal_id)[0] == "SENT"
    with journal_mutex(world.target) as store:
        assert store.reserve(world.operation, checkpoint=admitted) == reservation
        assert store.read(reservation, checkpoint=admitted) == ledger
        with pytest.raises(JournalError, match="SENT_SUBMISSION_UNRESOLVED"):
            store.commit_submission(
                reservation, PolicySubmission(world.target.context_sha256, sent, ledger), checkpoint=admitted
            )
        with pytest.raises(JournalError, match="UNRESOLVED_OPERATION"):
            store.reserve(
                replace(world.operation, operation_id=str(uuid4()), desired_revision=2),
                checkpoint=admitted,
                expected_version=receipt.journal_version,
            )


def test_observed_after_hash_receipt_resolves_own_intent_and_allows_next_revision(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        ledger, sent, _ = submit(store, reservation, world)
        observed = replace(ledger, policies=(PolicyOwnership(sent.resource, sent.owned_after),), pending=())
        store.persist_ledger(reservation, observed, checkpoint=admitted)
        step = PolicyStepReceipt(
            sent.submission_id,
            sent.resource,
            PolicySubmission(world.target.context_sha256, sent, ledger).submission_sha256,
            PolicyStepState.OBSERVED,
            True,
        )
        store.record_result(
            reservation, PolicyReconcileReceipt(observed, (step,)), checkpoint=admitted, completed=True
        )
        row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
        assert row.state == row.State.OBSERVED
        assert row.observed_revision == 1
        assert row.observed_at is not None
        new = store.reserve(
            replace(
                world.operation, operation_id=str(uuid4()), desired_revision=2, execution_id="execution-2"
            ),
            checkpoint=admitted,
            expected_version=row.version,
        )
        assert new.generation == reservation.generation + 1
        assert new.nonce != reservation.nonce
        assert store.read(new, checkpoint=admitted).policies == observed.policies
        with pytest.raises(JournalError, match="STALE_RESERVATION"):
            store.read(reservation, checkpoint=admitted)


@pytest.mark.parametrize("action", ["reserve", "read", "submit", "persist", "result"])
def test_enclosing_atomic_refused_before_checkpoint_or_provider(world, action):
    calls = []
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
        actions = {
            "reserve": lambda: store.reserve(world.operation, checkpoint=calls.append),
            "read": lambda: store.read(reservation, checkpoint=calls.append),
            "submit": lambda: store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, planned, ledger),
                checkpoint=calls.append,
            ),
            "persist": lambda: store.persist_ledger(reservation, ledger, checkpoint=calls.append),
            "result": lambda: store.record_result(
                reservation,
                PolicyReconcileReceipt(OwnedGrantLedger(world.target.context_sha256)),
                checkpoint=calls.append,
            ),
        }
        with transaction.atomic(), pytest.raises(JournalError, match="ENCLOSING_TRANSACTION_REFUSED"):
            actions[action]()
        assert calls == []
        assert GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id).state == "RESERVED"
    with (
        transaction.atomic(),
        pytest.raises(JournalError, match="ENCLOSING_TRANSACTION_REFUSED"),
        journal_mutex(world.target),
    ):
        pytest.fail("mutex must not be acquired")


def test_nonblocking_mutex_concurrency_has_single_effect_owner(world):
    with journal_mutex(world.target) as first:
        reservation = first.reserve(world.operation, checkpoint=admitted)

        def rival():
            close_old_connections()
            try:
                with journal_mutex(world.target):
                    return "entered"
            except JournalError as error:
                return str(error)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(rival).result(timeout=5) == "JOURNAL_BUSY"
        assert independent_row(reservation.journal_id)[0] == "RESERVED"
        with first._mutex_connection.cursor() as cursor:
            cursor.execute(
                "SELECT relation::regclass::text FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='relation' AND relation=%s::regclass",
                ["astrolift_services_gcpworkloadidentityjournal"],
            )
            assert cursor.fetchall() == []
    with journal_mutex(world.target):
        pass
    with pytest.raises(JournalError, match="MUTEX_NOT_HELD"):
        first.read(reservation, checkpoint=admitted)


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", str(uuid4())),
        ("app_id", str(uuid4())),
        ("cluster_id", str(uuid4())),
        ("provider_id", str(uuid4())),
        ("project_id", "other-project"),
        ("project_number", "98765432"),
        ("gsa_id", "other-identity"),
        ("gsa_unique_id", "987654321"),
        ("gke_cluster_id", "replacement"),
        ("context_sha256", "f" * 64),
        ("credential_declaration_sha256", "b" * 64),
    ],
)
def test_immutable_original_target_cannot_be_substituted(world, field, value):
    with journal_mutex(world.target) as store:
        store.reserve(world.operation, checkpoint=admitted)
    changed = replace(world.target, **{field: value})
    with (
        journal_mutex(changed) as store,
        pytest.raises(JournalError, match="CURRENT_OWNER_UNAVAILABLE|ORIGINAL_TARGET_CHANGED"),
    ):
        store.reserve(world.operation, checkpoint=admitted)
    assert GCPWorkloadIdentityJournal.objects.count() == 1


def test_original_ksa_uid_cannot_be_replaced(world):
    with journal_mutex(world.target) as store:
        store.reserve(world.operation, checkpoint=admitted)
    changed = replace(
        world.target,
        ksa_identities=(replace(world.target.ksa_identities[0], service_account_uid=str(uuid4())),),
    )
    with journal_mutex(changed) as store, pytest.raises(JournalError, match="ORIGINAL_TARGET_CHANGED"):
        store.reserve(world.operation, checkpoint=admitted)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other-workflow"),
        ("execution_id", "other-execution"),
        ("desired_revision", 2),
        ("desired_union_sha256", "f" * 64),
        ("authority_reference_sha256", "f" * 64),
    ],
)
def test_same_operation_uuid_does_not_rebind_acceptance(world, field, value):
    with journal_mutex(world.target) as store:
        store.reserve(world.operation, checkpoint=admitted)
        with pytest.raises(JournalError, match="OPERATION_IDENTITY_CHANGED"):
            store.reserve(replace(world.operation, **{field: value}), checkpoint=admitted)


@pytest.mark.parametrize("result", [True, False, "accepted"])
def test_checkpoint_is_mandatory_explicit_and_never_boolean_authority(world, result):
    with journal_mutex(world.target) as store:
        with pytest.raises(JournalError, match="CURRENT_AUTHORITY_REQUIRED"):
            store.reserve(world.operation, checkpoint=None)
        with pytest.raises(JournalError, match="CURRENT_AUTHORITY_UNCONFIRMED"):
            store.reserve(world.operation, checkpoint=lambda _: result)
    assert not GCPWorkloadIdentityJournal.objects.exists()


def test_current_source_withdrawal_after_last_parent_wait_prevents_commit(world):
    held = Event()
    release = Event()
    pids = Queue()

    def holder():
        close_old_connections()
        try:
            with transaction.atomic():
                row = ProviderPlugin._unscoped.select_for_update().get(pk=world.provider.pk)
                held.set()
                assert release.wait(5)
                row.is_enabled = False
                row.save(update_fields=["is_enabled"])
        finally:
            close_old_connections()

    def worker():
        close_old_connections()
        try:

            def fresh(context):
                admitted(context)
                if not context.provider.is_enabled:
                    raise JournalError("CURRENT_SOURCE_WITHDRAWN")

            with journal_mutex(world.target) as store:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                return store.reserve(world.operation, checkpoint=fresh)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        lock = pool.submit(holder)
        assert held.wait(5)
        pending = pool.submit(worker)
        worker_pid = pids.get(timeout=5)
        deadline = time.monotonic() + 5
        waiting = False
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [worker_pid])
                waiting = cursor.fetchone()[0] > 0
            if waiting:
                break
            time.sleep(0.01)
        release.set()
        assert waiting
        lock.result(timeout=5)
        with pytest.raises(JournalError, match="CURRENT_SOURCE_WITHDRAWN"):
            pending.result(timeout=5)
    assert not GCPWorkloadIdentityJournal.objects.exists()


def test_sent_commit_failure_does_not_acknowledge_effect(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
        store.commit_submission(
            reservation, PolicySubmission(world.target.context_sha256, planned, ledger), checkpoint=admitted
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE FUNCTION gcp_journal_commit_refuse() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.state='SENT' THEN RAISE EXCEPTION 'fixed commit refusal'; END IF; RETURN NEW; END $$"
            )
            cursor.execute(
                "CREATE CONSTRAINT TRIGGER gcp_journal_commit_refuse AFTER UPDATE ON astrolift_services_gcpworkloadidentityjournal DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION gcp_journal_commit_refuse()"
            )
        try:
            sent = replace(planned, submission_phase=PolicySubmissionPhase.SENT)
            with pytest.raises(DatabaseError):
                store.commit_submission(
                    reservation,
                    PolicySubmission(world.target.context_sha256, sent, replace(ledger, pending=(sent,))),
                    checkpoint=admitted,
                )
            assert independent_row(reservation.journal_id)[0] == "UNSENT"
            assert store.read(reservation, checkpoint=admitted) == ledger
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DROP TRIGGER gcp_journal_commit_refuse ON astrolift_services_gcpworkloadidentityjournal"
                )
                cursor.execute("DROP FUNCTION gcp_journal_commit_refuse()")


def test_unknown_receipt_no_takeover_and_false_observation_refused(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        ledger, sent, _ = submit(store, reservation, world)
        sha = PolicySubmission(world.target.context_sha256, sent, ledger).submission_sha256
        unknown = PolicyStepReceipt(sent.submission_id, sent.resource, sha, PolicyStepState.UNKNOWN, False)
        store.record_result(reservation, PolicyReconcileReceipt(ledger, (unknown,)), checkpoint=admitted)
        assert independent_row(reservation.journal_id)[0] == "UNKNOWN"
        with pytest.raises(JournalError, match="OBSERVED_OWNERSHIP_REQUIRED"):
            store.record_result(
                reservation,
                PolicyReconcileReceipt(ledger, (replace(unknown, state=PolicyStepState.OBSERVED),)),
                checkpoint=admitted,
            )
        with pytest.raises(JournalError, match="SENT_SUBMISSION_UNRESOLVED"):
            store.commit_submission(
                reservation, PolicySubmission(world.target.context_sha256, sent, ledger), checkpoint=admitted
            )
        with pytest.raises(JournalError, match="OBSERVED_OWNERSHIP_REQUIRED"):
            store.persist_ledger(reservation, replace(ledger, pending=()), checkpoint=admitted)


@pytest.mark.parametrize("change", ["resource", "member", "role", "duplicate", "secret_field"])
def test_ledger_cannot_store_foreign_resource_or_arbitrary_payload(world, change):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        if change == "resource":
            planned = replace(planned, resource=planned.resource.replace("12345678", "98765432"))
        elif change == "member":
            planned = replace(
                planned,
                owned_after=(replace(planned.owned_after[0], member="serviceAccount:other@example.invalid"),),
            )
        elif change == "role":
            planned = replace(planned, owned_after=(replace(planned.owned_after[0], role="roles/owner"),))
        elif change == "duplicate":
            planned = replace(planned, owned_after=planned.owned_after * 2)
        ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
        if change == "secret_field":
            ledger = {"token": "private-marker"}
            with pytest.raises((AttributeError, JournalError)):
                store.persist_ledger(reservation, ledger, checkpoint=admitted)
        else:
            with pytest.raises(JournalError, match="FOREIGN_LEDGER|INVALID_LEDGER"):
                store.commit_submission(
                    reservation,
                    PolicySubmission(world.target.context_sha256, planned, ledger),
                    checkpoint=admitted,
                )
        assert independent_row(reservation.journal_id)[0] == "RESERVED"


def test_protected_parents_live_uniqueness_and_soft_delete_recovery_guard(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
    row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
    with pytest.raises(ProtectedError):
        world.medops_app.delete()
    with pytest.raises(ValueError, match="RETIREMENT_REQUIRES_REVIEW"):
        row.soft_delete()
    with pytest.raises(ValueError, match="RESTORE_REQUIRES_REVIEW"):
        row.restore()
    row.pk = None
    row.guid = uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        row.save(force_insert=True)


def test_retired_journal_never_silently_resets_owned_grants(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
    from django.utils import timezone

    GCPWorkloadIdentityJournal._unscoped.filter(guid=reservation.journal_id).update(deleted_at=timezone.now())
    with (
        journal_mutex(world.target) as store,
        pytest.raises(JournalError, match="RETIRED_JOURNAL_REQUIRES_REVIEW"),
    ):
        store.reserve(world.operation, checkpoint=admitted)


@pytest.mark.parametrize("change", ["app_org", "team_org", "project_org", "provider"])
def test_current_canonical_parent_owner_change_refuses_old_target(world, change):
    foreign = ScopeWorld("foreign-gcp-journal")
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
    if change == "app_org":
        type(world.medops_app)._unscoped.filter(pk=world.medops_app.pk).update(organization=foreign.org)
    elif change == "team_org":
        type(world.medops)._unscoped.filter(pk=world.medops.pk).update(organization=foreign.org)
    elif change == "project_org":
        type(world.medops_project)._unscoped.filter(pk=world.medops_project.pk).update(
            organization=foreign.org
        )
    else:
        other = ProviderPlugin.objects.create(name="Other", slug="other-gcp", plugin_version="0.0.1")
        type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(provider_plugin=other)
    calls = []
    with (
        journal_mutex(world.target) as store,
        pytest.raises(JournalError, match="CURRENT_OWNER_UNAVAILABLE|CURRENT_TARGET_CHANGED"),
    ):
        store.read(reservation, checkpoint=calls.append)
    assert calls == []
    assert independent_row(reservation.journal_id)[0] == "RESERVED"


def test_new_unsent_cannot_enter_via_persist_or_direct_sent_hook(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
        with pytest.raises(JournalError, match="SUBMISSION_HOOK_REQUIRED"):
            store.persist_ledger(reservation, ledger, checkpoint=admitted)
        sent = replace(planned, submission_phase=PolicySubmissionPhase.SENT)
        with pytest.raises(JournalError, match="UNSENT_RESERVATION_REQUIRED"):
            store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, sent, replace(ledger, pending=(sent,))),
                checkpoint=admitted,
            )
        assert independent_row(reservation.journal_id)[0] == "RESERVED"


def test_global_unresolved_sent_blocks_other_resource_before_submission(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        ledger, _, _ = submit(store, reservation, world)
        planned = replace(intent(world), resource="projects/12345678/locations/us-central1/endpoints/456")
        next_ledger = replace(ledger, pending=(*ledger.pending, planned))
        with pytest.raises(JournalError, match="SENT_SUBMISSION_UNRESOLVED"):
            store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, planned, next_ledger),
                checkpoint=admitted,
            )
        assert store.read(reservation, checkpoint=admitted) == ledger


def test_different_pending_id_and_acceptance_digest_cannot_reuse_unsent(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
        store.commit_submission(
            reservation, PolicySubmission(world.target.context_sha256, planned, ledger), checkpoint=admitted
        )
        changed = replace(planned, submission_id=str(uuid4()))
        with pytest.raises(JournalError, match="SUBMISSION_IDENTITY_CHANGED"):
            store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, changed, replace(ledger, pending=(changed,))),
                checkpoint=admitted,
            )
        changed = replace(planned, desired_union_sha256="f" * 64)
        with pytest.raises(JournalError, match="SUBMISSION_IDENTITY_CHANGED"):
            store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, changed, replace(ledger, pending=(changed,))),
                checkpoint=admitted,
            )
        assert store.read(reservation, checkpoint=admitted) == ledger


def test_removal_obligation_prevents_new_operation_and_observed_revision(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        pending_ledger, sent, _ = submit(store, reservation, world)
        owned = PolicyOwnership(sent.resource, sent.owned_after)
        ledger = replace(pending_ledger, policies=(owned,), pending=())
        store.persist_ledger(reservation, ledger, checkpoint=admitted)
        obligation = owned
        ledger = replace(ledger, removals=(obligation,))
        store.persist_ledger(reservation, ledger, checkpoint=admitted)
        store.record_result(reservation, PolicyReconcileReceipt(ledger), checkpoint=admitted)
        row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
        assert row.observed_revision == 0 and row.observed_at is None
        with pytest.raises(JournalError, match="UNRESOLVED_OPERATION"):
            store.reserve(
                replace(world.operation, operation_id=str(uuid4()), desired_revision=2),
                expected_version=row.version,
                checkpoint=admitted,
            )
        assert store.read(reservation, checkpoint=admitted).removals == (obligation,)


@pytest.fixture
def native_bridge(world):
    from gcp.identity_owned import NativeGCPIdentity, desired_owned_union_sha256

    from providers.tests.gcp.test_identity_owned_2278 import CONTEXT, PERMISSIONS, UID
    from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

    context = replace(
        CONTEXT,
        organization_id=world.target.organization_id,
        app_id=world.target.app_id,
        cluster_id=world.target.cluster_id,
    )
    target = replace(
        world.target,
        project_id=context.project_id,
        project_number=context.project_number,
        region=context.region,
        gsa_id=context.service_account_id,
        gsa_unique_id=context.service_account_unique_id,
        context_sha256=context.fingerprint,
        ksa_identities=(replace(world.target.ksa_identities[0], service_account_uid=UID),),
    )
    operation = replace(
        world.operation,
        desired_union_sha256=desired_owned_union_sha256(context, PERMISSIONS, service_account_uids=(UID,)),
    )
    wire = SubmissionWire()
    wire.account.description = context.owner_description
    world.target, world.operation, world.wire, world.context = target, operation, wire, context

    def reconcile(store, reservation, hook=None):
        driver = NativeGCPIdentity(context, clients=wire.clients)
        return driver.reconcile(
            PERMISSIONS,
            service_account_uids=(UID,),
            ledger=store.read(reservation, checkpoint=admitted),
            checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted),
            persist=lambda ledger: store.persist_ledger(reservation, ledger, checkpoint=admitted),
            submission_hook=hook
            or (lambda submission: store.commit_submission(reservation, submission, checkpoint=admitted)),
        )

    world.reconcile = reconcile
    return world


def test_actual_sdk_setter_sees_committed_sent_without_parent_row_transaction(native_bridge):
    world = native_bridge
    observed = []
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def before_set(request):
            assert not connection.in_atomic_block
            state, ledger, version, _ = independent_row(reservation.journal_id)
            assert state == "SENT"
            pending = next(row for row in ledger["pending"] if row["resource"] == request.resource)
            assert pending["submission_phase"] == "SENT"
            observed.append((pending["submission_id"], version, request.resource))

        world.wire.before_set = before_set
        result = world.reconcile(store, reservation)
        assert world.wire.writes == len(observed) == 2
        assert result.receipt is not None
        store.record_result(reservation, result.receipt, checkpoint=admitted, completed=True)
        row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
        assert row.state == "OBSERVED" and row.observed_revision == 1
        assert not result.workload_ready
        second = world.reconcile(store, reservation)
        store.record_result(reservation, second.receipt, checkpoint=admitted, completed=True)
        assert world.wire.writes == 2


def test_actual_sdk_lost_send_reply_retains_unknown_and_recovers_only_after_hash(native_bridge):
    from gcp.identity_owned import NativeIdentityReconciliationError

    world = native_bridge
    world.wire.lost_reply = True
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        with pytest.raises(NativeIdentityReconciliationError) as caught:
            world.reconcile(store, reservation)
        assert world.wire.writes == 1
        store.record_result(reservation, caught.value.receipt, checkpoint=admitted)
        assert independent_row(reservation.journal_id)[0] == "UNKNOWN"
    with journal_mutex(world.target) as store:
        assert store.reserve(world.operation, checkpoint=admitted) == reservation
        result = world.reconcile(store, reservation)
        store.record_result(reservation, result.receipt, checkpoint=admitted, completed=True)
        assert world.wire.writes == 2
        assert independent_row(reservation.journal_id)[0] == "OBSERVED"


def test_actual_sdk_uncertain_sent_acknowledgement_has_zero_setters_and_no_resend(native_bridge):
    from gcp.identity_owned import NativeIdentityReconciliationError

    world = native_bridge
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def lost_ack(submission):
            receipt = store.commit_submission(reservation, submission, checkpoint=admitted)
            if submission.intent.submission_phase == PolicySubmissionPhase.SENT:
                raise RuntimeError("simulated lost database acknowledgement")
            return receipt

        with pytest.raises(NativeIdentityReconciliationError) as caught:
            world.reconcile(store, reservation, hook=lost_ack)
        assert world.wire.writes == world.wire.setter_entries == 0
        assert independent_row(reservation.journal_id)[0] == "SENT"
        assert any(
            step.state == PolicyStepState.UNKNOWN and not step.transport_invoked
            for step in caught.value.receipt.steps
        )
        with pytest.raises(JournalError, match="RESULT_IDENTITY_CHANGED"):
            store.record_result(reservation, caught.value.receipt, checkpoint=admitted)
    with journal_mutex(world.target) as store:
        with pytest.raises(NativeIdentityReconciliationError, match="SENT_SUBMISSION_UNRESOLVED"):
            world.reconcile(store, reservation)
        assert world.wire.writes == world.wire.setter_entries == 0


def test_actual_sdk_checkpoints_use_committed_current_source_between_steps(native_bridge):
    from gcp.identity_owned import NativeIdentityReconciliationError

    world = native_bridge
    calls = []
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def hook(submission):
            receipt = store.commit_submission(reservation, submission, checkpoint=admitted)
            calls.append(submission.intent.submission_phase)
            if submission.intent.submission_phase == PolicySubmissionPhase.UNSENT:
                ProviderPlugin._unscoped.filter(pk=world.provider.pk).update(is_enabled=False)
            return receipt

        def current_source(context):
            if not context.provider.is_enabled:
                raise JournalError("CURRENT_SOURCE_WITHDRAWN")

        from gcp.identity_owned import NativeGCPIdentity

        from providers.tests.gcp.test_identity_owned_2278 import PERMISSIONS, UID

        driver = NativeGCPIdentity(world.context, clients=world.wire.clients)
        with pytest.raises(NativeIdentityReconciliationError):
            driver.reconcile(
                PERMISSIONS,
                service_account_uids=(UID,),
                ledger=store.read(reservation, checkpoint=admitted),
                checkpoint=lambda: store.validate_current(reservation, checkpoint=current_source),
                persist=lambda ledger: store.persist_ledger(reservation, ledger, checkpoint=current_source),
                submission_hook=hook,
            )
        assert calls == [PolicySubmissionPhase.UNSENT]
        assert world.wire.writes == world.wire.setter_entries == 0
        assert independent_row(reservation.journal_id)[0] == "UNSENT"


def test_unsent_native_coincidence_never_adopts_foreign_equivalent_grants(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
        store.commit_submission(
            reservation, PolicySubmission(world.target.context_sha256, planned, ledger), checkpoint=admitted
        )
        coincident = replace(
            ledger, pending=(), policies=(PolicyOwnership(planned.resource, planned.owned_after),)
        )
        with pytest.raises(JournalError, match="UNSENT_OWNERSHIP_REFUSED"):
            store.persist_ledger(reservation, coincident, checkpoint=admitted)
        assert store.read(reservation, checkpoint=admitted) == ledger
        assert independent_row(reservation.journal_id)[0] == "UNSENT"


def test_canonical_foreign_grant_cannot_be_claimed_without_sent_intent(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        equivalent = PolicyOwnership(intent(world).resource, intent(world).owned_after)
        with pytest.raises(JournalError, match="OWNERSHIP_INTENT_REQUIRED"):
            store.persist_ledger(
                reservation,
                OwnedGrantLedger(world.target.context_sha256, policies=(equivalent,)),
                checkpoint=admitted,
            )
        with pytest.raises(JournalError, match="REMOVAL_OWNERSHIP_REQUIRED"):
            store.persist_ledger(
                reservation,
                OwnedGrantLedger(world.target.context_sha256, removals=(equivalent,)),
                checkpoint=admitted,
            )
        assert not store.read(reservation, checkpoint=admitted).policies


def test_actual_sdk_cleanup_preserves_owned_removal_obligations_across_revision(native_bridge):
    from gcp.identity_owned import NativeGCPIdentity, desired_owned_union_sha256

    world = native_bridge
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        result = world.reconcile(store, reservation)
        store.record_result(reservation, result.receipt, checkpoint=admitted, completed=True)
        row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
        operation = replace(
            world.operation,
            operation_id=str(uuid4()),
            execution_id="execution-remove",
            desired_revision=2,
            desired_union_sha256=desired_owned_union_sha256(world.context, [], service_account_uids=()),
        )
        next_reservation = store.reserve(operation, checkpoint=admitted, expected_version=row.version)
        driver = NativeGCPIdentity(world.context, clients=world.wire.clients)
        result = driver.reconcile(
            [],
            service_account_uids=(),
            ledger=store.read(next_reservation, checkpoint=admitted),
            checkpoint=lambda: store.validate_current(next_reservation, checkpoint=admitted),
            persist=lambda ledger: store.persist_ledger(next_reservation, ledger, checkpoint=admitted),
            submission_hook=lambda submission: store.commit_submission(
                next_reservation, submission, checkpoint=admitted
            ),
        )
        store.record_result(next_reservation, result.receipt, checkpoint=admitted, completed=True)
        assert not result.ledger.policies and not result.ledger.removals and not result.ledger.pending
        assert world.wire.writes == 4
        assert GCPWorkloadIdentityJournal.objects.get(guid=next_reservation.journal_id).observed_revision == 2
        assert all(
            any(
                binding.role == "roles/viewer" and binding.HasField("condition")
                for binding in policy.bindings
            )
            for policy in world.wire.policies.values()
        )


def test_actual_sdk_all_steps_observed_but_failed_final_union_is_not_completed(native_bridge):
    from gcp.identity_owned import NativeIdentityReconciliationError
    from google.api_core.exceptions import PermissionDenied

    world = native_bridge

    def fail_after_second_readback(name):
        if name == "GetIamPolicy" and world.wire.writes == 2:
            world.wire.error = PermissionDenied("private native marker")

    world.wire.after_call = fail_after_second_readback
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        with pytest.raises(NativeIdentityReconciliationError) as caught:
            world.reconcile(store, reservation)
        assert world.wire.writes == 2
        assert all(step.state == PolicyStepState.OBSERVED for step in caught.value.receipt.steps)
        store.record_result(reservation, caught.value.receipt, checkpoint=admitted)
        row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
        assert row.state == "RESERVED" and row.observed_revision == 0 and row.observed_at is None
        with pytest.raises(JournalError, match="UNRESOLVED_OPERATION"):
            store.reserve(
                replace(world.operation, operation_id=str(uuid4()), desired_revision=2),
                checkpoint=admitted,
                expected_version=row.version,
            )
        world.wire.after_call = None
        world.wire.error = None
        result = world.reconcile(store, reservation)
        assert result.receipt.steps == () and world.wire.writes == 2
        store.record_result(reservation, result.receipt, checkpoint=admitted, completed=True)
        assert independent_row(reservation.journal_id)[0] == "OBSERVED"


@pytest.mark.parametrize("change", ["large_role", "large_resource", "too_many_resources", "unchanged"])
def test_bounded_metadata_refuses_before_any_journal_effect(world, change):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        if change == "large_role":
            planned = replace(
                planned, owned_after=(replace(planned.owned_after[0], role="x" * (2 * 1024 * 1024 + 1)),)
            )
        elif change == "large_resource":
            planned = replace(planned, resource="x" * 257)
        elif change == "unchanged":
            planned = replace(planned, after_sha256=planned.before_sha256)
        ledger = OwnedGrantLedger(
            world.target.context_sha256, pending=(planned,) * (66 if change == "too_many_resources" else 1)
        )
        with pytest.raises(JournalError, match="INVALID_LEDGER|UNCHANGED_SUBMISSION_REFUSED"):
            store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, planned, ledger),
                checkpoint=admitted,
            )
        assert independent_row(reservation.journal_id)[0] == "RESERVED"


def test_lost_mutex_connection_cannot_validate_or_commit_effect(world):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        store._mutex_connection.close()
        with pytest.raises(JournalError, match="MUTEX_NOT_HELD"):
            store.validate_current(reservation, checkpoint=admitted)
        assert independent_row(reservation.journal_id)[0] == "RESERVED"


def test_cluster_then_app_writer_and_journal_have_no_lock_inversion(world):
    held = Event()
    attempt_app = Event()
    pids = Queue()

    def writer():
        close_old_connections()
        try:
            with transaction.atomic():
                type(world.cluster)._unscoped.select_for_update().get(pk=world.cluster.pk)
                held.set()
                assert attempt_app.wait(10)
                app = type(world.medops_app)._unscoped.select_for_update().get(pk=world.medops_app.pk)
                type(app)._unscoped.filter(pk=app.pk).update(name="Current observed app")
        finally:
            close_old_connections()

    def reserve():
        close_old_connections()
        try:
            with journal_mutex(world.target) as store:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                return store.reserve(world.operation, checkpoint=admitted)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(writer)
        assert held.wait(5)
        pending = pool.submit(reserve)
        worker_pid = pids.get(timeout=5)
        deadline = time.monotonic() + 5
        waiting = False
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [worker_pid])
                waiting = cursor.fetchone()[0] > 0
            if waiting:
                break
            time.sleep(0.01)
        attempt_app.set()
        assert waiting
        write.result(timeout=10)
        reservation = pending.result(timeout=10)
    assert independent_row(reservation.journal_id)[0] == "RESERVED"
    world.medops_app.refresh_from_db()
    assert world.medops_app.name == "Current observed app"


def test_provider_guid_replacement_cannot_reset_an_uncertain_original_journal(world):
    from django.utils import timezone

    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        submit(store, reservation, world)
    ProviderPlugin._unscoped.filter(pk=world.provider.pk).update(deleted_at=timezone.now())
    replacement = ProviderPlugin.objects.create(name="Replacement", slug="gcp", plugin_version="0.0.2")
    type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(provider_plugin=replacement)
    changed = replace(world.target, provider_id=str(replacement.guid))
    with journal_mutex(changed) as store, pytest.raises(JournalError, match="ORIGINAL_TARGET_CHANGED"):
        store.reserve(
            replace(world.operation, operation_id=str(uuid4()), desired_revision=2), checkpoint=admitted
        )
    assert GCPWorkloadIdentityJournal.objects.count() == 1
    assert independent_row(reservation.journal_id)[0] == "SENT"


def test_app_organization_move_on_shared_cluster_cannot_reset_uncertain_journal(world):
    type(world.cluster)._unscoped.filter(pk=world.cluster.pk).update(organization=None)
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        submit(store, reservation, world)
    other = ScopeWorld("gcp-journal-moved")
    type(world.medops_app)._unscoped.filter(pk=world.medops_app.pk).update(
        organization=other.org, team=other.medops, project=other.medops_project
    )
    native = NativeIdentityContext(
        str(other.org.guid),
        world.target.app_id,
        world.target.cluster_id,
        world.target.project_id,
        world.target.project_number,
        world.target.region,
        world.target.gsa_id,
        world.target.gsa_unique_id,
        CloudCredential("gcp", declared_account=world.target.project_id),
    )
    changed = replace(world.target, organization_id=str(other.org.guid), context_sha256=native.fingerprint)
    callbacks = []
    with journal_mutex(changed) as store, pytest.raises(JournalError, match="ORIGINAL_TARGET_CHANGED"):
        store.reserve(
            replace(world.operation, operation_id=str(uuid4()), desired_revision=2),
            checkpoint=lambda context: callbacks.append(context),
        )
    assert callbacks == []
    assert GCPWorkloadIdentityJournal._unscoped.count() == 1
    assert independent_row(reservation.journal_id)[0] == "SENT"


@pytest.mark.parametrize("field", ["organization_id", "provider_id"])
def test_mutex_original_app_cluster_anchor_survives_owner_reassignment(world, field):
    changed = replace(world.target, **{field: str(uuid4())})
    with journal_mutex(world.target):
        with pytest.raises(JournalError, match="JOURNAL_BUSY"), journal_mutex(changed):
            pytest.fail("owner reassignment must not acquire another effect mutex")


@pytest.mark.parametrize(
    "state,retired", [("RESERVED", False), ("SENT", False), ("OBSERVED", False), ("OBSERVED", True)]
)
def test_real_migration_refuses_retained_journal_and_empty_roundtrip(world, state, retired):
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder
    from django.utils import timezone

    before = ("astrolift_services", "0037_native_bedrock_connection_owner")
    after = ("astrolift_services", "0038_gcp_workload_identity_journal")
    original_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
    # Exercise this migration's exact boundary. Later empty migrations may
    # legitimately reverse before its guard runs, and are restored finally.
    MigrationExecutor(connection).migrate([after])
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        if state == "SENT":
            submit(store, reservation, world)
        elif state == "OBSERVED":
            store.record_result(
                reservation,
                PolicyReconcileReceipt(OwnedGrantLedger(world.target.context_sha256)),
                checkpoint=admitted,
                completed=True,
            )
    if retired:
        GCPWorkloadIdentityJournal._unscoped.filter(guid=reservation.journal_id).update(
            deleted_at=timezone.now()
        )
    snapshot = list(GCPWorkloadIdentityJournal._unscoped.values())
    recorded = set(MigrationRecorder(connection).applied_migrations())
    try:
        with pytest.raises(RuntimeError, match="GCP_IDENTITY_JOURNAL_ROLLBACK_REQUIRES_EMPTY_LEDGER"):
            MigrationExecutor(connection).migrate([before])
        assert list(GCPWorkloadIdentityJournal._unscoped.values()) == snapshot
        assert set(MigrationRecorder(connection).applied_migrations()) == recorded
        assert after in recorded
        # Only this exact disposable fixture is removed; retained production history must not be erased.
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM astrolift_services_gcpworkloadidentityjournal WHERE guid=%s",
                [reservation.journal_id],
            )
            assert cursor.rowcount == 1
        MigrationExecutor(connection).migrate([before])
        assert after not in MigrationRecorder(connection).applied_migrations()
        MigrationExecutor(connection).migrate(original_targets)
        assert after in MigrationRecorder(connection).applied_migrations()
        assert GCPWorkloadIdentityJournal._unscoped.count() == 0
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indexrelid='gcp_identity_journal_live_owner'::regclass"
            )
            definition = cursor.fetchone()[0]
        assert "(registered_app_id, tenant_cluster_id)" in definition
        assert "organization_id" not in definition and "provider_plugin_id" not in definition
    finally:
        MigrationExecutor(connection).migrate(original_targets)


def test_completed_configuration_reads_committed_exact_current_metadata_without_writes(world):
    from django.test.utils import CaptureQueriesContext

    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        with pytest.raises(JournalError, match="CONFIGURATION_NOT_COMPLETED"):
            store.completed_configuration(reservation, checkpoint=admitted)
        store.record_result(
            reservation,
            PolicyReconcileReceipt(OwnedGrantLedger(world.target.context_sha256)),
            checkpoint=admitted,
            completed=True,
        )
        with CaptureQueriesContext(connection) as queries:
            completed = store.completed_configuration(reservation, checkpoint=admitted)
        row = GCPWorkloadIdentityJournal._unscoped.get(guid=reservation.journal_id)
        assert (completed.journal_id, completed.journal_version, completed.generation) == (
            str(row.guid),
            row.version,
            row.generation,
        )
        assert completed.target == world.target
        assert completed.operation == world.operation
        assert completed.ledger_sha256 == row.ledger_sha256
        assert not any(
            query["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for query in queries
        )
        with pytest.raises(JournalError, match="STALE_RESERVATION"):
            store.completed_configuration(
                replace(reservation, operation_id=str(uuid4())), checkpoint=admitted
            )
        type(world.provider)._unscoped.filter(pk=world.provider.pk).update(is_enabled=False)

        def withdrawn(context):
            assert not context.provider.is_enabled
            raise JournalError("CURRENT_AUTHORITY_WITHDRAWN")

        with pytest.raises(JournalError, match="CURRENT_AUTHORITY_WITHDRAWN"):
            store.completed_configuration(reservation, checkpoint=withdrawn)


@pytest.mark.parametrize("unresolved", ["UNSENT", "SENT", "UNKNOWN", "removal", "revision", "steps"])
def test_completed_configuration_refuses_unresolved_current_configuration(world, unresolved):
    with journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        planned = intent(world)
        if unresolved == "UNSENT":
            ledger = OwnedGrantLedger(world.target.context_sha256, pending=(planned,))
            store.commit_submission(
                reservation,
                PolicySubmission(world.target.context_sha256, planned, ledger),
                checkpoint=admitted,
            )
        elif unresolved in ("SENT", "UNKNOWN"):
            ledger, sent, _ = submit(store, reservation, world)
            if unresolved == "UNKNOWN":
                store.record_result(
                    reservation,
                    PolicyReconcileReceipt(
                        ledger,
                        (
                            PolicyStepReceipt(
                                sent.submission_id,
                                sent.resource,
                                PolicySubmission(world.target.context_sha256, sent, ledger).submission_sha256,
                                PolicyStepState.UNKNOWN,
                                False,
                            ),
                        ),
                    ),
                    checkpoint=admitted,
                )
        else:
            store.record_result(
                reservation,
                PolicyReconcileReceipt(OwnedGrantLedger(world.target.context_sha256)),
                checkpoint=admitted,
                completed=True,
            )
            if unresolved == "removal":
                ledger = OwnedGrantLedger(
                    world.target.context_sha256,
                    removals=(PolicyOwnership(planned.resource, planned.owned_after),),
                )
                payload = owned_ledger_payload(ledger)
                import hashlib

                digest = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                GCPWorkloadIdentityJournal._unscoped.filter(guid=reservation.journal_id).update(
                    ledger=payload, ledger_sha256=digest
                )
            elif unresolved == "revision":
                GCPWorkloadIdentityJournal._unscoped.filter(guid=reservation.journal_id).update(
                    observed_revision=0
                )
            else:
                GCPWorkloadIdentityJournal._unscoped.filter(guid=reservation.journal_id).update(
                    submissions={str(uuid4()): {"state": "SENT"}}
                )
        with pytest.raises(JournalError, match="CONFIGURATION_NOT_COMPLETED"):
            store.completed_configuration(reservation, checkpoint=admitted)
