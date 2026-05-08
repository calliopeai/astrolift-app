"""
Configurable identity provider mutations.

Two surfaces under test:

1. ``_validate_idp_config`` (pure function): per-kind shape rules
   for oidc/okta/auth0/azure_ad/google/github/cognito/saml/local.
   Tests are pure validation — no DB, no resolver glue.
2. ``IdentityMutation`` resolvers for create / set-active /
   soft-delete: assert the org binding moves with set-active and
   that we refuse to soft-delete the active provider.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import IdentityProvider, Organization
from astrolift_identity.schema.mutations import (
    CreateIdentityProviderInput,
    IdentityMutation,
    SetActiveIdentityProviderInput,
    SoftDeleteByGuidInput,
    _validate_idp_config,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Validation (pure, no DB)
# ---------------------------------------------------------------------------


def _input(**kwargs):
    """Build a CreateIdentityProviderInput-shaped object for validation tests."""
    defaults = dict(
        kind="oidc",
        display_name=None,
        config=None,
        metadata_url=None,
        oidc_discovery_url=None,
        client_id=None,
        client_secret_ref=None,
        set_active=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_validate_local_passes():
    assert _validate_idp_config(_input(kind="local")) is None


def test_validate_oidc_requires_discovery_and_client_id():
    miss_disc = _validate_idp_config(_input(kind="oidc", client_id="x"))
    assert miss_disc.errors[0].field == "oidcDiscoveryUrl"

    miss_cid = _validate_idp_config(
        _input(kind="oidc", oidc_discovery_url="https://example.com/.well-known")
    )
    assert miss_cid.errors[0].field == "clientId"


def test_validate_oidc_passes_with_required_fields():
    assert (
        _validate_idp_config(
            _input(
                kind="oidc",
                oidc_discovery_url="https://example.com/.well-known",
                client_id="abc",
            )
        )
        is None
    )


def test_validate_cognito_requires_user_pool_id_and_region():
    res = _validate_idp_config(
        _input(
            kind="cognito",
            oidc_discovery_url="https://example.com/.well-known",
            client_id="abc",
            config={"region": "us-east-1"},  # missing user_pool_id
        )
    )
    assert res is not None
    assert res.errors[0].field == "config.user_pool_id"


def test_validate_cognito_passes_with_pool_and_region():
    assert (
        _validate_idp_config(
            _input(
                kind="cognito",
                oidc_discovery_url="https://example.com/.well-known",
                client_id="abc",
                config={"user_pool_id": "us-east-1_X", "region": "us-east-1"},
            )
        )
        is None
    )


def test_validate_saml_requires_metadata_url():
    res = _validate_idp_config(_input(kind="saml"))
    assert res is not None
    assert res.errors[0].field == "metadataUrl"


def test_validate_saml_passes_with_metadata():
    assert (
        _validate_idp_config(
            _input(kind="saml", metadata_url="https://idp.example/metadata")
        )
        is None
    )


# ---------------------------------------------------------------------------
# Resolvers (DB)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-idp-test")


@pytest.fixture
def fake_info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _grant(resolver):
    for p in [Permission.ORG_UPDATE, Permission.ORG_READ]:
        resolver.grant(p)


def test_create_local_idp_minimal_input(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id)):
        result = mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(
                kind="local", display_name="local accounts"
            ),
        )
    assert result.ok, result.errors
    assert result.data.kind == "local"
    assert result.data.is_active is False  # not set_active


def test_create_with_set_active_binds_to_org(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id)):
        result = mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(
                kind="local", display_name="primary", set_active=True
            ),
        )
    assert result.ok
    assert result.data.is_active is True

    org.refresh_from_db()
    assert org.identity_provider_id is not None


def test_set_active_switches_org_binding(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id)):
        a = mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(
                kind="local", display_name="a", set_active=True
            ),
        )
        b = mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(kind="local", display_name="b"),
        )
        switched = mut.set_active_identity_provider(
            fake_info, input=SetActiveIdentityProviderInput(id=b.data.id)
        )
    assert switched.ok, switched.errors
    org.refresh_from_db()
    assert (
        IdentityProvider.objects.get(pk=org.identity_provider_id).display_name
        == "b"
    )


def test_soft_delete_refuses_active_provider(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id)):
        a = mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(
                kind="local", display_name="active", set_active=True
            ),
        )
        result = mut.soft_delete_identity_provider(
            fake_info, input=SoftDeleteByGuidInput(id=a.data.id)
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_soft_delete_inactive_provider_succeeds(
    org, fake_info, permission_resolver
):
    _grant(permission_resolver)
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id)):
        mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(
                kind="local", display_name="active", set_active=True
            ),
        )
        b = mut.create_identity_provider(
            fake_info,
            input=CreateIdentityProviderInput(kind="local", display_name="b"),
        )
        result = mut.soft_delete_identity_provider(
            fake_info, input=SoftDeleteByGuidInput(id=b.data.id)
        )
    assert result.ok, result.errors
