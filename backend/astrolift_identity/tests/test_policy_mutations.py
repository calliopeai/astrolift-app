"""Tests for ABAC policy CRUD + the createdBy / updatedBy projections (#466).

Two surfaces under test:

1. ``create_policy`` / ``update_policy`` stamp ``created_by`` /
   ``updated_by`` from the active tenant actor so the policies table can
   render a "Created by" column without a follow-up audit-log join.
2. ``astrolift_policies`` resolver surfaces ``created_by_username`` /
   ``updated_by_username`` (or null when the FK is null for legacy rows).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Policy
from astrolift_identity.schema.mutations import (
    CreatePolicyInput,
    IdentityMutation,
    UpdatePolicyInput,
)
from astrolift_identity.schema.queries import IdentityQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-policy-test")


@pytest.fixture
def fake_info():
    return SimpleNamespace(context=SimpleNamespace(request=None))


def _grant(resolver):
    for p in [Permission.ORG_UPDATE, Permission.ORG_READ]:
        resolver.grant(p)


def _operator(email: str = "operator@astrolift.dev"):
    user, _ = User.objects.get_or_create(email=email, defaults={"username": email.split("@")[0]})
    return user


def test_create_policy_stamps_created_and_updated_by(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    actor = _operator()
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        result = mut.create_policy(
            fake_info,
            input=CreatePolicyInput(
                name="deny-prod-deploys-after-hours",
                slug="deny-prod-deploys-after-hours",
                scope_level="ORG",
                effect="DENY",
                action_pattern="app.deploy",
            ),
        )
    assert result.ok, result.errors
    assert result.data.created_by_username == actor.get_username()
    assert result.data.updated_by_username == actor.get_username()

    row = Policy.objects.get(slug="deny-prod-deploys-after-hours")
    assert row.created_by_id == actor.id
    assert row.updated_by_id == actor.id


def test_update_policy_rotates_updated_by_only(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    creator = _operator()
    editor = _operator(email="editor@astrolift.dev")
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=creator.id)):
        created = mut.create_policy(
            fake_info,
            input=CreatePolicyInput(
                name="deny-foo",
                slug="deny-foo",
                scope_level="ORG",
                effect="DENY",
            ),
        )
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=editor.id)):
        upd = mut.update_policy(
            fake_info,
            input=UpdatePolicyInput(id=created.data.id, action_pattern="bar.*"),
        )
    assert upd.ok, upd.errors
    assert upd.data.created_by_username == creator.get_username()
    assert upd.data.updated_by_username == editor.get_username()

    row = Policy.objects.get(slug="deny-foo")
    assert row.created_by_id == creator.id
    assert row.updated_by_id == editor.id


def test_policies_query_surfaces_usernames(org, fake_info, permission_resolver):
    _grant(permission_resolver)
    actor = _operator()
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        mut.create_policy(
            fake_info,
            input=CreatePolicyInput(
                name="deny-x",
                slug="deny-x",
                scope_level="ORG",
                effect="DENY",
            ),
        )
        rows = IdentityQuery().astrolift_policies(fake_info)
    assert len(rows) == 1
    assert rows[0].created_by_username == actor.get_username()
    assert rows[0].updated_by_username == actor.get_username()


def test_policies_query_handles_null_creator_legacy_row(org, fake_info, permission_resolver):
    """Policies created before the mutation started stamping the actor —
    or via raw ORM .create() — surface null usernames rather than 500."""
    _grant(permission_resolver)
    Policy.objects.create(
        organization=org,
        name="legacy",
        slug="legacy",
        scope_level="ORG",
        effect="DENY",
        action_pattern="*",
    )
    with tenant_context(TenantContext(organization_id=org.id)):
        rows = IdentityQuery().astrolift_policies(fake_info)
    assert len(rows) == 1
    assert rows[0].created_by_username is None
    assert rows[0].updated_by_username is None


def test_create_policy_with_no_actor_leaves_fields_null(org, fake_info, permission_resolver):
    """System/anonymous context (no actor_user_id) leaves the audit
    fields null but still succeeds."""
    _grant(permission_resolver)
    mut = IdentityMutation()
    with tenant_context(TenantContext(organization_id=org.id)):  # no actor_user_id
        result = mut.create_policy(
            fake_info,
            input=CreatePolicyInput(
                name="system-policy",
                slug="system-policy",
                scope_level="ORG",
                effect="DENY",
            ),
        )
    assert result.ok, result.errors
    assert result.data.created_by_username is None
    assert result.data.updated_by_username is None
