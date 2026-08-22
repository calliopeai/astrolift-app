"""Registration honours the org-only source policy (#1543).

The policy module has its own tests. These exist because a policy with
no enforcement at the mutation is the defect this repo has closed nine
times over: a setting an operator can turn on that changes nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegisterAppInput,
    RegisterAppRepoInput,
    RegistryMutation,
)
from astrolift_scm.models import SourceConnection
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-policy")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-policy")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-policy")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local", capabilities_manifest={}, config_schema={})]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local-policy",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


@pytest.fixture
def policy_on(monkeypatch):
    class _Cfg:
        RESTRICT_SOURCE_REPOS_TO_ORG = True

    monkeypatch.setattr("constance.config", _Cfg(), raising=False)


def _register(org, project, repo: str):
    with tenant_context(TenantContext(organization_id=org.id)):
        return RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Widgets",
                slug="widgets",
                source_repo=repo,
            ),
        )


def test_a_personal_repo_is_refused_at_registration(permission_resolver, policy_on):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    SourceConnection.objects.create(organization=org, kind="github_app_install", account_login="acme")

    result = _register(org, project, "someone-personal/widgets")

    assert not result.ok
    assert result.errors[0].field == "sourceRepo"
    assert "someone-personal" in result.errors[0].message
    assert not RegisteredApp.objects.filter(slug="widgets").exists()


def test_an_org_repo_still_registers(permission_resolver, policy_on):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    SourceConnection.objects.create(organization=org, kind="github_app_install", account_login="acme")

    result = _register(org, project, "acme/widgets")

    assert result.ok, result.errors
    assert RegisteredApp.objects.filter(slug="widgets").exists()


def test_a_personal_repo_registers_while_the_policy_is_off(permission_resolver):
    """The default is today's behaviour. Shipping this on would be an
    outage for every install with a personal repo already registered."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, "someone-personal/widgets")

    assert result.ok, result.errors


def test_the_bulk_repo_path_is_gated_too(permission_resolver, policy_on):
    """register_app_repo onboards every manifest in a repo at once, so an
    ungated path here would create many apps from one refused repo."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    SourceConnection.objects.create(organization=org, kind="github_app_install", account_login="acme")

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryMutation().register_app_repo(
            _info(),
            input=RegisterAppRepoInput(
                project_id=str(project.guid),
                source_repo="someone-personal/monorepo",
            ),
        )

    assert not result.ok
    assert "someone-personal" in result.errors[0].message
