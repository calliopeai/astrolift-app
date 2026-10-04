"""Saved reviewed Vertex serving intent versus native observations (#2277)."""

import pytest

from astrolift_workflows.managed_service_review import capture_reviewed_binding
from astrolift_workflows.vertex_managed_service import JOURNAL_KEY

from .test_vertex_operations_2277 import change_operation, complete, run
from .test_vertex_operations_2277 import world as world

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("divergence", ["minimum", "maximum", "machine", "traffic"])
def test_persisted_serving_request_requires_exact_observation_without_redeploy(request, divergence):
    w = request.getfixturevalue("world")
    w.service.config.update(min_replica_count=2, max_replica_count=4, machine_type="n1-standard-4")
    w.service.save()
    w.binding = capture_reviewed_binding(w.service.pk)
    complete(w)
    w.service.refresh_from_db()
    expected = {
        "min_replica_count": 2,
        "max_replica_count": 4,
        "machine_type": "n1-standard-4",
        "traffic_percentage": 100,
    }
    assert w.service.lifecycle_policy[JOURNAL_KEY]["serving_request"] == expected
    model = w.wire.endpoint.deployed_models[0]
    if divergence == "minimum":
        model.dedicated_resources.min_replica_count = 1
        model.status.available_replica_count = 1
    elif divergence == "maximum":
        model.dedicated_resources.max_replica_count = 3
    elif divergence == "machine":
        model.dedicated_resources.machine_spec.machine_type = "n1-standard-2"
    else:
        w.wire.endpoint.traffic_split.clear()
    w.wire.calls.clear()
    assert run(w)["pending"]
    w.service.refresh_from_db()
    assert w.service.lifecycle_policy[JOURNAL_KEY]["serving_request"] == expected
    assert not w.service.lifecycle_policy[JOURNAL_KEY]["complete"]
    assert all(name == "get" for name, _ in w.wire.calls)
    model.dedicated_resources.min_replica_count = 2
    model.dedicated_resources.max_replica_count = 4
    model.dedicated_resources.machine_spec.machine_type = "n1-standard-4"
    model.status.available_replica_count = 2
    w.wire.endpoint.traffic_split[model.id] = 100
    assert run(w)["complete"]
    assert all(name == "get" for name, _ in w.wire.calls)


def test_absent_endpoint_never_completes_requested_unproved_artifact_deletion(request):
    w = request.getfixturevalue("world")
    complete(w)
    change_operation(w, "deprovision")
    w.wire.endpoint = None
    w.wire.calls.clear()
    result = run(w, "deprovision", delete_data=True, force_destroy=True)
    assert result["refused"] and "delete_data refused" in result["message"]
    w.service.refresh_from_db()
    assert w.service.deleted_at is None
    assert not w.service.lifecycle_policy[JOURNAL_KEY]["complete"]
    assert not w.wire.calls
