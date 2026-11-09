"""Retained intent must still be valid when a remote publication comes back."""

from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from nvke.client import NvkeError
from nvke.operator_agent_client import OperatorAgentClient

from astrolift_clusters import nvke_install_plan_charts as charts
from astrolift_clusters import nvke_install_plan_trees as trees
from astrolift_clusters.models import NvkeInstallPlanCharts, NvkeInstallPlanTree
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_charts import chart_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plan_trees import tree_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_install_plans import prepared_fixture  # noqa: F401
from astrolift_clusters.tests.test_nvke_operator import agent, configured, world  # noqa: F401
from astrolift_clusters.tests.test_nvke_sites import context

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("kind", ["chart", "tree"])
@pytest.mark.parametrize("field", ["request_digest", "input_ciphertext", "input_backend_kind", "actor_id"])
def test_midflight_retained_intent_corruption_cannot_publish_prepared_metadata(
    request, monkeypatch, kind, field
):
    f = request.getfixturevalue("chart_fixture" if kind == "chart" else "tree_fixture")
    service, model, method = (
        (charts, NvkeInstallPlanCharts, "prepare_install_plan_charts")
        if kind == "chart"
        else (trees, NvkeInstallPlanTree, "prepare_install_plan_tree")
    )
    if field == "actor_id":
        value = (
            get_user_model().objects.create(username="other-artifact-actor-" + uuid4().hex, is_active=True).pk
        )
    else:
        value = {
            "request_digest": "0" * 64,
            "input_ciphertext": b"corrupt",
            "input_backend_kind": "unavailable",
        }[field]
    original = getattr(OperatorAgentClient, method)

    def corrupted(client, *args, **kwargs):
        response = original(client, *args, **kwargs)
        model.objects.filter(guid=f.body["artifactId"]).update(**{field: value})
        return response

    monkeypatch.setattr(OperatorAgentClient, method, corrupted)
    with context(f.state.world), pytest.raises(NvkeError) as error:
        service.prepare(body=f.body, **f.review)
    assert error.value.code in {"PREPARATION_UNAVAILABLE", "OPERATOR_INVALID_RESPONSE", "SOURCE_CHANGED"}
    row = model.objects.get(guid=f.body["artifactId"])
    assert row.status == "preparing" and row.summary == {}
    assert [kind for kind, _body in f.calls] == ["prepare"]
