"""A new install does not offer the Chat Studio integration (#2551).

Its install switch, ``CHAT_STUDIO_INTEGRATION_ALLOWED``, ships off: nothing on a
new install makes the builder API usable, so the organization's modules card
does not list the module and an org admin cannot turn it on until the install
admin turns the switch on. ``me.modules[].available`` tells the card which
modules the install offers. An install upgraded while an organization uses the
module keeps the switch on.

The suite runs with the switch on (the root conftest), so the shipped default is
read here at import, before any test patches it. Real Postgres and the real
Constance backend throughout.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from constance.codecs import dumps, loads
from constance.models import Constance
from constance.test import override_config
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from astrolift_identity import org_modules
from astrolift_identity.models import Organization, OrganizationModule
from astrolift_identity.schema.mutations import IdentityMutation, SetOrganizationModuleInput
from astrolift_identity.schema.queries import MeType
from astrolift_identity.tests.test_licence_modules_1879 import _PUBLIC, _licence
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

CHAT = org_modules.CHAT_STUDIO_INTEGRATION
RUNS = org_modules.CHAT_STUDIO_AGENT_RUNS
SWITCH = "CHAT_STUDIO_INTEGRATION_ALLOWED"
SHIPPED = django_settings.CONSTANCE_CONFIG[SWITCH][0]
MIGRATION = "0055_keep_chat_studio_integration_allowed"


@pytest.fixture
def org():
    return Organization.objects.create(name="Fresh Org", slug="fresh-org-2551")


@pytest.fixture
def user():
    return get_user_model().objects.create(username="fresh-2551", email="fresh-2551@astrolift.dev")


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


# ---- a new install ---------------------------------------------------------


def test_the_install_switch_ships_off():
    assert SHIPPED is False


def test_a_new_install_does_not_offer_the_chat_studio_integration(org, user):
    with override_config(**{SWITCH: SHIPPED}):
        assert org_modules.available(CHAT) is False
        rows = _modules(user, org)

    assert rows[CHAT].available is False
    assert rows[CHAT].enabled is False
    # The other per-org modules are offered as before; the install-wide ones always are.
    assert rows[RUNS].available is True
    assert rows["apps"].available is True


def test_an_org_admin_cannot_turn_it_on_until_the_install_offers_it(org, user, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with override_config(**{SWITCH: SHIPPED}):
        refused = _set(org, CHAT, True)
    assert refused.ok is False
    assert refused.errors[0].code == "MODULE_DISABLED_BY_INSTALL"
    assert not OrganizationModule.objects.filter(organization=org, key=CHAT).exists()

    with override_config(**{SWITCH: True}):
        assert _set(org, CHAT, True).ok is True
        rows = _modules(user, org)
    assert rows[CHAT].available is True
    assert rows[CHAT].enabled is True


def test_a_module_the_licence_withholds_is_not_available(org, user, settings):
    settings.CALLIOPE_LICENSE = _licence(astrolift_modules=[CHAT])
    settings.CALLIOPE_LICENSE_PUBLIC_KEY = _PUBLIC
    with override_config(**{SWITCH: True}):
        rows = _modules(user, org)

    assert rows[CHAT].available is True
    assert rows[RUNS].available is False
    assert rows[RUNS].licensed is False


# ---- an upgraded install ---------------------------------------------------


def _migrate():
    migration = importlib.import_module(f"astrolift_identity.migrations.{MIGRATION}")
    # The models as the migration sees them, not today's.
    historical = MigrationExecutor(connection).loader.project_state(("astrolift_identity", MIGRATION)).apps
    migration.keep_allowed_where_in_use(historical, None)


def _recorded():
    """The switch's stored value, or None when nothing is stored and the default applies."""
    row = Constance.objects.filter(key=SWITCH).first()
    return None if row is None else loads(row.value)


def test_an_upgrade_keeps_the_switch_on_where_an_organization_uses_the_module(org):
    OrganizationModule.objects.create(organization=org, key=CHAT, enabled=True)
    _migrate()
    # A second run is a no-op rather than a duplicate-key error.
    _migrate()

    assert _recorded() is True


@pytest.mark.parametrize("state", ["no_row", "turned_off", "row_deleted", "org_deleted"])
def test_an_upgrade_records_nothing_where_no_organization_uses_the_module(org, state):
    if state != "no_row":
        row = OrganizationModule.objects.create(organization=org, key=CHAT, enabled=state != "turned_off")
        if state == "row_deleted":
            row.soft_delete()
        if state == "org_deleted":
            org.soft_delete()
    _migrate()

    assert _recorded() is None


def test_an_upgrade_leaves_a_switch_an_admin_already_set(org):
    Constance.objects.create(key=SWITCH, value=dumps(False))
    OrganizationModule.objects.create(organization=org, key=CHAT, enabled=True)
    _migrate()

    assert _recorded() is False
