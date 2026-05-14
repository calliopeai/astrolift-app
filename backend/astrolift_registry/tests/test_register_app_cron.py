"""Tests for cron trigger support on register_app + update_app (#290).

The wizard at /apps/new captures a five-field cron expression in
step 4; before this change the resolver silently dropped it. These
tests pin the contract:

* mode=cron with a valid expression persists both fields
* mode=cron with an empty/whitespace expression is rejected
* mode=cron with a malformed expression is rejected
* mode!=cron clears cron_expression on update
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
    # register_app requires at least one active cluster in the org
    # (#315). bulk_create the plugin to dodge BaseCoreModel.save's
    # numeric ``version`` collision with the CharField on the model.
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
    )
    return org, project


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_register_app_with_cron_persists_expression(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Nightly",
                slug="nightly",
                source_repo="acme/nightly",
                trigger_mode="cron",
                cron_expression="0 6 * * *",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="nightly")
    assert app.trigger_mode == "cron"
    assert app.cron_expression == "0 6 * * *"


def test_register_app_cron_normalizes_whitespace(permission_resolver):
    """Multiple spaces between fields are collapsed to a single space."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Nightly",
                slug="nightly",
                source_repo="acme/nightly",
                trigger_mode="cron",
                cron_expression="  0   6 *  *   * ",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="nightly")
    assert app.cron_expression == "0 6 * * *"


def test_register_app_cron_mode_requires_expression(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Nightly",
                slug="nightly",
                source_repo="acme/nightly",
                trigger_mode="cron",
                cron_expression="",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "cronExpression"
    assert not RegisteredApp.objects.filter(slug="nightly").exists()


def test_register_app_cron_mode_rejects_whitespace_only_expression(
    permission_resolver,
):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Nightly",
                slug="nightly",
                source_repo="acme/nightly",
                trigger_mode="cron",
                cron_expression="   ",
            ),
        )

    assert not result.ok
    assert result.errors[0].field == "cronExpression"


@pytest.mark.parametrize(
    "expr",
    [
        "0 6 * *",  # only 4 fields
        "0 6 * * * *",  # 6 fields (no seconds support)
        "60 6 * * *",  # minute out of range
        "0 24 * * *",  # hour out of range
        "0 6 32 * *",  # day-of-month out of range
        "0 6 * 13 *",  # month out of range
        "0 6 * * 7",  # day-of-week out of range
        "0 6 * * MON",  # named weekday not supported
        "*/0 * * * *",  # zero step
        "5-3 * * * *",  # inverted range
        "0,, * * * *",  # empty list element
        "abc * * * *",  # garbage
    ],
)
def test_register_app_cron_rejects_invalid_expressions(expr, permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Nightly",
                slug=f"nightly-{abs(hash(expr)) % 10_000}",
                source_repo=f"acme/nightly-{abs(hash(expr)) % 10_000}",
                trigger_mode="cron",
                cron_expression=expr,
            ),
        )

    assert not result.ok, f"expected rejection for {expr!r}"
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "cronExpression"


def test_register_app_non_cron_mode_ignores_cron_expression(permission_resolver):
    """Passing a cron_expression while mode!=cron is silently dropped —
    the field is only meaningful in cron mode and the operator may
    have left it in state from a prior choice."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    with _ctx(org):
        result = RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name="Auto",
                slug="auto-app",
                source_repo="acme/auto",
                trigger_mode="auto_on_push",
                cron_expression="0 6 * * *",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="auto-app")
    assert app.trigger_mode == "auto_on_push"
    assert app.cron_expression == ""


def test_update_app_switching_to_cron_requires_expression(permission_resolver):
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
                trigger_mode="auto_on_push",
            ),
        )
        assert register.ok

        result = RegistryMutation().update_app(
            _info(),
            input=UpdateAppInput(
                id=register.data.id,
                trigger_mode="cron",
            ),
        )

    assert not result.ok
    assert result.errors[0].field == "cronExpression"


def test_update_app_switching_to_cron_persists_expression(permission_resolver):
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
                trigger_mode="auto_on_push",
            ),
        )
        assert register.ok

        result = RegistryMutation().update_app(
            _info(),
            input=UpdateAppInput(
                id=register.data.id,
                trigger_mode="cron",
                cron_expression="*/15 * * * *",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="app")
    assert app.trigger_mode == "cron"
    assert app.cron_expression == "*/15 * * * *"


def test_update_app_switching_away_from_cron_clears_expression(
    permission_resolver,
):
    """Flipping mode away from cron clears the stale expression so a
    later flip back to cron has to supply a fresh, deliberate value."""
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
                trigger_mode="cron",
                cron_expression="0 6 * * *",
            ),
        )
        assert register.ok

        result = RegistryMutation().update_app(
            _info(),
            input=UpdateAppInput(
                id=register.data.id,
                trigger_mode="manual",
            ),
        )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="app")
    assert app.trigger_mode == "manual"
    assert app.cron_expression == ""
