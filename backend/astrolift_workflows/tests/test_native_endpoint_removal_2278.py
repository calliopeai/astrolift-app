"""Teardown revokes a native GKE app's owned grants through its IAM journal (#2278).

Real PostgreSQL journals and mutexes, the real owned-identity port and its
actual GAPIC serializers over the no-network wire; no cloud is touched.
"""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from gcp.identity_owned import (
    NativeGCPIdentity,
    NativeIdentityContext,
    NativeIdentityError,
    PolicyIntent,
    PolicySubmission,
    PolicySubmissionPhase,
    desired_owned_union_sha256,
    owned_ledger_from_payload,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from astrolift_services.gcp_native_journal_resolution import (
    GUARD,
    JournalResolutionError,
    _lock_id,
    resolve_native_journals,
)
from astrolift_services.gcp_workload_identity_journal import (
    JournalError,
    JournalTarget,
    KSAIdentity,
    OperationIdentity,
    journal_mutex,
    removal_authority_sha256,
)
from astrolift_services.models import GCPNativeJournalResolution, GCPWorkloadIdentityJournal
from astrolift_workflows.activities import native_endpoint_removal as activity_module
from astrolift_workflows.native_endpoint_configuration import NativeConfigurationPorts
from astrolift_workflows.native_endpoint_removal import (
    NativeIdentityRemovalError,
    remove_native_endpoint_identity,
)
from core.cluster_credentials import credential_for_cluster
from core.tests.utils.scope_world import ScopeWorld, make_cluster
from providers.tests.gcp.test_identity_owned_2278 import (
    CONTEXT,
    ENDPOINT,
    NUMBER,
    PROJECT,
    REGION,
    ROLE,
    SECOND,
)
from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

pytestmark = pytest.mark.django_db(transaction=True)

OTHER_APP = "serviceAccount:other-app@fixture-project.iam.gserviceaccount.com"
FOREIGN_PRINCIPAL_UID = str(uuid4())


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def grants(policy):
    return {
        (binding.role, member)
        for binding in policy.bindings
        if not binding.HasField("condition")
        for member in binding.members
    }


def conditional(policy):
    return [binding for binding in policy.bindings if binding.HasField("condition")]


@pytest.fixture
def world():
    w = ScopeWorld("native-removal")
    cluster = make_cluster(w, "native-removal")
    provider = cluster.provider_plugin
    provider.slug = "gcp"
    provider.save()
    cluster.provider_config = {
        "project_id": PROJECT,
        "vertex_region": REGION,
        "endpoint_prediction_role": ROLE,
    }
    cluster.save()
    credential = credential_for_cluster(cluster)
    w.cluster, w.provider, w.app = cluster, provider, w.medops_app
    w.identity = NativeIdentityContext(
        str(w.org.guid),
        str(w.app.guid),
        str(cluster.guid),
        PROJECT,
        NUMBER,
        REGION,
        CONTEXT.service_account_id,
        CONTEXT.service_account_unique_id,
        credential,
    )
    w.ksa_uid = str(uuid4())
    w.target = JournalTarget(
        str(w.org.guid),
        str(w.app.guid),
        str(cluster.guid),
        str(provider.guid),
        PROJECT,
        NUMBER,
        REGION,
        CONTEXT.service_account_id,
        CONTEXT.service_account_unique_id,
        "us-central1-a",
        "owned-gke",
        "native-cluster-001",
        _hash(asdict(credential)),
        w.identity.fingerprint,
        (KSAIdentity(str(uuid4()), "app-namespace", "app-identity", str(uuid4()), w.ksa_uid),),
    )
    w.wire = SubmissionWire()
    w.wire.account.description = w.identity.owner_description
    # Another app already consumes the shared Endpoint, an operator holds an
    # unconditional viewer grant, and a foreign KSA may impersonate the GSA.
    w.wire.policies[SECOND].bindings.add(role=ROLE, members=[OTHER_APP])
    w.wire.policies[ENDPOINT].bindings.add(
        role="roles/aiplatform.viewer", members=["user:ops@example.invalid"]
    )
    w.wire.policies[CONTEXT.service_account_resource].bindings.add(
        role="roles/iam.workloadIdentityUser", members=[w.identity.principal(FOREIGN_PRINCIPAL_UID)]
    )
    w.ports_built = []

    def iam(identity):
        w.ports_built.append(identity)
        return NativeGCPIdentity(identity, clients=w.wire.clients)

    w.ports = NativeConfigurationPorts(iam=iam)
    return w


def deploy(w, endpoints=(ENDPOINT, SECOND)):
    """What the native executor's IAM step commits for a Deployment."""
    permissions = tuple({"role": ROLE, "resource": endpoint} for endpoint in endpoints)
    operation = OperationIdentity(
        str(uuid4()),
        "DeployAppWorkflow-fixture",
        "deploy-run",
        1,
        desired_owned_union_sha256(w.identity, permissions, service_account_uids=(w.ksa_uid,)),
        "e" * 64,
    )

    def admitted(checkpoint):
        assert checkpoint.operation == operation

    with journal_mutex(w.target) as store:
        reservation = store.reserve(operation, checkpoint=admitted)
        result = NativeGCPIdentity(w.identity, clients=w.wire.clients).reconcile(
            permissions,
            service_account_uids=(w.ksa_uid,),
            ledger=store.read(reservation, checkpoint=admitted),
            checkpoint=lambda: store.validate_current(reservation, checkpoint=admitted),
            persist=lambda value: store.persist_ledger(reservation, value, checkpoint=admitted),
            submission_hook=lambda value: store.commit_submission(reservation, value, checkpoint=admitted),
        )
        store.record_result(reservation, result.receipt, checkpoint=admitted, completed=True)
    w.wire.calls.clear()
    w.writes_before, w.setters_before = w.wire.writes, w.wire.setter_entries


def tear_down(w):
    type(w.app).objects.filter(pk=w.app.pk).update(provisioning_status="tearing_down")


def remove(w):
    return remove_native_endpoint_identity(
        w.app.pk,
        workflow_id=f"DeregisterAppWorkflow-{w.app.guid}",
        execution_id="teardown-run",
        ports=w.ports,
    )


def journal():
    return GCPWorkloadIdentityJournal.objects.get()


def owned(row):
    return owned_ledger_from_payload(row.ledger)


def app_grants(w):
    member = "serviceAccount:" + w.identity.email
    return {
        resource: grants(policy)
        & {(ROLE, member), ("roles/iam.workloadIdentityUser", w.identity.principal(w.ksa_uid))}
        for resource, policy in w.wire.policies.items()
    }


def test_teardown_revokes_every_owned_grant_and_keeps_other_consumers(world):
    w = world
    deploy(w)
    assert all(app_grants(w).values())
    foreign_conditions = {resource: conditional(policy) for resource, policy in w.wire.policies.items()}
    tear_down(w)

    assert remove(w) == [{"cluster": str(w.cluster.guid), "removed": 3}]

    assert not any(app_grants(w).values())
    assert (ROLE, OTHER_APP) in grants(w.wire.policies[SECOND])
    assert ("roles/aiplatform.viewer", "user:ops@example.invalid") in grants(w.wire.policies[ENDPOINT])
    assert ("roles/iam.workloadIdentityUser", w.identity.principal(FOREIGN_PRINCIPAL_UID)) in grants(
        w.wire.policies[CONTEXT.service_account_resource]
    )
    assert {
        resource: conditional(policy) for resource, policy in w.wire.policies.items()
    } == foreign_conditions
    # Only reads and owned policy writes: nothing is created, deployed or deleted.
    assert {name for name, _ in w.wire.calls} == {
        "GetProject",
        "GetServiceAccount",
        "GetIamPolicy",
        "SetIamPolicy",
    }
    assert w.wire.writes - w.writes_before == 3
    # The original GSA, pinned by its unique ID, is the only identity used.
    assert w.ports_built == [w.identity]
    row = journal()
    assert row.state == "OBSERVED" and row.observed_revision == row.desired_revision == 2
    assert row.generation == 2 and row.observed_at is not None
    assert row.authority_reference_sha256 == removal_authority_sha256(
        str(w.org.guid), str(w.app.guid), str(w.cluster.guid)
    )
    assert (row.workflow_id, row.execution_id) == (f"DeregisterAppWorkflow-{w.app.guid}", "teardown-run")
    ledger = owned(row)
    assert not ledger.policies and not ledger.pending and not ledger.removals


def test_a_completed_removal_is_not_repeated(world):
    w = world
    deploy(w)
    tear_down(w)
    remove(w)
    version, calls = journal().version, len(w.wire.calls)

    assert remove(w) == [{"cluster": str(w.cluster.guid), "removed": 0}]
    assert journal().version == version and len(w.wire.calls) == calls


def test_app_without_native_journal_is_left_alone(world):
    w = world
    tear_down(w)
    assert remove(w) == []
    assert not w.wire.calls and not w.ports_built


@pytest.mark.parametrize("status", ["ready", "failed"])
def test_an_app_that_is_not_tearing_down_keeps_its_grants(world, status):
    w = world
    deploy(w)
    type(w.app).objects.filter(pk=w.app.pk).update(provisioning_status=status)
    before = journal()

    with pytest.raises(NativeIdentityRemovalError, match="APP_NOT_TEARING_DOWN"):
        remove(w)
    assert not w.wire.calls and all(app_grants(w).values())
    assert journal().version == before.version and journal().operation_id == before.operation_id


def test_unreadable_endpoint_policy_keeps_the_removal_pending_then_resumes(world):
    """Partial inventory stays pending: an Endpoint policy that cannot be read
    is never taken as an empty one, and the same removal resumes later."""
    w = world
    deploy(w)
    tear_down(w)
    unreadable = w.wire.policies.pop(SECOND)

    with pytest.raises(NativeIdentityError, match="NATIVE_READ_OR_WRITE_UNCONFIRMED"):
        remove(w)
    row = journal()
    assert row.state != "OBSERVED" and row.desired_revision == 2
    ledger = owned(row)
    assert {item.resource for item in ledger.policies} == {SECOND, CONTEXT.service_account_resource}
    # Taken off before the unreadable policy, kept as an obligation until a
    # final pass over every policy observes it gone.
    assert [item.resource for item in ledger.removals] == [ENDPOINT]
    first_operation = row.operation_id

    w.wire.policies[SECOND] = unreadable
    assert remove(w) == [{"cluster": str(w.cluster.guid), "removed": 2}]
    row = journal()
    assert row.state == "OBSERVED" and row.operation_id == first_operation and row.desired_revision == 2
    assert not any(app_grants(w).values()) and (ROLE, OTHER_APP) in grants(w.wire.policies[SECOND])
    assert w.wire.writes - w.writes_before == 3


def test_lost_reply_resumes_the_same_removal_without_a_second_write(world):
    w = world
    deploy(w)
    tear_down(w)
    w.wire.lost_reply = True

    with pytest.raises(NativeIdentityError, match="NATIVE_SET_UNCONFIRMED"):
        remove(w)
    assert journal().state == "SENT" and w.wire.setter_entries - w.setters_before == 1

    remove(w)
    # The landed write is resolved by its exact after-hash, never sent again.
    assert w.wire.setter_entries - w.setters_before == 3 and w.wire.writes - w.writes_before == 3
    assert journal().state == "OBSERVED" and not any(app_grants(w).values())


def test_stuck_removal_is_settled_by_the_operator_then_completes(world):
    w = world
    deploy(w)
    tear_down(w)
    w.wire.lost_reply = True
    with pytest.raises(NativeIdentityError, match="NATIVE_SET_UNCONFIRMED"):
        remove(w)
    # Another writer moved the policy after the lost reply: the send cannot be
    # resolved by its after-hash, and it is never resent.
    w.wire.policies[ENDPOINT].bindings.add(role="roles/viewer", members=["user:later@example.invalid"])
    w.wire.policies[ENDPOINT].etag = b"moved"
    with pytest.raises(NativeIdentityError, match="SENT_SUBMISSION_UNRESOLVED"):
        remove(w)
    assert journal().state == "SENT" and w.wire.setter_entries - w.setters_before == 1

    operator = get_user_model().objects.create(username="operator-2278", email="operator-2278@acme.test")
    with pytest.raises(JournalResolutionError, match="RESOLUTION_GUARD_WINDOW"):
        resolve_native_journals(
            w.app, w.cluster, user=operator, attestation="checked the Endpoint policy by hand"
        )
    [resolution] = resolve_native_journals(
        w.app,
        w.cluster,
        user=operator,
        attestation="checked the Endpoint policy by hand",
        now=timezone.now() + GUARD + timedelta(minutes=1),
    )
    assert resolution.deployment is None and resolution.kind == "IAM" and resolution.prior_state == "SENT"
    assert GCPNativeJournalResolution.objects.get().deployment_id is None
    settled = journal()
    assert settled.state == "RESOLVED"

    remove(w)
    row = journal()
    assert row.state == "OBSERVED" and row.desired_revision == 3 and not owned(row).policies
    assert not any(app_grants(w).values()) and ("roles/viewer", "user:later@example.invalid") in grants(
        w.wire.policies[ENDPOINT]
    )


def test_settling_a_removal_requires_the_app_to_be_tearing_down(world):
    w = world
    deploy(w)
    tear_down(w)
    w.wire.lost_reply = True
    with pytest.raises(NativeIdentityError):
        remove(w)
    type(w.app).objects.filter(pk=w.app.pk).update(provisioning_status="failed")
    operator = get_user_model().objects.create(username="operator-2278b", email="operator-2278b@acme.test")

    with pytest.raises(JournalResolutionError, match="OPERATION_OWNER_UNAVAILABLE"):
        resolve_native_journals(
            w.app,
            w.cluster,
            user=operator,
            attestation="checked the Endpoint policy by hand",
            now=timezone.now() + GUARD + timedelta(minutes=1),
        )
    assert journal().state == "SENT" and not GCPNativeJournalResolution.objects.exists()


@pytest.mark.rarely
def test_migration_0047_reverses_only_while_no_removal_was_settled(world):
    from django.db.migrations.executor import MigrationExecutor

    w = world
    before = ("astrolift_services", "0046_gcp_native_journal_resolution")
    original_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        MigrationExecutor(connection).migrate([before])
        MigrationExecutor(connection).migrate(original_targets)
        operator = get_user_model().objects.create(username="operator-2278c", email="operator-2278c@acme.test")
        GCPNativeJournalResolution.objects.create(
            organization=w.org,
            registered_app=w.app,
            tenant_cluster=w.cluster,
            deployment=None,
            resolved_by=operator,
            created_by=operator,
            kind="IAM",
            journal_guid=uuid4(),
            operation_id=uuid4(),
            generation=2,
            prior_state="SENT",
            prior_ledger_sha256="a" * 64,
            resolved_ledger_sha256="b" * 64,
            unresolved=[],
            attestation="checked the Endpoint policy by hand",
        )
        with pytest.raises(RuntimeError, match="GCP_NATIVE_REMOVAL_RESOLUTION_ROLLBACK_REQUIRES_EMPTY_HISTORY"):
            MigrationExecutor(connection).migrate([before])
        assert GCPNativeJournalResolution.objects.get().deployment_id is None
    finally:
        MigrationExecutor(connection).migrate(original_targets)


@pytest.mark.parametrize("phase", ["RESERVED", "SENT"])
def test_unresolved_deployment_operation_refuses_before_any_native_call(world, phase):
    w = world
    deploy(w)
    row = journal()
    operation = OperationIdentity(
        str(uuid4()), "DeployAppWorkflow-later", "deploy-run-2", 2, row.desired_union_sha256, "e" * 64
    )

    def admitted(checkpoint):
        assert checkpoint.operation == operation

    with journal_mutex(w.target) as store:
        reservation = store.reserve(operation, checkpoint=admitted, expected_version=row.version)
        ledger = store.read(reservation, checkpoint=admitted)
        if phase == "SENT":
            intent = PolicyIntent(
                ENDPOINT,
                "1" * 64,
                "2" * 64,
                ledger.policies[0].grants,
                str(uuid4()),
                PolicySubmissionPhase.UNSENT,
                "3" * 64,
                row.desired_union_sha256,
            )
            pending = replace(ledger, pending=(intent,))
            store.commit_submission(
                reservation, PolicySubmission(w.target.context_sha256, intent, pending), checkpoint=admitted
            )
            sent = replace(intent, submission_phase=PolicySubmissionPhase.SENT)
            store.commit_submission(
                reservation,
                PolicySubmission(w.target.context_sha256, sent, replace(pending, pending=(sent,))),
                checkpoint=admitted,
            )
    tear_down(w)
    before = journal()

    reason = "PRIOR_SEND_UNRESOLVED" if phase == "SENT" else "PRIOR_OPERATION_INCOMPLETE"
    with pytest.raises(NativeIdentityRemovalError, match=reason):
        remove(w)
    assert not w.wire.calls and not w.ports_built
    assert journal().version == before.version and all(app_grants(w).values())


def test_a_live_configuration_keeps_the_removal_pending(world):
    """The executor holds the preparation and IAM mutexes for its whole run."""
    w = world
    deploy(w)
    tear_down(w)
    holder = connection.Database.connect(**connection.get_connection_params())
    holder.autocommit = True
    try:
        with holder.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(%s)",
                [_lock_id("astrolift-gcp-gke-preparation-v1", str(w.app.guid), str(w.cluster.guid))],
            )
        with pytest.raises(NativeIdentityRemovalError, match="PREPARATION_BUSY"):
            remove(w)
    finally:
        holder.close()
    with journal_mutex(w.target), pytest.raises(JournalError, match="JOURNAL_BUSY"):
        remove(w)
    assert not w.wire.calls and journal().desired_revision == 1 and all(app_grants(w).values())


@pytest.mark.parametrize("change", ["unique_id", "description", "disabled"])
def test_replaced_or_foreign_gsa_keeps_the_removal_pending(world, change):
    w = world
    deploy(w)
    tear_down(w)
    setattr(w.wire.account, change, True if change == "disabled" else "110099999999999999999")

    with pytest.raises(NativeIdentityError, match="SERVICE_ACCOUNT_OWNERSHIP_UNVERIFIED"):
        remove(w)
    assert w.wire.writes == w.writes_before and all(app_grants(w).values())
    assert journal().state != "OBSERVED"


@pytest.mark.parametrize("withdrawal", ["teardown_state", "credential"])
def test_withdrawal_while_the_removal_runs_stops_it_before_any_write(world, withdrawal):
    """Every native call is fenced by a fresh current-state checkpoint."""
    w = world
    deploy(w)
    tear_down(w)
    withdrawn = []

    def withdraw(name):
        if name == "GetIamPolicy" and not withdrawn:
            withdrawn.append(name)
            if withdrawal == "teardown_state":
                type(w.app).objects.filter(pk=w.app.pk).update(provisioning_status="failed")
            else:
                # The declared credential names the cluster; a rename changes it.
                type(w.cluster).objects.filter(pk=w.cluster.pk).update(slug="renamed-native-removal")

    w.wire.after_call = withdraw
    with pytest.raises(NativeIdentityError, match="CURRENT_OPERATION_UNCONFIRMED"):
        remove(w)
    assert withdrawn and w.wire.writes == w.writes_before and all(app_grants(w).values())
    assert journal().state != "OBSERVED"


def test_renamed_cluster_credential_declaration_refuses_before_any_native_call(world):
    w = world
    deploy(w)
    tear_down(w)
    type(w.cluster).objects.filter(pk=w.cluster.pk).update(slug="renamed-native-removal")

    with pytest.raises(NativeIdentityRemovalError, match="CURRENT_IDENTITY_SOURCE_UNAVAILABLE"):
        remove(w)
    assert not w.wire.calls and journal().desired_revision == 1


def test_changed_credential_declaration_refuses_before_any_native_call(world):
    w = world
    deploy(w)
    tear_down(w)
    w.cluster.provider_config = {**w.cluster.provider_config, "project_id": "other-project"}
    w.cluster.save()

    with pytest.raises(NativeIdentityRemovalError, match="CURRENT_IDENTITY_SOURCE_UNAVAILABLE"):
        remove(w)
    assert not w.wire.calls and journal().desired_revision == 1


def test_altered_original_target_refuses_before_any_native_call(world):
    w = world
    deploy(w)
    tear_down(w)
    row = journal()
    snapshot = {**row.target_snapshot, "gsa_unique_id": "110099999999999999999"}
    GCPWorkloadIdentityJournal.objects.filter(pk=row.pk).update(target_snapshot=snapshot)

    with pytest.raises(NativeIdentityRemovalError, match="ORIGINAL_TARGET_INTEGRITY_FAILED"):
        remove(w)
    assert not w.wire.calls


@pytest.mark.asyncio
async def test_registered_activity_returns_results_and_only_fixed_reasons(monkeypatch):
    seen = []

    def removed(registered_app_id, *, workflow_id, execution_id, ports):
        seen.append((registered_app_id, workflow_id, execution_id, type(ports)))
        return [{"cluster": "c", "removed": 2}]

    monkeypatch.setattr(
        "astrolift_workflows.native_endpoint_removal.remove_native_endpoint_identity", removed
    )
    env = ActivityEnvironment()
    assert await env.run(activity_module.remove_native_endpoint_identity, 7) == [
        {"cluster": "c", "removed": 2}
    ]
    assert seen == [(7, env.info.workflow_id, env.info.workflow_run_id, NativeConfigurationPorts)]

    for error, reason in (
        (NativeIdentityRemovalError("PRIOR_SEND_UNRESOLVED"), "PRIOR_SEND_UNRESOLVED"),
        (JournalError("JOURNAL_BUSY"), "JOURNAL_BUSY"),
        (RuntimeError("private native canary: projects/fixture-project"), "REMOVAL_UNCONFIRMED"),
    ):

        def refused(*args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(
            "astrolift_workflows.native_endpoint_removal.remove_native_endpoint_identity", refused
        )
        with pytest.raises(ApplicationError) as failure:
            await ActivityEnvironment().run(activity_module.remove_native_endpoint_identity, 7)
        assert failure.value.message == "NATIVE_IDENTITY_REMOVAL_PENDING:" + reason
        assert failure.value.non_retryable and "canary" not in str(failure.value)
