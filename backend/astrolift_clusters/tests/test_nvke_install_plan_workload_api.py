import json

import pytest
from django.utils import timezone

from astrolift_clusters.models import NvkeInstallPlanWorkload
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_contexts import review_input as context_review
from astrolift_clusters.tests.test_nvke_install_plan_workloads import (
    agent,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    tree_fixture,  # noqa: F401
    workload_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_schema import execute
from astrolift_identity.models import RoleBinding
from core.schema.audit import MutationAuditLog

pytestmark = pytest.mark.django_db
FIELDS = "ok errors{code} data{id version status planId planDigest chartArtifactId chartArtifactDigest treeId treeDigest artifactDigest sourceDigest captureDigest workloadDigest itemCount resourceCount imageUseCount imageReferenceCount issueCount observedAt executionState continuationAuthorized}"


def review_input(f):
    return {key: value for key, value in context_review(f.parent).items() if key not in {"item", "ordinal"}}


def prepare(f, text=None):
    field = "prepareNvkeOperatorInstallPlanWorkload"
    result = execute(
        f.state.world,
        "mutation($renamed:PrepareNvkeOperatorInstallPlanWorkloadInput!){"
        + field
        + "(input:$renamed){"
        + FIELDS
        + "}}",
        {"renamed": {**review_input(f), "requestText": json.dumps(f.body) if text is None else text}},
    )
    assert result.errors is None
    return result.data[field]


def test_typed_prepare_review_and_explicit_retry_preserve_counts_and_private_audit(workload_fixture):  # noqa: F811
    f = workload_fixture
    result = prepare(f)
    assert result["ok"]
    data = result["data"]
    assert data["id"] == f.body["artifactId"]
    assert data["workloadDigest"] == f.result["workloadDigest"]
    assert data["itemCount"] == f.result["itemCount"]
    assert data["resourceCount"] == f.result["resourceCount"]
    assert data["imageUseCount"] == f.result["imageUseCount"]
    assert not data["continuationAuthorized"]
    for field in (
        "reviewNvkeOperatorInstallPlanWorkload",
        "recoverNvkeOperatorInstallPlanWorkloadPreparation",
    ):
        result = execute(
            f.state.world,
            "mutation($v:NvkeOperatorInstallPlanWorkloadReviewInput!){"
            + field
            + "(input:$v){"
            + FIELDS
            + "}}",
            {"v": {**review_input(f), "artifactId": f.body["artifactId"]}},
        )
        assert result.errors is None and result.data[field]["ok"] and result.data[field]["data"] == data
    audit = MutationAuditLog.objects.filter(operation="nvke.operator_install_plan_workload_prepare").latest(
        "timestamp"
    )
    assert f.parent.body["artifactId"] not in str(audit.variables)
    assert "contextBindings" not in str(data)
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]


def test_rejected_and_denied_workload_requests_never_expose_private_text_or_collect(workload_fixture):  # noqa: F811
    f = workload_fixture
    for text in ("invalid PRIVATE_WORKLOAD", json.dumps({**f.body, "credentials": "PRIVATE_WORKLOAD"})):
        assert not prepare(f, text)["ok"]
        audit = MutationAuditLog.objects.filter(
            operation="nvke.operator_install_plan_workload_prepare"
        ).latest("timestamp")
        assert "PRIVATE_WORKLOAD" not in str(audit.variables)
    RoleBinding.objects.filter(user=f.state.world.user).update(deleted_at=timezone.now())
    assert not prepare(f)["ok"]
    assert not NvkeInstallPlanWorkload.objects.exists() and not f.calls


def test_wrong_context_digest_is_refused_before_inventory_collection(workload_fixture):  # noqa: F811
    f = workload_fixture
    changed = {
        **f.body,
        "contextBindings": {
            f.parent.body["item"]: {"contextId": f.parent.body["artifactId"], "contextDigest": "f" * 64}
        },
    }
    result = prepare(f, json.dumps(changed))
    assert not result["ok"] and result["data"] is None
    assert not NvkeInstallPlanWorkload.objects.exists() and not f.calls
