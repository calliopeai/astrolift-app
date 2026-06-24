"""register_app materializes the manifest's Workload rows (#1014).

Before this, register_app stored manifest_raw + bootstrapped the env but
never created Workload rows — so a manifest-declared agent was deployable
(the renderer reads manifest_raw) but unmanageable (run-spec / scale / live
status / the agent fleet all key on Workload rows). This pins that a
kind=agent manifest workload becomes a Workload row on registration, at
parity with the SCM/registerAgentRepo persist path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp, Workload
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_AGENT_MANIFEST = """
name = "svc"

[[workloads]]
name = "svcagent"
kind = "agent"
replicas = 2

  [[workloads.containers]]
  name = "app"
  is_primary = true
  image_ref = "docker.io/library/busybox:latest"
  command = ["/bin/sh", "-c", "sleep infinity"]
"""


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-wl")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-wl")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-wl")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local-wl", capabilities_manifest={}, config_schema={})]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local-wl",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_register_app_materializes_agent_workload(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Svc",
                slug="svc",
                source_kind="direct_upload",
                source_repo="",
                build_mode="none",
                manifest_raw=_AGENT_MANIFEST,
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="svc")
    w = Workload.objects.filter(registered_app=app, kind="agent", deleted_at__isnull=True).first()
    assert w is not None, "agent workload should be materialized from the manifest"
    assert w.slug == "svcagent"
    assert w.replicas == 2


def test_register_app_without_manifest_creates_no_workloads(permission_resolver):
    """A register with no manifest_raw still works and simply creates no
    Workload rows (the persist step is guarded on a non-empty manifest)."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Bare",
                slug="bare",
                source_repo="acme/bare",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="bare")
    assert Workload.objects.filter(registered_app=app).count() == 0
