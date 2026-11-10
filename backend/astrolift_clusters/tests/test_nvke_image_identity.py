from uuid import uuid4

import pytest
from nvke.client import NvkeError

from astrolift_clusters import nvke_install_plan_charts as charts
from astrolift_clusters import nvke_install_plan_contexts as contexts
from astrolift_clusters import nvke_install_plan_trees as trees
from astrolift_clusters import nvke_install_plan_verifications as verifications
from astrolift_clusters import nvke_install_plans as plans
from astrolift_clusters.models import NvkeInstallPlanImage, NvkeInstallPlanTree
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_verifications import (
    agent,  # noqa: F401
    chart_fixture,  # noqa: F401
    configured,  # noqa: F401
    context_fixture,  # noqa: F401
    prepared_fixture,  # noqa: F401
    tree_fixture,  # noqa: F401
    verification_fixture,  # noqa: F401
    world,  # noqa: F401
)
from astrolift_clusters.tests.test_nvke_operator import source_review
from astrolift_clusters.tests.test_nvke_sites import context
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("status", ["preparing", "prepared", "deleted"])
def test_partial_completed_and_deleted_image_ids_exclude_all_other_artifact_kinds(
    verification_fixture,  # noqa: F811
    status,
):
    f = verification_fixture
    identity = str(uuid4())
    sealed = encrypt_at_rest(b'{"policy":"reviewed","reference":"private.example.test/owned:v1"}')
    row = NvkeInstallPlanImage.objects.create(
        guid=identity,
        tree=NvkeInstallPlanTree.objects.get(guid=f.body["treeId"]),
        actor=f.original.actor,
        source_binding={"original": str(f.original.guid)},
        request_digest="a" * 64,
        input_backend_kind=sealed.backend_kind,
        input_ciphertext=sealed.backend_ref,
        status="prepared" if status == "prepared" else "preparing",
    )
    if status == "deleted":
        row.soft_delete()
    with context(f.state.world):
        for invoke in (
            lambda: plans.prepare(
                body={"format": 1, "planId": identity, "request": None, "maximumInputBytes": "10000"},
                **source_review(f.state, f.parent.original),
            ),
            lambda: charts.prepare(
                body={**f.parent.tree.chart.body, "artifactId": identity}, **f.parent.tree.chart.review
            ),
            lambda: trees.prepare(
                body={**f.parent.tree.body, "artifactId": identity}, **f.parent.tree.review
            ),
            lambda: contexts.prepare(body={**f.parent.body, "artifactId": identity}, **f.parent.review),
            lambda: verifications.prepare(body={**f.body, "artifactId": identity}, **f.review),
        ):
            with pytest.raises(NvkeError) as error:
                invoke()
            assert error.value.code == "CONFLICT"
    assert not f.calls
    assert NvkeInstallPlanImage.all_objects.filter(guid=identity).exists()
