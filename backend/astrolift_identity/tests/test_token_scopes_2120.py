"""Narrow cluster scopes and a picker that explains itself (#2120).

Setting a cluster's config from a script used to need an ``admin`` token, the
full-power wildcard. ``write:clusters`` and ``manage:clusters`` cover the
cluster surface without it, and the scope catalog is the one list the picker
reads, derived from enforcement so the two cannot disagree.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity import device_flow
from astrolift_identity.api_tokens import (
    ALLOWED_SCOPES,
    CLI_DEVICE_SCOPES,
    SCOPE_CATALOG,
    SCOPE_MANAGE_CLUSTERS,
    SCOPE_PRESETS,
    SCOPE_WRITE_CLUSTERS,
    permissions_for_scopes,
    token_scope_allows_permission,
)
from core.permissions import Permission


def _token(*scopes):
    return SimpleNamespace(scopes=list(scopes))


def test_write_clusters_updates_a_cluster_and_nothing_else_on_it():
    token = _token(SCOPE_WRITE_CLUSTERS)

    assert token_scope_allows_permission(token, "cluster.update")
    assert not token_scope_allows_permission(token, "cluster.manage")
    assert not token_scope_allows_permission(token, "cluster.register")
    assert not token_scope_allows_permission(token, "cluster.unregister")


def test_manage_clusters_operates_a_cluster_and_nothing_else_on_it():
    token = _token(SCOPE_MANAGE_CLUSTERS)

    assert token_scope_allows_permission(token, "cluster.manage")
    assert not token_scope_allows_permission(token, "cluster.update")
    assert not token_scope_allows_permission(token, "cluster.register")


def test_registering_and_removing_a_cluster_stay_admin_only():
    every_narrow_scope = [s for s in ALLOWED_SCOPES if s != "admin"]
    granted = permissions_for_scopes(every_narrow_scope)

    assert "cluster.register" not in granted
    assert "cluster.unregister" not in granted


def test_the_catalog_is_exactly_the_allowed_scopes():
    assert {s.value for s in SCOPE_CATALOG} == set(ALLOWED_SCOPES)
    assert len(SCOPE_CATALOG) == len(ALLOWED_SCOPES)


def test_every_preset_names_only_real_scopes():
    for _key, _label, scopes in SCOPE_PRESETS:
        assert set(scopes) <= ALLOWED_SCOPES


def test_admin_grants_every_permission():
    assert permissions_for_scopes(["admin"]) == {p.value for p in Permission}


def test_what_the_picker_shows_is_what_enforcement_does():
    for scope in SCOPE_CATALOG:
        for permission in permissions_for_scopes([scope.value]):
            assert token_scope_allows_permission(_token(scope.value), permission)


def test_a_cluster_operator_device_login_asks_for_the_cluster_surface():
    scopes = device_flow.token_scopes_for_client_kind("cli-operator")

    assert set(CLI_DEVICE_SCOPES) <= set(scopes)
    assert {SCOPE_WRITE_CLUSTERS, SCOPE_MANAGE_CLUSTERS} <= set(scopes)
    assert "admin" not in scopes


def test_the_plain_cli_login_is_unchanged():
    scopes = device_flow.token_scopes_for_client_kind("cli")

    assert scopes == list(CLI_DEVICE_SCOPES)
    assert SCOPE_WRITE_CLUSTERS not in scopes


# ---- the GraphQL surface --------------------------------------------------


@pytest.mark.django_db
def test_effective_permissions_are_scopes_narrowed_by_the_owners_roles(monkeypatch):
    from astrolift_identity.schema import types as identity_types

    monkeypatch.setattr(
        "core.schema.types.permission_analysis._held_slugs",
        lambda user, org_id: {"cluster.update": ["operator"], "app.read": ["operator"]},
    )
    token = SimpleNamespace(
        scopes=[SCOPE_WRITE_CLUSTERS, SCOPE_MANAGE_CLUSTERS, "read:apps"], user=object(), organization_id=1
    )

    # manage:clusters asks for cluster.manage, which the owner does not hold.
    assert identity_types.token_effective_permissions(token) == ["app.read", "cluster.update"]


@pytest.mark.django_db
def test_the_catalog_greys_out_a_scope_the_callers_roles_never_grant(monkeypatch, permission_resolver):
    from astrolift_identity.schema.queries import IdentityQuery
    from core.tenancy import TenantContext, tenant_context

    monkeypatch.setattr(
        "core.schema.types.permission_analysis._held_slugs",
        lambda user, org_id: {"cluster.update": ["operator"], "api_token.create": ["operator"]},
    )
    permission_resolver.grant(Permission.API_TOKEN_CREATE)
    info = SimpleNamespace(context=SimpleNamespace(user=SimpleNamespace(is_authenticated=True)))

    with tenant_context(TenantContext(organization_id=1)):
        catalog = IdentityQuery().astrolift_api_token_scope_catalog(info)

    by_value = {s.value: s for s in catalog.scopes}
    assert by_value[SCOPE_WRITE_CLUSTERS].available is True
    assert by_value[SCOPE_MANAGE_CLUSTERS].available is False
    assert by_value[SCOPE_MANAGE_CLUSTERS].unavailable_reason
    assert {p.key for p in catalog.presets} >= {"read_only", "cli", "ci_deploy", "cluster_operator"}
