"""A new app may not take another app's (or the platform's) namespace (#1912)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.namespaces import namespace_refusal
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _org(slug):
    org = Organization.objects.create(name=slug, slug=slug)
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{uuid.uuid4().hex[:4]}")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug=f"demo-{uuid.uuid4().hex[:4]}"
    )
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local", slug=f"local-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug=f"local-{uuid.uuid4().hex[:6]}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _register(org, project, slug):
    info = SimpleNamespace(context=SimpleNamespace(user=None, request=None))
    with tenant_context(TenantContext(organization_id=org.id)):
        return RegistryMutation().register_app(
            info,
            input=RegisterAppInput(
                project_id=str(project.guid), name=slug, slug=slug, source_repo=f"acme/{slug}"
            ),
        )


def test_the_second_org_cannot_register_into_the_first_orgs_namespace(permission_resolver):
    permission_resolver.grant(Permission.APP_CREATE)
    acme, acme_project = _org("acme")
    acme_x, acme_x_project = _org("acme-x")

    first = _register(acme, acme_project, "x-y")
    second = _register(acme_x, acme_x_project, "y")

    assert first.ok, first.errors
    assert RegisteredApp.objects.get(organization=acme).k8s_namespace == "acme-x-y"
    assert second.ok is False
    assert second.errors[0].code == "VALIDATION"
    assert not RegisteredApp.objects.filter(organization=acme_x).exists()


def test_platform_namespaces_are_reserved():
    assert namespace_refusal("astrolift-agents-x", organization_id=1)
    assert namespace_refusal("astrolift-system", organization_id=1)
    assert namespace_refusal("kube-system", organization_id=1)
    assert namespace_refusal("acme-web", organization_id=1) is None


def test_a_deleted_app_frees_its_namespace_only_for_its_own_org(permission_resolver):
    permission_resolver.grant(Permission.APP_CREATE)
    acme, acme_project = _org("acme")
    acme_x, _ = _org("acme-x")
    first = _register(acme, acme_project, "x-y")
    assert first.ok, first.errors
    RegisteredApp.objects.get(organization=acme).soft_delete()

    assert namespace_refusal("acme-x-y", organization_id=acme.id) is None
    assert namespace_refusal("acme-x-y", organization_id=acme_x.id)
