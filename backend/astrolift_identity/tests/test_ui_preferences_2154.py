"""Server-side UI preferences and the org default for restricted settings (#2154)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, UserPreferences
from astrolift_identity.schema.mutations import IdentityMutation, UpdateMyUiPreferencesInput
from astrolift_identity.schema.mutations.types import UpdateOrganizationInput
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-2154")


@pytest.fixture
def user():
    return get_user_model().objects.create(username="prefs-2154", email="prefs-2154@acme.test")


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


def _read(org, user):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        return IdentityQuery().astrolift_my_ui_preferences(_info(user))


def _write(org, user, **fields):
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        return IdentityMutation().update_my_ui_preferences(_info(user), UpdateMyUiPreferencesInput(**fields))


def test_defaults_without_a_row_and_the_read_creates_none(org, user):
    prefs = _read(org, user)
    assert prefs.home_layout is None
    assert prefs.home_layout_asked is False
    assert (prefs.fleet_view, prefs.workflow_view, prefs.app_view, prefs.motion) == (
        "orbit",
        "transit",
        "auto",
        "system",
    )
    assert prefs.flow_particles is True
    assert prefs.restricted_settings == "show"
    assert prefs.restricted_settings_choice is None
    assert prefs.appearance == {}
    assert not UserPreferences.objects.filter(user=user).exists()


def test_partial_update_saves_and_reads_back(org, user):
    result = _write(org, user, home_layout="operator", fleet_view="hive", flow_particles=False)
    assert result.ok, result.errors
    assert result.data.home_layout == "operator"
    assert result.data.home_layout_asked is True, "choosing a layout answers the question"

    # A second write leaves the first one's fields alone.
    assert _write(org, user, motion="reduced", appearance={"ground": "paper", "accent": "copper"}).ok
    prefs = _read(org, user)
    assert (prefs.home_layout, prefs.fleet_view, prefs.motion) == ("operator", "hive", "reduced")
    assert prefs.flow_particles is False
    assert prefs.appearance == {"ground": "paper", "accent": "copper"}


def test_null_resets_a_field_to_its_default(org, user):
    assert _write(org, user, fleet_view="swarm", home_layout="apps", restricted_settings="hide").ok
    assert _write(org, user, fleet_view=None, home_layout=None, restricted_settings=None).ok
    prefs = _read(org, user)
    assert prefs.fleet_view == "orbit"
    assert prefs.home_layout is None
    assert prefs.home_layout_asked is True
    assert prefs.restricted_settings_choice is None


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"home_layout": "finance"}, "homeLayout"),
        ({"fleet_view": "planets"}, "fleetView"),
        ({"workflow_view": "orbit"}, "workflowView"),
        ({"app_view": "graphs"}, "appView"),
        ({"motion": "slow"}, "motion"),
        ({"restricted_settings": "grey"}, "restrictedSettings"),
        ({"appearance": {"ground": "neon"}}, "appearance"),
    ],
)
def test_values_outside_the_registry_are_refused_and_nothing_is_written(org, user, fields, field):
    assert _write(org, user, fleet_view="hive").ok
    result = _write(org, user, flow_particles=False, **fields)
    assert not result.ok
    assert result.errors[0].field == field
    prefs = _read(org, user)
    assert prefs.fleet_view == "hive"
    assert prefs.flow_particles is True, "a refused input wrote part of itself"


def test_org_default_applies_until_the_person_chooses(org, user, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = IdentityMutation().update_organization(
            _info(user), UpdateOrganizationInput(id=str(org.guid), restricted_settings_default="hide")
        )
    assert result.ok, result.errors
    assert result.data.restricted_settings_default == "hide"

    prefs = _read(org, user)
    assert (prefs.restricted_settings, prefs.restricted_settings_org_default) == ("hide", "hide")

    assert _write(org, user, restricted_settings="show").ok
    prefs = _read(org, user)
    assert prefs.restricted_settings == "show"
    assert prefs.restricted_settings_choice == "show"


def test_org_default_is_validated(org, user, permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = IdentityMutation().update_organization(
            _info(user), UpdateOrganizationInput(id=str(org.guid), restricted_settings_default="maybe")
        )
    assert not result.ok
    assert result.errors[0].field == "restrictedSettingsDefault"
    org.refresh_from_db()
    assert org.restricted_settings_default == "show"


def test_preferences_are_per_person_and_the_default_per_org(org, user):
    other_org = Organization.objects.create(
        name="Other", slug="other-2154", restricted_settings_default="hide"
    )
    someone = get_user_model().objects.create(username="someone-2154", email="someone-2154@acme.test")
    assert _write(org, user, fleet_view="graph").ok

    assert _read(org, someone).fleet_view == "orbit", "one person's choice reached another"
    assert _read(org, user).restricted_settings == "show"
    # The same person in another org reads that org's default.
    assert _read(other_org, user).restricted_settings == "hide"
    assert _read(other_org, user).fleet_view == "graph"


def test_anonymous_is_refused(org):
    anon = SimpleNamespace(is_authenticated=False)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=None)):
        result = IdentityMutation().update_my_ui_preferences(
            _info(anon), UpdateMyUiPreferencesInput(fleet_view="hive")
        )
    assert not result.ok
