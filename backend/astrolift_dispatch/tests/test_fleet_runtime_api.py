"""Operational fleet and runtime read endpoints for Dispatch clients."""

from __future__ import annotations

import hashlib
import json

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_agents.models import AgentEnvironmentSpec, AgentTask, DispatcherInstance
from astrolift_identity.models import Organization

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def org():
    return Organization.objects.create(name="Fleet Org", slug="fleet-org")


@pytest.fixture
def raw_key():
    return "f" * 64


@pytest.fixture
def dispatcher(org, raw_key):
    return DispatcherInstance.objects.create(
        organization=org,
        name="Fleet Dispatcher",
        slug="fleet-dispatcher",
        endpoint="https://dispatch.example/",
        api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        cloud=DispatcherInstance.Cloud.K8S_NATIVE,
        backend=DispatcherInstance.Backend.K8S_JOB,
        status=DispatcherInstance.Status.ACTIVE,
    )


def _auth(raw_key):
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


def test_fleet_lists_active_tasks_and_is_org_scoped(org, dispatcher, raw_key):
    spec = AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Codex",
        slug="codex",
        runtime="codex",
        agent_type=AgentEnvironmentSpec.AgentType.CODEX,
    )
    active = AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        environment_spec=spec,
        status=AgentTask.Status.RUNNING,
        external_id="pod-1",
    )
    AgentTask.objects.create(
        organization=org,
        dispatcher=dispatcher,
        status=AgentTask.Status.COMPLETED,
    )

    response = Client().get("/api/dispatch/v1/fleet/", **_auth(raw_key))

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["agents"] == [
        {
            "task_id": str(active.guid),
            "status": "running",
            "agent_slug": None,
            "runtime": "codex",
            "dispatcher_id": str(dispatcher.guid),
            "external_id": "pod-1",
            "started_at": None,
            "ended_at": None,
            "vnc_url": None,
        }
    ]


def test_fleet_rejects_invalid_token(raw_key):
    response = Client().get("/api/dispatch/v1/fleet/", **_auth("wrong"))

    assert response.status_code == 401


def test_runtimes_returns_catalog(dispatcher, raw_key):
    response = Client().get("/api/dispatch/v1/runtimes/", **_auth(raw_key))

    assert response.status_code == 200
    names = [entry["name"] for entry in json.loads(response.content)["runtimes"]]
    assert "codex" in names


def test_dispatcher_scope_excludes_other_dispatchers_and_organizations(org, dispatcher, raw_key):
    other_org = Organization.objects.create(name="Other Org", slug="other-org")
    other_dispatcher = DispatcherInstance.objects.create(
        organization=org, name="Other Dispatcher", slug="other-dispatcher"
    )
    own = AgentTask.objects.create(organization=org, dispatcher=dispatcher, status="running")
    AgentTask.objects.create(organization=org, dispatcher=other_dispatcher, status="running")
    AgentTask.objects.create(organization=org, status="running")
    AgentTask.objects.create(organization=other_org, dispatcher=dispatcher, status="running")
    AgentTask.objects.create(
        organization=org, dispatcher=dispatcher, status="running", deleted_at=timezone.now()
    )
    client = Client()
    response = client.get("/api/dispatch/v1/fleet/?scope=dispatcher", **_auth(raw_key))
    assert response.status_code == 200
    data = response.json()
    assert [task["task_id"] for task in data["agents"]] == [str(own.guid)]
    assert data["scope"] == {
        "version": 1,
        "kind": "dispatcher",
        "organization_id": str(org.guid),
        "dispatcher_id": str(dispatcher.guid),
    }
    assert data["has_more"] is False
    assert len(client.get("/api/dispatch/v1/fleet/", **_auth(raw_key)).json()["agents"]) == 3
    assert (
        client.get("/api/dispatch/v1/runtimes/?scope=dispatcher", **_auth(raw_key)).json()["scope"]
        == data["scope"]
    )


@pytest.mark.parametrize("resource", ["fleet", "runtimes"])
def test_read_scope_rejects_unknown_and_deleted_org(resource, org, dispatcher, raw_key):
    client = Client()
    assert client.get(f"/api/dispatch/v1/{resource}/?scope=all", **_auth(raw_key)).status_code == 400
    org.deleted_at = timezone.now()
    org.save()
    assert client.get(f"/api/dispatch/v1/{resource}/", **_auth(raw_key)).status_code == 401


def test_dispatcher_scope_reports_truncation_and_terminal_filter(org, dispatcher, raw_key):
    AgentTask.objects.bulk_create(
        [AgentTask(organization=org, dispatcher=dispatcher, status="completed") for _ in range(201)]
    )
    client = Client()
    url = "/api/dispatch/v1/fleet/?scope=dispatcher"
    assert client.get(url, **_auth(raw_key)).json()["agents"] == []
    data = client.get(url + "&include_terminal=1", **_auth(raw_key)).json()
    assert len(data["agents"]) == 200
    assert data["has_more"] is True
