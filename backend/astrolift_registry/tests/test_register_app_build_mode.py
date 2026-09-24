"""Tests for build-mode support on register_app + update_app.

RegisteredApp grew a ``build_mode`` axis (``ci_pushed`` /
``platform_build`` / ``none``) plus the ``dockerfile_path`` /
``build_context`` / ``build_args`` trio that the platform-build path
consumes. These tests pin the contract:

* register defaults to ``ci_pushed`` with the documented field defaults
* register with ``platform_build`` + custom build inputs persists them
* an unknown ``build_mode`` is rejected
* a non-object ``build_args`` payload is rejected
* the GraphQL type surfaces every field on the success envelope
* update flips ``build_mode`` and ``build_args`` while leaving
  un-sent fields untouched
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
    # register_app rejects an org with zero managed clusters (#315/#316);
    # bulk_create the plugin to dodge the version-field collision the
    # other registry tests document.
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


def test_register_app_build_mode_defaults(permission_resolver):
    """A register call that omits the build fields lands the documented
    defaults: ci_pushed / Dockerfile / "." / {}."""
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
    assert app.build_mode == RegisteredApp.BuildMode.CI_PUSHED.value
    assert app.dockerfile_path == "Dockerfile"
    assert app.build_context == "."
    assert app.build_args == {}
    # The GraphQL type mirrors the row.
    assert result.data.build_mode == "ci_pushed"
    assert result.data.dockerfile_path == "Dockerfile"
    assert result.data.build_context == "."
    assert result.data.build_args == {}


def test_register_app_platform_build_persists_inputs(permission_resolver):
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
                build_mode="platform_build",
                dockerfile_path="docker/web.Dockerfile",
                build_context="services/web",
                build_args={"NODE_ENV": "production", "PORT": 8080},
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="built")
    assert app.build_mode == "platform_build"
    assert app.dockerfile_path == "docker/web.Dockerfile"
    assert app.build_context == "services/web"
    # Scalar values are stringified into a flat str->str map.
    assert app.build_args == {"NODE_ENV": "production", "PORT": "8080"}
    assert result.data.build_mode == "platform_build"
    assert result.data.build_args == {"NODE_ENV": "production", "PORT": "8080"}


def test_register_app_build_mode_none(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Prebuilt",
                slug="prebuilt",
                source_repo="acme/prebuilt",
                build_mode="none",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="prebuilt")
    assert app.build_mode == "none"


def test_register_app_rejects_unknown_build_mode(permission_resolver):
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
                build_mode="kaniko",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "buildMode"
    assert not RegisteredApp.objects.filter(slug="bad").exists()


def test_register_app_rejects_non_object_build_args(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Bad Args",
                slug="bad-args",
                source_repo="acme/bad-args",
                build_args=["NODE_ENV=production"],
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "buildArgs"
    assert not RegisteredApp.objects.filter(slug="bad-args").exists()


def test_register_app_rejects_non_scalar_build_arg_value(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Nested Args",
                slug="nested-args",
                source_repo="acme/nested-args",
                build_args={"FEATURES": {"flag": True}},
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "buildArgs"


def test_update_app_changes_build_mode_and_args(permission_resolver):
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
                build_mode="platform_build",
                dockerfile_path="ops/Dockerfile",
                build_args={"TARGET": "release"},
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="app")
    assert app.build_mode == "platform_build"
    assert app.dockerfile_path == "ops/Dockerfile"
    assert app.build_args == {"TARGET": "release"}
    # build_context was not sent — it keeps its default.
    assert app.build_context == "."


def test_update_app_leaves_build_fields_untouched_when_omitted(permission_resolver):
    """An update that doesn't mention the build fields must not reset
    them — the None sentinel means 'leave as-is', not 'clear'."""
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
                build_mode="platform_build",
                dockerfile_path="docker/api.Dockerfile",
                build_args={"K": "V"},
            ),
        )
        assert register.ok, register.errors

        # Touch an unrelated field only.
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
    assert app.build_mode == "platform_build"
    assert app.dockerfile_path == "docker/api.Dockerfile"
    assert app.build_args == {"K": "V"}


def test_update_app_rejects_unknown_build_mode(permission_resolver):
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
                build_mode="buildah",
            ),
        )

    assert not result.ok
    assert result.errors[0].field == "buildMode"
    # The rejected update must not have mutated the row.
    app = RegisteredApp.objects.get(slug="app")
    assert app.build_mode == "ci_pushed"


# --- dockerfile_path / build_context are repo-root-relative paths, not
# arbitrary filesystem paths (#1756 adversarial review, H3) -------------
#
# Kaniko takes both verbatim as --dockerfile / --context-sub-path, so an
# app-level value with no further base to resolve against must be
# rejected at register/update time, the same way a container-level value
# is rejected once resolved (astrolift_manifest.parser / build_image.py).


def test_register_app_rejects_an_absolute_dockerfile_path(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Bad Path",
                slug="bad-path",
                source_repo="acme/bad-path",
                dockerfile_path="/etc/passwd",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "dockerfilePath"
    assert not RegisteredApp.objects.filter(slug="bad-path").exists()


def test_register_app_rejects_a_build_context_that_escapes_the_repo_root(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Escapes",
                slug="escapes",
                source_repo="acme/escapes",
                build_context="../..",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "buildContext"
    assert not RegisteredApp.objects.filter(slug="escapes").exists()


def test_update_app_rejects_an_absolute_build_context(permission_resolver):
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
                build_context="/abs/path",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "buildContext"
    # The rejected update must not have mutated the row.
    app = RegisteredApp.objects.get(slug="app")
    assert app.build_context == "."
