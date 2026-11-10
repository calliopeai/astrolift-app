from uuid import uuid4

import pytest
from django.db.models import F
from django.utils import timezone
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_images as images
from astrolift_clusters import nvke_install_plan_workloads as workloads
from astrolift_clusters.models import NvkeInstallPlanImage, NvkeInstallPlanWorkload
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
from astrolift_clusters.tests.test_nvke_install_plan_images import (
    image_fixture,  # noqa: F401
    review_input,
)
from astrolift_clusters.tests.test_nvke_install_plan_workloads import workload_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context

pytestmark = pytest.mark.django_db


@pytest.fixture
def linked_image_fixture(workload_fixture, image_fixture):  # noqa: F811
    workload, image = workload_fixture, image_fixture
    with context(workload.state.world):
        workloads.prepare(body=workload.body, **workload.review)
    row = NvkeInstallPlanWorkload.objects.get(guid=workload.body["artifactId"])
    image.body.update(workloadId=str(row.guid), workloadDigest=row.summary["workloadDigest"])
    image.review.update(
        workload_id=str(row.guid),
        expected_workload_version=row.version,
        workload_digest=row.summary["workloadDigest"],
    )
    image.result.update(
        sourceKind="registry",
        workloadId=str(row.guid),
        workloadDigest=row.summary["workloadDigest"],
    )
    image.workload = row
    return image


def test_linked_image_lost_reply_read_and_private_retry_keep_workload_source(
    linked_image_fixture, monkeypatch
):
    f = linked_image_fixture
    before = list(NvkeInstallPlanWorkload.all_objects.values())

    def lost(_client, job, **kwargs):
        f.invoke("prepare", job, **kwargs)
        raise NvkeError("OPERATOR_UNAVAILABLE", "Lost reply", ambiguous=True)

    with context(f.state.world):
        with monkeypatch.context() as patch:
            patch.setattr(OperatorAgentClient, "prepare_install_plan_image", lost)
            with pytest.raises(NvkeError):
                images.prepare(body=f.body, **f.review)
        assert NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"]).status == "preparing"
        result = images.read(artifact_id=f.body["artifactId"], **f.review)
        assert images.recover_preparation(artifact_id=f.body["artifactId"], **f.review) == result
    assert result["workload_id"] == str(f.workload.guid)
    assert result["workload_version"] == f.workload.version
    assert result["workload_digest"] == f.body["workloadDigest"]
    assert result["metadata"] == f.result
    assert [kind for kind, _ in f.calls] == ["prepare", "read", "prepare"]
    assert all(body["workloadId"] == str(f.workload.guid) for _, body in f.calls)
    assert set(f.calls[1][1]) == images.protocol.BASE | images.protocol.WORKLOAD
    assert list(NvkeInstallPlanWorkload.all_objects.values()) == before
    assert not result["metadata"]["continuationAuthorized"]


@pytest.mark.parametrize(
    "change",
    [
        {"workload_id": None},
        {"expected_workload_version": None},
        {"workload_digest": None},
        {"expected_workload_version": True},
        {"expected_workload_version": 999},
        {"workload_digest": "9" * 64},
        {"workload_id": str(uuid4())},
    ],
)
def test_incomplete_or_changed_review_refuses_before_image_intent(linked_image_fixture, change):
    f = linked_image_fixture
    with context(f.state.world), pytest.raises(NvkeError):
        images.prepare(body=f.body, **{**f.review, **change})
    assert not f.calls and not NvkeInstallPlanImage.objects.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"deleted_at": timezone.now()},
        {"status": "preparing"},
        {"version": F("version") + 1},
        {"summary": {}},
    ],
)
def test_unavailable_workload_refuses_before_image_intent(linked_image_fixture, change):
    f = linked_image_fixture
    NvkeInstallPlanWorkload.all_objects.filter(pk=f.workload.pk).update(**change)
    with context(f.state.world), pytest.raises(NvkeError):
        images.prepare(body=f.body, **f.review)
    assert not f.calls and not NvkeInstallPlanImage.objects.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"workloadId": str(uuid4())},
        {"workloadDigest": "9" * 64},
    ],
)
def test_private_body_cannot_change_the_reviewed_workload(linked_image_fixture, change):
    f = linked_image_fixture
    with context(f.state.world), pytest.raises(NvkeError):
        images.prepare(body={**f.body, **change}, **f.review)
    assert not f.calls and not NvkeInstallPlanImage.objects.exists()


def test_body_cannot_add_workload_without_explicit_review(linked_image_fixture):
    f = linked_image_fixture
    review = {k: v for k, v in f.review.items() if k not in images.SOURCE_FIELDS[-3:]}
    with context(f.state.world), pytest.raises(NvkeError):
        images.prepare(body=f.body, **review)
    assert not f.calls and not NvkeInstallPlanImage.objects.exists()


def test_linked_image_cannot_downgrade_to_graph_only_recovery(linked_image_fixture):
    f = linked_image_fixture
    with context(f.state.world):
        images.prepare(body=f.body, **f.review)
        for change in (dict.fromkeys(images.SOURCE_FIELDS[-3:]), {"workload_digest": "9" * 64}):
            with pytest.raises(NvkeError):
                images.read(artifact_id=f.body["artifactId"], **{**f.review, **change})
    assert len(f.calls) == 1


def test_workload_change_during_acquisition_prevents_image_publication(linked_image_fixture, monkeypatch):
    f = linked_image_fixture

    def drift(_client, job, **kwargs):
        result = f.invoke("prepare", job, **kwargs)
        NvkeInstallPlanWorkload.all_objects.filter(pk=f.workload.pk).update(deleted_at=timezone.now())
        return result

    monkeypatch.setattr(OperatorAgentClient, "prepare_install_plan_image", drift)
    with context(f.state.world), pytest.raises(NvkeError):
        images.prepare(body=f.body, **f.review)
    assert NvkeInstallPlanImage.objects.get(guid=f.body["artifactId"]).summary == {}


def test_linked_image_typed_api_preserves_workload_metadata(linked_image_fixture):
    import json

    from astrolift_clusters.tests.test_nvke_schema import execute

    f = linked_image_fixture
    source = {
        **review_input(f),
        "workloadId": str(f.workload.guid),
        "expectedWorkloadVersion": f.workload.version,
        "workloadDigest": f.body["workloadDigest"],
    }
    expected = None
    for action in (
        "prepareNvkeOperatorInstallPlanImage",
        "reviewNvkeOperatorInstallPlanImage",
        "recoverNvkeOperatorInstallPlanImagePreparation",
    ):
        prepare = action.startswith("prepare")
        input_type = (
            "PrepareNvkeOperatorInstallPlanImageInput"
            if prepare
            else "NvkeOperatorInstallPlanImageReviewInput"
        )
        response = execute(
            f.state.world,
            "mutation($v:"
            + input_type
            + "!){"
            + action
            + "(input:$v){ok errors{code} data{id workloadId workloadVersion workloadDigest continuationAuthorized}}}",
            {
                "v": {
                    **source,
                    **(
                        {"requestText": json.dumps(f.body)}
                        if prepare
                        else {"artifactId": f.body["artifactId"]}
                    ),
                }
            },
        )
        assert response.errors is None and response.data[action]["ok"]
        row = response.data[action]["data"]
        assert row["workloadId"] == str(f.workload.guid)
        assert row["workloadVersion"] == f.workload.version
        assert row["workloadDigest"] == f.body["workloadDigest"]
        assert row["continuationAuthorized"] is False
        if expected is not None:
            assert row == expected
        expected = row
