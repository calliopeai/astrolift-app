"""Tests for reprovisionManagedService mutation + editable_fields contract (#745)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import (
    ReprovisionManagedServiceInput,
    ServicesMutation,
    UpdateManagedServiceInput,
)
from astrolift_workflows.client import WorkflowHandle
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str = "reprovision-test"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-reprv")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-reprv")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-reprv")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native-reprv",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native-reprv")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="local-reprv",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Reprv App",
        slug="reprv-app",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app, env


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- reprovisionManagedService ----------------------------------------


def test_reprovision_transitions_active_to_pending(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow"):
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.PENDING


def test_reprovision_transitions_failed_to_pending(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.REDIS,
        name="cache",
        status=ManagedService.Status.FAILED,
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow"):
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert result.ok, result.errors
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.PENDING


def test_reprovision_failed_row_starts_provision_workflow(permission_resolver):
    """Regression for #1038: reprovision on a failed row must (re)start
    ProvisionManagedServiceWorkflow, not just flip status to PENDING.
    Before the fix the row sat PENDING forever — nothing drove it. Revert
    the start_workflow call in reprovision_managed_service and this fails."""
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.FAILED,
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow") as start_wf:
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert result.ok, result.errors
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.PENDING
    start_wf.assert_called_once()
    call = start_wf.call_args
    assert call.args[0] == "ProvisionManagedServiceWorkflow"
    assert call.kwargs["args"][0].managed_service_id == svc.pk
    assert call.kwargs["workflow_id"] == f"ProvisionManagedServiceWorkflow-{svc.guid}"


def test_reprovision_blocked_while_deprovisioning(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.DEPROVISIONING,
    )
    with _ctx(org):
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_reprovision_blocked_while_provisioning(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.PROVISIONING,
    )
    with _ctx(org):
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_reprovision_not_found(permission_resolver):
    org, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID("00000000-0000-0000-0000-000000000001"),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_reprovision_requires_permission():
    org, app, env = _scaffold()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().reprovision_managed_service(
            _info(user=_make_user()),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- updateManagedService editable_fields validation ------------------
# When the driver is not resolvable (no real cluster plugin in tests),
# editable_fields defaults to ["*"] — meaning all config changes are
# allowed. We test that the happy path still works.


def test_update_managed_service_config_allowed_when_no_driver(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        config={"max_connections": 100},
        status=ManagedService.Status.ACTIVE,
        backend_ref="postgres/primary",
    )
    handle = WorkflowHandle(
        workflow_id=f"UpdateManagedServiceWorkflow-{svc.guid}",
        run_id="run-update-1",
        enqueued=True,
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow", return_value=handle) as start_wf:
        result = ServicesMutation().update_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"max_connections": 200},
            ),
        )
    assert result.ok, result.errors
    svc.refresh_from_db()
    assert svc.config["max_connections"] == 200
    assert svc.status == ManagedService.Status.UPDATING
    assert svc.applied_config == {"max_connections": 100}
    assert svc.operation_workflow_id == f"UpdateManagedServiceWorkflow-{svc.guid}"
    assert svc.operation_run_id == "run-update-1"
    start_wf.assert_called_once()
    assert start_wf.call_args.args[0] == "UpdateManagedServiceWorkflow"


def test_update_managed_service_noop_does_not_start_workflow(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        config={"max_connections": 100},
        applied_config={"max_connections": 100},
        status=ManagedService.Status.ACTIVE,
        backend_ref="postgres/primary",
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow") as start_wf:
        result = ServicesMutation().update_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"max_connections": 100},
            ),
        )
    assert result.ok, result.errors
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.ACTIVE
    start_wf.assert_not_called()


def test_update_managed_service_fails_closed_when_temporal_is_disabled(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        config={"max_connections": 100},
        status=ManagedService.Status.ACTIVE,
        backend_ref="postgres/primary",
    )
    disabled = WorkflowHandle(
        workflow_id=f"UpdateManagedServiceWorkflow-{svc.guid}",
        run_id="",
        enqueued=False,
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow", return_value=disabled):
        result = ServicesMutation().update_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"max_connections": 200},
            ),
        )

    assert result.ok is False
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.FAILED
    assert "not enqueued" in svc.status_error


def test_update_managed_service_rejects_concurrent_update(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        config={"max_connections": 100},
        applied_config={"max_connections": 50},
        status=ManagedService.Status.UPDATING,
        backend_ref="postgres/primary",
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow") as start_wf:
        result = ServicesMutation().update_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"max_connections": 200},
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    start_wf.assert_not_called()


def _real_plugin_scaffold(plugin_slug: str):
    """Like ``_scaffold`` but bound to a *registered* provider plugin.

    ``_scaffold`` invents a plugin slug, so no driver resolves and
    ``_editable_fields_for`` falls back to the permissive ``["*"]``. Using the
    real slug makes the mutation consult the real driver.
    """
    org = Organization.objects.create(name="Acme", slug=f"acme-{plugin_slug}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{plugin_slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{plugin_slug}")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=plugin_slug,
                slug=plugin_slug,
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{plugin_slug}",
        name="Local",
        provider_plugin=ProviderPlugin.objects.get(slug=plugin_slug),
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Real App",
        slug=f"app-{plugin_slug}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return org, app, env


def test_update_managed_service_rejects_a_change_the_real_driver_cannot_apply(permission_resolver):
    """#1376, end to end through the real CNPG driver.

    The driver inherited the permissive ``["*"]`` while implementing
    ``update()`` as a courtesy no-op, so a ``storage_size`` change was accepted,
    a workflow ran, and the row came back ACTIVE with ``applied_config``
    advanced over a Cluster CRD nobody had re-applied. Now the driver declares
    ``editable_fields() == []`` and the change never gets past the API: no
    workflow, no state change, and the operator is told to reprovision.
    """
    org, app, env = _real_plugin_scaffold("k8s_native")
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        variant="cnpg",
        name="primary",
        config={"storage_size": "10Gi"},
        applied_config={"storage_size": "10Gi"},
        status=ManagedService.Status.ACTIVE,
        backend_ref="postgres/primary",
    )
    with (
        _ctx(org),
        patch("astrolift_workflows.client.start_workflow") as start_wf,
    ):
        result = ServicesMutation().update_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"storage_size": "50Gi"},
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "config"
    assert "reprovisionManagedService" in result.errors[0].message
    start_wf.assert_not_called()
    svc.refresh_from_db()
    assert svc.config == {"storage_size": "10Gi"}
    assert svc.applied_config == {"storage_size": "10Gi"}
    assert svc.status == ManagedService.Status.ACTIVE
    assert svc.operation_kind == ""


def test_update_managed_service_write_does_not_clobber_a_concurrent_column_write(permission_resolver):
    """The accepted update saves only the desired-state columns.

    ``select_for_update`` locks the row against other locking writers, but the
    update workflow's activities and ``revealManagedServiceConnection`` write
    without taking it. A full-row ``save()`` pushed the snapshot read at the top
    of this transaction back over their columns; a scoped one cannot.
    """
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        config={"max_connections": 100},
        status=ManagedService.Status.ACTIVE,
        backend_ref="postgres/primary",
    )

    def _write_from_elsewhere(_svc):
        # Runs inside the mutation's transaction, after it loaded the row and
        # before it saves: the window a concurrent scoped write lands in.
        ManagedService.objects.filter(pk=svc.pk).update(
            backend_ref="postgres/primary-v2",
            last_action_kind="connection.reveal",
        )
        return ["*"]

    handle = WorkflowHandle(
        workflow_id=f"UpdateManagedServiceWorkflow-{svc.guid}",
        run_id="run-clobber",
        enqueued=True,
    )
    with (
        _ctx(org),
        patch("astrolift_services.schema.types._editable_fields_for", side_effect=_write_from_elsewhere),
        patch("astrolift_workflows.client.start_workflow", return_value=handle),
    ):
        result = ServicesMutation().update_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"max_connections": 200},
            ),
        )

    assert result.ok, result.errors
    svc.refresh_from_db()
    assert svc.backend_ref == "postgres/primary-v2"
    assert svc.last_action_kind == "connection.reveal"
    assert svc.config == {"max_connections": 200}
    assert svc.status == ManagedService.Status.UPDATING


def test_update_project_service_uses_update_workflow(permission_resolver):
    # storage_class_pvc rather than redis/operator: the Redis operator declares
    # no in-place path at all (#1376), so the mutation now refuses a size
    # change before starting anything. It used to pass here only because the
    # driver was unreachable and the check fell open to ["*"] -- the in-cluster
    # drivers became resolvable from a cloud-plugin cluster in #1484.
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.PROJECT_UPDATE)
    svc = ManagedService.objects.create(
        project=app.project,
        tenant_cluster=env.tenant_cluster,
        environment_name="production",
        kind=ManagedService.Kind.FILESYSTEM,
        variant="storage_class_pvc",
        name="shared",
        config={"size": "small"},
        status=ManagedService.Status.ACTIVE,
        backend_ref="filesystem/shared",
    )
    handle = WorkflowHandle(
        workflow_id=f"UpdateManagedServiceWorkflow-{svc.guid}",
        run_id="run-project-update",
        enqueued=True,
    )
    with _ctx(org), patch("astrolift_workflows.client.start_workflow", return_value=handle) as start_wf:
        result = ServicesMutation().update_project_managed_service(
            _info(user=_make_user()),
            input=UpdateManagedServiceInput(
                id=GUID(str(svc.guid)),
                config={"size": "medium"},
            ),
        )
    assert result.ok, result.errors
    svc.refresh_from_db()
    assert svc.status == ManagedService.Status.UPDATING
    assert svc.applied_config == {"size": "small"}
    assert svc.operation_run_id == "run-project-update"
    assert start_wf.call_args.args[0] == "UpdateManagedServiceWorkflow"
