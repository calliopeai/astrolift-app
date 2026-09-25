"""registerApp with an inline manifest that attaches a project managed service (#1759 re-review).

Registration reconciles the manifest's managed services once the app's
environment exists (``_bootstrap_app_environments``). An
``owner_scope = "project"`` entry naming an existing project database
attaches it to the new app, the same attachment attachProjectManagedService
makes, which needs project.update. APP_CREATE alone must not do it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from core.permissions import Permission, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_MANIFEST = """
name = "svc"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true

[[managed_services]]
kind = "postgres"
name = "shared"
owner_scope = "project"
environment = "production"
bind_workloads = ["web"]
"""


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold(monkeypatch):
    monkeypatch.setattr(
        "astrolift_services.managed_service_catalog.resolve_variant",
        lambda **kwargs: SimpleNamespace(variant=kwargs.get("requested_variant") or "resolved-default"),
    )
    monkeypatch.setattr("astrolift_services.managed_service_catalog.validate_config", lambda *_: None)
    org = Organization.objects.create(name="Acme", slug="acme-psa")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-psa")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-psa")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local-psa", capabilities_manifest={}, config_schema={})]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local-psa",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    shared = ManagedService.objects.create(
        project=project,
        tenant_cluster=cluster,
        environment_name="production",
        kind="postgres",
        name="shared",
        variant="resolved-default",
        config={"size": "small"},
        status=ManagedService.Status.ACTIVE,
    )
    return org, project, shared


def _register(org, project):
    with tenant_context(TenantContext(organization_id=org.id)):
        return RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Svc",
                slug="svc",
                source_kind="direct_upload",
                source_repo="",
                build_mode="none",
                manifest_raw=_MANIFEST,
            ),
        )


def test_register_app_refuses_a_project_attachment_without_project_update(permission_resolver, monkeypatch):
    org, project, shared = _scaffold(monkeypatch)
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project)

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert result.errors[0].field == "manifestRaw"
    assert not RegisteredApp.objects.filter(slug="svc").exists()
    assert not ManagedServiceAttachment.objects.filter(managed_service=shared).exists()


def test_register_app_attaches_a_project_service_with_project_update(permission_resolver, monkeypatch):
    org, project, shared = _scaffold(monkeypatch)
    permission_resolver.grant(Permission.APP_CREATE)
    permission_resolver.grant(
        Permission.PROJECT_UPDATE, scope=PermissionScope(kind=ScopeKind.PROJECT, id=project.pk)
    )

    result = _register(org, project)

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="svc")
    attachment = ManagedServiceAttachment.objects.get(
        managed_service=shared, app_environment__registered_app=app, deleted_at__isnull=True
    )
    assert attachment.workload_names == ["web"]
