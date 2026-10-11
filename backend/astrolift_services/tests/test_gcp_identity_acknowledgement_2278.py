"""Original HTTP/actual Worker + PostgreSQL + owned TLS acknowledgments, never cloud."""

from dataclasses import asdict, replace
from threading import Timer
from time import monotonic
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from gcp.gke_identity_preparation import GKEPreparationError
from temporalio import activity

from astrolift_lifecycle.deployment_execution_receipt import admitted_deployment_execution
from astrolift_lifecycle.models import DeploymentExecutionReceipt
from astrolift_services.gcp_gke_preparation_journal import (
    PreparationOperation,
    PreparationTarget,
    preparation_journal_mutex,
)
from astrolift_services.gcp_identity_acknowledgement import (
    AcknowledgementError,
    retain_acknowledgement,
    retained_acknowledgements,
)
from astrolift_services.models import GCPGKEPreparationJournal, GCPIdentityAcknowledgement
from astrolift_workflows.gcp_identity_inputs import AcceptedPreparationTemplate, LogicalSubject
from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.tests.test_native_execution_checkpoint_2278 import (
    bind_and_checkpoint,
    run_activity,
)
from astrolift_workflows.tests.test_native_execution_checkpoint_2278 import (
    world as checkpoint_world,
)
from providers.tests.gcp.test_gke_identity_preparation_2278 import BLANK
from providers.tests.gcp.test_gke_identity_preparation_2278 import native as native_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch, client, tmp_path):
    return checkpoint_world.__wrapped__(monkeypatch, client, tmp_path)


@pytest.fixture
def native_tls(tmp_path, monkeypatch):
    yield from native_fixture.__wrapped__(tmp_path, monkeypatch)


def snapshot(row):
    row.refresh_from_db()
    return row.state, row.version, row.ledger, row.submissions


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["withdraw", "lost_reply", "replacement"])
async def test_actual_worker_original_post_uid_acknowledgement_does_not_complete_after_withdrawal(
    world, client, temporal_env, native_tls, scenario, caplog, capsys
):
    driver, _, wire, sdk_wire = native_tls
    caplog.set_level("DEBUG")
    wire["before_write"] = None

    @activity.defn(name="checkpoint_native_boundary")
    def operation(input: DeployAppInput):
        with bind_and_checkpoint(input) as current:
            with admitted_deployment_execution(input) as execution:
                pass
            identity = replace(
                BLANK.identity,
                organization_id=str(world.org.guid),
                app_id=str(world.medops_app.guid),
                cluster_id=str(world.cluster.guid),
            )
            context = replace(BLANK, identity=identity)
            driver.context = driver.observer.context = context
            logical = LogicalSubject(
                (str(world.env.guid),), context.subjects[0].namespace, context.subjects[0].name
            )
            target = PreparationTarget(context, str(world.cluster.provider_plugin.guid), "c" * 64, (logical,))
            plan = PreparationOperation(
                execution.operation_uuid,
                execution.workflow_id,
                execution.run_id,
                1,
                AcceptedPreparationTemplate((), (logical,), "d" * 64),
                input.identity_authority,
            )
            with preparation_journal_mutex(target) as store:
                reservation = store.reserve(plan, checkpoint=lambda _: current())
                failures = []
                parent_checks = []

                def retain(ack):
                    try:
                        for table, pk in (
                            ("astrolift_identity_organization", world.org.pk),
                            (
                                "astrolift_lifecycle_deploymentexecutionreceipt",
                                DeploymentExecutionReceipt.objects.get(guid=execution.receipt_guid).pk,
                            ),
                        ):
                            db = connection.Database.connect(**connection.get_connection_params())
                            timer = Timer(1, db.rollback)
                            try:
                                with db.cursor() as cursor:
                                    cursor.execute(f"SELECT id FROM {table} WHERE id=%s FOR UPDATE", [pk])
                                timer.start()
                                started = monotonic()
                                reason = None
                                try:
                                    retain_acknowledgement(reservation, ack, execution=execution)
                                except AcknowledgementError as error:
                                    reason = str(error)
                                parent_checks.append((reason, monotonic() - started))
                            finally:
                                timer.cancel()
                                db.rollback()
                                db.close()
                        return retain_acknowledgement(reservation, ack, execution=execution)
                    except AcknowledgementError as error:
                        failures.append(str(error))
                        raise

                kwargs = {
                    "operation_id": reservation.operation_id,
                    "commit_submission": lambda record: store.commit_submission(
                        reservation, record, checkpoint=lambda _: current()
                    ),
                    "commit_observation": lambda record: store.commit_observation(
                        reservation, record, checkpoint=lambda _: current()
                    ),
                    "checkpoint": current,
                    "strict_acknowledgements": True,
                    "acknowledgement_hook": retain,
                }

                # Callback thread owns its own DB connection; no real credential leaves this fixture.
                def after_write(*args):
                    try:
                        if scenario != "lost_reply":
                            type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
                    finally:
                        connection.close()

                wire["after_write"] = after_write
                if scenario == "lost_reply":
                    wire["lost"] = True
                with pytest.raises(GKEPreparationError):
                    driver.prepare(ledger=store.read(reservation, checkpoint=lambda _: current()), **kwargs)
                row = GCPGKEPreparationJournal.objects.get(guid=reservation.journal_id)
                before = snapshot(row)
                assert before[0] == "SENT" and len(before[2]["pending"]) == 1 and not before[2]["objects"]
                acks = retained_acknowledgements(reservation, execution=execution)
                assert len(acks) == (0 if scenario == "lost_reply" else 1), failures
                if scenario != "lost_reply":
                    assert len(parent_checks) == 2
                    assert all(
                        reason == "JOURNAL_BUSY" and elapsed < 0.8 for reason, elapsed in parent_checks
                    )
                assert snapshot(row) == before
                type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=False)
                wire["after_write"] = None
                if acks:
                    assert acks[0].uid == next(iter(wire["objects"].values()))["metadata"]["uid"]
                    receipt = retain_acknowledgement(reservation, acks[0], execution=execution)
                    assert GCPIdentityAcknowledgement.objects.count() == 1
                    db = connection.Database.connect(**connection.get_connection_params())
                    try:
                        with db.cursor() as cursor:
                            cursor.execute(
                                "SELECT id FROM astrolift_services_gcpgkepreparationjournal WHERE guid=%s FOR UPDATE",
                                [reservation.journal_id],
                            )
                        with pytest.raises(AcknowledgementError, match="JOURNAL_BUSY"):
                            retain_acknowledgement(reservation, acks[0], execution=execution)
                    finally:
                        db.rollback()
                        db.close()
                    assert snapshot(row) == before
                    assert retain_acknowledgement(reservation, acks[0], execution=execution) == receipt
                    with pytest.raises(AcknowledgementError):
                        retain_acknowledgement(
                            replace(reservation, nonce=str(uuid4())), acks[0], execution=execution
                        )
                    with pytest.raises(AcknowledgementError):
                        retain_acknowledgement(
                            reservation, replace(acks[0], uid=str(uuid4())), execution=execution
                        )
                    with pytest.raises(AcknowledgementError):
                        retain_acknowledgement(
                            reservation, acks[0], execution=replace(execution, run_id=str(uuid4()))
                        )
                    saved = GCPIdentityAcknowledgement.objects.get()
                    assert saved.target_sha256 == reservation.target_sha256
                    assert saved.accepted_union_sha256 == reservation.accepted_union_template_sha256
                    assert saved.authority_reference_sha256 == reservation.authority_reference_sha256
                    assert len(saved.binding_sha256) == 64
                    for kind in ("UPDATE", "DELETE"):
                        with pytest.raises(DatabaseError, match="GCP_ACKNOWLEDGEMENT_IMMUTABLE"):
                            with transaction.atomic():
                                with connection.cursor() as cursor:
                                    cursor.execute(
                                        f"{kind} "
                                        + (
                                            "astrolift_services_gcpidentityacknowledgement SET generation=generation"
                                            if kind == "UPDATE"
                                            else "FROM astrolift_services_gcpidentityacknowledgement"
                                        )
                                        + " WHERE id=%s",
                                        [saved.pk],
                                    )
                    # Original acknowledged reply cannot be rewritten or erased on migration rollback.
                    executor = MigrationExecutor(connection)
                    with pytest.raises(
                        RuntimeError, match="GCP_ACKNOWLEDGEMENT_ROLLBACK_REQUIRES_EMPTY_HISTORY"
                    ):
                        executor.migrate([("astrolift_services", "0041_gcp_gke_app_apply_journal")])
                    assert (
                        GCPIdentityAcknowledgement.objects.get().acknowledgement_sha256
                        == saved.acknowledgement_sha256
                    )
                count = len(wire["effects"])
                calls = len(sdk_wire.calls), len(wire["requests"])
                if scenario == "replacement":
                    next(iter(wire["objects"].values()))["metadata"]["uid"] = str(uuid4())
                if scenario in ("replacement", "lost_reply"):
                    with pytest.raises(GKEPreparationError):
                        driver.prepare(
                            ledger=store.read(reservation, checkpoint=lambda _: current()),
                            acknowledgements=acks,
                            **kwargs,
                        )
                    assert len(wire["effects"]) == count and snapshot(row) == before
                    if scenario == "lost_reply":
                        assert (len(sdk_wire.calls), len(wire["requests"])) == calls
                else:
                    result = driver.prepare(
                        ledger=store.read(reservation, checkpoint=lambda _: current()),
                        acknowledgements=acks,
                        **kwargs,
                    )
                    assert result.configuration_observed and not result.workload_ready
                    assert result.ledger.objects[0].uid == acks[0].uid
                    assert len(wire["effects"]) == 2
        return {"retained": scenario != "lost_reply", "configuration_only": scenario == "withdraw"}

    result = await run_activity(world, client, temporal_env, operation)
    assert result["retained"] == (scenario != "lost_reply")
    assert await sync_to_async(DeploymentExecutionReceipt.objects.filter(kind="BOUND").count)() == 1
    output = capsys.readouterr()
    assert (
        world.headers["HTTP_AUTHORIZATION"]
        not in str([(record.getMessage(), vars(record)) for record in caplog.records])
        + output.out
        + output.err
    )


def test_actual_ack_migration_empty_reverse_forward_restores_all_original_leaf_targets():
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    assert not GCPIdentityAcknowledgement._unscoped.exists()
    try:
        executor.migrate([("astrolift_services", "0041_gcp_gke_app_apply_journal")])
        assert "astrolift_services_gcpidentityacknowledgement" not in connection.introspection.table_names()
        executor = MigrationExecutor(connection)
        executor.migrate([("astrolift_services", "0042_gcp_identity_acknowledgement")])
        assert "astrolift_services_gcpidentityacknowledgement" in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate(leaves)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_actual_worker_native_iam_reply_retains_hashes_without_grant_completion(
    world, client, temporal_env, caplog, capsys, lost_reply
):
    from gcp.identity_owned import (
        NativeGCPIdentity,
        NativeIdentityReconciliationError,
        desired_owned_union_sha256,
    )

    from astrolift_lifecycle.deployment_identity_origin import _digest
    from astrolift_services.gcp_workload_identity_journal import (
        JournalTarget,
        KSAIdentity,
        OperationIdentity,
        journal_mutex,
    )
    from astrolift_services.models import GCPWorkloadIdentityJournal
    from providers.tests.gcp.test_identity_owned_2278 import CONTEXT, PERMISSIONS, UID
    from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

    caplog.set_level("DEBUG")

    @activity.defn(name="checkpoint_native_boundary")
    def operation(input: DeployAppInput):
        with bind_and_checkpoint(input) as current:
            with admitted_deployment_execution(input) as execution:
                pass
            native = replace(
                CONTEXT,
                organization_id=str(world.org.guid),
                app_id=str(world.medops_app.guid),
                cluster_id=str(world.cluster.guid),
            )
            target = JournalTarget(
                native.organization_id,
                native.app_id,
                native.cluster_id,
                str(world.cluster.provider_plugin.guid),
                native.project_id,
                native.project_number,
                native.region,
                native.service_account_id,
                native.service_account_unique_id,
                "us-central1-a",
                "owned-gke",
                "native-cluster-001",
                "c" * 64,
                native.fingerprint,
                (KSAIdentity(str(world.env.guid), "app-namespace", "app-identity", str(uuid4()), UID),),
            )
            plan = OperationIdentity(
                execution.operation_uuid,
                execution.workflow_id,
                execution.run_id,
                1,
                desired_owned_union_sha256(native, PERMISSIONS, service_account_uids=(UID,)),
                _digest(asdict(input.identity_authority)),
            )
            wire = SubmissionWire()
            wire.account.description = native.owner_description
            wire.lost_reply = lost_reply
            marker = b"synthetic-private-response-etag"

            def after(name):
                if name == "SetIamPolicy":
                    type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
                    for policy in wire.policies.values():
                        policy.etag = marker

            wire.after_call = after
            with NativeGCPIdentity(native, clients=wire.clients) as driver, journal_mutex(target) as store:
                reservation = store.reserve(plan, checkpoint=lambda _: current())
                kwargs = {
                    "permissions": PERMISSIONS,
                    "service_account_uids": (UID,),
                    "checkpoint": current,
                    "persist": lambda ledger: store.persist_ledger(
                        reservation, ledger, checkpoint=lambda _: current()
                    ),
                    "submission_hook": lambda submission: store.commit_submission(
                        reservation, submission, checkpoint=lambda _: current()
                    ),
                    "acknowledgement_hook": lambda ack: retain_acknowledgement(
                        reservation, ack, execution=execution
                    ),
                }
                with pytest.raises(NativeIdentityReconciliationError):
                    driver.reconcile(ledger=store.read(reservation, checkpoint=lambda _: current()), **kwargs)
                row = GCPWorkloadIdentityJournal.objects.get(guid=reservation.journal_id)
                before = snapshot(row)
                assert before[0] == "SENT" and before[2]["pending"] and not before[2]["policies"]
                acks = retained_acknowledgements(reservation, execution=execution)
                assert len(acks) == (0 if lost_reply else 1)
                assert snapshot(row) == before
                if acks:
                    assert marker.decode() not in str(
                        GCPIdentityAcknowledgement.objects.get().acknowledgement
                    )
                    assert retain_acknowledgement(
                        reservation, acks[0], execution=execution
                    ).acknowledgement_sha256
                type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=False)
                wire.after_call = None
                writes = wire.writes
                result = driver.reconcile(
                    ledger=store.read(reservation, checkpoint=lambda _: current()), **kwargs
                )
                assert result.receipt is not None and not result.receipt.ledger.pending
                assert all(step.state.value == "OBSERVED" for step in result.receipt.steps)
                assert len(result.ledger.policies) == 2
                assert wire.writes == writes + 1  # fresh-read recovery, only the second resource needs Set.
                assert not store.read(reservation, checkpoint=lambda _: current()).pending
        return {"retained": not lost_reply, "current_fresh_read": True}

    result = await run_activity(world, client, temporal_env, operation)
    assert result["retained"] == (not lost_reply)
    output = capsys.readouterr()
    assert (
        "synthetic-private-response-etag"
        not in str([(record.getMessage(), vars(record)) for record in caplog.records])
        + output.out
        + output.err
    )
