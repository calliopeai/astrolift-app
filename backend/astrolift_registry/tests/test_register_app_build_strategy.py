"""Tests for build-strategy support on register_app + update_app (#867).

``build_strategy`` is the orthogonal "how to build" axis next to
``build_mode``: ``off`` (default), ``dockerfile``, ``buildpacks``,
``nixpacks``. These tests pin the input contract:

* register accepts a non-default ``build_strategy`` and persists it
* register defaults to ``off`` when the field is omitted
* an unknown ``build_strategy`` is rejected (deny-by-default) and the
  row is never created
* update flips ``build_strategy`` and leaves it untouched when omitted
* an unknown ``build_strategy`` on update is rejected without mutating
  the row
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegisterAppInput,
    RegistryMutation,
    UpdateAppInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local",
                slug="local",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local",
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


def test_register_app_build_strategy_defaults_off(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Plain",
                slug="plain",
                source_repo="acme/plain",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="plain")
    assert app.build_strategy == RegisteredApp.BuildStrategy.OFF.value
    assert result.data.build_strategy == "off"


def test_register_app_persists_build_strategy(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Built",
                slug="built",
                source_repo="acme/built",
                build_strategy="dockerfile",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="built")
    assert app.build_strategy == "dockerfile"
    assert result.data.build_strategy == "dockerfile"


@pytest.mark.parametrize("strategy", ["buildpacks", "nixpacks"])
def test_register_app_accepts_all_strategies(permission_resolver, strategy):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name=strategy,
                slug=strategy,
                source_repo=f"acme/{strategy}",
                build_strategy=strategy,
            ),
        )

    assert result.ok, result.errors
    assert RegisteredApp.objects.get(slug=strategy).build_strategy == strategy


def test_register_app_rejects_unknown_build_strategy(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Bad",
                slug="bad",
                source_repo="acme/bad",
                build_strategy="kaniko",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "buildStrategy"
    assert not RegisteredApp.objects.filter(slug="bad").exists()


def test_update_app_changes_build_strategy(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        register = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="app",
                source_repo="acme/app",
            ),
        )
        assert register.ok, register.errors

        result = RegistryMutation().update_app(
            _info(),
            input=UpdateAppInput(
                id=register.data.id,
                build_strategy="nixpacks",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="app")
    assert app.build_strategy == "nixpacks"


def test_update_app_leaves_build_strategy_untouched_when_omitted(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        register = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="app",
                source_repo="acme/app",
                build_strategy="buildpacks",
            ),
        )
        assert register.ok, register.errors

        result = RegistryMutation().update_app(
            _info(),
            input=UpdateAppInput(
                id=register.data.id,
                description="new description",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="app")
    assert app.description == "new description"
    assert app.build_strategy == "buildpacks"


def test_update_app_rejects_unknown_build_strategy(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        register = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="App",
                slug="app",
                source_repo="acme/app",
                build_strategy="dockerfile",
            ),
        )
        assert register.ok, register.errors

        result = RegistryMutation().update_app(
            _info(),
            input=UpdateAppInput(
                id=register.data.id,
                build_strategy="buildah",
            ),
        )

    assert not result.ok
    assert result.errors[0].field == "buildStrategy"
    # The rejected update must not have mutated the row.
    app = RegisteredApp.objects.get(slug="app")
    assert app.build_strategy == "dockerfile"
