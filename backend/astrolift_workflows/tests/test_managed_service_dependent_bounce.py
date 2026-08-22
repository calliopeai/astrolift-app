"""Dependent workloads get bounced when a binding's value moves (#126).

``astrolift_workflows/managed_service_states.py`` carried the rule for which
workloads a rebind touches and had no caller, so nothing restarted the pods
that mount a managed service's connection envelope: a provisioned service's
env never reached a running workload, and a rotated password or a moved
endpoint left pods holding values the backend no longer accepts.

These drive ``_sync_binding_rows`` and the bounce activity's sync body, which
is where the wiring lives.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.managed_service import Binding, ValueRef
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceBinding,
)
from astrolift_workflows.activities.managed_service_lifecycle import (
    _bounce_dependent_workloads_sync,
    _sync_binding_rows,
)

pytestmark = pytest.mark.django_db


class _RecordingDriver:
    """Cluster driver stub in the shape the rotation bounce already assumes."""

    def __init__(self) -> None:
        self.patched: list[tuple[str, str, str, str]] = []

    def list_workloads(self, cluster_slug: str, namespace: str):
        return [("Deployment", "api"), ("Service", "api"), ("Deployment", "worker")]

    def patch_workload(self, cluster_slug, namespace, kind, name, patch_body):  # noqa: ANN001
        self.patched.append((cluster_slug, namespace, kind, name))
        assert (
            "kubectl.kubernetes.io/restartedAt" in (patch_body["spec"]["template"]["metadata"]["annotations"])
        )
        return {}


def _graph(suffix: str):
    org = Organization.objects.create(name="Acme", slug=f"acme-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{suffix}")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug=f"demo-{suffix}",
    )
    plugin = ProviderPlugin.objects.create(
        name="K8s",
        slug=f"k8s-{suffix}",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{suffix}",
        name="Cluster",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"app-{suffix}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return SimpleNamespace(
        org=org,
        team=team,
        project=project,
        cluster=cluster,
        app=app,
        env=env,
    )


def _app_private_service(graph) -> ManagedService:
    return ManagedService.objects.create(
        registered_app=graph.app,
        app_environment=graph.env,
        kind=ManagedService.Kind.POSTGRES,
        variant="cnpg",
        name="primary",
        config={},
    )


def _binding(host: str, password_ref: str) -> Binding:
    return Binding(
        env_vars={
            "DATABASE_HOST": ValueRef(literal=host),
            "DATABASE_PASSWORD": ValueRef(secret_ref=password_ref),
        },
    )


def _sync(svc, binding):
    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        return_value=binding,
    ):
        return _sync_binding_rows(svc)


def _bounce(svc, rebound, driver):
    with (
        patch(
            "core.cluster_management._driver_for_cluster",
            return_value=driver,
        ),
        patch(
            "core.cluster_management._context_for_cluster",
            return_value=SimpleNamespace(slug=svc.app_environment.tenant_cluster.slug),
        ),
    ):
        return _bounce_dependent_workloads_sync(svc.pk, list(rebound))


def test_first_sync_reports_every_binding_as_new() -> None:
    graph = _graph("b1")
    svc = _app_private_service(graph)
    rebound = _sync(svc, _binding("db.internal", "secret/db#pw"))
    assert sorted(rebound) == sorted(
        ManagedServiceBinding.objects.filter(managed_service=svc).values_list("pk", flat=True),
    )


def test_resync_with_identical_values_reports_nothing_rebound() -> None:
    """An update that rewrites the same envelope must not restart anybody."""
    graph = _graph("b2")
    svc = _app_private_service(graph)
    _sync(svc, _binding("db.internal", "secret/db#pw"))
    assert _sync(svc, _binding("db.internal", "secret/db#pw")) == []


def test_only_the_changed_key_is_reported_rebound() -> None:
    graph = _graph("b3")
    svc = _app_private_service(graph)
    _sync(svc, _binding("db.internal", "secret/db#pw"))
    rebound = _sync(svc, _binding("db-2.internal", "secret/db#pw"))
    changed = ManagedServiceBinding.objects.get(
        managed_service=svc,
        env_key="DATABASE_HOST",
    )
    assert rebound == [changed.pk]


def test_a_rebound_binding_restarts_the_consuming_app_environment() -> None:
    graph = _graph("b4")
    svc = _app_private_service(graph)
    rebound = _sync(svc, _binding("db.internal", "secret/db#pw"))
    driver = _RecordingDriver()

    bounced = _bounce(svc, rebound, driver)

    from core.app_deploy import namespace_for_app

    assert bounced == 2
    assert [name for _slug, _ns, _kind, name in driver.patched] == ["api", "worker"]
    namespaces = {ns for _slug, ns, _kind, _name in driver.patched}
    assert namespaces == {namespace_for_app(graph.app)}


def test_nothing_rebound_means_nothing_patched() -> None:
    graph = _graph("b5")
    svc = _app_private_service(graph)
    _sync(svc, _binding("db.internal", "secret/db#pw"))
    driver = _RecordingDriver()

    assert _bounce(svc, [], driver) == 0
    assert driver.patched == []


def test_a_project_service_reaches_its_attached_environments() -> None:
    """A project-owned service reaches a workload through an attachment, not
    through its own app_environment column, which is null for those rows."""
    graph = _graph("b6")
    svc = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.REDIS,
        variant="operator",
        name="shared-cache",
        config={},
    )
    ManagedServiceAttachment.objects.create(
        managed_service=svc,
        app_environment=graph.env,
    )
    rebound = _sync(svc, _binding("cache.internal", "secret/cache#pw"))
    driver = _RecordingDriver()

    with (
        patch("core.cluster_management._driver_for_cluster", return_value=driver),
        patch(
            "core.cluster_management._context_for_cluster",
            return_value=SimpleNamespace(slug=graph.cluster.slug),
        ),
    ):
        bounced = _bounce_dependent_workloads_sync(svc.pk, list(rebound))

    from core.app_deploy import namespace_for_app

    assert bounced == 2
    assert {ns for _slug, ns, _kind, _name in driver.patched} == {namespace_for_app(graph.app)}


def test_a_detached_environment_is_not_restarted() -> None:
    graph = _graph("b7")
    svc = ManagedService.objects.create(
        project=graph.project,
        tenant_cluster=graph.cluster,
        kind=ManagedService.Kind.REDIS,
        variant="operator",
        name="shared-cache",
        config={},
    )
    attachment = ManagedServiceAttachment.objects.create(
        managed_service=svc,
        app_environment=graph.env,
    )
    rebound = _sync(svc, _binding("cache.internal", "secret/cache#pw"))
    # Soft delete, per the platform rule; the bounce must respect it.
    attachment.deleted_at = timezone.now()
    attachment.save(update_fields=["deleted_at", "updated_at", "version"])

    driver = _RecordingDriver()
    with (
        patch("core.cluster_management._driver_for_cluster", return_value=driver),
        patch(
            "core.cluster_management._context_for_cluster",
            return_value=SimpleNamespace(slug=graph.cluster.slug),
        ),
    ):
        assert _bounce_dependent_workloads_sync(svc.pk, list(rebound)) == 0
    assert driver.patched == []


def test_the_bounce_activity_is_registered_with_the_worker() -> None:
    """An activity a registered workflow calls but no worker serves fails at
    runtime, on the branch that reaches it."""
    from astrolift_workflows.activities.managed_service_lifecycle import (
        bounce_workloads_bound_to_managed_service,
    )
    from astrolift_workflows.worker import ACTIVITIES

    assert bounce_workloads_bound_to_managed_service in ACTIVITIES


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_provision_workflow_hands_the_rebound_ids_to_the_bounce(monkeypatch) -> None:
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        bounce_workloads_bound_to_managed_service,
        finalize_managed_service_provision,
        mark_managed_service_provisioning,
        provision_managed_service,
    )
    from astrolift_workflows.inputs import Actor, ProvisionManagedServiceInput
    from astrolift_workflows.workflows.provision_managed_service import (
        ProvisionManagedServiceWorkflow,
    )

    bounced: list[list] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is mark_managed_service_provisioning:
            return None
        if activity_fn is provision_managed_service:
            return {"handle": "postgres/example", "ready": True}
        if activity_fn is finalize_managed_service_provision:
            return [7, 9]
        if activity_fn is bounce_workloads_bound_to_managed_service:
            bounced.append(kwargs["args"])
            return 2
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    result = _run(
        ProvisionManagedServiceWorkflow().run(
            ProvisionManagedServiceInput(
                managed_service_id=41,
                actor=Actor(kind="system"),
            ),
        ),
    )

    assert result.ok is True
    assert bounced == [[41, [7, 9]]]


def test_update_workflow_hands_the_rebound_ids_to_the_bounce(monkeypatch) -> None:
    from temporalio import workflow as temporalio_workflow

    from astrolift_workflows.activities import (
        bounce_workloads_bound_to_managed_service,
        check_managed_service_ready,
        finalize_managed_service_update,
        update_managed_service,
    )
    from astrolift_workflows.inputs import Actor, UpdateManagedServiceInput
    from astrolift_workflows.workflows.update_managed_service import (
        UpdateManagedServiceWorkflow,
    )

    bounced: list[list] = []

    async def execute(activity_fn, *args, **kwargs):
        if activity_fn is update_managed_service:
            return {"handle": "postgres/example"}
        if activity_fn is check_managed_service_ready:
            return "available"
        if activity_fn is finalize_managed_service_update:
            return [3]
        if activity_fn is bounce_workloads_bound_to_managed_service:
            bounced.append(kwargs["args"])
            return 1
        raise AssertionError(f"unexpected activity {activity_fn}")

    monkeypatch.setattr(temporalio_workflow, "execute_activity", execute)
    result = _run(
        UpdateManagedServiceWorkflow().run(
            UpdateManagedServiceInput(
                managed_service_id=42,
                actor=Actor(kind="system"),
            ),
        ),
    )

    assert result.ok is True
    assert bounced == [[42, [3]]]
