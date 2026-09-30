"""A live runtime-class setting reaches boxes and missing classes prevent apply."""

import pytest
from django.test import override_settings

from astrolift_agents.services import agent_box
from astrolift_agents.tests import test_agent_box as box_tests

pytestmark = pytest.mark.django_db
org = box_tests.org
cluster = box_tests.cluster


@override_settings(AGENT_RUNTIME_CLASS="gvisor")
def test_runtime_class_reaches_box_render(org):
    box = box_tests._box(org)
    job = agent_box.render_agent_box_job(box=box, image="fixture:1", namespace="ns", job_name="box-fixture")
    assert job["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"


@override_settings(AGENT_RUNTIME_CLASS="gvisor")
def test_missing_runtime_fails_box_before_apply(org, cluster, monkeypatch):
    box = box_tests._box(org, image="fixture:1")
    driver = cluster.driver
    monkeypatch.setattr(driver, "get_manifest", lambda *_args: None, raising=False)
    with pytest.raises(agent_box.AgentBoxError, match="RuntimeClass.*not available"):
        agent_box.start_agent_box(box)
    box.refresh_from_db()
    assert box.status == "failed"
    assert "RuntimeClass" in box.last_error
    assert not driver.applied
