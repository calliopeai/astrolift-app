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
from astrolift_clusters import nvke_install_plan_trees as trees
from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters.models import (
    NvkeInstallPlan,
    NvkeInstallPlanCharts,
    NvkeInstallPlanTree,
    NvkeOperation,
)
from astrolift_clusters.nvke_sites import NvkeSiteError
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_charts import chart_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plans import prepared_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_operator import agent, configured, source_review, world  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context
from astrolift_identity.models import Member, RoleBinding
from core.permissions import Permission, PermissionDenied
from core.secrets import EncryptedSecret, decrypt
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree_fixture(chart_fixture, monkeypatch):  # noqa: F811
    f = chart_fixture
    with context(f.state.world):
        publication = charts.prepare(body=f.body, **f.review)
    body = {
        "format": 1,
        "artifactId": str(uuid4()),
        "planId": f.body["planId"],
        "planDigest": f.body["planDigest"],
        "chartArtifactId": f.body["artifactId"],
        "chartArtifactDigest": f.result["artifactDigest"],
        "maximumBytes": "9007199254740994",
    }
    result = {
        "format": 1,
        "artifactId": body["artifactId"],
        "planId": body["planId"],
        "planDigest": body["planDigest"],
        "chartArtifactId": body["chartArtifactId"],
        "chartArtifactDigest": body["chartArtifactDigest"],
        "treeDigest": "d" * 64,
        "fileCount": 7,
        "bytes": body["maximumBytes"],
        "executionState": "requires-native-rendering-images-and-live-verification",
        "continuationAuthorized": False,
    }
    calls = []

    def prepare(_client, job, *, intent, body):
        assert job == str(f.original.guid) and intent == f.original.request_digest
        assert NvkeInstallPlanTree.objects.filter(guid=body["artifactId"]).exists()
        calls.append(("prepare", copy.deepcopy(body)))
        return copy.deepcopy(result)

    def read(
        _client, job, *, intent, plan_id, plan_digest, chart_artifact_id, chart_artifact_digest, artifact_id
    ):
        assert job == str(f.original.guid) and intent == f.original.request_digest
        assert (plan_id, plan_digest, chart_artifact_id, chart_artifact_digest, artifact_id) == (
            body["planId"],
            body["planDigest"],
            body["chartArtifactId"],
            body["chartArtifactDigest"],
            body["artifactId"],
        )
        calls.append(("read", None))
        return copy.deepcopy(result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_tree", prepare)
    monkeypatch.setattr(OperatorAgentClient, "install_plan_tree", read)
    review = {
        **f.review,
        "chart_artifact_id": publication["id"],
        "expected_chart_artifact_version": publication["version"],
        "chart_artifact_digest": body["chartArtifactDigest"],
    }
    return SimpleNamespace(
        state=f.state, original=f.original, body=body, result=result, calls=calls, review=review, chart=f
    )


def test_durable_encrypted_intent_and_read_preserve_parent_and_original(tree_fixture):
    f = tree_fixture
    original = NvkeOperation.objects.filter(pk=f.original.pk).values().get()
    parent = NvkeInstallPlan.objects.filter(guid=f.body["planId"]).values().get()
    publication = NvkeInstallPlanCharts.objects.filter(guid=f.body["chartArtifactId"]).values().get()
    with context(f.state.world):
        result = trees.prepare(body=f.body, **f.review)
        assert trees.read(artifact_id=f.body["artifactId"], **f.review) == result
        assert trees.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    row = NvkeInstallPlanTree.objects.get(guid=f.body["artifactId"])
    assert json.loads(decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))) == f.body
    assert f.body["maximumBytes"].encode() not in bytes(row.input_ciphertext)
    assert row.status == "prepared" and result["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert NvkeOperation.objects.filter(pk=f.original.pk).values().get() == original
    assert NvkeInstallPlan.objects.filter(guid=f.body["planId"]).values().get() == parent
    assert NvkeInstallPlanCharts.objects.filter(guid=f.body["chartArtifactId"]).values().get() == publication
    assert NvkeOperation.objects.count() == 1


def test_lost_reply_recovers_by_read_without_another_preparation(tree_fixture, monkeypatch):
    f = tree_fixture

    def lost(*_a, **_k):
        f.calls.append(("lost", None))
        raise NvkeError("OPERATOR_UNAVAILABLE", "Deliberately lost response", ambiguous=True)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_tree", lost)
    with context(f.state.world):
        with pytest.raises(NvkeError):
            trees.prepare(body=f.body, **f.review)
        assert NvkeInstallPlanTree.objects.get(guid=f.body["artifactId"]).status == "preparing"
        assert trees.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["lost", "read"]


@pytest.mark.parametrize(
    "change",
    [
        "plan-version",
        "plan-digest",
        "revoke",
        "original-version",
        "delete-parent",
        "chart-version",
        "chart-digest",
        "delete-chart",
    ],
)
def test_midflight_source_or_permission_change_cannot_publish(tree_fixture, monkeypatch, change):
    f = tree_fixture

    def drift(*_a, **_k):
        if change == "plan-version":
            NvkeInstallPlan.objects.filter(guid=f.body["planId"]).update(version=999)
        elif change == "plan-digest":
            row = NvkeInstallPlan.objects.get(guid=f.body["planId"])
            value = copy.deepcopy(row.summary)
            value["plan"]["planDigest"] = "f" * 64
            NvkeInstallPlan.objects.filter(pk=row.pk).update(summary=value)
        elif change == "revoke":
            RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
        elif change == "original-version":
            NvkeOperation.objects.filter(pk=f.original.pk).update(version=999)
        elif change == "chart-version":
            NvkeInstallPlanCharts.objects.filter(guid=f.body["chartArtifactId"]).update(version=999)
        elif change == "chart-digest":
            row = NvkeInstallPlanCharts.objects.get(guid=f.body["chartArtifactId"])
            value = copy.deepcopy(row.summary)
            value["artifactDigest"] = "f" * 64
            NvkeInstallPlanCharts.objects.filter(pk=row.pk).update(summary=value)
        elif change == "delete-chart":
            NvkeInstallPlanCharts.objects.filter(guid=f.body["chartArtifactId"]).update(
                deleted_at=timezone.now()
            )
        else:
            NvkeInstallPlan.objects.filter(guid=f.body["planId"]).update(deleted_at=timezone.now())
        return copy.deepcopy(f.result)

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_tree", drift)
    with context(f.state.world), pytest.raises((NvkeError, PermissionDenied)):
        trees.prepare(body=f.body, **f.review)
    row = NvkeInstallPlanTree.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("continuationAuthorized", True),
        ("bytes", "9223372036854775807"),
        ("privatePath", "PRIVATE_CHART_INPUT"),
        ("artifactId", str(uuid4())),
        ("chartArtifactId", str(uuid4())),
        ("chartArtifactDigest", "f" * 64),
        ("bytes", "1"),
        ("fileCount", 0),
    ],
)
def test_custom_adapter_malformed_or_authorizing_reply_is_redacted(tree_fixture, field, value):
    f = tree_fixture
    f.result[field] = value
    with context(f.state.world), pytest.raises(NvkeError) as error:
        trees.prepare(body=f.body, **f.review)
    assert error.value.code == "OPERATOR_INVALID_RESPONSE" and "PRIVATE_CHART_INPUT" not in str(error.value)
    assert NvkeInstallPlanTree.objects.get(guid=f.body["artifactId"]).summary == {}


def test_another_current_admin_can_read_but_cannot_retry_private_intent(tree_fixture):
    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
    caller = get_user_model().objects.create(username="chart-admin-" + uuid4().hex, is_active=True)
    Member.objects.create(user=caller, scope_kind="ORG", scope_id=f.state.world.org.pk)
    bind_role(
        caller,
        permissions=[Permission.NVKE_ADMIN, Permission.NVKE_READ],
        kind="ORG",
        scope_id=f.state.world.org.pk,
        slug="chart-admin-" + uuid4().hex,
    )
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    review = {**f.review, "expected_actor_id": caller.pk}
    with context(SimpleNamespace(org=f.state.world.org, user=caller)):
        assert trees.read(artifact_id=f.body["artifactId"], **review)["metadata"] == f.result
        with pytest.raises(NvkeError) as error:
            trees.recover_preparation(artifact_id=f.body["artifactId"], **review)
        assert error.value.code == "PRECONDITION"
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_changed_intent_deleted_identity_and_cross_kind_collision_are_refused(tree_fixture):
    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            trees.prepare(body={**f.body, "maximumBytes": "1"}, **f.review)
        assert error.value.code == "CONFLICT"
        with pytest.raises(NvkeError):
            trees.prepare(body={**f.body, "artifactId": str(f.original.guid)}, **f.review)
        NvkeInstallPlanTree.objects.filter(guid=f.body["artifactId"]).update(deleted_at=timezone.now())
        with pytest.raises(NvkeError):
            trees.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError):
            trees.read(artifact_id=f.body["artifactId"], **f.review)
    assert [kind for kind, _ in f.calls] == ["prepare"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("summary", {}),
        ("status", "preparing"),
        ("request_digest", "f" * 64),
        ("input_ciphertext", b"tampered"),
        ("charts_id", None),
    ],
)
def test_prepared_identity_private_intent_and_summary_are_immutable(tree_fixture, field, value):
    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
    row = NvkeInstallPlanTree.objects.get(guid=f.body["artifactId"])
    setattr(row, field, value)
    with pytest.raises(ValueError):
        row.save()


def test_tampered_retained_private_intent_is_refused_before_agent_retry(tree_fixture):
    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
        NvkeInstallPlanTree.objects.filter(guid=f.body["artifactId"]).update(request_digest="0" * 64)
        with pytest.raises(NvkeError) as error:
            trees.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
        assert error.value.code == "PREPARATION_UNAVAILABLE"
    assert [kind for kind, _ in f.calls] == ["prepare"]


def test_tree_identity_cannot_be_reused_as_a_plan(tree_fixture):
    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            plans.prepare(
                body={
                    "format": 1,
                    "planId": f.body["artifactId"],
                    "request": None,
                    "maximumInputBytes": "10000",
                },
                **source_review(f.state, f.original),
            )
        assert error.value.code == "CONFLICT"


def graph_review(f):
    return {
        "operationId": str(f.original.guid),
        "expectedActorId": f.state.world.user.pk,
        "expectedSiteVersion": f.state.site.version,
        "expectedAuthorityVersion": f.state.authority.version,
        "expectedOperationVersion": f.original.version,
        "planId": f.body["planId"],
        "expectedPlanVersion": f.review["expected_plan_version"],
        "planDigest": f.body["planDigest"],
        "chartArtifactId": f.body["chartArtifactId"],
        "expectedChartArtifactVersion": f.review["expected_chart_artifact_version"],
        "chartArtifactDigest": f.body["chartArtifactDigest"],
    }


FIELDS = "ok errors{code} data{id version status planId planVersion chartArtifactId chartArtifactVersion operationId operationVersion siteId siteVersion authorityId authorityVersion planDigest chartArtifactDigest treeDigest fileCount bytes executionState continuationAuthorized}"


def test_typed_schema_prepare_review_retry_and_private_audit(tree_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = tree_fixture
    prepare = (
        "mutation($alias:PrepareNvkeOperatorInstallPlanTreeInput!){prepareNvkeOperatorInstallPlanTree(input:$alias){"
        + FIELDS
        + "}}"
    )
    review = graph_review(f)
    response = execute(f.state.world, prepare, {"alias": {**review, "requestText": json.dumps(f.body)}})
    assert response.errors is None and response.data["prepareNvkeOperatorInstallPlanTree"]["ok"]
    data = response.data["prepareNvkeOperatorInstallPlanTree"]["data"]
    assert data["id"] == f.body["artifactId"] and data["bytes"] == f.body["maximumBytes"]
    assert data["chartArtifactId"] == f.body["chartArtifactId"] and data["continuationAuthorized"] is False
    for action in ("reviewNvkeOperatorInstallPlanTree", "recoverNvkeOperatorInstallPlanTreePreparation"):
        response = execute(
            f.state.world,
            "mutation($v:NvkeOperatorInstallPlanTreeReviewInput!){" + action + "(input:$v){" + FIELDS + "}}",
            {"v": {**review, "artifactId": f.body["artifactId"]}},
        )
        assert (
            response.errors is None and response.data[action]["ok"] and response.data[action]["data"] == data
        )
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_tree_prepare").latest(
        "timestamp"
    )
    assert f.original.request_digest not in str(audit.variables)
    assert f.body["maximumBytes"] not in str(audit.variables)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]


def test_typed_schema_rejected_private_text_and_denied_caller_are_redacted(tree_fixture):
    from astrolift_clusters.tests.test_nvke_schema import execute
    from core.schema.audit import MutationAuditLog

    f = tree_fixture
    query = "mutation($renamed:PrepareNvkeOperatorInstallPlanTreeInput!){prepareNvkeOperatorInstallPlanTree(input:$renamed){ok errors{code}}}"
    values = {**graph_review(f), "requestText": json.dumps({**f.body, "credentials": "PRIVATE_CHART_INPUT"})}
    response = execute(f.state.world, query, {"renamed": values})
    assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanTree"]["ok"]
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_tree_prepare").latest(
        "timestamp"
    )
    assert "PRIVATE_CHART_INPUT" not in str(audit.variables)
    assert not NvkeInstallPlanTree.objects.exists() and f.calls == []
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    response = execute(
        f.state.world, query, {"renamed": {**values, "requestText": "not json PRIVATE_CHART_INPUT"}}
    )
    assert response.errors is None and not response.data["prepareNvkeOperatorInstallPlanTree"]["ok"]
    assert f.calls == []


def test_wrong_plan_review_refused_before_agent_io(tree_fixture):
    f = tree_fixture
    with context(f.state.world):
        for change in (
            {"expected_plan_version": 0},
            {"plan_digest": "f" * 64},
            {"plan_id": uuid4()},
            {"expected_chart_artifact_version": 0},
            {"chart_artifact_digest": "f" * 64},
            {"chart_artifact_id": uuid4()},
        ):
            with pytest.raises(NvkeError):
                trees.prepare(body=f.body, **{**f.review, **change})
    assert f.calls == [] and not NvkeInstallPlanTree.objects.exists()


def test_new_token_can_read_but_cannot_repeat_original_private_intent(tree_fixture):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = tree_fixture

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
            trees.prepare(body=f.body, **f.review)
    finally:
        reset_current_api_token(mark)
    ApiToken.objects.filter(pk=original.pk).update(is_revoked=True)
    mark = set_current_api_token(replacement)
    try:
        with context(f.state.world):
            assert trees.read(artifact_id=f.body["artifactId"], **f.review)["metadata"] == f.result
            with pytest.raises(NvkeError) as error:
                trees.recover_preparation(artifact_id=f.body["artifactId"], **f.review)
            assert error.value.code == "PRECONDITION"
    finally:
        reset_current_api_token(mark)
    assert [kind for kind, _ in f.calls] == ["prepare", "read"]


def test_token_revoked_during_remote_prepare_cannot_publish(tree_fixture, monkeypatch):
    from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken

    f = tree_fixture
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

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_tree", revoked)
    mark = set_current_api_token(token)
    try:
        with context(f.state.world), pytest.raises((PermissionDenied, NvkeSiteError)) as error:
            trees.prepare(body=f.body, **f.review)
        if isinstance(error.value, NvkeSiteError):
            assert error.value.code == "AUTHORITY_REVOKED"
    finally:
        reset_current_api_token(mark)
    assert NvkeInstallPlanTree.objects.get(guid=f.body["artifactId"]).summary == {}


def test_other_tenant_cannot_read_retained_artifacts(tree_fixture):
    from astrolift_clusters.tests.test_nvke_sites import world as world_fixture

    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
    other = world_fixture.__wrapped__()
    with context(other), pytest.raises((NvkeError, PermissionDenied)):
        trees.read(artifact_id=f.body["artifactId"], **{**f.review, "expected_actor_id": other.user.pk})
    assert [kind for kind, _ in f.calls] == ["prepare"]


def test_tree_identity_cannot_be_reused_as_a_chart_publication(tree_fixture):
    f = tree_fixture
    with context(f.state.world):
        trees.prepare(body=f.body, **f.review)
        with pytest.raises(NvkeError) as error:
            charts.prepare(body={**f.chart.body, "artifactId": f.body["artifactId"]}, **f.chart.review)
        assert error.value.code == "CONFLICT"
    assert [kind for kind, _ in f.calls] == ["prepare"]
