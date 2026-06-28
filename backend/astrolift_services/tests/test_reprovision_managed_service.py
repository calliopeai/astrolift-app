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
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo-reprv"
    )
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
    )
    with _ctx(org):
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
