from __future__ import annotations

import copy
from uuid import uuid4

import pytest
from django.core import signing
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters.models import NvkeInstallPlan, NvkeOperation
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_operator import (  # noqa: F401
    agent,
    configured,
    partial_install,
    source_review,
    world,
)
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import RoleBinding
from core.permissions import PermissionDenied
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


def summary(state, row, plan_id):
    date = "2026-11-08T20:00:00+00:00"
    return {
        "format": 1,
        "itemCount": 2,
        "retainedBytes": "10",
        "proposedBytes": "10",
        "infrastructure": {
            "apiReady": True,
            "observedAt": date,
            "clusterUid": str(uuid4()),
            "workloadCount": 1,
        },
        "plan": {
            "format": 1,
            "scope": "remaining-management-install-proposal",
            "id": str(plan_id),
            "preparedAt": date,
            "source": {
                **dict.fromkeys(plans.protocol.DIGEST_FIELDS, "a" * 64),
                "siteId": str(state.site.guid),
                "instanceId": str(state.authority.instance_id),
                "jobId": str(row.guid),
                "nativeRunId": str(uuid4()),
                "nativeRevision": row.authority["revision"],
                "nativeExitCode": 1,
                "originalStartedAt": date,
                "reviewObservedAt": date,
                "originalDeploymentEvidence": "reconstructed-prepared-deployment",
                "bundleManifestSha256": state.authority.bundle_manifest_sha256,
            },
            "phaseEvidence": "retained-native-stderr-order-only",
            "requiresOriginalSourceVerification": True,
            "requiresExplicitAcknowledgement": True,
            "continuationAuthorized": False,
            "firstRemainingOrdinal": 1,
            "planDigest": "b" * 64,
        },
    }


@pytest.fixture
def prepared_fixture(configured, monkeypatch):  # noqa: F811
    state = configured
    row = partial_install(state)
    plan_id = uuid4()
    body = {
        "format": 1,
        "planId": str(plan_id),
        "request": None,
        "maximumInputBytes": "1000000",
        "overrides": {"files": {"bounded.json": "PRIVATE_PLAN_VALUES"}},
    }
    metadata = summary(state, row, plan_id)
    calls = []

    def prepare(_client, job, *, intent, body):
        assert job == str(row.guid) and intent == row.request_digest
        calls.append(copy.deepcopy(body))
        assert NvkeInstallPlan.objects.filter(guid=plan_id, status="preparing").exists() or len(calls) > 1
        return copy.deepcopy(metadata)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan", prepare)
    monkeypatch.setattr(OperatorAgentClient, "install_plan", lambda *_a, **_k: copy.deepcopy(metadata))
    return state, row, plan_id, body, metadata, calls


def test_preparation_is_durable_encrypted_and_leaves_original_operation_unchanged(prepared_fixture):
    state, row, plan_id, body, metadata, calls = prepared_fixture
    original = NvkeOperation.objects.filter(pk=row.pk).values().get()
    args = source_review(state, row)
    with context(state.world):
        result = plans.prepare(body=body, **args)
        assert plans.read(plan_id=plan_id, **args) == result
        assert plans.recover_preparation(plan_id=plan_id, **args) == result
    saved = NvkeInstallPlan.objects.get(guid=plan_id)
    assert saved.status == "prepared" and result["metadata"] == metadata
    assert b"PRIVATE_PLAN_VALUES" not in bytes(saved.input_ciphertext)
    assert (
        "PRIVATE_PLAN_VALUES"
        in decrypt(EncryptedSecret(saved.input_backend_kind, bytes(saved.input_ciphertext))).decode()
    )
    assert "PRIVATE_PLAN_VALUES" not in str(saved.summary) and saved.version == result["version"]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert NvkeOperation.objects.filter(pk=row.pk).values().get() == original
    assert NvkeOperation.objects.count() == 1
    assert list((state.agent.root / "state/jobs").iterdir()) == []


def test_lost_reply_recovers_by_get_without_another_preparation(prepared_fixture, monkeypatch):
    state, row, plan_id, body, metadata, calls = prepared_fixture

    def disconnect(*_a, **_k):
        calls.append(True)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Unconfirmed reply", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan", disconnect)
    args = source_review(state, row)
    with context(state.world):
        with pytest.raises(NvkeError):
            plans.prepare(body=body, **args)
        assert NvkeInstallPlan.objects.get(guid=plan_id).status == "preparing"
        result = plans.read(plan_id=plan_id, **args)
    assert result["metadata"] == metadata and len(calls) == 1


def test_exact_identity_conflicts_and_deleted_preparation_cannot_be_reused(prepared_fixture):
    state, row, plan_id, body, _metadata, calls = prepared_fixture
    args = source_review(state, row)
    with context(state.world):
        plans.prepare(body=body, **args)
        with pytest.raises(NvkeError) as error:
            plans.prepare(body={**body, "maximumInputBytes": "2000000"}, **args)
        assert error.value.code == "CONFLICT" and len(calls) == 1
        NvkeInstallPlan.objects.filter(guid=plan_id).update(deleted_at=timezone.now())
        with pytest.raises(NvkeError):
            plans.prepare(body=body, **args)
        with pytest.raises(NvkeError):
            plans.read(plan_id=plan_id, **args)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["version", "revoke", "success"])
def test_midflight_source_or_permission_changes_do_not_publish_a_plan(prepared_fixture, monkeypatch, change):
    state, row, plan_id, body, metadata, _calls = prepared_fixture

    def altered(*_a, **_k):
        if change == "revoke":
            RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
        elif change == "success":
            NvkeOperation.objects.filter(pk=row.pk).update(status="succeeded", version=row.version + 1)
        else:
            NvkeOperation.objects.filter(pk=row.pk).update(version=row.version + 1)
        return metadata

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan", altered)
    with context(state.world), pytest.raises((NvkeError, PermissionDenied)):
        plans.prepare(body=body, **source_review(state, row))
    assert NvkeInstallPlan.objects.get(guid=plan_id).status == "preparing"
    assert NvkeInstallPlan.objects.get(guid=plan_id).summary == {}


@pytest.mark.parametrize(
    "path,value",
    [
        (("plan", "continuationAuthorized"), True),
        (("plan", "source", "siteId"), str(uuid4())),
        (("privatePayload",), "PRIVATE"),
    ],
)
def test_malformed_adapter_metadata_cannot_enter_persistent_public_summary(prepared_fixture, path, value):
    state, row, plan_id, body, metadata, _calls = prepared_fixture
    target = metadata
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with context(state.world), pytest.raises(NvkeError) as error:
        plans.prepare(body=body, **source_review(state, row))
    assert error.value.code == "OPERATOR_INVALID_RESPONSE"
    assert NvkeInstallPlan.objects.get(guid=plan_id).summary == {}


def test_pages_bind_current_caller_source_search_view_and_limit(prepared_fixture, monkeypatch):
    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    calls = []

    def page(_client, job, **kwargs):
        calls.append(kwargs)
        return {
            "format": 1,
            "planId": str(plan_id),
            "planDigest": "b" * 64,
            "originalJobId": str(row.guid),
            "view": "items",
            "totalCount": 2,
            "nextCursor": "native-opaque-cursor",
            "items": [
                {
                    "name": "base",
                    "type": "template",
                    "originalType": "template",
                    "contentSha256": "a" * 64,
                    "originalContentSha256": "a" * 64,
                    "ordinal": 0,
                    "originalObservation": "returned-before-next-observed-item",
                    "proposedAction": "skip",
                }
            ],
        }

    monkeypatch.setattr(OperatorAgentClient, "install_plan_page", page)
    args = source_review(state, row)
    page_args = {"plan_id": plan_id, "plan_digest": "b" * 64, "view": "items", "limit": 1, **args}
    with context(state.world):
        plans.prepare(body=body, **args)
        result = plans.page(**page_args)
        cursor = result["next_cursor"]
        decoded = signing.loads(cursor, salt=plans.CURSOR_SALT)
        assert isinstance(decoded["source"], str) and row.request_digest not in str(decoded)
        for changed in ({"view": "workloads"}, {"search": "base"}, {"limit": 2}, {"after": ""}):
            with pytest.raises(NvkeError):
                plans.page(**{**page_args, "after": cursor, **changed})
        assert len(calls) == 1
        RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
        with pytest.raises(PermissionDenied):
            plans.page(**{**page_args, "after": cursor})
    assert len(calls) == 1


def test_plan_immutable_private_intent_and_ownership(prepared_fixture):
    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    with context(state.world):
        plans.prepare(body=body, **source_review(state, row))
    saved = NvkeInstallPlan.objects.get(guid=plan_id)
    saved.request_digest = "c" * 64
    with pytest.raises(ValueError):
        saved.save()


def test_another_current_admin_can_read_after_original_caller_revocation(prepared_fixture):
    from types import SimpleNamespace

    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member
    from core.permissions import Permission
    from core.tests.utils.scope_world import bind_role

    state, row, plan_id, body, metadata, calls = prepared_fixture
    args = source_review(state, row)
    with context(state.world):
        plans.prepare(body=body, **args)
    caller = get_user_model().objects.create(username="plan-admin-" + uuid4().hex, is_active=True)
    Member.objects.create(user=caller, scope_kind="ORG", scope_id=state.world.org.pk)
    bind_role(
        caller,
        permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
        kind="ORG",
        scope_id=state.world.org.pk,
        slug="plan-admin-" + uuid4().hex,
    )
    RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
    args["expected_actor_id"] = caller.pk
    with context(SimpleNamespace(org=state.world.org, user=caller)):
        assert plans.read(plan_id=plan_id, **args)["metadata"] == metadata
        with pytest.raises(NvkeError) as error:
            plans.recover_preparation(plan_id=plan_id, **args)
        assert error.value.code == "PRECONDITION"
    assert len(calls) == 1
    assert NvkeInstallPlan.objects.get(guid=plan_id).actor_id == state.world.user.pk


@pytest.mark.parametrize("field,value", [("summary", {}), ("status", "preparing"), ("guid", uuid4())])
def test_prepared_plan_metadata_and_identity_cannot_change(prepared_fixture, field, value):
    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    with context(state.world):
        plans.prepare(body=body, **source_review(state, row))
    saved = NvkeInstallPlan.objects.get(guid=plan_id)
    setattr(saved, field, value)
    with pytest.raises(ValueError):
        saved.save()


def test_changed_site_and_actor_are_refused_before_reading_an_agent(prepared_fixture, monkeypatch):
    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    args = source_review(state, row)
    with context(state.world):
        plans.prepare(body=body, **args)
    calls = []
    monkeypatch.setattr(OperatorAgentClient, "install_plan", lambda *_a, **_k: calls.append(True))
    with context(state.world):
        for changes in (
            {"expected_actor_id": state.world.user.pk + 999},
            {"expected_site_version": 0},
            {"expected_authority_version": 0},
            {"expected_operation_version": 0},
        ):
            with pytest.raises((NvkeError, PermissionDenied, NvkeSiteError)):
                plans.read(plan_id=plan_id, **{**args, **changes})
    assert not calls


def test_workload_pages_revalidate_decimal_precision_and_refuse_private_fields(prepared_fixture, monkeypatch):
    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    observed = {
        "namespace": "system",
        "kind": "Deployment",
        "name": "controller",
        "generation": "9007199254740993",
        "observedGeneration": None,
        "desiredReplicas": "1",
        "readyReplicas": None,
        "updatedReplicas": "0",
        "observation": "requires-review",
    }

    def page(*_a, **_k):
        return {
            "format": 1,
            "planId": str(plan_id),
            "planDigest": "b" * 64,
            "originalJobId": str(row.guid),
            "view": "workloads",
            "totalCount": 1,
            "nextCursor": None,
            "items": [copy.deepcopy(observed)],
        }

    monkeypatch.setattr(OperatorAgentClient, "install_plan_page", page)
    args = source_review(state, row)
    with context(state.world):
        plans.prepare(body=body, **args)
        page_args = {"plan_id": plan_id, "plan_digest": "b" * 64, "view": "workloads", **args}
        result = plans.page(**page_args)
        assert result["items"][0]["generation"] == "9007199254740993"
        assert result["items"][0]["readyReplicas"] is None
        observed["privatePath"] = "PRIVATE"
        with pytest.raises(NvkeError) as error:
            plans.page(**page_args)
        assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)


def test_plan_deleted_during_peer_page_is_never_exposed(prepared_fixture, monkeypatch):
    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    args = source_review(state, row)
    with context(state.world):
        plans.prepare(body=body, **args)

    def deleted(*_a, **_k):
        NvkeInstallPlan.objects.filter(guid=plan_id).update(deleted_at=timezone.now())
        return {"private": "PRIVATE"}

    monkeypatch.setattr(OperatorAgentClient, "install_plan_page", deleted)
    with context(state.world), pytest.raises(NvkeError) as error:
        plans.page(plan_id=plan_id, plan_digest="b" * 64, view="items", **args)
    assert error.value.code == "NOT_FOUND" and "PRIVATE" not in str(error.value)


def test_typed_schema_prepare_review_recovery_and_private_audit(prepared_fixture):
    import json

    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    state, row, plan_id, body, _metadata, calls = prepared_fixture
    review = {
        "operationId": str(row.guid),
        "expectedActorId": state.world.user.pk,
        "expectedSiteVersion": state.site.version,
        "expectedAuthorityVersion": state.authority.version,
        "expectedOperationVersion": row.version,
    }
    fields = "ok errors{code} data{id version operationId operationVersion itemCount retainedBytes plan{id planDigest continuationAuthorized source{nativeRunId nativeExitCode originalDeploymentEvidence}}}"
    query = (
        "mutation($alias:PrepareNvkeOperatorInstallPlanInput!){prepareNvkeOperatorInstallPlan(input:$alias){"
        + fields
        + "}}"
    )
    response = execute(state.world, query, {"alias": {**review, "requestText": json.dumps(body)}})
    assert response.errors is None and response.data["prepareNvkeOperatorInstallPlan"]["ok"]
    data = response.data["prepareNvkeOperatorInstallPlan"]["data"]
    assert data["id"] == str(plan_id) and data["operationVersion"] == row.version
    assert data["itemCount"] == 2 and data["plan"]["continuationAuthorized"] is False
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_prepare").latest(
        "timestamp"
    )
    assert "PRIVATE_PLAN_VALUES" not in json.dumps(audit.variables) and row.request_digest not in str(
        audit.variables
    )
    for action in ("reviewNvkeOperatorInstallPlan", "recoverNvkeOperatorInstallPlanPreparation"):
        response = execute(
            state.world,
            "mutation($review:NvkeOperatorInstallPlanReviewInput!){"
            + action
            + "(input:$review){"
            + fields
            + "}}",
            {"review": {**review, "planId": str(plan_id)}},
        )
        assert response.errors is None and response.data[action]["ok"]
        assert response.data[action]["data"] == data
    assert len(calls) == 2
    RoleBinding.objects.filter(user=state.world.user).update(deleted_at=timezone.now())
    refused = execute(state.world, query, {"alias": {**review, "requestText": json.dumps(body)}})
    assert (
        refused.errors is None
        and not refused.data["prepareNvkeOperatorInstallPlan"]["ok"]
        and len(calls) == 2
    )


def test_typed_schema_workload_page_preserves_int64_and_null_readiness(prepared_fixture, monkeypatch):
    from astrolift_clusters.tests.test_nvke_schema import execute

    state, row, plan_id, body, _metadata, _calls = prepared_fixture
    args = source_review(state, row)
    with context(state.world):
        plans.prepare(body=body, **args)
    value = {
        "format": 1,
        "planId": str(plan_id),
        "planDigest": "b" * 64,
        "originalJobId": str(row.guid),
        "view": "workloads",
        "totalCount": 1,
        "nextCursor": None,
        "items": [
            {
                "namespace": "system",
                "kind": "Deployment",
                "name": "controller",
                "generation": "9007199254740993",
                "observedGeneration": None,
                "desiredReplicas": "1",
                "readyReplicas": None,
                "updatedReplicas": "0",
                "observation": "requires-review",
            }
        ],
    }
    monkeypatch.setattr(OperatorAgentClient, "install_plan_page", lambda *_a, **_k: copy.deepcopy(value))
    query = """query($review:NvkeOperatorInstallPlanReviewInput!,$digest:String!){
    astroliftNvkeOperatorInstallPlanWorkloadPage(input:$review,planDigest:$digest){ok errors{code} data{
    planId totalCount nextCursor items{generation readyReplicas observation}}}}"""
    response = execute(
        state.world,
        query,
        {
            "digest": "b" * 64,
            "review": {
                "operationId": str(row.guid),
                "expectedActorId": state.world.user.pk,
                "expectedSiteVersion": state.site.version,
                "expectedAuthorityVersion": state.authority.version,
                "expectedOperationVersion": row.version,
                "planId": str(plan_id),
            },
        },
    )
    assert response.errors is None and response.data["astroliftNvkeOperatorInstallPlanWorkloadPage"]["ok"]
    result = response.data["astroliftNvkeOperatorInstallPlanWorkloadPage"]["data"]
    assert result["items"] == [
        {"generation": "9007199254740993", "readyReplicas": None, "observation": "requires-review"}
    ]


def test_schema_duplicate_private_json_is_refused_without_a_preparation(prepared_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute

    state, row, _plan_id, _body, _metadata, calls = prepared_fixture
    query = """mutation($input:PrepareNvkeOperatorInstallPlanInput!){prepareNvkeOperatorInstallPlan(input:$input){ok errors{code}}}"""
    response = execute(
        state.world,
        query,
        {
            "input": {
                "operationId": str(row.guid),
                "expectedActorId": state.world.user.pk,
                "expectedSiteVersion": state.site.version,
                "expectedAuthorityVersion": state.authority.version,
                "expectedOperationVersion": row.version,
                "requestText": '{"format":1,"format":1}',
            }
        },
    )
    assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlan"]["ok"]
    assert response.data["prepareNvkeOperatorInstallPlan"]["errors"][0]["code"] == "INVALID_INPUT"
    assert not calls and not NvkeInstallPlan.objects.exists()
