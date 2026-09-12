"""An agent that belongs to an app reads that app's bindings (#1700).

The app half of an app+agent pair reads and writes its bucket fine; the
agent half reported `{"object_store": "absent"}`. Agent Jobs are spawned
into the per-org agents namespace with a per-task Secret and never get
`envFrom: secretRef: astrolift-bindings-<slug>` -- and could not, because
a Kubernetes Secret is namespace-scoped, so the app's Secret is not
referenceable from there at all.

The values ride the per-task Secret instead, which is the mechanism the
project-scoped attachments already use. The one judgement call is which
environment's bindings an agent gets: an app with more than one has no
answer, so it inherits nothing rather than being handed whichever
environment sorted first.
"""

from __future__ import annotations

import pytest

from astrolift_dispatch.agent_secrets import (
    agent_container_env,
    app_managed_service_env_vars,
    app_managed_service_secret_refs,
)
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_services.models import ManagedService, ManagedServiceBinding

pytestmark = pytest.mark.django_db


def _cluster(org, suffix):
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    plugin = ProviderPlugin(
        name="K8s",
        slug=f"k8s-{suffix}",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return TenantCluster.objects.create(
        organization=org,
        name="prod",
        slug=f"prod-{suffix}",
        provider_plugin=ProviderPlugin.objects.get(slug=f"k8s-{suffix}"),
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )


@pytest.fixture
def pair(db):
    """An app with one environment, a bound object store, and an agent."""
    org = Organization.objects.create(name="Conflict", slug="conflict-1700")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1700")
    project = Project.objects.create(organization=org, team=team, name="P", slug="p-1700")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Quake Dash",
        slug="quake-dash",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=_cluster(org, "1700"),
        name="production",
    )
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="object_store",
        name="quake-data",
        backend_ref="arn:aws:s3:::astrolift-quake-data",
        status="active",
    )
    ManagedServiceBinding.objects.create(
        managed_service=service,
        env_key="OBJECT_STORE_BUCKET",
        env_value_ref="astrolift-quake-data",
        is_secret=False,
    )
    ManagedServiceBinding.objects.create(
        managed_service=service,
        env_key="OBJECT_STORE_SECRET_KEY",
        env_value_ref="astrolift/quake-dash/object-store",
        is_secret=True,
    )
    agent = Workload.objects.create(
        registered_app=app,
        name="Brief",
        slug="quake-brief",
        kind=Workload.Kind.AGENT,
    )
    return type("Pair", (), {"org": org, "app": app, "env": env, "agent": agent, "service": service})


def test_plain_bindings_reach_the_agent(pair):
    assert app_managed_service_env_vars(pair.agent) == {"OBJECT_STORE_BUCKET": "astrolift-quake-data"}


def test_secret_bindings_become_refs_for_the_per_task_secret(pair):
    assert app_managed_service_secret_refs(pair.agent) == [
        {
            "env_var": "OBJECT_STORE_SECRET_KEY",
            "uri": "astrolift/quake-dash/object-store",
        }
    ]


def test_the_container_env_carries_both(pair):
    env = {e["name"]: e for e in agent_container_env(None, "task-secret", workload=pair.agent)}

    assert env["OBJECT_STORE_BUCKET"]["value"] == "astrolift-quake-data"
    assert env["OBJECT_STORE_SECRET_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "task-secret"


def test_an_agent_with_no_app_inherits_nothing():
    assert app_managed_service_env_vars(None) == {}
    assert app_managed_service_secret_refs(None) == []


def test_an_app_with_two_environments_inherits_nothing(pair):
    """Choosing between production and staging on the agent's behalf is
    choosing which database it gets, and nothing in the agent says which."""
    AppEnvironment.objects.create(
        registered_app=pair.app,
        tenant_cluster=_cluster(pair.org, "1700b"),
        name="staging",
    )

    assert app_managed_service_env_vars(pair.agent) == {}
    assert app_managed_service_secret_refs(pair.agent) == []


def test_a_service_that_is_not_active_is_not_inherited(pair):
    pair.service.status = "pending"
    pair.service.save(update_fields=["status"])

    assert app_managed_service_env_vars(pair.agent) == {}


def test_a_dispatcher_owned_env_name_is_refused(pair):
    """A binding must not be able to overwrite ASTROLIFT_TRIGGER_PAYLOAD or
    the callback credential."""
    ManagedServiceBinding.objects.create(
        managed_service=pair.service,
        env_key="ASTROLIFT_CLUSTER_KEY",
        env_value_ref="nope",
        is_secret=False,
    )

    assert "ASTROLIFT_CLUSTER_KEY" not in app_managed_service_env_vars(pair.agent)
