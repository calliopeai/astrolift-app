"""Protected HTTP origin -> actual source/prep/IAM -> PG committed TLS app apply."""

import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from queue import Queue
from threading import Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction
from gcp.gke_app_apply import (
    ApplyError,
    ApplyIntent,
    ApplyPhase,
    ExecutionBinding,
    GKEAppApply,
    NativeEvidence,
    NativeRejection,
    compile_app_plan,
)
from gcp.gke_identity_observation import GKEObservationContext, KSASubject
from google.iam.v1 import policy_pb2
from temporalio.testing import ActivityEnvironment

from astrolift_lifecycle.deployment_execution_receipt import (
    admitted_deployment_execution,
    expect_deployment_execution,
)
from astrolift_lifecycle.deployment_identity_origin import (
    DeploymentAuthorityContext,
    original_actor,
    persist_deployment_origin,
)
from astrolift_lifecycle.models import Deployment
from astrolift_services.gcp_app_identity_plan import endpoint_app_checkpoint, produce_endpoint_app_plan
from astrolift_services.gcp_gke_app_apply_journal import (
    AcceptedApply,
    AppApplyJournalError,
    SourceFence,
    app_apply_mutex,
)
from astrolift_services.gcp_gke_preparation_journal import (
    PreparationOperation,
    PreparationTarget,
    preparation_journal_mutex,
)
from astrolift_services.gcp_identity_source import bootstrap_original_identity
from astrolift_services.gcp_workload_identity_journal import journal_mutex
from astrolift_services.models import GCPGKEAppApplyJournal, GCPGKEAppApplyOperation
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import SourceWire as EndpointWire
from astrolift_services.tests.test_gcp_gke_preparation_journal_2278 import (
    admitted,
    admitted_iam,
    annotate,
    iam_operation,
    prepare,
    reconcile_iam,
)
from astrolift_services.tests.test_gcp_identity_source_2278 import world as source_world
from astrolift_workflows.inputs import DeployAppInput
from core.gcp_prepared_identity_render import inject_prepared_identity, prepared_identity_for_deployment
from core.permissions import PermissionDenied
from providers.tests.gcp.test_gke_app_apply_2278 import native as tls_fixture
from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def bridge(monkeypatch, client, tmp_path):
    w = source_world.__wrapped__(monkeypatch, client, tmp_path)
    gen = tls_fixture.__wrapped__(tmp_path, monkeypatch)
    prep_driver, _, http, wire = next(gen)
    try:
        w.cluster.endpoint = wire.cluster.endpoint
        w.cluster.ca_cert = wire.cluster.master_auth.cluster_ca_certificate
        w.cluster.save()
        w.native.cluster.endpoint = w.cluster.endpoint
        w.native.cluster.master_auth.cluster_ca_certificate = w.cluster.ca_cert
        original = bootstrap_original_identity(w.authority, w.operation, native_factory=w.factory)
        w.original = original
        # The logical producer consumes the actual newly recorded GSA, not the
        # legacy slug-derived identifier from the original fixture.
        w.source = EndpointWire(original.identity, w.source.endpoint)
        w.deployment = Deployment.objects.create(
            registered_app=w.medops_app,
            app_environment=w.env,
            status="pending",
            trigger_kind="manual",
            image_digest="sha256:" + "1" * 64,
        )
        persist_deployment_origin(w.deployment, w.authority)
        w.deployment_context = DeploymentAuthorityContext(str(w.deployment.guid))
        w.activity = ActivityEnvironment()
        workflow_id = f"DeployAppWorkflow-{w.medops_app.guid}-{w.env.guid}"
        w.activity.info = replace(
            w.activity.info,
            workflow_id=workflow_id,
            workflow_run_id=str(uuid4()),
            workflow_type="DeployAppWorkflow",
        )
        w.input = DeployAppInput(
            w.medops_app.pk,
            w.env.pk,
            w.deployment.pk,
            {"web": "fixture@sha256:" + "1" * 64},
            "manual",
            original_actor(w.authority),
            identity_authority=w.authority,
        )
        with transaction.atomic():
            expect_deployment_execution(w.deployment, "DeployAppWorkflow", workflow_id, [w.input])

        def bind():
            with admitted_deployment_execution(w.input, bind=True) as execution:
                return execution

        w.execution = w.activity.run(bind)

        plan = produce_endpoint_app_plan(
            w.authority,
            original.identity,
            observer_factory=w.source.observer,
            deployment_context=w.deployment_context,
        )
        context = GKEObservationContext(
            original.identity,
            original.cluster.location,
            original.cluster.cluster_name,
            original.cluster.native_cluster_id,
            tuple(KSASubject(s.namespace, s.name, "", "") for s in plan.template.subjects),
        )
        wire.cluster.name = original.cluster.cluster_name
        wire.cluster.id = original.cluster.native_cluster_id
        wire.cluster.location = original.cluster.location
        w.target = PreparationTarget(
            context, str(w.cluster.provider_plugin.guid), "c" * 64, plan.template.subjects
        )
        w.operation = PreparationOperation(
            w.execution.operation_uuid,
            w.execution.workflow_id,
            w.execution.run_id,
            1,
            plan.template,
            w.authority,
        )
        w.provider = w.cluster.provider_plugin
        w.reference = w.authority
        prep_driver.context = prep_driver.observer.context = context
        w.driver, w.http, w.wire = prep_driver, http, wire
        monkeypatch.setattr("tests.gcp.test_identity_owned_2278.ROLE", plan.template.permissions[0].role)
        w.iam_wire = SubmissionWire()
        w.iam_wire.account.name = (
            f"projects/{original.identity.project_id}/serviceAccounts/{original.identity.email}"
        )
        w.iam_wire.account.email = original.identity.email
        w.iam_wire.account.description = original.identity.owner_description
        w.iam_wire.role.name = plan.template.permissions[0].role
        w.iam_wire.policies = {
            r: policy_pb2.Policy(version=3, etag=b"initial")
            for r in (
                original.identity.service_account_resource,
                *(g.resource for g in plan.template.permissions),
            )
        }
        w.source_plan = plan
        http["before_write"] = None
        with preparation_journal_mutex(w.target) as prep:
            prep_res = prep.reserve(w.operation, checkpoint=admitted)
            prepare(w, prep, prep_res)
            native_sha = prep.bind_native_union(prep_res, checkpoint=admitted)
            with journal_mutex(prep.iam_target(prep_res, checkpoint=admitted)) as iam_store:
                iam_res = iam_store.reserve(
                    iam_operation(w, native_sha), checkpoint=lambda c: admitted_iam(w, c)
                )
                reconcile_iam(w, iam_store, iam_res)
                iam = (iam_store, iam_res)
                annotate(w, prep, prep_res, iam)
                identity = prepared_identity_for_deployment(
                    w.deployment,
                    prep,
                    prep_res,
                    iam=iam,
                    checkpoint=admitted,
                    iam_checkpoint=lambda c: admitted_iam(w, c),
                )
                w.identity = identity
                pinned = replace(
                    context,
                    subjects=(
                        KSASubject(
                            identity.namespace,
                            identity.service_account_name,
                            identity.namespace_uid,
                            identity.service_account_uid,
                        ),
                    ),
                )
                driver = GKEAppApply(pinned, clients=wire.clients)
                driver.observer._credentials = prep_driver.observer._credentials
                raw = [
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "binding"},
                        "stringData": {"key": "private-manifest-canary"},
                    },
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": "web"},
                        "spec": {
                            "replicas": 1,
                            "selector": {"matchLabels": {"app": "web"}},
                            "template": {
                                "metadata": {"labels": {"app": "web"}},
                                "spec": {
                                    "containers": [{"name": "web", "image": "fixture@sha256:" + "1" * 64}]
                                },
                            },
                        },
                    },
                ]
                bodies = inject_prepared_identity(raw, identity)
                execution = ExecutionBinding(
                    str(w.deployment.guid),
                    w.operation.operation_id,
                    w.operation.workflow_id,
                    w.operation.execution_id,
                )
                w.compiled, w.bodies = compile_app_plan(
                    pinned, execution, bodies, identity_sha256=identity.fingerprint
                )
                accepted = AcceptedApply(
                    w.compiled, identity, SourceFence.from_original(original), w.execution
                )
                w.accepted = accepted
                http["after_write"] = (
                    lambda m, p: http["objects"][p]["metadata"].update(generation=1)
                    if http["objects"][p]["kind"] == "Deployment"
                    else None
                )
                with app_apply_mutex(
                    accepted,
                    preparation=prep,
                    reservation=prep_res,
                    iam=iam,
                    preparation_checkpoint=admitted,
                    iam_checkpoint=lambda c: admitted_iam(w, c),
                ) as store:
                    w.store, w.app_driver = store, driver
                    w.reservation = store.reserve(checkpoint=lambda c: current(w, c))
                    try:
                        yield w
                    finally:
                        driver.close()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def current(w, context):
    def admission():
        with admitted_deployment_execution(w.input) as execution:
            assert execution == w.execution
            endpoint_app_checkpoint(w.source_plan)()
            assert str(context.deployment.guid) == execution.deployment_guid

    w.activity.run(admission)


def call(w, *, submission=None, evidence=None):
    def checkpoint(c):
        return current(w, c)

    result = w.app_driver.apply(
        w.compiled,
        w.bodies,
        ledger=w.store.read(w.reservation, checkpoint=checkpoint),
        checkpoint=lambda: w.store.validate_current(w.reservation, checkpoint=checkpoint),
        commit_submission=submission
        or (lambda i: w.store.commit_submission(w.reservation, i, checkpoint=checkpoint)),
        commit_evidence=evidence
        or (lambda e: w.store.commit_evidence(w.reservation, e, checkpoint=checkpoint)),
        commit_observation=lambda e: w.store.commit_observation(w.reservation, e, checkpoint=checkpoint),
        commit_rejection=lambda e: w.store.commit_rejection(w.reservation, e, checkpoint=checkpoint),
    )
    w.store.record_result(w.reservation, result, checkpoint=checkpoint)
    return result


def independent(guid):
    db = connection.Database.connect(**connection.get_connection_params())
    try:
        db.autocommit = True
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT state,ledger FROM astrolift_services_gcpgkeappapplyjournal WHERE guid=%s", [guid]
            )
            return cursor.fetchone()
    finally:
        db.close()


def test_actual_http_pg_source_iam_tls_all_resources_committed_before_effect(bridge):
    w = bridge
    before = len(w.http["effects"])

    def write(method, path, body):
        assert not connection.in_atomic_block
        assert independent(w.reservation.journal_id)[0] == "SENT"

    w.http["before_write"] = write
    result = call(w)
    assert result.configuration_observed and not result.workload_ready
    assert len(result.ledger.resources) == 2 and len(w.http["effects"]) == before + 2
    assert independent(w.reservation.journal_id)[0] == "OBSERVED"
    row = GCPGKEAppApplyOperation.objects.get()
    assert row.identity_source_id and row.preparation_operation_id and row.deployment_id == w.deployment.pk
    assert "private-manifest-canary" not in str(row.accepted_plan) and w.private_key not in str(
        row.prepared_identity
    )
    assert call(w).configuration_observed and len(w.http["effects"]) == before + 2


def test_lost_native_create_stays_sent_no_takeover_or_name_adoption(bridge):
    w = bridge
    before = len(w.http["effects"])
    w.http["lost"] = True
    with pytest.raises(ApplyError, match="UNCONFIRMED"):
        call(w)
    assert independent(w.reservation.journal_id)[0] == "SENT"
    with pytest.raises(ApplyError, match="UNKNOWN_NO_RESEND"):
        call(w)
    assert len(w.http["effects"]) == before + 1


def test_lost_sent_database_acknowledgment_has_zero_native_effect(bridge):
    w = bridge
    before = len(w.http["effects"])

    def submission(intent):
        receipt = w.store.commit_submission(w.reservation, intent, checkpoint=lambda c: current(w, c))
        if intent.phase == ApplyPhase.SENT:
            raise ValueError("private-error-canary")
        return receipt

    with pytest.raises(ApplyError, match="UNCONFIRMED"):
        call(w, submission=submission)
    assert independent(w.reservation.journal_id)[0] == "SENT"
    assert len(w.http["effects"]) == before
    with pytest.raises(ApplyError, match="UNKNOWN_NO_RESEND"):
        call(w)


def test_post_write_token_withdrawal_retains_exact_uid_only(bridge):
    w = bridge
    before = len(w.http["effects"])

    def withdraw(method, path):
        type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)

    w.http["after_write"] = withdraw
    with pytest.raises(ApplyError):
        call(w)
    row = GCPGKEAppApplyJournal.objects.get()
    assert row.state == "EVIDENCE" and len(row.ledger["resources"]) == 1
    assert row.observed_at is None and len(w.http["effects"]) == before + 1
    with pytest.raises(PermissionDenied):
        w.store.validate_current(w.reservation, checkpoint=lambda c: current(w, c))
    assert len(w.http["effects"]) == before + 1


def test_evidence_database_ack_loss_recovers_only_committed_original_uid(bridge):
    w = bridge
    before = len(w.http["effects"])

    def lost(evidence):
        w.store.commit_evidence(w.reservation, evidence, checkpoint=lambda c: current(w, c))
        raise ValueError("lost commit acknowledgement")

    with pytest.raises(ApplyError):
        call(w, evidence=lost)
    assert independent(w.reservation.journal_id)[0] == "EVIDENCE"
    assert call(w).configuration_observed and len(w.http["effects"]) == before + 2


def test_metadata_uid_ledger_and_execution_history_are_db_protected(bridge):
    w = bridge
    call(w)
    row = GCPGKEAppApplyJournal.objects.get()
    before = deepcopy(row.ledger)
    changed = deepcopy(before)
    changed["resources"][0]["uid"] = str(uuid4())
    with pytest.raises(DatabaseError), transaction.atomic():
        GCPGKEAppApplyJournal._unscoped.filter(pk=row.pk).update(ledger=changed)
    assert GCPGKEAppApplyJournal.objects.get().ledger == before
    with pytest.raises(DatabaseError), transaction.atomic():
        GCPGKEAppApplyOperation._unscoped.update(execution_id="other-run")
    with pytest.raises(DatabaseError), transaction.atomic():
        GCPGKEAppApplyJournal._unscoped.filter(pk=row.pk).delete()


def test_enclosing_transaction_refuses_without_native_calls(bridge):
    w = bridge
    before = len(w.http["effects"])
    with transaction.atomic(), pytest.raises(ValueError, match="ENCLOSING_TRANSACTION"):
        w.store.read(w.reservation, checkpoint=lambda c: current(w, c))
    assert len(w.http["effects"]) == before


@pytest.mark.parametrize("change", ["token", "run", "deployment", "source", "prep", "iam"])
def test_current_final_checkpoint_withdrawal_refuses_before_effect(bridge, change):
    from astrolift_services.models import GCPGKEPreparationJournal, GCPWorkloadIdentityJournal

    w = bridge
    before = len(w.http["effects"])
    if change == "token":
        type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
    elif change == "run":
        w.activity.info = replace(w.activity.info, workflow_run_id=str(uuid4()))
    elif change == "deployment":
        Deployment.objects.filter(pk=w.deployment.pk).update(status="succeeded")
    elif change == "source":
        type(w.service).objects.filter(pk=w.service.pk).update(version=w.service.version + 1)
    elif change == "prep":
        GCPGKEPreparationJournal._unscoped.filter(guid=w.identity.preparation_journal_guid).update(
            version=w.identity.preparation_journal_version + 1
        )
    else:
        GCPWorkloadIdentityJournal._unscoped.filter(guid=w.identity.iam_journal_guid).update(state="UNKNOWN")
    with pytest.raises((ValueError, PermissionDenied)):
        call(w)
    assert len(w.http["effects"]) == before
    assert independent(w.reservation.journal_id)[0] == "RESERVED"


def test_actual_rejection_history_survives_successful_safe_retry(bridge):
    w = bridge
    before = len(w.http["effects"])
    w.http["before_write"] = lambda *_: {
        "apiVersion": "v1",
        "kind": "Status",
        "status": "Failure",
        "code": 403,
        "reason": "Forbidden",
        "message": "private-rejection-canary",
    }
    with pytest.raises(ApplyError, match="REQUEST_REJECTED"):
        call(w)
    assert len(w.http["effects"]) == before
    old = GCPGKEAppApplyOperation.objects.get().observations
    assert len(old) == 1 and "private-rejection-canary" not in str(old)
    w.http["before_write"] = None
    assert call(w).configuration_observed
    row = GCPGKEAppApplyOperation.objects.get()
    assert all(row.observations[key] == value for key, value in old.items())
    with pytest.raises(DatabaseError), transaction.atomic():
        GCPGKEAppApplyOperation._unscoped.filter(pk=row.pk).update(observations={})


def test_original_incarnation_replaced_after_uid_commit_refuses_no_followon(bridge):
    w = bridge
    before = len(w.http["effects"])

    def evidence(record):
        receipt = w.store.commit_evidence(w.reservation, record, checkpoint=lambda c: current(w, c))
        w.http["objects"][record.resource.resource.path]["metadata"]["uid"] = str(uuid4())
        return receipt

    with pytest.raises(ApplyError, match="RESPONSE_UNVERIFIED"):
        call(w, evidence=evidence)
    assert len(w.http["effects"]) == before + 1
    assert independent(w.reservation.journal_id)[0] == "EVIDENCE"


def test_retained_history_blocks_rollback_before_trigger_removal(bridge):
    from django.db.migrations.executor import MigrationExecutor

    with pytest.raises(RuntimeError, match="APP_APPLY_ROLLBACK_REQUIRES_REVIEW"):
        MigrationExecutor(connection).migrate([("astrolift_services", "0040_gcp_identity_sources")])
    assert GCPGKEAppApplyJournal.objects.count() == 1
    with pytest.raises(DatabaseError), transaction.atomic():
        GCPGKEAppApplyJournal._unscoped.update(original_target={})


def test_empty_migration_roundtrip_restores_all_newest_targets():
    from django.db.migrations.executor import MigrationExecutor

    original = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        MigrationExecutor(connection).migrate([("astrolift_services", "0040_gcp_identity_sources")])
        MigrationExecutor(connection).migrate([("astrolift_services", "0041_gcp_gke_app_apply_journal")])
        assert not GCPGKEAppApplyJournal._unscoped.exists()
    finally:
        MigrationExecutor(connection).migrate(original)


@pytest.mark.parametrize("phase", ["SENT", "EVIDENCE", "REJECTED"])
def test_stale_completion_cannot_clear_committed_unresolved_attempt(bridge, phase):
    w = bridge
    complete = call(w)
    resource = complete.ledger.resources[-1]
    intent = ApplyIntent(
        str(uuid4()),
        ApplyPhase.UNSENT,
        resource.resource,
        "PATCH",
        "a" * 64,
        resource.uid,
        resource.resource_version,
    )

    def checkpoint(c):
        return current(w, c)

    w.store.commit_submission(w.reservation, intent, checkpoint=checkpoint)
    intent = replace(intent, phase=ApplyPhase.SENT)
    w.store.commit_submission(w.reservation, intent, checkpoint=checkpoint)
    if phase == "EVIDENCE":
        w.store.commit_evidence(w.reservation, NativeEvidence(intent, resource), checkpoint=checkpoint)
    elif phase == "REJECTED":
        w.store.commit_rejection(
            w.reservation, NativeRejection(intent, 403, "Forbidden", "b" * 64), checkpoint=checkpoint
        )
    before = independent(w.reservation.journal_id), len(w.http["effects"])
    with pytest.raises(AppApplyJournalError, match="COMMITTED_ATTEMPT_UNRESOLVED"):
        w.store.record_result(w.reservation, complete, checkpoint=checkpoint)
    assert (independent(w.reservation.journal_id), len(w.http["effects"])) == before


def test_actual_locked_workload_writer_collision_releases_cluster_without_effect(bridge):
    from astrolift_lifecycle.action_preconditions import locked_workload
    from astrolift_registry.models import Workload
    from core.tenancy import TenantContext, tenant_context

    w = bridge
    workload = Workload.objects.get(registered_app=w.medops_app, slug="web")
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
                tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)),
                connection.execute_wrapper(pause),
            ):
                with locked_workload(workload.guid, environment_guid=w.env.guid) as (row, env):
                    assert row.pk == workload.pk and env.pk == w.env.pk
            return True
        finally:
            close_old_connections()

    waited = []
    before = independent(w.reservation.journal_id), len(w.http["effects"])
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
                    assert waited
                return execute(sql, params, many, context)

            with connection.execute_wrapper(before_app), pytest.raises(ValueError, match="^JOURNAL_BUSY$"):
                w.store.validate_current(w.reservation, checkpoint=lambda c: current(w, c))
        finally:
            attempt_cluster.set()
        assert future.result(timeout=5)
    assert waited and (independent(w.reservation.journal_id), len(w.http["effects"])) == before
