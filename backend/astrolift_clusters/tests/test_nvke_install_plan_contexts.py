from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_charts as charts
from astrolift_clusters import nvke_install_plan_contexts as contexts
from astrolift_clusters import nvke_install_plan_trees as trees
from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanContext,
    NvkeInstallPlanTree,
    NvkeOperation,
)
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_charts import chart_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_trees import (
    graph_review,
    tree_fixture,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_install_plans import prepared_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_operator import agent, configured, source_review, world  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import Member, RoleBinding
from core.permissions import Permission, PermissionDenied
from core.secrets import EncryptedSecret, decrypt
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def context_fixture(tree_fixture, monkeypatch):  # noqa: F811
    f = tree_fixture
    with context(f.state.world):
        parent = trees.prepare(body=f.body, **f.review)
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        "planId": f.body["planId"],
        "planDigest": f.body["planDigest"],
        "treeId": f.body["artifactId"],
        "treeDigest": f.result["treeDigest"],
        "item": "nktp",
        "revision": "9007199254740993",
        "upgrade": True,
        "maximumBytes": "16777216",
        "maximumArtifactBytes": "1048576",
        "maximumCalls": 512,
        "maximumIterations": 64,
        "timeoutSeconds": 300,
    }
    result = {
        **{k: v for k, v in body.items() if k not in contexts.protocol.WRITE_FIELDS},
        "ordinal": 1,
        "captureDigest": "1" * 64,
        "frozenChartSha256": "2" * 64,
        "contextDigest": "3" * 64,
        "documentCount": 1,
        "lookupCount": 0,
        "observedAt": "2026-11-09T16:00:00+00:00",
        "executionState": contexts.protocol.STATE,
        "continuationAuthorized": False,
    }
    calls = []

    def invoke(kind, job, intent, value, chart_artifact_id, chart_artifact_digest):
        assert job == str(f.original.guid) and intent == f.original.request_digest
        assert chart_artifact_id == f.body["chartArtifactId"]
        assert chart_artifact_digest == f.body["chartArtifactDigest"]
        assert NvkeInstallPlanContext.objects.filter(guid=value["artifactId"]).exists()
        expected = (
            body
            if kind == "prepare"
            else {k: v for k, v in body.items() if k not in contexts.protocol.WRITE_FIELDS}
        )
        assert value == expected
        calls.append((kind, copy.deepcopy(value)))
        return copy.deepcopy(result)

    def prepare(_client, job, *, intent, body, chart_artifact_id, chart_artifact_digest):
        return invoke("prepare", job, intent, body, chart_artifact_id, chart_artifact_digest)

    def read(_client, job, *, intent, body, chart_artifact_id, chart_artifact_digest):
        return invoke("read", job, intent, body, chart_artifact_id, chart_artifact_digest)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_context", prepare)
    monkeypatch.setattr(OperatorAgentClient, "install_plan_context", read)
    review = {
        **f.review,
        "tree_id": parent["id"],
        "expected_tree_version": parent["version"],
        "tree_digest": f.result["treeDigest"],
        "item": "nktp",
        "ordinal": 1,
    }
    return SimpleNamespace(
        state=f.state, original=f.original, tree=f, body=body, result=result, review=review, calls=calls
    )


def test_durable_encrypted_intent_read_and_private_retry_preserve_all_parents(context_fixture):
    f = context_fixture
    before = {
        model: list(model.all_objects.order_by("pk").values())
        for model in (
            NvkeOperation,
            NvkeInstallPlan,
            NvkeInstallPlanCharts,
            NvkeInstallPlanTree,
        )
    }
    with context(f.state.world):
        result = contexts.prepare(body=f.body, **f.review)
        assert contexts.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert contexts.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"])
    assert json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))) == f.body
    assert f.body["revision"].encode() not in bytes(row.input_ciphertext)
    assert row.status == "prepared" and result["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert result["chart_artifact_digest"] == f.tree.body["chartArtifactDigest"]
    for model, snapshot in before.items():
        assert list(model.all_objects.order_by("pk").values()) == snapshot


def test_lost_prepare_reply_recovers_by_get_without_another_prepare(context_fixture, monkeypatch):
    f = context_fixture

    def lost(*_a, **_k):
        f.calls.append(("lost", None))
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost response", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_context", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError) as error:
            contexts.prepare(body=f.body, **f.review)
        assert error.value.ambiguous
        assert NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"]).status == "preparing"
        assert contexts.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["lost", "read"]


@pytest.mark.parametrize(
    "change",
    [
        "plan-version",
        "chart-version",
        "tree-version",
        "tree-digest",
        "delete-tree",
        "original-version",
        "revoke",
    ],
)
def test_source_or_permission_changes_during_remote_capture_cannot_publish(
    context_fixture, monkeypatch, change
):
    f = context_fixture

    def drift(*_a, **_k):
        if change == "revoke":
            RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
        elif change == "original-version":
            NvkeOperation.objects.filter(pk=f.original.pk).update(version=999)
        elif change == "plan-version":
            NvkeInstallPlan.objects.filter(guid=f.body["planId"]).update(version=999)
        elif change == "chart-version":
            NvkeInstallPlanCharts.objects.filter(guid=f.tree.body["chartArtifactId"]).update(version=999)
        elif change == "tree-version":
            NvkeInstallPlanTree.objects.filter(guid=f.body["treeId"]).update(version=999)
        elif change == "tree-digest":
            value = copy.deepcopy(f.tree.result)
            value["treeDigest"] = "f" * 64
            NvkeInstallPlanTree.objects.filter(guid=f.body["treeId"]).update(summary=value)
        else:
            NvkeInstallPlanTree.objects.filter(guid=f.body["treeId"]).update(deleted_at=timezone.now())
        return copy.deepcopy(f.result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_context", drift)
    with context(f.state.world), pytest.raises((NvkeError, NvkeSiteError, PermissionDenied)):
        contexts.prepare(body=f.body, **f.review)
    assert NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"]).summary == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("ordinal", 2),
        ("item", "controller"),
        ("treeDigest", "f" * 64),
        ("captureDigest", "PRIVATE_CONTEXT"),
        ("contextDigest", None),
        ("frozenChartSha256", "PRIVATE_CONTEXT"),
        ("documentCount", -1),
        ("lookupCount", True),
        ("observedAt", "yesterday PRIVATE_CONTEXT"),
        ("continuationAuthorized", True),
        ("privatePath", "PRIVATE_CONTEXT"),
    ],
)
def test_custom_adapter_cannot_publish_private_unbound_or_authorizing_data(context_fixture, field, value):
    f = context_fixture
    f.result[field] = value
    with context(f.state.world), pytest.raises(NvkeError) as error:
        contexts.prepare(body=f.body, **f.review)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE_CONTEXT" not in str(error.value)
    assert NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"]).summary == {}


def test_another_current_admin_can_read_but_cannot_retry_original_private_intent(context_fixture):
    f = context_fixture
    with context(f.state.world):
        contexts.prepare(body=f.body, **f.review)
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
            contexts.read(artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": caller.pk})[
                "metadata"
            ]
            == f.result
        )
        with pytest.raises(NvkeError) as error:
            contexts.recover_preparation(
                artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": caller.pk}
            )
        assert error.value.code == "PRECONDITION"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


@pytest.mark.parametrize(
    "change",
    [
        {"expected_tree_version": 999},
        {"tree_digest": "f" * 64},
        {"tree_id": uuid4()},
        {"ordinal": True},
        {"ordinal": -1},
        {"ordinal": 3},
        {"item": "../PRIVATE_CONTEXT"},
    ],
)
def test_wrong_tree_review_refuses_before_agent_or_intent(context_fixture, change):
    f = context_fixture
    with context(f.state.world), pytest.raises(NvkeError):
        contexts.prepare(body=f.body, **{**f.review, **change})
    assert f.calls == [] and not NvkeInstallPlanContext.objects.exists()


def test_changed_or_deleted_context_intent_is_not_reused(context_fixture):
    f = context_fixture
    with context(f.state.world):
        contexts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            contexts.prepare(body={**f.body, "maximumCalls": 511}, **f.review)
        assert error.value.code == "CONFLICT"
        NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"]).soft_delete()
        with pytest.raises(NvkeError):
            contexts.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError):
            contexts.read(artifact_id=f.body["artifactId"], **f.review)
    assert [kind for kind, _ in f.calls] == ["prepare"]


def test_partial_context_identity_is_reserved_across_plan_chart_and_tree(context_fixture, monkeypatch):
    f = context_fixture

    def lost(*_a, **_k):
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost response", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_context", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError):
            contexts.prepare(body=f.body, **f.review)
        for invoke in (
            lambda: plans.prepare(
                body={
                    "format": 1,
                    "planId": f.body["artifactId"],
                    "request": None,
                    "maximumInputBytes": "10000",
                },
                **source_review(f.state, f.original),
            ),
            lambda: charts.prepare(
                body={**f.tree.chart.body, "artifactId": f.body["artifactId"]}, **f.tree.chart.review
            ),
            lambda: trees.prepare(body={**f.tree.body, "artifactId": f.body["artifactId"]}, **f.tree.review),
        ):
            with pytest.raises(NvkeError) as error:
                invoke()
            assert error.value.code == "CONFLICT"
    assert NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"]).status == "preparing"


def test_tampered_ciphertext_is_unavailable_before_agent_retry(context_fixture):
    f = context_fixture
    with context(f.state.world):
        contexts.prepare(body=f.body, **f.review)
        NvkeInstallPlanContext.objects.filter(guid=f.body["artifactId"]).update(input_ciphertext=b"bad")
        with pytest.raises(NvkeError) as error:
            contexts.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
        assert error.value.code == "PREPARATION_UNAVAILABLE"
    assert [kind for kind, _ in f.calls] == ["prepare"]


def review_input(f):
    return {
        **graph_review(f.tree),
        "treeId": f.body["treeId"],
        "expectedTreeVersion": f.review["expected_tree_version"],
        "treeDigest": f.body["treeDigest"],
        "item": "nktp",
        "ordinal": 1,
    }


FIELDS = "ok errors{code} data{id version status planId planVersion chartArtifactId chartArtifactVersion chartArtifactDigest treeId treeVersion treeDigest operationId operationVersion siteId siteVersion authorityId authorityVersion item ordinal captureDigest frozenChartSha256 contextDigest documentCount lookupCount observedAt executionState continuationAuthorized}"


def test_typed_schema_prepare_read_retry_and_private_audit(context_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = context_fixture
    query = (
        "mutation($alias:PrepareNvkeOperatorInstallPlanContextInput!){prepareNvkeOperatorInstallPlanContext(input:$alias){"
        + FIELDS
        + "}}"
    )
    response = execute(
        f.state.world, query, {"alias": {**review_input(f), "requestText": json.dumps(f.body)}}
    )
    assert response.errors is None and response.data["prepareNvkeOperatorInstallPlanContext"]["ok"]
    result = response.data["prepareNvkeOperatorInstallPlanContext"]["data"]
    assert result["id"] == f.body["artifactId"] and result["ordinal"] == 1
    assert result["contextDigest"] == f.result["contextDigest"] and result["continuationAuthorized"] is False
    for action in (
        "reviewNvkeOperatorInstallPlanContext",
        "recoverNvkeOperatorInstallPlanContextPreparation",
    ):
        response = execute(
            f.state.world,
            "mutation($v:NvkeOperatorInstallPlanContextReviewInput!){"
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
    log = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_context_prepare").latest(
        "timestamp"
    )
    assert f.body["revision"] not in str(log.variables) and f.original.request_digest not in str(
        log.variables
    )
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]


def test_private_rejected_text_and_denied_caller_are_redacted(context_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = context_fixture
    query = "mutation($renamed:PrepareNvkeOperatorInstallPlanContextInput!){prepareNvkeOperatorInstallPlanContext(input:$renamed){ok errors{code}}}"
    for text in (json.dumps({**f.body, "credentials": "PRIVATE_CONTEXT"}), "not json PRIVATE_CONTEXT"):
        response = execute(f.state.world, query, {"renamed": {**review_input(f), "requestText": text}})
        assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanContext"]["ok"]
        log = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_context_prepare").latest(
            "timestamp"
        )
        assert "PRIVATE_CONTEXT" not in str(log.variables)
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.state.world, query, {"renamed": {**review_input(f), "requestText": json.dumps(f.body)}}
    )
    assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanContext"]["ok"]
    assert f.calls == [] and not NvkeInstallPlanContext.objects.exists()


def test_new_token_can_read_but_cannot_repeat_original_private_intent(context_fixture):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = context_fixture

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
            contexts.prepare(body=f.body, **f.review)
    finally:
        reset_current_api_token(mark)
    ApiToken.objects.filter(pk=original.pk).update(is_revoked=True)
    mark = set_current_api_token(replacement)
    try:
        with context(f.state.world):
            assert contexts.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
            with pytest.raises(NvkeError) as error:
                contexts.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
            assert error.value.code == "PRECONDITION"
    finally:
        reset_current_api_token(mark)
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_token_revoked_during_remote_prepare_cannot_publish(context_fixture, monkeypatch):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = context_fixture
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

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_context", revoked)
    mark = set_current_api_token(token)
    try:
        with context(f.state.world), pytest.raises((PermissionDenied, NvkeSiteError)) as error:
            contexts.prepare(body=f.body, **f.review)
        if isinstance(error.value, NvkeSiteError):
            assert error.value.code == "AUTHORITY_REVOKED"
    finally:
        reset_current_api_token(mark)
    assert NvkeInstallPlanContext.objects.get(guid=f.body["artifactId"]).summary == {}


def test_other_tenant_cannot_read_retained_artifacts(context_fixture):
    from astrolift_clusters.tests.test_nvke_sites import world as world_fixture

    f = context_fixture
    with context(f.state.world):
        contexts.prepare(body=f.body, **f.review)
    other = world_fixture.__wrapped__()
    with context(other), pytest.raises((NvkeError, PermissionDenied)):
        contexts.read(artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": other.user.pk})
    assert [kind for kind, _ in f.calls] == ["prepare"]
