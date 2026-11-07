from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError

from astrolift_clusters import nvke_maintenance as maintenance
from astrolift_clusters import nvke_operations as operations
from astrolift_clusters.tests import test_nvke_access as support
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import RoleBinding
from core.permissions import PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return support.world.__wrapped__()


@pytest.fixture
def configured(world):
    return support.configured.__wrapped__(world)


def payload():
    return {
        "nextCertRenewalDue": "2000-01-01T00:00:00Z",
        "conditions": {
            "provisioned": {"status": "True", "message": "PRIVATE_PROVISIONING_MESSAGE"},
            "updateInProgress": {
                "type": "certificate_renewal",
                "startedAt": "2026-11-07T00:00:00Z",
                "message": "PRIVATE_LOCK_MESSAGE",
            },
            "certificateRenewal": {
                "cycleID": "operator-k8s-cert-renewal-" + str(uuid4()),
                "state": "failed",
                "forced": False,
                "phase": "renew-node",
                "startedAt": "2026-11-07T00:00:00Z",
                "completedAt": "2026-11-07T01:00:00Z",
                "failedNodeID": str(uuid4()),
                "message": "PRIVATE_CREDENTIAL_DETAIL",
                "privateKey": "PRIVATE_KEY",
            },
        },
    }


def read(state, source, **changes):
    args = {
        "site_id": str(state.site.guid),
        "operation_id": str(source.guid),
        "expected_actor_id": state.world.user.pk,
        "expected_version": source.version,
    }
    args.update(changes)
    with context(state.world):
        return maintenance.read(**args)


def test_failed_renewal_and_retained_lock_are_not_hidden_by_provisioned(configured):
    native = payload()
    source, _name, identity = support.observed(configured, overrides=native)
    row, data = read(configured, source)
    assert row.pk == source.pk and data["cluster_id"] == identity
    assert data["mutation_lock_owner"] == "certificate_renewal"
    assert data["mutation_lock_reported"] is True
    assert data["renewal"]["state"] == "failed"
    assert data["renewal"]["retained_mutation_lock"] is True
    assert data["renewal"]["forced"] is False
    assert data["renewal"]["workflow_id"] == native["conditions"]["certificateRenewal"]["cycleID"]
    assert data["renewal"]["failed_node_id"] == native["conditions"]["certificateRenewal"]["failedNodeID"]
    assert data["renewal_schedule_overdue_at_observation"] is True
    assert "PRIVATE" not in str(data)
    assert "expiry" not in str(data) and "not_after" not in str(data)


@pytest.mark.parametrize(
    "state,outcome,overdue",
    [
        ("accepted", None, False),
        ("succeeded", "unchanged", True),
        ("succeeded", "renewed", True),
        ("failed", "renewed", True),
    ],
)
def test_schedule_and_successful_renewal_outcome_have_separate_meanings(configured, state, outcome, overdue):
    native = payload()
    native["conditions"]["certificateRenewal"].update(state=state, outcome=outcome)
    source, _, _ = support.observed(configured, overrides=native)
    _, data = read(configured, source)
    assert data["renewal_schedule_overdue_at_observation"] is overdue
    assert data["renewal"]["outcome"] == (outcome if state == "succeeded" else None)


def test_overdue_is_computed_at_the_saved_observation_not_the_current_clock(configured):
    native = payload()
    native["nextCertRenewalDue"] = (timezone.now() - timedelta(days=1)).isoformat()
    source, _, _ = support.observed(configured, overrides=native)
    source.finished_at = timezone.now() - timedelta(days=2)
    source.save(update_fields=["finished_at", "updated_at", "version"])
    assert read(configured, source)[1]["renewal_schedule_overdue_at_observation"] is False


def test_operator_workflow_identity_preserves_printable_unicode(configured):
    native = payload()
    workflow = "operator-更新-" + str(uuid4())
    native["conditions"]["certificateRenewal"]["cycleID"] = workflow
    source, _, _ = support.observed(configured, overrides=native)
    assert read(configured, source)[1]["renewal"]["workflow_id"] == workflow


@pytest.mark.parametrize(
    "conditions,reported,overdue",
    [
        (None, None, None),
        ({}, False, True),
        ({"updateInProgress": {"type": "certificate_renewal"}}, True, None),
        ({"certificateRenewal": {"state": "PRIVATE_UNKNOWN"}}, False, None),
    ],
)
def test_missing_and_unknown_observations_are_not_success_or_zero(configured, conditions, reported, overdue):
    native = payload()
    native["conditions"] = conditions
    source, _, _ = support.observed(configured, overrides=native)
    data = read(configured, source)[1]
    assert data["mutation_lock_reported"] is reported
    assert data["renewal_schedule_overdue_at_observation"] is overdue
    if conditions and "certificateRenewal" in conditions:
        assert data["renewal"]["state"] is None
        assert data["renewal"]["retained_mutation_lock"] is None
    else:
        assert data["renewal"] is None
    assert "PRIVATE" not in str(data)


@pytest.mark.parametrize("phase,locked", [("prepare", False), ("rewrite", True)])
def test_rotation_failure_retains_the_reported_recovery_owner(configured, phase, locked):
    native = payload()
    native["conditions"].pop("certificateRenewal")
    native["conditions"].pop("updateInProgress")
    if locked:
        native["conditions"]["updateInProgress"] = {"type": "etcd_key_rotation"}
    workflow = "operator-etcd-key-rotation-" + str(uuid4())
    native["conditions"]["etcdKeyRotation"] = {
        "workflowID": workflow,
        "state": "failed",
        "phase": phase,
        "failureReason": "operation_failed",
        "lastProgressAt": "2026-11-07T01:00:00Z",
        "keys": ["PRIVATE_ROTATION_KEY"],
    }
    source, _, _ = support.observed(configured, overrides=native)
    rotation = read(configured, source)[1]["etcd_key_rotation"]
    assert rotation["retained_mutation_lock"] is locked
    assert rotation["workflow_id"] == workflow and rotation["phase"] == phase
    assert rotation["failure_reason"] == "operation_failed"
    assert "PRIVATE" not in str(rotation)


@pytest.mark.parametrize("change", ["actor", "version", "site", "status", "identity", "permission"])
def test_maintenance_requires_current_tenant_permission_and_the_exact_read(configured, change, monkeypatch):
    source, _, _ = support.observed(
        configured, overrides={**payload(), **({"id": str(uuid4())} if change == "identity" else {})}
    )
    kwargs = {}
    if change == "actor":
        kwargs["expected_actor_id"] = configured.world.user.pk + 1
    elif change == "version":
        kwargs["expected_version"] = source.version + 1
    elif change == "site":
        kwargs["site_id"] = str(uuid4())
    elif change == "status":
        source.status = "failed"
        source.save(update_fields=["status", "updated_at", "version"])
    elif change == "permission":
        RoleBinding.objects.filter(user=configured.world.user).update(deleted_at=timezone.now())

        def denied_decode(_row):
            pytest.fail("decrypted maintenance data before checking permission")

        monkeypatch.setattr(operations, "decode_result", denied_decode)
    with pytest.raises((NvkeError, PermissionDenied)):
        read(configured, source, **kwargs)


def test_graphql_returns_only_typed_observed_maintenance_metadata(configured):
    source, _, identity = support.observed(configured, overrides=payload())
    result = execute(
        configured.world,
        """
    query($site:GUID!,$operation:GUID!,$actor:Int!,$version:Int!) {
      astroliftNvkeMaintenanceObservation(siteId:$site,operationId:$operation,expectedActorId:$actor,expectedVersion:$version) {
        siteId operationId operationVersion observedAt clusterId nextCertificateRenewalDue renewalScheduleOverdueAtObservation
        mutationLockReported mutationLockOwner mutationLockStartedAt
        renewal { workflowId state outcome forced scheduledFor startedAt completedAt failedNodeId phase retainedMutationLock }
        etcdKeyRotation { workflowId state phase startedAt lastProgressAt completedAt failureReason retainedMutationLock }
      }
    }
    """,
        {
            "site": str(configured.site.guid),
            "operation": str(source.guid),
            "actor": configured.world.user.pk,
            "version": source.version,
        },
    )
    assert result.errors is None
    data = result.data["astroliftNvkeMaintenanceObservation"]
    assert data["clusterId"] == identity and data["operationVersion"] == source.version
    assert data["observedAt"]
    assert data["renewal"]["retainedMutationLock"] is True
    assert "PRIVATE" not in str(data)


def test_invalid_values_and_free_text_never_become_metadata(configured):
    native = payload()
    native["nextCertRenewalDue"] = "PRIVATE_DATE"
    native["conditions"]["updateInProgress"] = {"type": "PRIVATE_OWNER", "startedAt": "not-a-date"}
    native["conditions"]["certificateRenewal"].update(
        cycleID="https://user:PRIVATE_TOKEN@invalid.test/\n",
        forced=0,
        phase="PRIVATE_PHASE",
        failedNodeID="PRIVATE_NODE",
        completedAt="not-a-date",
    )
    source, _, _ = support.observed(configured, overrides=native)
    data = read(configured, source)[1]
    assert data["mutation_lock_owner"] is None
    assert data["mutation_lock_reported"] is True
    assert data["next_certificate_renewal_due"] is None
    assert data["renewal_schedule_overdue_at_observation"] is None
    assert data["renewal"]["retained_mutation_lock"] is None
    for field in ("workflow_id", "forced", "phase", "failed_node_id", "completed_at"):
        assert data["renewal"][field] is None
    assert "PRIVATE" not in str(data)


def test_foreign_observations_are_not_visible_and_connection_rotation_preserves_history(configured):
    source, _, _ = support.observed(configured, overrides=payload())
    foreign = support.configured.__wrapped__(support.world.__wrapped__())
    with pytest.raises(NvkeError) as hidden:
        read(foreign, source, site_id=str(configured.site.guid))
    assert hidden.value.code == "NOT_FOUND"
    configured.connection.soft_delete()
    assert read(configured, source)[1]["renewal"]["state"] == "failed"


@pytest.mark.parametrize("data", [{"response": "PRIVATE_BROKEN_RESPONSE"}, []])
def test_malformed_saved_response_has_a_safe_failure(configured, data):
    source, _, _ = support.observed(configured, overrides=payload())
    operations.save_result(source, data)
    source.save(update_fields=["result_backend_kind", "result_ciphertext", "updated_at", "version"])
    with pytest.raises(NvkeError) as error:
        read(configured, source)
    assert error.value.code == "SNAPSHOT_UNAVAILABLE"
    assert "PRIVATE" not in str(error.value)
