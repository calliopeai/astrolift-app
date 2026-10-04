"""Real PostgreSQL commits + owned native TLS/GAPIC transport, no cloud effects."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import asdict, replace
from queue import Queue
from threading import Event
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from gcp.gke_identity_preparation import GKEPreparationError
from gcp.identity_owned import NativeGCPIdentity

from astrolift_identity import abac
from astrolift_identity.models import Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.scopes import _app_scope
from astrolift_services.gcp_gke_preparation_journal import (
    PreparationJournalError,
    PreparationOperation,
    PreparationStage,
    PreparationTarget,
    authority_reference_from_payload,
    authority_reference_payload,
    preparation_journal_mutex,
)
from astrolift_services.gcp_workload_identity_journal import JournalError, OperationIdentity, journal_mutex
from astrolift_services.models import GCPGKEPreparationJournal, GCPGKEPreparationOperation
from astrolift_services.native_identity_authority import current_app_identity_authority
from astrolift_services.tests.test_native_identity_authority_2278 import capture
from astrolift_services.tests.test_native_identity_authority_2278 import world as authority_world
from astrolift_workflows.gcp_identity_inputs import AcceptedPreparationTemplate, EndpointGrant, LogicalSubject
from core.permissions import Permission, PermissionDenied, check_permission
from providers.tests.gcp.test_gke_identity_preparation_2278 import BLANK, SA, TOKEN
from providers.tests.gcp.test_gke_identity_preparation_2278 import native as native_fixture
from providers.tests.gcp.test_identity_owned_2278 import PERMISSIONS
from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("world", ["browser_session"], indirect=True)
@pytest.mark.parametrize("withdrawal", ["logout", "sidecar_revoke", "sidecar_expire"])
def test_current_browser_withdrawal_after_committed_sent_cannot_effect(bridge, withdrawal):
    from django.contrib.sessions.models import Session

    from astrolift_identity.models import AstroliftSession

    world = bridge
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def withdraw(record):
            receipt = store.commit_submission(reservation, record, checkpoint=admitted)
            if record.intent.phase.value == "SENT":
                if withdrawal == "logout":
                    Session.objects.filter(session_key=world.private_key).delete()
                else:
                    field = "revoked_at" if withdrawal == "sidecar_revoke" else "expires_at"
                    AstroliftSession.all_objects.filter(pk=world.sidecar.pk).update(**{field: timezone.now()})
            return receipt

        with pytest.raises(GKEPreparationError):
            prepare(world, store, reservation, hook=withdraw)
        assert independent_row(reservation.journal_id)[0] == "SENT"
        assert world.http["effects"] == []
        with pytest.raises(PermissionDenied):
            store.validate_current(reservation, checkpoint=admitted)
        if withdrawal != "logout":
            world.sidecar.refresh_from_db()
            if withdrawal == "sidecar_revoke":
                assert world.sidecar.revoked_at is not None
            else:
                assert world.sidecar.expires_at <= timezone.now()


@pytest.mark.parametrize(
    "change", [{"state": "UNKNOWN"}, {"version": 999}, {"desired_union_sha256": "e" * 64}]
)
def test_current_iam_withdrawal_between_annotation_phases_has_no_patch(bridge, change):
    from astrolift_services.models import GCPWorkloadIdentityJournal

    world = bridge
    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        prepare(world, store, reservation)
        native_sha = store.bind_native_union(reservation, checkpoint=admitted)
        with journal_mutex(store.iam_target(reservation, checkpoint=admitted)) as iam_store:
            iam_reservation = iam_store.reserve(
                iam_operation(world, native_sha), checkpoint=lambda context: admitted_iam(world, context)
            )
            reconcile_iam(world, iam_store, iam_reservation)
            iam = (iam_store, iam_reservation)
            verified = store.verified_annotation_receipt(
                reservation,
                iam=iam,
                iam_checkpoint=lambda context: admitted_iam(world, context),
                checkpoint=admitted,
            )

            def withdraw(record):
                receipt = store.commit_submission(reservation, record, checkpoint=admitted, iam=iam)
                if record.intent.phase.value == "UNSENT":
                    GCPWorkloadIdentityJournal._unscoped.filter(guid=iam_reservation.journal_id).update(
                        **change
                    )
                return receipt

            with pytest.raises(GKEPreparationError):
                world.driver.annotate(
                    operation_id=reservation.operation_id,
                    ledger=store.read(reservation, checkpoint=admitted),
                    commit_submission=withdraw,
                    commit_observation=lambda record: store.commit_observation(
                        reservation, record, checkpoint=admitted, iam=iam
                    ),
                    checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted, iam=iam),
                    iam_receipt=verified,
                    expected_desired_union_sha256=native_sha,
                )
            assert len(world.http["effects"]) == 2
            assert all(method == "POST" for method, *_ in world.http["effects"])
            assert independent_row(reservation.journal_id)[0] == "UNSENT"


def test_invalid_iam_completion_input_has_fixed_refusal_without_transport(world):
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        with pytest.raises(PreparationJournalError, match="CURRENT_IAM_MUTEX_REQUIRED"):
            store.verified_annotation_receipt(
                reservation, iam=(), iam_checkpoint=lambda context: None, checkpoint=admitted
            )


@pytest.fixture
def world(monkeypatch, client, request):
    w = authority_world.__wrapped__(monkeypatch)
    reference = capture(w, client, monkeypatch, getattr(request, "param", "api_token"))
    provider = w.cluster.provider_plugin
    provider.slug = "gcp"
    provider.save()
    identity = replace(
        BLANK.identity,
        organization_id=str(w.org.guid),
        app_id=str(w.medops_app.guid),
        cluster_id=str(w.cluster.guid),
    )
    context = replace(BLANK, identity=identity)
    logical = LogicalSubject((str(w.env.guid),), context.subjects[0].namespace, context.subjects[0].name)
    w.target = PreparationTarget(context, str(provider.guid), "c" * 64, (logical,))
    w.operation = PreparationOperation(
        str(uuid4()),
        "identity-workflow",
        "identity-execution",
        1,
        AcceptedPreparationTemplate(
            tuple(EndpointGrant(**value) for value in PERMISSIONS), (logical,), "d" * 64
        ),
        reference,
    )
    w.provider, w.reference = provider, reference
    return w


def admitted(context):
    # Receiving-worker admission through the actual signed original credential.
    with current_app_identity_authority(context.operation.authority) as environment:
        assert environment.registered_app_id == context.app.pk
        assert environment.tenant_cluster_id == context.cluster.pk
        for alias in context.environments:
            with abac.operation_attributes(
                environment=alias.name, region=context.target.context.identity.region
            ):
                check_permission(
                    Permission.APP_UPDATE,
                    scope=_app_scope(guid=context.app.guid, permission=Permission.APP_UPDATE),
                )


def independent_row(guid):
    db = connection.Database.connect(**connection.get_connection_params())
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT state,ledger,version,operation_id FROM astrolift_services_gcpgkepreparationjournal WHERE guid=%s",
                [guid],
            )
            row = cursor.fetchone()
            return row[0], json.loads(row[1]) if isinstance(row[1], str) else row[1], row[2], str(row[3])
    finally:
        db.close()


@pytest.fixture
def native_tls(tmp_path, monkeypatch):
    yield from native_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def bridge(world, native_tls):
    driver, _, state, wire = native_tls
    driver.context = world.target.context
    driver.observer.context = world.target.context
    world.driver, world.http, world.wire = driver, state, wire
    return world


def prepare(world, store, reservation, *, hook=None):
    result = world.driver.prepare(
        operation_id=reservation.operation_id,
        ledger=store.read(reservation, checkpoint=admitted),
        commit_submission=hook
        or (lambda record: store.commit_submission(reservation, record, checkpoint=admitted)),
        commit_observation=lambda record: store.commit_observation(reservation, record, checkpoint=admitted),
        checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted),
    )
    store.record_result(
        reservation, result, stage=PreparationStage.PREPARE, checkpoint=admitted, completed=True
    )
    return result


def iam_operation(world, native_sha):
    value = world.operation
    return OperationIdentity(
        value.operation_id,
        value.workflow_id,
        value.execution_id,
        value.desired_revision,
        native_sha,
        value.authority_reference_sha256,
    )


def reconcile_iam(world, iam_store, reservation):
    wire = getattr(world, "iam_wire", None) or SubmissionWire()
    world.iam_wire = wire
    wire.account.description = world.target.context.identity.owner_description
    driver = NativeGCPIdentity(world.target.context.identity, clients=wire.clients)
    result = driver.reconcile(
        tuple(asdict(grant) for grant in world.operation.template.permissions),
        service_account_uids=tuple(
            subject.service_account_uid for subject in iam_store.target.ksa_identities
        ),
        ledger=iam_store.read(reservation, checkpoint=lambda context: admitted_iam(world, context)),
        checkpoint=lambda: iam_store.validate_current(
            reservation, checkpoint=lambda context: admitted_iam(world, context)
        ),
        persist=lambda ledger: iam_store.persist_ledger(
            reservation, ledger, checkpoint=lambda context: admitted_iam(world, context)
        ),
        submission_hook=lambda submission: iam_store.commit_submission(
            reservation, submission, checkpoint=lambda context: admitted_iam(world, context)
        ),
    )
    iam_store.record_result(
        reservation, result.receipt, checkpoint=lambda context: admitted_iam(world, context), completed=True
    )
    driver.close()
    return result, wire


def admitted_iam(world, context):
    with current_app_identity_authority(world.operation.authority):
        assert context.target.app_id == world.target.context.identity.app_id


def annotate(world, store, reservation, iam):
    verified = store.verified_annotation_receipt(
        reservation, iam=iam, iam_checkpoint=lambda context: admitted_iam(world, context), checkpoint=admitted
    )
    native_sha = verified.desired_union_sha256
    result = world.driver.annotate(
        operation_id=reservation.operation_id,
        ledger=store.read(reservation, checkpoint=admitted),
        commit_submission=lambda record: store.commit_submission(
            reservation, record, checkpoint=admitted, iam=iam
        ),
        commit_observation=lambda record: store.commit_observation(
            reservation, record, checkpoint=admitted, iam=iam
        ),
        checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted, iam=iam),
        iam_receipt=verified,
        expected_desired_union_sha256=native_sha,
    )
    store.record_result(
        reservation, result, stage=PreparationStage.ANNOTATE, checkpoint=admitted, completed=True, iam=iam
    )
    return result


def test_real_http_authority_payload_and_committed_reservation(world):
    payload = authority_reference_payload(world.reference)
    assert authority_reference_from_payload(payload) == world.reference
    assert world.private_key not in json.dumps(payload)
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        assert independent_row(reservation.journal_id)[0] == "RESERVED"
        saved = GCPGKEPreparationOperation._unscoped.get(operation_id=world.operation.operation_id)
        assert saved.accepted_authority_reference == payload
        assert saved.accepted_union_template == world.operation.template.payload
        assert store.reserve(world.operation, checkpoint=admitted) == reservation
        with pytest.raises(PreparationJournalError, match="OPERATION_IDENTITY_CHANGED"):
            store.reserve(
                replace(
                    world.operation,
                    template=replace(world.operation.template, source_snapshot_sha256="e" * 64),
                ),
                checkpoint=admitted,
            )


def test_actual_tls_create_sees_committed_sent_without_outer_transaction(bridge, caplog):
    world = bridge
    commits = []
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def before_write(method, path, body):
            assert not connection.in_atomic_block
            state, ledger, version, operation = independent_row(reservation.journal_id)
            assert state == "SENT" and operation == reservation.operation_id
            assert ledger["pending"][0]["phase"] == "SENT"
            commits.append(version)

        world.http["before_write"] = before_write
        result = prepare(world, store, reservation)
        assert len(world.http["effects"]) == len(commits) == 2
        assert result.configuration_observed and not result.workload_ready
        assert independent_row(reservation.journal_id)[0] == "PREPARED"
        assert all(subject.uid for subject in result.ledger.objects)
        assert store.bind_native_union(reservation, checkpoint=admitted) != world.operation.template.sha256
        with pytest.raises(PreparationJournalError, match="UNRESOLVED_OPERATION"):
            store.reserve(
                replace(world.operation, operation_id=str(uuid4()), desired_revision=2), checkpoint=admitted
            )
    assert TOKEN not in caplog.text and world.private_key not in caplog.text


def test_actual_tls_and_gapic_successful_iam_only_then_annotation(bridge):
    world = bridge
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        world.http["before_write"] = None
        prepare(world, store, reservation)
        native_sha = store.bind_native_union(reservation, checkpoint=admitted)
        target = store.iam_target(reservation, checkpoint=admitted)
        with journal_mutex(target) as iam_store:
            iam_reservation = iam_store.reserve(
                iam_operation(world, native_sha), checkpoint=lambda context: admitted_iam(world, context)
            )
            with pytest.raises(JournalError, match="CONFIGURATION_NOT_COMPLETED"):
                store.verified_annotation_receipt(
                    reservation,
                    iam=(iam_store, iam_reservation),
                    iam_checkpoint=lambda context: admitted_iam(world, context),
                    checkpoint=admitted,
                )
            result, wire = reconcile_iam(world, iam_store, iam_reservation)
            assert not result.workload_ready and not result.ledger.pending and wire.writes == 2
            finished = annotate(world, store, reservation, (iam_store, iam_reservation))
            assert finished.configuration_observed and not finished.workload_ready
            assert len(world.http["effects"]) == 3 and world.http["effects"][-1][0] == "PATCH"
            row = GCPGKEPreparationJournal._unscoped.get(guid=reservation.journal_id)
            assert row.state == "OBSERVED" and row.observed_revision == 1
            assert (
                world.http["objects"][SA]["metadata"]["annotations"]["iam.gke.io/gcp-service-account"]
                == world.target.context.identity.email
            )


def test_lost_sent_commit_acknowledgement_has_zero_native_effect_and_no_resend(bridge):
    world = bridge
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def lose_ack(record):
            receipt = store.commit_submission(reservation, record, checkpoint=admitted)
            if record.intent.phase.value == "SENT":
                raise RuntimeError("synthetic lost DB acknowledgement")
            return receipt

        with pytest.raises(GKEPreparationError, match="JOURNAL_COMMIT_UNCONFIRMED"):
            prepare(world, store, reservation, hook=lose_ack)
        assert independent_row(reservation.journal_id)[0] == "SENT" and world.http["effects"] == []
        with pytest.raises(GKEPreparationError, match="SENT_SUBMISSION_UNRESOLVED"):
            prepare(world, store, reservation)
        assert world.http["effects"] == []


def test_lost_tls_create_reply_recovers_original_uid_without_second_create(bridge):
    world = bridge
    world.http["before_write"] = None
    world.http["lost"] = True
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        with pytest.raises(GKEPreparationError):
            prepare(world, store, reservation)
        assert len(world.http["effects"]) == 1
        assert independent_row(reservation.journal_id)[0] == "SENT"
        result = prepare(world, store, reservation)
        assert len(world.http["effects"]) == 2
        assert len(result.ledger.objects) == 2


@pytest.mark.parametrize("bad", [False, True, "current", 0])
def test_non_none_admission_never_commits_or_discovers(world, bad):
    with preparation_journal_mutex(world.target) as store:
        with pytest.raises(PreparationJournalError, match="CURRENT_AUTHORITY_UNCONFIRMED"):
            store.reserve(world.operation, checkpoint=lambda context: bad)
    assert not GCPGKEPreparationJournal._unscoped.exists()


def test_outer_transaction_refused_before_mutex_or_effect(world):
    with (
        transaction.atomic(),
        pytest.raises(JournalError, match="ENCLOSING_TRANSACTION_REFUSED"),
        preparation_journal_mutex(world.target),
    ):
        pytest.fail("outer transaction admitted")


@pytest.mark.parametrize("change", ["nonce", "authority", "generation"])
def test_stale_reservation_cannot_read_or_effect(world, change):
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        changed = replace(
            reservation,
            **(
                {"nonce": str(uuid4())}
                if change == "nonce"
                else {"authority_reference_sha256": "a" * 64}
                if change == "authority"
                else {"generation": 2}
            ),
        )
        with pytest.raises(PreparationJournalError, match="STALE_RESERVATION"):
            store.read(changed, checkpoint=admitted)


def test_current_token_revocation_between_committed_phases_has_no_tls_effect(bridge):
    world = bridge
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def revoke(record):
            receipt = store.commit_submission(reservation, record, checkpoint=admitted)
            if record.intent.phase.value == "UNSENT":
                type(world.token)._unscoped.filter(pk=world.token.pk).update(is_revoked=True)
            return receipt

        with pytest.raises(GKEPreparationError):
            prepare(world, store, reservation, hook=revoke)
        assert independent_row(reservation.journal_id)[0] == "UNSENT"
        assert world.http["effects"] == []


@pytest.mark.parametrize("retired", ["journal", "operation"])
def test_any_retired_history_refuses_reset(world, retired):
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        model = GCPGKEPreparationJournal if retired == "journal" else GCPGKEPreparationOperation
        model._unscoped.all().update(deleted_at=timezone.now())
        with pytest.raises(PreparationJournalError, match="RETIRED_.*REQUIRES_REVIEW"):
            store.reserve(world.operation, checkpoint=admitted)
    assert independent_row(reservation.journal_id)[0] == "RESERVED"


def shared_alias(world, *, original_retires=False):
    extra = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster,
        name="staging",
        **({"guid": "00000000-0000-0000-0000-000000000001"} if original_retires else {}),
    )
    subject = replace(
        world.operation.template.subjects[0],
        environment_ids=tuple(sorted((str(world.env.guid), str(extra.guid)))),
    )
    world.target = replace(world.target, original_subjects=(subject,))
    world.operation = replace(
        world.operation, template=replace(world.operation.template, subjects=(subject,))
    )
    return extra


def test_shared_two_environment_aliases_create_one_original_ksa(bridge):
    world = bridge
    shared_alias(world)
    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        result = prepare(world, store, reservation)
        assert len(world.http["effects"]) == 2
        assert len([obj for obj in result.ledger.objects if obj.kind == "ServiceAccount"]) == 1
        target = store.iam_target(reservation, checkpoint=admitted)
        assert len(target.ksa_identities) == 1
        assert target.ksa_identities[0].environment_id == world.target.original_subjects[0].environment_ids[0]
        accepted = GCPGKEPreparationOperation._unscoped.get(operation_id=world.operation.operation_id)
        assert len(accepted.accepted_union_template["subjects"][0]["environment_ids"]) == 2


def test_shared_alias_denied_by_actual_abac_has_zero_preparation_effect(bridge):
    world = bridge
    shared_alias(world)
    Policy.objects.create(
        organization=world.org,
        name="production only",
        slug="production-only",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="app.update",
        conditions=[{"kind": "env_match", "env_in": ["production"]}],
    )
    with current_app_identity_authority(world.reference):
        with abac.operation_attributes(environment="production"):
            check_permission(
                Permission.APP_UPDATE,
                scope=_app_scope(guid=world.medops_app.guid, permission=Permission.APP_UPDATE),
            )
    with preparation_journal_mutex(world.target) as store:
        with pytest.raises(PermissionDenied):
            store.reserve(world.operation, checkpoint=admitted)
    assert world.http["effects"] == [] and world.wire.calls == []
    assert not GCPGKEPreparationJournal._unscoped.exists()


@pytest.mark.parametrize("original_retires", [False, True])
def test_next_detach_plan_retains_history_uids_and_only_removes_endpoint_grants(bridge, original_retires):
    world = bridge
    extra = shared_alias(world, original_retires=original_retires)
    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        prepare(world, store, reservation)
        first_native = store.bind_native_union(reservation, checkpoint=admitted)
        target = store.iam_target(reservation, checkpoint=admitted)
        with journal_mutex(target) as iam_store:
            first_iam = iam_store.reserve(
                iam_operation(world, first_native), checkpoint=lambda context: admitted_iam(world, context)
            )
            reconcile_iam(world, iam_store, first_iam)
            first_result = annotate(world, store, reservation, (iam_store, first_iam))
            first_row = GCPGKEPreparationJournal._unscoped.get(guid=reservation.journal_id)
            old_accepted = GCPGKEPreparationOperation._unscoped.get(operation_id=world.operation.operation_id)
            old_payload = old_accepted.accepted_union_template
            first_iam_row = iam_store._operation(first_iam)
            assert first_iam_row.desired_union_sha256 == first_native
            from astrolift_services.models import GCPWorkloadIdentityJournal

            expected_iam_version = GCPWorkloadIdentityJournal._unscoped.get(guid=first_iam.journal_id).version
            if original_retires:
                extra.soft_delete()
                assert target.ksa_identities[0].environment_id == str(extra.guid)
            logical = replace(world.operation.template.subjects[0], environment_ids=(str(world.env.guid),))
            world.operation = replace(
                world.operation,
                operation_id=str(uuid4()),
                desired_revision=2,
                template=AcceptedPreparationTemplate((), (logical,), "e" * 64),
            )
            next_reservation = store.reserve(
                world.operation,
                checkpoint=admitted,
                expected_version=first_row.version,
                previous_iam=(iam_store, first_iam),
            )
            assert next_reservation.generation == 2
            assert store.read(next_reservation, checkpoint=admitted).objects == first_result.ledger.objects
            prepare(world, store, next_reservation)
            new_native = store.bind_native_union(next_reservation, checkpoint=admitted)
            assert new_native != first_native
            second_iam = iam_store.reserve(
                iam_operation(world, new_native),
                checkpoint=lambda context: admitted_iam(world, context),
                expected_version=expected_iam_version,
            )
            result, wire = reconcile_iam(world, iam_store, second_iam)
            assert wire.writes == 3
            assert len(result.ledger.policies) == 1
            assert (
                result.ledger.policies[0].resource == world.target.context.identity.service_account_resource
            )
            assert result.ledger.policies[0].grants[0].role == "roles/iam.workloadIdentityUser"
            annotate(world, store, next_reservation, (iam_store, second_iam))
            assert len(world.http["effects"]) == 3  # original NS/KSA create and one original link patch
            old_accepted.refresh_from_db()
            assert old_accepted.accepted_union_template == old_payload and old_accepted.state == "OBSERVED"
            assert GCPGKEPreparationOperation._unscoped.count() == 2
            assert store.read(next_reservation, checkpoint=admitted).objects == first_result.ledger.objects


def test_lost_observation_commit_ack_recovers_same_created_uid_without_adopting_other_object(bridge):
    world = bridge
    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)

        def lost(record):
            store.commit_observation(reservation, record, checkpoint=admitted)
            raise RuntimeError("synthetic observation commit acknowledgement lost")

        with pytest.raises(GKEPreparationError, match="JOURNAL_COMMIT_UNCONFIRMED"):
            world.driver.prepare(
                operation_id=reservation.operation_id,
                ledger=store.read(reservation, checkpoint=admitted),
                commit_submission=lambda record: store.commit_submission(
                    reservation, record, checkpoint=admitted
                ),
                commit_observation=lost,
                checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted),
            )
        saved = store.read(reservation, checkpoint=admitted)
        assert len(saved.objects) == 1 and len(world.http["effects"]) == 1
        result = prepare(world, store, reservation)
        assert saved.objects[0] in result.ledger.objects and len(world.http["effects"]) == 2


def test_actual_locked_workload_writer_collision_rolls_back_and_releases_cluster(world):
    from astrolift_lifecycle.action_preconditions import locked_workload
    from astrolift_registry.models import Workload
    from core.tenancy import TenantContext, tenant_context

    workload = Workload.objects.get(registered_app=world.medops_app, slug="web")
    held, attempt_cluster = Event(), Event()
    pids = Queue()

    def writer():
        close_old_connections()

        def pause(execute, sql, params, many, context):
            if "astrolift_clusters_tenantcluster" in sql and "FOR UPDATE" in sql:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                held.set()
                assert attempt_cluster.wait(5)
            return execute(sql, params, many, context)

        try:
            with (
                tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)),
                connection.execute_wrapper(pause),
            ):
                with locked_workload(workload.guid, environment_guid=world.env.guid) as (row, env):
                    assert row.pk == workload.pk and env.pk == world.env.pk
            return True
        finally:
            close_old_connections()

    waited = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        try:
            assert held.wait(5)
            writer_pid = pids.get(timeout=5)

            def before_app(execute, sql, params, many, context):
                if "astrolift_registry_registeredapp" in sql and "FOR UPDATE" in sql:
                    assert "NOWAIT" in sql
                    attempt_cluster.set()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [writer_pid])
                            if cursor.fetchone()[0]:
                                waited.append(True)
                                break
                        time.sleep(0.01)
                    assert waited, "actual existing writer must be blocked on our current cluster lock"
                return execute(sql, params, many, context)

            with (
                preparation_journal_mutex(world.target) as store,
                connection.execute_wrapper(before_app),
                pytest.raises(PreparationJournalError, match="^JOURNAL_BUSY$"),
            ):
                store.reserve(world.operation, checkpoint=admitted)
        finally:
            attempt_cluster.set()
        assert future.result(timeout=5)
    assert waited and not GCPGKEPreparationJournal._unscoped.exists()


def test_pure_validation_closes_ports_and_does_not_discover_credentials(bridge, monkeypatch):
    from astrolift_services import gcp_gke_preparation_journal as module

    world = bridge
    privacy = ContextVar("peer-private-validation", default=None)
    created = []

    class ValidationPort(NativeGCPIdentity):
        def __init__(self, context):
            super().__init__(context)
            self.token = privacy.set("owned-test-scope")
            created.append(self)

        def close(self):
            if not self._closed:
                privacy.reset(self.token)
            super().close()

        def _native(self):
            pytest.fail("pure stored-plan validation must not construct native clients or discover ADC")

    monkeypatch.setattr(module, "NativeGCPIdentity", ValidationPort)

    def checkpoint(context):
        assert privacy.get() is None
        admitted(context)

    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=checkpoint)
        prepare(world, store, reservation)
        store.bind_native_union(reservation, checkpoint=checkpoint)
        store.read(reservation, checkpoint=checkpoint)
    assert created and all(port._closed for port in created) and privacy.get() is None


def test_mutex_anchor_cannot_change_with_org_provider_metadata(world):
    changed = replace(world.target, provider_id=str(uuid4()), credential_declaration_sha256="e" * 64)
    with (
        preparation_journal_mutex(world.target),
        pytest.raises(PreparationJournalError, match="PREPARATION_BUSY"),
        preparation_journal_mutex(changed),
    ):
        pytest.fail("same original app/cluster cannot obtain a second effect mutex")


def test_accepted_operation_metadata_and_derived_plan_are_immutable(bridge):
    world = bridge
    world.http["before_write"] = None
    with preparation_journal_mutex(world.target) as store:
        reservation = store.reserve(world.operation, checkpoint=admitted)
        prepare(world, store, reservation)
        derived = store.bind_native_union(reservation, checkpoint=admitted)
        row = GCPGKEPreparationOperation._unscoped.get(operation_id=world.operation.operation_id)
        row.accepted_authority_reference = {**row.accepted_authority_reference, "signature": "a" * 64}
        with pytest.raises(ValueError, match="ACCEPTED_METADATA_IMMUTABLE"):
            row.save()
        row.refresh_from_db()
        row.derived_native_union_sha256 = "f" * 64
        with pytest.raises(ValueError, match="NATIVE_UNION_IMMUTABLE"):
            row.save()
        row.refresh_from_db()
        assert row.derived_native_union_sha256 == derived


@pytest.mark.parametrize("retired", [False, True])
def test_real_migration_rollback_refuses_any_retained_history_then_empty_roundtrip(world, retired):
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    original = MigrationExecutor(connection).loader.graph.leaf_nodes()
    original_records = set(MigrationRecorder(connection).applied_migrations())
    original_tables = set(connection.introspection.table_names())
    before = ("astrolift_services", "0038_gcp_workload_identity_journal")
    after = ("astrolift_services", "0039_gcp_gke_preparation_journal")
    try:
        # Isolate this guard from legitimate reversal of later empty migrations.
        MigrationExecutor(connection).migrate([after])
        with preparation_journal_mutex(world.target) as store:
            reservation = store.reserve(world.operation, checkpoint=admitted)
        if retired:
            GCPGKEPreparationJournal._unscoped.all().update(deleted_at=timezone.now())
            GCPGKEPreparationOperation._unscoped.all().update(deleted_at=timezone.now())
        rows = list(GCPGKEPreparationJournal._unscoped.values())
        history = list(GCPGKEPreparationOperation._unscoped.values())
        recorded = set(MigrationRecorder(connection).applied_migrations())
        with pytest.raises(RuntimeError, match="GCP_PREPARATION_ROLLBACK_REQUIRES_EMPTY_HISTORY"):
            MigrationExecutor(connection).migrate([before])
        assert list(GCPGKEPreparationJournal._unscoped.values()) == rows
        assert list(GCPGKEPreparationOperation._unscoped.values()) == history
        assert set(MigrationRecorder(connection).applied_migrations()) == recorded
        # Remove only these owned disposable fixtures; never a production rollback remedy.
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM astrolift_services_gcpgkepreparationoperation WHERE operation_id=%s",
                [reservation.operation_id],
            )
            cursor.execute(
                "DELETE FROM astrolift_services_gcpgkepreparationjournal WHERE guid=%s",
                [reservation.journal_id],
            )
        MigrationExecutor(connection).migrate([before])
        assert after not in MigrationRecorder(connection).applied_migrations()
        MigrationExecutor(connection).migrate(original)
        assert after in MigrationRecorder(connection).applied_migrations()
        assert not GCPGKEPreparationJournal._unscoped.exists()
    finally:
        MigrationExecutor(connection).migrate(original)
        assert set(MigrationRecorder(connection).applied_migrations()) == original_records
        assert set(connection.introspection.table_names()) == original_tables
