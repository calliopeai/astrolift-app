"""Tests for setEnvironmentSetting / clearEnvironmentSetting mutations (#744)."""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import EnvironmentSetting
from astrolift_lifecycle.schema.mutations import (
    ClearEnvironmentSettingInput,
    LifecycleMutation,
    SetEnvironmentSettingInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _ctx(org, actor=None):
    return tenant_context(
        TenantContext(
            organization_id=org.id,
            actor_user_id=actor.id if actor is not None else None,
        )
    )


# ── set ──────────────────────────────────────────────────────────────────────


def test_creates_new_setting(org, env, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(
                environment_id=str(env.guid),
                key="LOG_LEVEL",
                value="debug",
            ),
        )

    assert result.ok, result.errors
    assert result.data.key == "LOG_LEVEL"
    assert result.data.value == "debug"
    assert (
        EnvironmentSetting.objects.filter(
            app_environment=env, key="LOG_LEVEL", deleted_at__isnull=True
        ).count()
        == 1
    )


def test_updates_existing_setting(org, env, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(environment_id=str(env.guid), key="X", value="first"),
        )
        result = LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(environment_id=str(env.guid), key="X", value="second"),
        )

    assert result.ok, result.errors
    assert result.data.value == "second"
    assert (
        EnvironmentSetting.objects.filter(app_environment=env, key="X", deleted_at__isnull=True).count() == 1
    )


def test_rejects_empty_key(org, env, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(environment_id=str(env.guid), key="  ", value="v"),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"


def test_rejects_unknown_environment(org, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(
                environment_id="00000000-0000-0000-0000-000000000000",
                key="K",
                value="v",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_set_denied_without_permission(org, env, actor, fake_info):
    with _ctx(org, actor):
        result = LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(environment_id=str(env.guid), key="K", value="v"),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ── clear ─────────────────────────────────────────────────────────────────────


def test_soft_deletes_setting(org, env, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        LifecycleMutation().set_environment_setting(
            fake_info,
            input=SetEnvironmentSettingInput(environment_id=str(env.guid), key="FEAT", value="on"),
        )
        result = LifecycleMutation().clear_environment_setting(
            fake_info,
            input=ClearEnvironmentSettingInput(environment_id=str(env.guid), key="FEAT"),
        )

    assert result.ok, result.errors
    assert (
        EnvironmentSetting.objects.filter(app_environment=env, key="FEAT", deleted_at__isnull=True).count()
        == 0
    )
    assert EnvironmentSetting.all_objects.filter(app_environment=env, key="FEAT").count() == 1


def test_clear_not_found_when_no_setting(org, env, actor, fake_info, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org, actor):
        result = LifecycleMutation().clear_environment_setting(
            fake_info,
            input=ClearEnvironmentSettingInput(environment_id=str(env.guid), key="NONEXISTENT"),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_clear_denied_without_permission(org, env, actor, fake_info):
    with _ctx(org, actor):
        result = LifecycleMutation().clear_environment_setting(
            fake_info,
            input=ClearEnvironmentSettingInput(environment_id=str(env.guid), key="K"),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
