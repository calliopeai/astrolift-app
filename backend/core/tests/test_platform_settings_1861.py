"""Platform settings in the web admin (#1861).

Every Constance key is editable here or says why not; values are cleaned by
the Django admin's own form field; a dangerous change needs confirming; only
the platform operator reads or writes; every change is audited with its old
and new value. Writes run inside ``override_config`` so the cached Constance
values are restored after each test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from constance import config
from constance.test import override_config
from django.conf import settings
from django.contrib.auth import get_user_model

import core.mutations as core_mutations
from core import platform_settings
from core.permissions import Permission, PermissionDenied
from core.schema.types.platform_settings import PlatformSettingsMutations, PlatformSettingsQuery
from core.schema.types.server_info import _PUBLIC_FEATURE_FLAGS

pytestmark = pytest.mark.django_db

_DEFAULTS = {key: options[0] for key, options in settings.CONSTANCE_CONFIG.items()}


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user, request=None))


def _operator():
    return get_user_model()(username="settings-operator", is_superuser=True, is_active=True)


def _org_admin():
    return get_user_model()(username="org-admin", is_superuser=False, is_active=True)


@pytest.fixture
def audit():
    captured = []
    original = core_mutations._audit_writer

    def _writer(entry):
        captured.append(entry)
        original(entry)

    core_mutations.register_audit_writer(_writer)
    yield captured
    core_mutations.register_audit_writer(original)


# ---- the catalogue ----------------------------------------------------


def test_every_setting_is_editable_here_or_says_why_not():
    rows = {row.key: row for row in platform_settings.list_settings()}

    assert set(rows) == set(settings.CONSTANCE_CONFIG)
    for row in rows.values():
        assert row.editable != bool(row.read_only_reason), row.key
    flags = {constance_key for constance_key, _p, _d in _PUBLIC_FEATURE_FLAGS}
    assert {key for key, row in rows.items() if not row.editable} == flags | set(
        platform_settings.DJANGO_ONLY
    )


def test_the_curated_lists_name_real_settings():
    assert set(platform_settings.DJANGO_ONLY) <= set(settings.CONSTANCE_CONFIG)
    assert set(platform_settings.DANGEROUS) <= set(settings.CONSTANCE_CONFIG)


def test_settings_follow_the_admin_fieldsets_and_types():
    rows = {row.key: row for row in platform_settings.list_settings()}

    assert rows["AGENT_POD_UID"].group == "Agent pods" and rows["AGENT_POD_UID"].kind == "int"
    assert rows["AGENT_POD_UID"].warning
    assert rows["VLLM_FRONTEND_DEFAULT"].kind == "choice"
    assert rows["VLLM_FRONTEND_DEFAULT"].choices == ("rust", "python")
    assert rows["REQUIRE_STEP_UP_AUTH"].kind == "bool"
    assert {row.kind for row in rows.values()} <= {"bool", "choice", "int", "float", "decimal", "string"}


@override_config(GOOGLE_PLAY_INTEGRITY_API_KEY="private-key-canary")
def test_a_credential_is_never_read_back():
    row = platform_settings.get_setting("GOOGLE_PLAY_INTEGRITY_API_KEY")

    assert row.value == "" and row.default == "" and not row.editable
    assert "private-key-canary" not in repr(platform_settings.list_settings())


# ---- writes -----------------------------------------------------------


@override_config(AGENT_POD_UID=_DEFAULTS["AGENT_POD_UID"])
def test_a_value_is_cleaned_by_the_admin_field():
    with pytest.raises(platform_settings.PlatformSettingError) as refused:
        platform_settings.set_setting("AGENT_POD_UID", "0", confirmed=True)  # root, posix_id refuses it
    assert refused.value.code == "VALIDATION"

    with pytest.raises(platform_settings.PlatformSettingError):
        platform_settings.set_setting("AGENT_POD_UID", "lots", confirmed=True)

    before, after = platform_settings.set_setting("AGENT_POD_UID", "42043", confirmed=True)
    assert before.value == "42042" and after.value == "42043" and after.changed
    assert config.AGENT_POD_UID == 42043


@override_config(AGENT_POD_UID=_DEFAULTS["AGENT_POD_UID"])
def test_a_dangerous_change_needs_confirming():
    with pytest.raises(platform_settings.PlatformSettingError) as refused:
        platform_settings.set_setting("AGENT_POD_UID", "42043")
    assert refused.value.code == "PRECONDITION"
    assert config.AGENT_POD_UID == 42042


@override_config(VLLM_FRONTEND_DEFAULT="rust", AUDIT_RETENTION_DAYS=_DEFAULTS["AUDIT_RETENTION_DAYS"])
def test_choices_yes_no_and_reset():
    with pytest.raises(platform_settings.PlatformSettingError):
        platform_settings.set_setting("VLLM_FRONTEND_DEFAULT", "fortran")
    platform_settings.set_setting("VLLM_FRONTEND_DEFAULT", "python")
    assert config.VLLM_FRONTEND_DEFAULT == "python"

    with pytest.raises(platform_settings.PlatformSettingError):
        platform_settings.set_setting("REQUIRE_STEP_UP_AUTH", "yes", confirmed=True)

    platform_settings.set_setting("AUDIT_RETENTION_DAYS", "7")
    before, after = platform_settings.reset_setting("AUDIT_RETENTION_DAYS")
    assert before.value == "7" and not after.changed


def test_a_feature_flag_or_credential_is_refused_here():
    for key in ("TEMPORAL_ENABLED", "GOOGLE_PLAY_INTEGRITY_API_KEY"):
        with pytest.raises(platform_settings.PlatformSettingError) as refused:
            platform_settings.set_setting(key, "true", confirmed=True)
        assert refused.value.code == "VALIDATION"

    with pytest.raises(platform_settings.PlatformSettingError) as missing:
        platform_settings.set_setting("NOT_A_SETTING", "1")
    assert missing.value.code == "NOT_FOUND"


# ---- GraphQL ----------------------------------------------------------


def test_only_the_platform_operator_reads_or_writes(permission_resolver):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)  # every org admin holds it

    with pytest.raises(PermissionDenied):
        PlatformSettingsQuery().astrolift_platform_settings(_info(_org_admin()))
    result = PlatformSettingsMutations().set_platform_setting(
        _info(_org_admin()), key="AUDIT_RETENTION_DAYS", value="7"
    )
    assert result.ok is False and result.errors[0].code == "PERMISSION_DENIED"
    assert config.AUDIT_RETENTION_DAYS == _DEFAULTS["AUDIT_RETENTION_DAYS"]

    rows = PlatformSettingsQuery().astrolift_platform_settings(_info(_operator()))
    assert len(rows) == len(settings.CONSTANCE_CONFIG)


@override_config(AUDIT_RETENTION_DAYS=_DEFAULTS["AUDIT_RETENTION_DAYS"])
def test_a_change_is_audited_with_old_and_new_value(permission_resolver, audit):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)

    result = PlatformSettingsMutations().set_platform_setting(
        _info(_operator()), key="AUDIT_RETENTION_DAYS", value="7"
    )

    assert result.ok, result.errors
    assert result.data.previous_value == str(_DEFAULTS["AUDIT_RETENTION_DAYS"])
    assert result.data.setting.value == "7"
    row = audit[-1]
    assert row.action == "admin.platform_setting.set"
    assert (row.target_kind, row.target_id) == ("PlatformSetting", "AUDIT_RETENTION_DAYS")
    assert row.extra["previous_value"] == str(_DEFAULTS["AUDIT_RETENTION_DAYS"])
    assert row.extra["value"] == "7"


@override_config(AGENT_POD_UID=_DEFAULTS["AGENT_POD_UID"])
def test_a_refusal_names_the_field(permission_resolver):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)
    mutations = PlatformSettingsMutations()

    bad = mutations.set_platform_setting(_info(_operator()), key="AGENT_POD_UID", value="0", confirmed=True)
    unconfirmed = mutations.set_platform_setting(_info(_operator()), key="AGENT_POD_UID", value="42043")

    assert (bad.errors[0].code, bad.errors[0].field) == ("VALIDATION", "value")
    assert (unconfirmed.errors[0].code, unconfirmed.errors[0].field) == ("PRECONDITION", "key")


def test_both_writes_are_step_up_gated():
    from astrolift_identity.step_up import list_gated_resolvers

    gated = {probe.resolver.rsplit(".", 1)[1] for probe in list_gated_resolvers()}
    assert {"set_platform_setting", "reset_platform_setting"} <= gated


# ---- agent secret stores (read-only) ---------------------------------


class _FakeSecrets:
    def __init__(self, store):
        self.store = store

    def get(self, path):
        return self.store.get(path)


def _secrets_world(monkeypatch, store):
    import astrolift_agents.services.agent_cluster as agent_cluster
    import core.app_deploy as app_deploy
    from astrolift_agents.models import AgentEnvironmentSpec
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Vault Org", slug="vault-org")
    cluster = SimpleNamespace(slug="vault-eks", organization_id=org.pk)
    monkeypatch.setattr(agent_cluster, "resolve_agent_cluster", lambda _org: cluster)
    backend = _FakeSecrets({k.format(org=org.guid): v for k, v in store.items()})
    monkeypatch.setattr(app_deploy, "driver_for_capability", lambda _c, _cap: backend)
    AgentEnvironmentSpec.objects.create(
        organization=org,
        name="Claude Dev",
        slug="claude-dev",
        agent_type=AgentEnvironmentSpec.AgentType.CLAUDE,
        secret_refs=[
            {"uri": f"sm:agents/{org.guid}/gh", "env_var": "GITHUB_TOKEN"},
            {"uri": f"sm:agents/{org.guid}/slack", "env_var": "SLACK_TOKEN"},
        ],
    )
    return org


def test_the_secret_inventory_reports_presence_never_values(monkeypatch, permission_resolver):
    from core import platform_secrets

    _secrets_world(monkeypatch, {"sm:agents/{org}/gh": {"value": "private-token-canary"}})
    permission_resolver.grant(Permission.ADMIN_ELEVATE)

    stores = {row.organization_slug: row for row in platform_secrets.list_stores()}
    assert stores["vault-org"].cluster_slug == "vault-eks" and stores["vault-org"].spec_count == 1
    assert stores["vault-org"].shared_cluster is False

    found = PlatformSettingsQuery().astrolift_platform_secret_inventory(
        _info(_operator()), organization_slug="vault-org"
    )
    by_var = {ref.env_var: ref for ref in found.refs}
    assert by_var["GITHUB_TOKEN"].exists is True and by_var["SLACK_TOKEN"].exists is False
    assert {ref.spec_slug for ref in found.refs} == {"claude-dev"}
    assert "private-token-canary" not in repr(found)
    assert (
        PlatformSettingsQuery().astrolift_platform_secret_inventory(
            _info(_operator()), organization_slug="nobody"
        )
        is None
    )


def test_the_secret_stores_are_the_platform_operators_alone(permission_resolver):
    permission_resolver.grant(Permission.ADMIN_ELEVATE)

    with pytest.raises(PermissionDenied):
        PlatformSettingsQuery().astrolift_platform_secret_stores(_info(_org_admin()))
    with pytest.raises(PermissionDenied):
        PlatformSettingsQuery().astrolift_platform_secret_inventory(
            _info(_org_admin()), organization_slug="vault-org"
        )
