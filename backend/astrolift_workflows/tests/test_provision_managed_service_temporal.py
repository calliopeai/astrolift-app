"""ProvisionManagedServiceWorkflow under a real Temporal test server (#1688).

Existing coverage pins the two ends of this pipeline separately:
``test_provision_managed_service_ready.py`` calls ``_provision_sync`` directly
to check the ``ready`` flag passthrough, and
``test_managed_service_dependent_bounce.py`` calls ``_sync_binding_rows``
directly with a hand-built ``Binding``. Neither runs the workflow itself, so
a seam between them -- the workflow never reaching ``provision_managed_service``,
or ``finalize_managed_service_provision`` never being scheduled after it --
would not be caught by either.

#1688 reported exactly that class of gap: a manifest-declared managed service
sat PENDING forever, no driver call ever appeared in the worker logs, and the
app's binding secret existed but was empty. The root cause (a lost first
workflow-start enqueue that nothing retried) is fixed in 4aa2feb2 and pinned
by ``astrolift_manifest/tests/test_persist.py``. This test covers what that
fix does not: once ``ProvisionManagedServiceWorkflow`` actually runs, it must
reach the provider driver and the resulting ``ManagedServiceBinding`` rows
must carry the driver's real connection output, not placeholders. Those rows
are exactly what ``_update_secrets_sync`` (proven separately in
``test_azure_managed_binding_render.py``) turns into the workload's Secret.

Runs the real workflow class and its real activities against a Temporal
test server; only the provider driver and the cluster driver used by the
best-effort pod-bounce step are faked, so no cloud call is ever made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _sdk.managed_service import Binding, ProvisionResult, ValueRef

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceBinding
from astrolift_workflows.activities import (
    bounce_workloads_bound_to_managed_service,
    check_managed_service_ready,
    finalize_managed_service_provision,
    mark_managed_service_failed,
    mark_managed_service_provisioning,
    provision_managed_service,
)
from astrolift_workflows.inputs import Actor, ProvisionManagedServiceInput
from astrolift_workflows.workflows.provision_managed_service import (
    ProvisionManagedServiceWorkflow,
)
from core.testing.temporal import temporal_worker

pytestmark = pytest.mark.django_db(transaction=True)


class _RecordingClusterDriver:
    """Cluster driver stub for the workflow's best-effort pod-bounce step.

    Not the seam this test pins -- present only so
    ``bounce_workloads_bound_to_managed_service`` (the workflow's last
    activity) has something to call instead of a real cluster."""

    def __init__(self) -> None:
        self.patched: list[tuple[str, str]] = []

    def list_workloads(self, cluster_slug: str, namespace: str):
        return []

    def patch_workload(self, cluster_slug, namespace, kind, name, patch_body):  # noqa: ANN001
        self.patched.append((kind, name))
        return {}


class _FakeAuroraLikeDriver:
    """Stands in for a real cloud driver (e.g. ``AuroraDriver``). Records
    every call it receives and returns real connection material, so the
    test can assert the driver was actually invoked and that its output
    is what lands on the binding rows -- never touches a real cloud API."""

    calls: list[str] = []

    def __init__(self, *, config):
        self._config = config

    def provision(self, spec):
        _FakeAuroraLikeDriver.calls.append("provision")
        return ProvisionResult(
            ok=True,
            handle="postgres/e2e-1688-cluster",
            message="provisioned",
            ready=True,
        )

    def binding(self, handle, config=None):
        _FakeAuroraLikeDriver.calls.append("binding")
        return Binding(
            env_vars={
                "POSTGRES_HOST": ValueRef(literal="e2e-1688-cluster.example.internal"),
                "POSTGRES_PASSWORD": ValueRef(secret_ref="secretsmanager/e2e-1688-cluster/master"),
            },
            notes="fake binding for #1688 coverage",
        )


def _make_service() -> ManagedService:
    org = Organization.objects.create(name="Acme", slug="acme-1688")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-1688")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-1688")
    plugin = ProviderPlugin.objects.create(
        name="E2E",
        slug="e2e-1688",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="e2e-1688-cluster",
        name="E2E Cluster",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-1688",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        variant="aurora_postgres_serverless_v2",
        name="records",
        config={},
    )


async def test_provision_workflow_calls_driver_and_populates_binding_rows(temporal_env):
    """The #1688 regression scenario: once the workflow runs, it must call
    the driver and the binding rows must carry the driver's real output --
    not sit empty behind an ACTIVE row."""
    from asgiref.sync import sync_to_async

    _FakeAuroraLikeDriver.calls = []
    svc = await sync_to_async(_make_service)()
    cluster_driver = _RecordingClusterDriver()

    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=_FakeAuroraLikeDriver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
        patch("core.cluster_management._driver_for_cluster", return_value=cluster_driver),
        patch(
            "core.cluster_management._context_for_cluster",
            return_value=SimpleNamespace(slug="e2e-1688-cluster"),
        ),
    ):
        async with temporal_worker(
            temporal_env,
            workflows=[ProvisionManagedServiceWorkflow],
            activities=[
                mark_managed_service_provisioning,
                provision_managed_service,
                check_managed_service_ready,
                finalize_managed_service_provision,
                mark_managed_service_failed,
                bounce_workloads_bound_to_managed_service,
            ],
        ):
            result = await temporal_env.client.execute_workflow(
                ProvisionManagedServiceWorkflow.run,
                ProvisionManagedServiceInput(managed_service_id=svc.pk, actor=Actor(kind="system")),
                id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
                task_queue="astrolift-test",
            )

    assert result.ok is True, result.message
    # The driver was actually called -- #1688's "no driver call is made".
    assert _FakeAuroraLikeDriver.calls == ["provision", "binding"]

    await sync_to_async(svc.refresh_from_db)()
    assert svc.status == ManagedService.Status.ACTIVE
    assert svc.backend_ref == "postgres/e2e-1688-cluster"
    assert svc.connection_secret_ref != ""

    def _bindings() -> dict[str, tuple[str, bool]]:
        return {
            row.env_key: (row.env_value_ref, row.is_secret)
            for row in ManagedServiceBinding.objects.filter(managed_service=svc)
        }

    bindings = await sync_to_async(_bindings)()
    # The binding secret carries the driver's connection outputs --
    # #1688's "binding secret is created empty" -- rather than nothing.
    assert bindings == {
        "POSTGRES_HOST": ("e2e-1688-cluster.example.internal", False),
        "POSTGRES_PASSWORD": ("secretsmanager/e2e-1688-cluster/master", True),
    }
    assert cluster_driver.patched, "the workflow's dependent-workload bounce never ran"


async def test_provision_workflow_marks_failed_without_ever_calling_the_driver_when_disabled(temporal_env):
    """Control case: a driver that refuses to provision must not leave a
    binding behind, and the row must say why instead of sitting silent."""
    from asgiref.sync import sync_to_async

    class _RefusingDriver:
        def __init__(self, *, config):
            pass

        def provision(self, spec):
            return ProvisionResult(ok=False, handle="", message="capacity exhausted", errors=["quota"])

    svc = await sync_to_async(_make_service)()

    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=_RefusingDriver),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    ):
        async with temporal_worker(
            temporal_env,
            workflows=[ProvisionManagedServiceWorkflow],
            activities=[
                mark_managed_service_provisioning,
                provision_managed_service,
                check_managed_service_ready,
                finalize_managed_service_provision,
                mark_managed_service_failed,
                bounce_workloads_bound_to_managed_service,
            ],
        ):
            result = await temporal_env.client.execute_workflow(
                ProvisionManagedServiceWorkflow.run,
                ProvisionManagedServiceInput(managed_service_id=svc.pk, actor=Actor(kind="system")),
                id=f"ProvisionManagedServiceWorkflow-{svc.guid}",
                task_queue="astrolift-test",
            )

    assert result.ok is False
    # #1916: the workflow's own result and the row must carry the driver's
    # actual reason, not Temporal's generic ActivityError envelope.
    assert "Activity task failed" not in result.message
    assert "capacity exhausted" in result.message

    await sync_to_async(svc.refresh_from_db)()
    assert svc.status == ManagedService.Status.FAILED
    assert svc.status_error != ""
    assert "Activity task failed" not in svc.status_error
    assert "capacity exhausted" in svc.status_error
    assert svc.backend_ref == ""

    exists = await sync_to_async(ManagedServiceBinding.objects.filter(managed_service=svc).exists)()
    assert exists is False


async def test_provision_workflow_redacts_a_credential_in_the_drivers_message(temporal_env):
    """A driver that echoes a rejected config value back in its refusal
    message must not leak it onto the row or the workflow result (#1916).
    The platform's secret-ref convention keeps *known* secret fields as
    references before a driver ever runs; this is defense in depth for a
    literal that lands under a key the convention doesn't recognize."""
    from asgiref.sync import sync_to_async

    class _RefusingDriverWithSecretyMessage:
        def __init__(self, *, config):
            pass

        def provision(self, spec):
            return ProvisionResult(
                ok=False,
                handle="",
                message="config rejected: password=hunter2 for host db.internal",
                errors=["invalid_config"],
            )

    svc = await sync_to_async(_make_service)()

    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=_RefusingDriverWithSecretyMessage),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    ):
        async with temporal_worker(
            temporal_env,
            workflows=[ProvisionManagedServiceWorkflow],
            activities=[
                mark_managed_service_provisioning,
                provision_managed_service,
                check_managed_service_ready,
                finalize_managed_service_provision,
                mark_managed_service_failed,
                bounce_workloads_bound_to_managed_service,
            ],
        ):
            result = await temporal_env.client.execute_workflow(
                ProvisionManagedServiceWorkflow.run,
                ProvisionManagedServiceInput(managed_service_id=svc.pk, actor=Actor(kind="system")),
                id=f"ProvisionManagedServiceWorkflow-{svc.guid}-secret",
                task_queue="astrolift-test",
            )

    assert result.ok is False
    assert "hunter2" not in result.message
    assert "***redacted***" in result.message

    await sync_to_async(svc.refresh_from_db)()
    assert svc.status == ManagedService.Status.FAILED
    assert "hunter2" not in svc.status_error
    assert "***redacted***" in svc.status_error
