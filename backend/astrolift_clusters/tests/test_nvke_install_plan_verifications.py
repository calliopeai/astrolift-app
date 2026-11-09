from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_contexts as contexts
from astrolift_clusters import nvke_install_plan_verifications as verifications
from astrolift_clusters.models import NvkeInstallPlanContext, NvkeInstallPlanVerification
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_contexts import (
    agent,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    tree_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_sites import context
from core.secrets import EncryptedSecret, decrypt

pytestmark = pytest.mark.django_db


@pytest.fixture
def verification_fixture(context_fixture, monkeypatch):  # noqa: F811
    f = context_fixture
    with context(f.state.world):
        parent = contexts.prepare(body=f.body, **f.review)
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        "contextId": parent["id"],
        "contextDigest": f.result["contextDigest"],
        **{k: f.body[k] for k in ("planId", "planDigest", "treeId", "treeDigest", "item")},
        "maximumBytes": "16777216",
        "maximumCaptureAgeSeconds": 3600,
        "maximumCalls": 512,
        "timeoutSeconds": 300,
    }
    result = {
        **{k: v for k, v in body.items() if k not in verifications.protocol.WRITE_FIELDS},
        "artifactDigest": "4" * 64,
        "chartArtifactId": f.tree.body["chartArtifactId"],
        "chartArtifactDigest": f.tree.body["chartArtifactDigest"],
        "ordinal": 1,
        "captureDigest": f.result["captureDigest"],
        "frozenChartSha256": f.result["frozenChartSha256"],
        "verificationDigest": "5" * 64,
        "observedAt": f.result["observedAt"],
        "requests": 3,
        "bytes": "123",
        "executionState": verifications.protocol.STATE,
        "continuationAuthorized": False,
    }
    calls = []

    def invoke(kind, job, *, intent, body: dict, chart_artifact_id, chart_artifact_digest):
        assert job == str(f.original.guid) and intent == f.original.request_digest
        assert (
            chart_artifact_id == result["chartArtifactId"]
            and chart_artifact_digest == result["chartArtifactDigest"]
        )
        assert NvkeInstallPlanVerification.objects.filter(guid=body["artifactId"]).exists()
        calls.append((kind, copy.deepcopy(body)))
        return copy.deepcopy(result)

    monkeypatch.setattr(
        OperatorAgentClient,
        "prepare_install_plan_verification",
        lambda self, job, **kw: invoke("prepare", job, **kw),
    )
    monkeypatch.setattr(
        OperatorAgentClient, "install_plan_verification", lambda self, job, **kw: invoke("read", job, **kw)
    )
    return SimpleNamespace(
        state=f.state,
        original=f.original,
        parent=f,
        body=body,
        result=result,
        calls=calls,
        review={
            **f.review,
            "context_id": parent["id"],
            "expected_context_version": parent["version"],
            "context_digest": f.result["contextDigest"],
        },
    )


def test_encrypted_intent_historical_read_and_private_retry_preserve_context(verification_fixture):
    f = verification_fixture
    before = list(NvkeInstallPlanContext.all_objects.order_by("pk").values())
    with context(f.state.world):
        result = verifications.prepare(body=f.body, **f.review)
        assert verifications.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert verifications.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanVerification.objects.get(guid=f.body["artifactId"])
    assert json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))) == f.body
    assert b'"maximumCalls"' not in bytes(row.input_ciphertext)
    assert result["metadata"] == f.result and row.status == "prepared"
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert f.calls[1][1] == {k: v for k, v in f.body.items() if k not in verifications.protocol.WRITE_FIELDS}
    assert list(NvkeInstallPlanContext.all_objects.order_by("pk").values()) == before


def test_lost_reply_keeps_durable_intent_for_explicit_read(verification_fixture, monkeypatch):
    f = verification_fixture

    def lost(*a, **kw):
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost response", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_verification", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError) as error:
            verifications.prepare(body=f.body, **f.review)
        assert error.value.ambiguous
        assert NvkeInstallPlanVerification.objects.get(guid=f.body["artifactId"]).status == "preparing"
        assert verifications.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["read"]


@pytest.mark.parametrize("change", ["version", "summary", "delete"])
def test_context_drift_during_verification_cannot_publish(verification_fixture, monkeypatch, change):
    f = verification_fixture

    def drift(*a, **kw):
        row = NvkeInstallPlanContext.objects.filter(guid=f.body["contextId"])
        if change == "version":
            row.update(version=999)
        elif change == "summary":
            row.update(summary={**f.parent.result, "lookupCount": 1})
        else:
            row.update(deleted_at=timezone.now())
        return f.result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_verification", drift)
    with context(f.state.world), pytest.raises(NvkeError):
        verifications.prepare(body=f.body, **f.review)
    assert NvkeInstallPlanVerification.objects.get(guid=f.body["artifactId"]).summary == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("contextDigest", "f" * 64),
        ("captureDigest", "f" * 64),
        ("ordinal", 2),
        ("continuationAuthorized", True),
        ("privatePath", "PRIVATE"),
        ("requests", True),
    ],
)
def test_adapter_cannot_publish_unbound_private_or_authorizing_data(verification_fixture, field, value):
    f = verification_fixture
    f.result[field] = value
    with context(f.state.world), pytest.raises(NvkeError) as error:
        verifications.prepare(body=f.body, **f.review)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE" not in str(error.value)
    assert NvkeInstallPlanVerification.objects.get(guid=f.body["artifactId"]).summary == {}


def test_changed_policy_cannot_reuse_verification_uuid(verification_fixture):
    f = verification_fixture
    with context(f.state.world):
        verifications.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            verifications.prepare(body={**f.body, "maximumCalls": 511}, **f.review)
    assert error.value.code == "CONFLICT" and len(f.calls) == 1


def review_input(f):
    from astrolift_clusters.tests.test_nvke_install_plan_contexts import review_input as parent_review

    return {
        **parent_review(f.parent),
        "contextId": f.body["contextId"],
        "expectedContextVersion": f.review["expected_context_version"],
        "contextDigest": f.body["contextDigest"],
    }


FIELDS = "ok errors{code} data{id version status contextId contextVersion contextDigest artifactDigest verificationDigest ordinal requests bytes executionState continuationAuthorized}"


def test_typed_schema_prepare_read_retry_and_private_audit(verification_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = verification_fixture
    action = "prepareNvkeOperatorInstallPlanVerification"
    query = (
        "mutation($alias:PrepareNvkeOperatorInstallPlanVerificationInput!){"
        + action
        + "(input:$alias){"
        + FIELDS
        + "}}"
    )
    response = execute(
        f.state.world, query, {"alias": {**review_input(f), "requestText": json.dumps(f.body)}}
    )
    assert response.errors is None and response.data[action]["ok"]
    result = response.data[action]["data"]
    assert result["id"] == f.body["artifactId"] and result["continuationAuthorized"] is False
    for action in (
        "reviewNvkeOperatorInstallPlanVerification",
        "recoverNvkeOperatorInstallPlanVerificationPreparation",
    ):
        response = execute(
            f.state.world,
            "mutation($v:NvkeOperatorInstallPlanVerificationReviewInput!){"
            + action
            + "(input:$v){"
            + FIELDS
            + "}}",
            {"v": {**review_input(f), "artifactId": f.body["artifactId"]}},
        )
        assert (
            response.errors is None
            and response.data[action]["ok"]
            and response.data[action]["data"] == result
        )
    audit = MutationAuditLog.objects.filter(
        operation="nvke.operator_install_plan_verification_prepare"
    ).latest("timestamp")
    assert f.body["maximumBytes"] not in str(audit.variables) and f.parent.original.request_digest not in str(
        audit.variables
    )
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]


def test_rejected_text_and_denied_caller_never_leak_private_values(verification_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from astrolift_identity.models import RoleBinding
    from core.schema.audit import MutationAuditLog

    f = verification_fixture
    action = "prepareNvkeOperatorInstallPlanVerification"
    query = (
        "mutation($renamed:PrepareNvkeOperatorInstallPlanVerificationInput!){"
        + action
        + "(input:$renamed){ok errors{code}}}"
    )
    for text in (
        json.dumps({**f.body, "credentials": "PRIVATE_VERIFICATION"}),
        "not json PRIVATE_VERIFICATION",
    ):
        response = execute(f.state.world, query, {"renamed": {**review_input(f), "requestText": text}})
        assert response.errors is None and not response.data[action]["ok"]
        audit = MutationAuditLog.objects.filter(
            operation="nvke.operator_install_plan_verification_prepare"
        ).latest("timestamp")
        assert "PRIVATE_VERIFICATION" not in str(audit.variables)
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.state.world, query, {"renamed": {**review_input(f), "requestText": json.dumps(f.body)}}
    )
    assert response.errors is None and not response.data[action]["ok"]
    assert not NvkeInstallPlanVerification.objects.exists() and f.calls == []


def test_partial_verification_uuid_is_reserved_across_all_parent_kinds(verification_fixture, monkeypatch):
    from astrolift_clusters import nvke_install_plan_charts as charts
    from astrolift_clusters import nvke_install_plan_trees as trees
    from astrolift_clusters import nvke_install_plans as plans
    from astrolift_clusters.tests.test_nvke_operator import source_review

    f = verification_fixture

    def lost(*a, **kw):
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost response", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_verification", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError):
            verifications.prepare(body=f.body, **f.review)
        for invoke in (
            lambda: plans.prepare(
                body={
                    "format": 1,
                    "planId": f.body["artifactId"],
                    "request": None,
                    "maximumInputBytes": "10000",
                },
                **source_review(f.state, f.parent.original),
            ),
            lambda: charts.prepare(
                body={**f.parent.tree.chart.body, "artifactId": f.body["artifactId"]},
                **f.parent.tree.chart.review,
            ),
            lambda: trees.prepare(
                body={**f.parent.tree.body, "artifactId": f.body["artifactId"]}, **f.parent.tree.review
            ),
            lambda: contexts.prepare(
                body={**f.parent.body, "artifactId": f.body["artifactId"]}, **f.parent.review
            ),
        ):
            with pytest.raises(NvkeError) as error:
                invoke()
            assert error.value.code == "CONFLICT"
    assert NvkeInstallPlanVerification.objects.get(guid=f.body["artifactId"]).status == "preparing"


def test_tampered_private_intent_refuses_before_retry(verification_fixture):
    f = verification_fixture
    with context(f.state.world):
        verifications.prepare(body=f.body, **f.review)
        NvkeInstallPlanVerification.objects.filter(guid=f.body["artifactId"]).update(input_ciphertext=b"bad")
        with pytest.raises(NvkeError) as error:
            verifications.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
    assert error.value.code == "PREPARATION_UNAVAILABLE" and len(f.calls) == 1


def test_another_current_admin_can_read_but_cannot_retry_original_private_intent(verification_fixture):
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import Member, RoleBinding
    from core.permissions import Permission
    from core.tests.utils.scope_world import bind_role

    f = verification_fixture
    with context(f.state.world):
        verifications.prepare(body=f.body, **f.review)
    caller = get_user_model().objects.create(username="context-admin-" + uuid4().hex, is_active=True)
    Member.objects.create(user=caller, scope_kind="ORG", scope_id=f.state.world.org.pk)
    bind_role(
        caller,
        permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
        kind="ORG",
        scope_id=f.state.world.org.pk,
        slug="context-admin-" + uuid4().hex,
    )
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    with context(SimpleNamespace(org=f.state.world.org, user=caller)):
        assert (
            verifications.read(
                artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": caller.pk}
            )["metadata"]
            == f.result
        )
        with pytest.raises(NvkeError) as error:
            verifications.recover_preparation(
                artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": caller.pk}
            )
        assert error.value.code == "PRECONDITION"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_new_token_can_read_but_cannot_repeat_original_private_intent(verification_fixture):

    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = verification_fixture

    def token():
        issued = mint_token()
        return ApiToken.objects.create(
            user=f.state.world.user,
            organization=f.state.world.org,
            name="chart token",
            token_hash=issued.token_hash,
            token_last_4=issued.last4,
            scopes=["admin", "read"],
        )

    original, replacement = token(), token()
    mark = set_current_api_token(original)
    try:
        with context(f.state.world):
            verifications.prepare(body=f.body, **f.review)
    finally:
        reset_current_api_token(mark)
    ApiToken.objects.filter(pk=original.pk).update(is_revoked=True)
    mark = set_current_api_token(replacement)
    try:
        with context(f.state.world):
            assert verifications.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
            with pytest.raises(NvkeError) as error:
                verifications.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
            assert error.value.code == "PRECONDITION"
    finally:
        reset_current_api_token(mark)
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_token_revoked_during_remote_prepare_cannot_publish(verification_fixture, monkeypatch):
    from astrolift_clusters.nvke_sites import NvkeSiteError
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken
    from core.permissions import PermissionDenied

    f = verification_fixture
    issued = mint_token()
    token = ApiToken.objects.create(
        user=f.state.world.user,
        organization=f.state.world.org,
        name="chart revoked token",
        token_hash=issued.token_hash,
        token_last_4=issued.last4,
        scopes=["admin", "read"],
    )

    def revoked(*_a, **_k):
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
        return copy.deepcopy(f.result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_verification", revoked)
    mark = set_current_api_token(token)
    try:
        with context(f.state.world), pytest.raises((PermissionDenied, NvkeSiteError)) as error:
            verifications.prepare(body=f.body, **f.review)
        if isinstance(error.value, NvkeSiteError):
            assert error.value.code == "AUTHORITY_REVOKED"
    finally:
        reset_current_api_token(mark)
    assert NvkeInstallPlanVerification.objects.get(guid=f.body["artifactId"]).summary == {}


def test_other_tenant_cannot_read_retained_artifacts(verification_fixture):
    from astrolift_clusters.tests.test_nvke_sites import world as world_fixture
    from core.permissions import PermissionDenied

    f = verification_fixture
    with context(f.state.world):
        verifications.prepare(body=f.body, **f.review)
    other = world_fixture.__wrapped__()
    with context(other), pytest.raises((NvkeError, PermissionDenied)):
        verifications.read(
            artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": other.user.pk}
        )
    assert [kind for kind, _ in f.calls] == ["prepare"]
