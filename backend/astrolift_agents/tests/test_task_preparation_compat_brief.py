"""A registered agent with no Brief runs from its image alone (#1762).

An app registered from a raw manifest (no source connection) persists the
agent Workload but never assembles a Brief. ``prepare_agent_task`` then
freezes a compatibility brief; that brief has no source to slice, so it must
not claim a payload bundle the spawner would fail to find.
"""

from __future__ import annotations

import pytest

from astrolift_agents.models import AgentTask
from astrolift_agents.services.task_preparation import prepare_agent_task
from astrolift_dispatch.brief_injector import inject_brief_into_job_spec
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import Container, RegisteredApp, Workload

pytestmark = pytest.mark.django_db


def _job_manifest() -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "agent-x"},
        "spec": {"template": {"spec": {"containers": [{"name": "agent", "env": []}]}}},
    }


@pytest.fixture
def raw_agent(db):
    org = Organization.objects.create(name="Raw Co", slug="raw-co")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-raw")
    project = Project.objects.create(organization=org, team=team, name="Office", slug="office-raw")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Raw Agents",
        slug="raw-agents",
        provisioning_status="ready",
    )
    workload = Workload.objects.create(
        registered_app=app,
        name="strategy",
        slug="strategy",
        kind=Workload.Kind.AGENT.value,
        run_family=Workload.RunFamily.TASK.value,
    )
    Container.objects.create(
        workload=workload,
        name="agent",
        is_primary=True,
        image_ref="docker.io/example/agent-runner:latest",
    )
    return {"org": org, "workload": workload}


def test_compatibility_brief_never_requires_a_payload_bundle(raw_agent):
    task = AgentTask.objects.create(
        organization=raw_agent["org"],
        agent_definition=raw_agent["workload"],
        status=AgentTask.Status.DRAFT,
        dispatch_input={"task": "smoke"},
    )

    prepare_agent_task(task, context={"trigger": "manual"})

    task.refresh_from_db()
    snapshot = task.brief.manifest_snapshot
    assert snapshot["requires_payload"] is False
    assert snapshot["agent_package"]["delivery"]["payload_required"] is False
    assert snapshot["agent_package"]["runtime"]["image"] == "docker.io/example/agent-runner:latest"
    assert task.brief.context["source"] == "registered_workload_compatibility"

    # The spawner-side injection is the call that raised before the fix.
    manifest = inject_brief_into_job_spec(_job_manifest(), task)
    env = {
        e["name"]: e.get("value", "") for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["AGENT_PROMPT"].startswith("Begin your task now")
    assert '"task":"smoke"' in env["AGENT_PROMPT"]
