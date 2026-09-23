"""Per-org module enablement (#1859).

An org admin turns a module on for their org; the install admin can force it
off for every org. ``astrolift_identity.org_modules`` combines the two, and
``me.modules`` reports the result as ``enabled`` next to the permission-derived
``can_*`` fields.

Real Postgres and the real Constance backend throughout; ``me.modules`` reads
real RoleBindings, the mutation gate uses the controllable resolver the other
identity mutation tests use.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from constance.test import override_config
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_identity import org_modules
from astrolift_identity.models import Organization, OrganizationModule, Role, RoleBinding
from astrolift_identity.schema.mutations import IdentityMutation, SetOrganizationModuleInput
from astrolift_identity.schema.queries import MeType
from core.permissions import ORG_MODULE_KEYS, Permission, module_entitlements
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

CHAT = org_modules.CHAT_STUDIO_INTEGRATION
ATTACH = org_modules.AGENT_LIVE_ATTACH


@pytest.fixture
def org_a():
    return Organization.objects.create(name="Org A", slug="org-a-modules")


@pytest.fixture
def org_b():
    return Organization.objects.create(name="Org B", slug="org-b-modules")


@pytest.fixture
def user():
    return get_user_model().objects.create(username="modules-user", email="modules-user@astrolift.dev")


def _enable(org, key):
    return OrganizationModule.objects.create(organization=org, key=key, enabled=True)


def _bind(user, org, perms):
    role = Role.objects.create(
        name=f"r-{org.slug}",
        slug=f"r-{org.slug}",
        scope_level=Role.ScopeLevel.ORG,
        permissions=list(perms),
        is_system=False,
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)


def _modules(user, org):
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        rows = MeType(id=str(user.pk), profile=None).modules(info)
    return {r.key: r for r in rows}


def _set(org, key, enabled):
    info = SimpleNamespace(context=SimpleNamespace(user=None, request=None))
    with tenant_context(TenantContext(organization_id=org.id)):
        return IdentityMutation().set_organization_module(
            info, input=SetOrganizationModuleInput(key=key, enabled=enabled)
        )


# ---- the state helper ---------------------------------------------------


def test_module_is_off_until_the_org_turns_it_on(org_a):
    assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_NOT_ENABLED)
    _enable(org_a, CHAT)
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)
    # One module on says nothing about the other.
    assert org_modules.module_state(org_a.id, ATTACH) == (False, org_modules.REASON_NOT_ENABLED)


def test_an_org_row_turned_off_or_deleted_is_off(org_a):
    row = _enable(org_a, CHAT)
    row.enabled = False
    row.save()
    assert org_modules.module_state(org_a.id, CHAT)[0] is False
    row.enabled = True
    row.save()
    row.soft_delete()
    assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_NOT_ENABLED)


def test_install_force_off_beats_the_org_switch(org_a):
    _enable(org_a, CHAT)
    with override_config(CHAT_STUDIO_INTEGRATION_ALLOWED=False):
        assert org_modules.module_state(org_a.id, CHAT) == (False, org_modules.REASON_DISABLED_BY_INSTALL)
        assert org_modules.enabled_modules(org_a.id) == frozenset()
    assert org_modules.enabled_modules(org_a.id) == frozenset({CHAT})


def test_install_reason_wins_even_when_the_org_never_enabled_it(org_a):
    """The install switch is checked first, so the reason names the only
    admin who can actually change the outcome."""
    with override_config(AGENT_LIVE_ATTACH_ALLOWED=False):
        assert org_modules.module_state(org_a.id, ATTACH) == (False, org_modules.REASON_DISABLED_BY_INSTALL)


def test_the_module_keys_agree_across_layers():
    """core.permissions cannot import the identity app, so it names the keys
    itself; they must stay the model's keys and the install switches' keys."""
    model_keys = {k.value for k in OrganizationModule.Key}
    assert set(ORG_MODULE_KEYS) == model_keys == set(org_modules.INSTALL_SWITCHES)


# ---- me.modules ---------------------------------------------------------


def test_me_modules_org_a_enabled_org_b_not(org_a, org_b, user):
    _enable(org_a, CHAT)
    _bind(user, org_a, [Permission.APP_READ.value, Permission.APP_CREATE.value])
    _bind(user, org_b, [Permission.APP_READ.value, Permission.APP_CREATE.value])

    a = _modules(user, org_a)
    b = _modules(user, org_b)

    assert a[CHAT].enabled is True
    assert b[CHAT].enabled is False
    # Same permissions in both orgs, so the capability half is identical;
    # only the org switch differs.
    assert (a[CHAT].can_view, a[CHAT].can_create) == (b[CHAT].can_view, b[CHAT].can_create) == (True, True)
    assert a[ATTACH].enabled is False


def test_me_modules_keeps_the_entity_modules_enabled(org_b, user):
    _bind(user, org_b, [Permission.APP_READ.value])
    mods = _modules(user, org_b)
    assert list(mods) == ["apps", "agents", "workflows", "admin", CHAT, ATTACH]
    for key in ("apps", "agents", "workflows", "admin"):
        assert mods[key].enabled is True, key


def test_me_modules_capabilities_follow_permissions(org_a, user):
    _enable(org_a, ATTACH)
    _bind(user, org_a, [Permission.AGENT_READ.value, Permission.AGENT_BOX_ATTACH.value])
    mods = _modules(user, org_a)
    attach = mods[ATTACH]
    assert (attach.enabled, attach.can_view, attach.can_run) == (True, True, True)
    assert (attach.can_create, attach.can_manage) == (False, False)
    # agent.read alone does not grant attach.
    assert mods[CHAT].can_view is False


def test_me_modules_reflects_an_install_force_off(org_a, user):
    _enable(org_a, CHAT)
    _bind(user, org_a, [Permission.APP_READ.value])
    with override_config(CHAT_STUDIO_INTEGRATION_ALLOWED=False):
        assert _modules(user, org_a)[CHAT].enabled is False
    assert _modules(user, org_a)[CHAT].enabled is True


def test_superuser_holds_every_capability_but_not_a_disabled_module(org_b):
    boss = get_user_model().objects.create(
        username="modules-root", email="root@astrolift.dev", is_superuser=True
    )
    mods = _modules(boss, org_b)
    assert mods[CHAT].can_create is True
    assert mods[CHAT].enabled is False
    assert mods["apps"].enabled is True


def test_module_entitlements_is_pure_about_enabled():
    rows = {r.key: r for r in module_entitlements([], org_modules_enabled={CHAT})}
    assert rows[CHAT].enabled is True
    assert rows[ATTACH].enabled is False


# ---- setOrganizationModule ---------------------------------------------


def test_org_admin_turns_a_module_on_and_off(org_a, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    on = _set(org_a, CHAT, True)
    assert on.ok is True, on.errors
    assert (on.data.key, on.data.enabled) == (CHAT, True)
    assert org_modules.module_state(org_a.id, CHAT) == (True, None)

    off = _set(org_a, CHAT, False)
    assert off.ok is True, off.errors
    assert off.data.enabled is False
    assert org_modules.module_state(org_a.id, CHAT)[0] is False
    # Toggling reuses the one live row rather than stacking new ones.
    assert OrganizationModule.objects.filter(organization=org_a, key=CHAT).count() == 1


def test_setting_a_module_only_touches_the_callers_org(org_a, org_b, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    assert _set(org_a, CHAT, True).ok is True
    assert org_modules.module_state(org_b.id, CHAT)[0] is False


def test_member_without_org_update_is_denied(org_a, permission_resolver):
    permission_resolver.grant(Permission.ORG_READ)
    result = _set(org_a, CHAT, True)
    assert result.ok is False
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert not OrganizationModule.objects.filter(organization=org_a).exists()


def test_enabling_a_module_the_install_forced_off_is_refused(org_a, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with override_config(CHAT_STUDIO_INTEGRATION_ALLOWED=False):
        refused = _set(org_a, CHAT, True)
        assert refused.ok is False
        assert refused.errors[0].code == "PRECONDITION"
        assert not OrganizationModule.objects.filter(organization=org_a).exists()
        # Turning it off is always allowed.
        assert _set(org_a, CHAT, False).ok is True


def test_unknown_module_is_a_validation_error(org_a, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    result = _set(org_a, "billing_magic", True)
    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"


# ---- existing builder callers keep access -------------------------------


def test_orgs_with_a_dev_environment_get_the_builder_module(org_a, org_b, user):
    from astrolift_lifecycle.models import DevEnvironment

    DevEnvironment.objects.create(organization=org_a, creator=user)
    name = "0032_enable_chat_studio_module_for_builder_orgs"
    migration = importlib.import_module(f"astrolift_identity.migrations.{name}")
    # The models as the migration sees them, not today's.
    historical = MigrationExecutor(connection).loader.project_state(("astrolift_identity", name)).apps
    migration.enable_for_existing_builder_orgs(historical, None)
    # A second run is a no-op rather than a duplicate-row error.
    migration.enable_for_existing_builder_orgs(historical, None)

    assert org_modules.module_state(org_a.id, CHAT) == (True, None)
    assert org_modules.module_state(org_b.id, CHAT)[0] is False
    assert OrganizationModule.objects.filter(organization=org_a, key=CHAT).count() == 1
